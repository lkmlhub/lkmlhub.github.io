import gzip
import http.client
import unittest
from unittest import mock

from lorehub import fetch


class FakeResponse:
    status = 200

    def __init__(self, body, headers):
        self._body = body
        self.headers = headers

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TransportErrorTests(unittest.TestCase):
    def test_truncated_gzip_is_a_fetch_error_and_is_retried(self):
        good = gzip.compress(b"<feed/>")
        bad = good[:-6]
        calls = []

        def fake_urlopen(req, timeout=60):
            calls.append(1)
            body = bad if len(calls) == 1 else good
            return FakeResponse(body, {"Content-Encoding": "gzip"})

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(fetch.FetchError):
                fetch.urllib_transport("https://example.test/x", {})
            f = fetch.Fetcher(sleep=lambda s: None, jitter=False, min_gap=0)
            calls.clear()
            resp = f.get("https://example.test/x")
        self.assertEqual(resp.body, b"<feed/>")
        self.assertEqual(len(calls), 2)

    def test_incomplete_read_is_a_fetch_error(self):
        def fake_urlopen(req, timeout=60):
            raise http.client.IncompleteRead(b"abc", 10)

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(fetch.FetchError):
                fetch.urllib_transport("https://example.test/x", {})

    def test_persistent_truncation_gives_up_with_fetch_error(self):
        bad = gzip.compress(b"hello world" * 100)[:-8]

        def fake_urlopen(req, timeout=60):
            return FakeResponse(bad, {"Content-Encoding": "gzip"})

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            f = fetch.Fetcher(sleep=lambda s: None, jitter=False, min_gap=0, max_retries=2)
            with self.assertRaises(fetch.FetchError):
                f.get("https://example.test/x")


if __name__ == "__main__":
    unittest.main()
