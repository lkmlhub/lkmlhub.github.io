"""Polite HTTP client: rate limited, concurrency capped, honours Retry-After, backs off."""

import gzip
import http.client
import random
import threading
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

from . import USER_AGENT

MAX_BYTES = 16 * 1024 * 1024


class FetchError(Exception):
    def __init__(self, message, status=0, backoff=False):
        super().__init__(message)
        self.status = status
        self.backoff = backoff  # the server asked us to go away for longer than we will wait


@dataclass
class Response:
    status: int
    body: bytes = b""
    headers: dict = field(default_factory=dict)


def urllib_transport(url, headers, timeout=60):
    """Default transport: returns Response; never raises for HTTP status codes."""
    req = urllib.request.Request(url, headers=headers)
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status, hdrs, body = r.status, r.headers, r.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            status, hdrs, body = e.code, e.headers, e.read(65536)
        hdrs = {k.lower(): v for k, v in hdrs.items()}
        if hdrs.get("content-encoding") == "gzip" and body:
            body = gzip.decompress(body)
    except (EOFError, zlib.error, http.client.HTTPException) as e:
        # a truncated or garbled body (cut connection, short gzip stream): a retryable transport error
        raise FetchError("incomplete response for %s: %s" % (url, e)) from e
    if len(body) > MAX_BYTES:
        raise FetchError("response too large")
    return Response(status, body, hdrs)


def parse_retry_after(value, now=None):
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
        return max(0.0, when.timestamp() - (now if now is not None else time.time()))
    except (TypeError, ValueError):
        return None


class Fetcher:
    def __init__(
        self,
        user_agent=USER_AGENT,
        min_gap=0.5,
        max_concurrent=2,
        max_retries=4,
        max_backoff=120.0,
        transport=urllib_transport,
        sleep=time.sleep,
        clock=time.monotonic,
        jitter=True,
    ):
        self.user_agent = user_agent
        self.min_gap = min_gap
        self.max_retries = max_retries
        self.max_backoff = max_backoff
        self.transport = transport
        self.sleep = sleep
        self.clock = clock
        self.jitter = jitter
        self._slots = threading.Semaphore(max_concurrent)
        self._lock = threading.Lock()
        self._next_start = {}
        self.requests = 0
        self.bytes = 0

    def _host(self, url):
        return url.split("/", 3)[2] if "//" in url else url

    def _reserve(self, host, extra_delay=0.0):
        with self._lock:
            now = self.clock()
            start = max(now, self._next_start.get(host, 0.0))
            if extra_delay:
                start = max(start, now + extra_delay)
            self._next_start[host] = start + self.min_gap
        return max(0.0, start - now)

    def _penalise(self, host, delay):
        with self._lock:
            self._next_start[host] = max(self._next_start.get(host, 0.0), self.clock() + delay)

    def get(self, url, etag=None, last_modified=None):
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        host = self._host(url)
        last_status = 0
        for attempt in range(self.max_retries + 1):
            wait = self._reserve(host)
            if wait:
                self.sleep(wait)
            retry_delay = None
            with self._slots:
                try:
                    resp = self.transport(url, headers)
                    self.requests += 1
                    self.bytes += len(resp.body)
                except (OSError, FetchError) as e:
                    resp = None
                    last_status = 0
            if resp is not None:
                last_status = resp.status
                if resp.status in (200, 304):
                    return resp
                if resp.status in (429, 503):
                    retry_delay = parse_retry_after(resp.headers.get("retry-after"))
                    if retry_delay is None:
                        retry_delay = 5.0 * (2 ** attempt)
                    elif retry_delay > self.max_backoff:
                        raise FetchError(
                            "server asked to retry after %ds, stopping" % retry_delay,
                            resp.status,
                            backoff=True,
                        )
                elif resp.status in (500, 502, 504):
                    retry_delay = 2.0 * (2 ** attempt)
                else:
                    hint = " (the server rejects requests without a descriptive User-Agent)" if resp.status == 403 else ""
                    raise FetchError("HTTP %d for %s%s" % (resp.status, url, hint), resp.status)
            else:
                retry_delay = 2.0 * (2 ** attempt)
            if attempt == self.max_retries:
                break
            retry_delay = min(retry_delay, self.max_backoff)
            if self.jitter:
                retry_delay += random.uniform(0, 0.25 * retry_delay)
            self._penalise(host, retry_delay)
        raise FetchError("giving up on %s (last status %s)" % (url, last_status), last_status)
