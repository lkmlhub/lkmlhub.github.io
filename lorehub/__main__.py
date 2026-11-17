"""Command line: python3 -m lorehub {crawl,build,all}."""

import argparse
import sys
import time

from . import __version__
from .backfill import Config, run_backfill as backfill_run
from .complete import Config as CompleteConfig, run_completion
from .crawl import CacheSource, LiveSource, crawl
from .fetch import Fetcher
from .retention import Limits, apply_meta_limits, effective_retention, trim_to_budget
from .state import MAX_DIFF_MSGS, State


def log(msg):
    print(msg, flush=True)


def split_lists(value):
    return [x.strip() for x in value.split(",") if x.strip()] if value else None


def add_common(p):
    p.add_argument("--state", default="state", help="state directory (default: state)")
    p.add_argument("--lists", help="comma separated list names (default: all)")


def add_backfill_args(p, default_days=180):
    p.add_argument("--backfill-days", type=float, default=default_days,
                   help="history horizon in days for the backfill, 0 turns it off (default %g)" % default_days)
    p.add_argument("--backfill-min", type=float, default=12, help="backfill time budget per run in minutes")
    p.add_argument("--backfill-requests", type=int, default=900, help="max backfill requests per run")
    p.add_argument("--backfill-total", type=int, default=12000, help="max backfill requests over all runs")
    p.add_argument("--backfill-list-pages", type=int, default=60,
                   help="max 200-entry pages per list (a safety cap, busy lists stop earlier when their newest "
                        "1000 patches are stored)")
    p.add_argument("--max-state-mb", type=float, default=650,
                   help="data branch budget in MB: above it the per list patch limit is lowered (0 = off, default 650)")
    p.add_argument("--max-diff-msgs", type=int, default=MAX_DIFF_MSGS,
                   help="patch messages per list that keep a diff, whole series newest first (default %d)" % MAX_DIFF_MSGS)


def backfill_config(args, budget_s=None, limits=None):
    return Config(days=args.backfill_days, budget_s=args.backfill_min * 60 if budget_s is None else budget_s,
                  max_requests=args.backfill_requests, max_total=args.backfill_total,
                  list_pages=args.backfill_list_pages, keep_bodies=not getattr(args, "no_bodies", False),
                  fill_patches=int((limits.max_diff_msgs if limits else args.max_diff_msgs) * 1.1))


def limits_of(args, state=None):
    lim = Limits(retention_days=effective_retention(args.backfill_days), max_diff_msgs=args.max_diff_msgs)
    return apply_meta_limits(state, lim) if state is not None else lim


def add_crawl_args(p):
    p.add_argument("--budget-min", type=float, default=25, help="crawl time budget in minutes")
    p.add_argument("--deep-budget", type=int, default=12,
                   help="max search-atom requests per run to sweep gaps on busy lists (default 12, 0 = off)")
    p.add_argument("--deep-pages", type=int, default=2, help="max search pages per list per run")
    p.add_argument("--complete-requests", type=int, default=300,
                   help="max thread requests per run to complete the conversation of incomplete series (0 = off)")
    p.add_argument("--complete-min", type=float, default=8, help="thread completion time budget per run in minutes")
    add_backfill_args(p)
    p.add_argument("--from-cache", help="replay raw feeds saved by --save-raw instead of fetching")
    p.add_argument("--save-raw", help="save raw HTTP responses into this directory")
    p.add_argument("--no-bodies", action="store_true", help="do not store message bodies")


def add_build_args(p):
    p.add_argument("--out", default="public", help="output directory (default: public)")
    p.add_argument("--base", default="/", help="site base path, only used by 404.html (default /)")
    p.add_argument("--max-site-mb", type=float, default=700, help="site size cap in MB (whole series are left out above it)")
    p.add_argument("--now", type=int, help="override the current time (unix seconds)")


def run_crawl(args):
    state = State(args.state)
    limits = limits_of(args, state)
    now = int(time.time())
    started = time.monotonic()
    if args.from_cache:
        source = CacheSource(args.from_cache)
    else:
        source = LiveSource(Fetcher(), args.save_raw)
    summary = crawl(
        state, source, now, names=split_lists(args.lists), budget_s=args.budget_min * 60,
        deep_budget=args.deep_budget, deep_pages=args.deep_pages,
        keep_bodies=not args.no_bodies, log=log, limits=limits,
    )
    log("crawl summary: %s" % summary)
    if args.backfill_days > 0 and not summary["aborted"]:
        # the normal crawl has priority: the backfill only gets what is left of the run, at most --backfill-min
        left = args.budget_min * 60 - (time.monotonic() - started)
        budget = min(args.backfill_min * 60, left)
        if budget >= 30:
            summary["backfill"] = backfill_run(state, source, now, backfill_config(args, budget, limits),
                                               names=split_lists(args.lists), log=log, limits=limits)
        else:
            log("backfill skipped: the crawl used the whole time budget")
    if args.complete_requests > 0 and not summary["aborted"] and not summary.get("backfill", {}).get("aborted"):
        left = args.budget_min * 60 - (time.monotonic() - started)
        budget = min(args.complete_min * 60, left)
        touched = set(summary.get("touched", [])) | set(summary.get("backfill", {}).get("touched", []))
        if budget >= 30:
            summary["completion"] = run_completion(
                state, source, now, CompleteConfig(max_requests=args.complete_requests, budget_s=budget),
                names=split_lists(args.lists), touched=touched, log=log, limits=limits, days=args.backfill_days)
        else:
            log("thread completion skipped: the run used the whole time budget")
    if args.max_state_mb > 0:
        trimmed = trim_to_budget(state, now, int(args.max_state_mb * 1e6), limits, log=log)
        if trimmed:
            summary["trimmed_to"] = trimmed
    return summary


def run_backfill(args):
    state = State(args.state)
    source = CacheSource(args.from_cache) if args.from_cache else LiveSource(Fetcher(), args.save_raw)
    names = split_lists(args.lists)
    if args.backfill_days <= 0:
        sys.exit("--backfill-days must be positive")
    limits = limits_of(args, state)
    return backfill_run(state, source, int(time.time()), backfill_config(args, None, limits), names=names, log=log,
                        limits=limits)


def run_complete(args):
    state = State(args.state)
    source = CacheSource(args.from_cache) if args.from_cache else LiveSource(Fetcher(), args.save_raw)
    now = int(time.time())
    return run_completion(state, source, now, CompleteConfig(max_requests=args.complete_requests, budget_s=args.complete_min * 60),
                          names=split_lists(args.lists), touched=None, log=log, limits=limits_of(args, state), days=args.backfill_days)


def run_build(args):
    from .build import build_site

    state = State(args.state)
    now = args.now or int(time.time())
    report = build_site(
        state, args.out, now, names=split_lists(args.lists), base=args.base,
        max_site_mb=args.max_site_mb, log=log,
    )
    log("built %s: %d lists, %.1f MB" % (args.out, report["lists"], report["bytes"] / 1e6))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lorehub", description=__doc__)
    ap.add_argument("--version", action="version", version="lorehub " + __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("crawl", help="fetch feeds into the state directory")
    add_common(p)
    add_crawl_args(p)
    p = sub.add_parser("backfill", help="run only the resumable history backfill (search atom)")
    add_common(p)
    add_backfill_args(p)
    p.add_argument("--no-bodies", action="store_true", help="do not store message bodies")
    p.add_argument("--from-cache", help="replay raw responses saved by --save-raw")
    p.add_argument("--save-raw", help="save raw HTTP responses into this directory")
    p = sub.add_parser("complete", help="run only the thread completion queue (whole threads of incomplete series)")
    add_common(p)
    p.add_argument("--complete-requests", type=int, default=300, help="max thread requests in this run")
    p.add_argument("--complete-min", type=float, default=8, help="time budget in minutes")
    add_backfill_args(p)
    p.add_argument("--from-cache", help="replay raw responses saved by --save-raw")
    p.add_argument("--save-raw", help="save raw HTTP responses into this directory")
    p = sub.add_parser("build", help="build the static site from the state directory")
    add_common(p)
    add_build_args(p)
    p = sub.add_parser("all", help="crawl then build")
    add_common(p)
    add_crawl_args(p)
    add_build_args(p)
    args = ap.parse_args(argv)
    if args.cmd == "crawl":
        run_crawl(args)
    elif args.cmd == "backfill":
        run_backfill(args)
    elif args.cmd == "complete":
        run_complete(args)
    elif args.cmd == "build":
        run_build(args)
    else:
        run_crawl(args)
        run_build(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
