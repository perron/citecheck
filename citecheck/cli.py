"""Command line entry point."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List

from . import __version__
from .extract import extract
from .verify import Source, check

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
    p.add_argument("--require-quote", action="store_true",
                   help="treat citations with no snippet (range-only) as problems")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--version", action="version", version=f"citecheck {__version__}")
    return p


def main(argv: List[str] = None) -> int:
    args = build_parser().parse_args(argv)
    if not os.path.isdir(args.root):
        print(f"citecheck: --root {args.root!r} is not a directory", file=sys.stderr)
        return 2
    if args.slack < 0:
        print("citecheck: --slack must be 0 or more", file=sys.stderr)
        return 2
    src = Source(args.root, args.ref)
    cache: dict = {}
    results = []
    for rp in args.reports:
        try:
            if rp == "-":
                text, name = sys.stdin.read(), "<stdin>"
            else:
                with open(rp, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                name = rp
            cites = extract(text, name)
        except OSError as exc:
            print(f"citecheck: cannot read {rp}: {exc}", file=sys.stderr)
            return 2
        except ValueError as exc:
            print(f"citecheck: {rp} is not valid JSON: {exc}", file=sys.stderr)
            return 2
        for c in cites:
            results.append(check(c, src, args.slack, cache))

    def bad(r):
        return (not r.ok) or (args.require_quote and r.status == "range-only")

    problems = [r for r in results if bad(r)]
    counts: dict = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1

    if args.json:
        print(json.dumps({
            "total": len(results),
            "problems": len(problems),
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
            print(f"{label:<5} {r.citation.source}  {r.citation.text}  {r.status}"
                  + (f": {r.detail}" if r.detail and label != "ok" else ""))
        if not results:
            print("no citations found")
        else:
            parts = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
            print(f"\n{len(results)} citations: {parts}. {len(problems)} problem(s).")
    if not results:
        return 1  # nothing to verify is not a pass
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
