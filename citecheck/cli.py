"""Command line entry point."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List

from . import __version__
from .extract import extract
from .verify import Source, check, check_ref

FAIL_LABEL = "FAIL"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="citecheck",
        description="Check that the file:line citations and quoted snippets in an AI "
                    "agent's report (Markdown or JSON findings) match the code.")
    p.add_argument("reports", nargs="+", metavar="REPORT",
                   help="Markdown or JSON report files; '-' reads stdin")
    p.add_argument("--root", default=".", help="repo root the citations are relative to (default: .)")
    p.add_argument("--ref", help="check against this git revision of --root instead of the working tree")
    p.add_argument("--slack", type=int, default=0, metavar="N",
                   help="accept a snippet that starts up to N lines outside the cited range (default 0)")
    p.add_argument("--allow-range-only", action="store_true",
                   help="do not fail citations that have no quote to compare (they stay marked 'weak'; "
                        "only the line numbers are checked)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--version", action="version", version=f"citecheck {__version__}")
    return p


def _clean(text: str) -> str:
    """Escape control characters so a crafted report cannot rewrite the terminal output."""
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode() for ch in text)


def main(argv: List[str] = None) -> int:
    args = build_parser().parse_args(argv)
    if not os.path.isdir(args.root):
        print(f"citecheck: --root {args.root!r} is not a directory", file=sys.stderr)
        return 2
    if args.slack < 0:
        print("citecheck: --slack must be 0 or more", file=sys.stderr)
        return 2
    if args.ref is not None:
        err = check_ref(args.root, args.ref)
        if err:
            print(f"citecheck: {err}", file=sys.stderr)
            return 2
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    src = Source(args.root, args.ref)
    cache: dict = {}
    results = []
    for rp in args.reports:
        try:
            if rp == "-":
                text, name = sys.stdin.read(), "<stdin>"
            else:
                with open(rp, "r", encoding="utf-8-sig", errors="replace") as fh:
                    text = fh.read()
                name = rp
            cites = extract(text, name)
        except OSError as exc:
            print(f"citecheck: cannot read {rp}: {exc}", file=sys.stderr)
            return 2
        except ValueError as exc:
            print(f"citecheck: {rp} is not valid JSON: {exc}", file=sys.stderr)
            return 2
        except RecursionError:
            print(f"citecheck: {rp} is nested too deeply", file=sys.stderr)
            return 2
        for c in cites:
            r = check(c, src, args.slack, cache)
            if r is not None:
                results.append(r)

    def bad(r):
        if r.status == "range-only":
            return not args.allow_range_only
        return not r.ok

    problems = [r for r in results if bad(r)]
    counts: dict = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1

    if args.json:
        print(json.dumps({
            "total": len(results),
            "problems": len(problems),
            "no_citations_found": not results,
            "counts": counts,
            "results": [{"source": r.citation.source, "citation": r.citation.text,
                         "path": r.citation.path, "start": r.citation.start, "end": r.citation.end,
                         "has_snippet": bool(r.citation.snippet and r.citation.snippet.strip()),
                         "status": r.status, "detail": r.detail, "problem": bad(r)} for r in results],
        }, indent=2))
    else:
        for r in results:
            if bad(r):
                label = FAIL_LABEL
            elif r.status == "range-only":
                label = "weak"
            else:
                label = "ok"
            print(_clean(f"{label:<5} {r.citation.source}  {r.citation.text}  {r.status}"
                         + (f": {r.detail}" if r.detail else "")))
        if not results:
            print("no citations found")
        else:
            parts = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
            print(f"\n{len(results)} citation{'' if len(results) == 1 else 's'}: {parts}. {len(problems)} problem(s).")
    if not results:
        return 1  # nothing to verify is not a pass
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
