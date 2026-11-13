"""Check one citation against a working tree or a git revision."""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .extract import Citation

MAX_BYTES = 5 * 1024 * 1024
OK = {"verified", "range-only"}


@dataclass
class Result:
    citation: Citation
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in OK


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


_ELLIPSIS = re.compile(r"^(?:(?://|#|--|/\*|\*)\s*)?(?:\.\.\.|\u2026)\s*(?:\*/)?$")


def _segments(snippet: str) -> List[List[str]]:
    segs: List[List[str]] = [[]]
    for raw in snippet.splitlines():
        n = _norm(raw)
        if not n:
            continue
        if _ELLIPSIS.match(n):
            if segs[-1]:
                segs.append([])
            continue
        segs[-1].append(n)
    return [s for s in segs if s]


def find_snippet(file_lines: List[str], snippet: str) -> List[Tuple[int, int]]:
    """Return (first_line, last_line) 1-based for every place the snippet occurs.

    Blank lines and whitespace differences are ignored. Lines that are only an
    ellipsis split the snippet into segments that must appear in order.
    """
    segs = _segments(snippet)
    if not segs:
        return []
    kept = [(i + 1, _norm(l)) for i, l in enumerate(file_lines) if _norm(l)]
    norm = [t for _, t in kept]
    nums = [i for i, _ in kept]

    def occurrences(seg, frm):
        m = len(seg)
        for p in range(frm, len(norm) - m + 1):
            if norm[p:p + m] == seg:
                yield p

    hits = []
    for p0 in occurrences(segs[0], 0):
        pos = p0 + len(segs[0])
        last = pos - 1
        good = True
        for seg in segs[1:]:
            nxt = next(occurrences(seg, pos), None)
            if nxt is None:
                good = False
                break
            pos = nxt + len(seg)
            last = pos - 1
        if good:
            hits.append((nums[p0], nums[last]))
    return hits


class Source:
    """Reads files from a directory root, or from a git revision of it."""

    def __init__(self, root: str, ref: Optional[str] = None):
        self.root = os.path.realpath(root)
        self.ref = ref

    def read(self, path: str) -> Tuple[Optional[List[str]], Optional[str], str]:
        """Return (lines, error_status, detail)."""
        rel = path
        if os.path.isabs(rel):
            ap = os.path.realpath(rel)
            if ap == self.root or ap.startswith(self.root + os.sep):
                rel = os.path.relpath(ap, self.root)
            else:
                return None, "outside-root", "absolute path is outside --root"
        norm = os.path.normpath(rel)
        if norm == ".." or norm.startswith(".." + os.sep):
            return None, "outside-root", "path escapes --root"
        norm = norm.replace(os.sep, "/")
        if self.ref:
            return self._read_git(norm)
        full = os.path.realpath(os.path.join(self.root, norm))
        if not (full == self.root or full.startswith(self.root + os.sep)):
            return None, "outside-root", "path resolves outside --root (symlink?)"
        if not os.path.exists(full):
            return None, "missing-file", "no such file"
        if os.path.isdir(full):
            return None, "missing-file", "is a directory"
        try:
            if os.path.getsize(full) > MAX_BYTES:
                return None, "unreadable", "file larger than 5 MB"
            with open(full, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            return None, "unreadable", str(exc)
        return data.decode("utf-8", "replace").splitlines(), None, ""

    def _read_git(self, norm: str):
        try:
            p = subprocess.run(["git", "-C", self.root, "show", f"{self.ref}:./{norm}"],
                               capture_output=True)
        except OSError as exc:
            return None, "unreadable", f"git not runnable: {exc}"
        if p.returncode != 0:
            err = p.stderr.decode("utf-8", "replace").strip().splitlines()
            return None, "missing-file", f"not in {self.ref}: {err[0] if err else 'git show failed'}"
        if len(p.stdout) > MAX_BYTES:
            return None, "unreadable", "file larger than 5 MB"
        return p.stdout.decode("utf-8", "replace").splitlines(), None, ""


def check(c: Citation, src: Source, slack: int = 0, cache: Optional[dict] = None) -> Result:
    cache = cache if cache is not None else {}
    if c.path not in cache:
        cache[c.path] = src.read(c.path)
    lines, err, detail = cache[c.path]
    if err:
        return Result(c, err, detail)
    n = len(lines)
    if c.start < 1 or c.end < c.start or c.end > n:
        return Result(c, "out-of-range", f"cites lines {c.start}-{c.end} but the file has {n} lines")
    if c.snippet is None or not c.snippet.strip():
        return Result(c, "range-only", "lines exist; no snippet to compare")
    hits = find_snippet(lines, c.snippet)
    if not hits:
        return Result(c, "quote-not-found", "snippet does not appear anywhere in the file")
    for a, b in hits:
        if c.start - slack <= a <= c.end + slack:
            if slack and (a < c.start or b > c.end):
                return Result(c, "verified", f"snippet at lines {a}-{b} (within slack {slack})")
            return Result(c, "verified", f"snippet at lines {a}-{b}")
    # Nearest occurrence elsewhere
    a, b = min(hits, key=lambda h: abs(h[0] - c.start))
    where = f"{a}-{b}" if a != b else str(a)
    more = f" (+{len(hits) - 1} more)" if len(hits) > 1 else ""
    return Result(c, "moved", f"snippet is at line {where}{more}, not {c.start}-{c.end}"
                  if c.start != c.end else f"snippet is at line {where}{more}, not {c.start}")
