"""Check one citation against a working tree or a git revision."""
from __future__ import annotations

import os
import re
import stat
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .extract import Citation

MAX_BYTES = 5 * 1024 * 1024
OK = {"verified"}
WEAK = "range-only"
# Environment variables that would point git at a different repository.
_GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
                 "GIT_CEILING_DIRECTORIES", "GIT_PREFIX")


def git_env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROP}


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
        if "\x00" in rel:
            return None, "missing-file", "invalid path (NUL byte)"
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
        try:
            st = os.stat(full)
            if stat.S_ISDIR(st.st_mode):
                return None, "missing-file", "is a directory"
            if not stat.S_ISREG(st.st_mode):
                return None, "unreadable", "not a regular file"
            if st.st_size > MAX_BYTES:
                return None, "unreadable", "file larger than 5 MB"
            with open(full, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            return None, "unreadable", str(exc)
        return data.decode("utf-8", "replace").splitlines(), None, ""

    def _read_git(self, norm: str):
        spec = f"{self.ref}:./{norm}"
        try:
            sz = subprocess.run(["git", "-C", self.root, "cat-file", "-s", spec],
                                capture_output=True, env=git_env())
            if sz.returncode == 0 and int(sz.stdout.strip() or 0) > MAX_BYTES:
                return None, "unreadable", "file larger than 5 MB"
            p = subprocess.run(["git", "-C", self.root, "show", spec],
                               capture_output=True, env=git_env())
        except (OSError, ValueError) as exc:
            return None, "unreadable", f"git not runnable: {exc}"
        if p.returncode != 0:
            err = p.stderr.decode("utf-8", "replace").strip().splitlines()
            return None, "missing-file", f"not in {self.ref}: {err[0] if err else 'git show failed'}"
        if len(p.stdout) > MAX_BYTES:
            return None, "unreadable", "file larger than 5 MB"
        return p.stdout.decode("utf-8", "replace").splitlines(), None, ""


def check_ref(root: str, ref: str) -> Optional[str]:
    """Return an error message if ref is not a usable revision of the repo at root."""
    if not ref or ref.startswith("-"):
        return "--ref must be a git revision (not empty, not starting with '-')"
    try:
        p = subprocess.run(["git", "-C", root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                           capture_output=True, env=git_env())
    except OSError as exc:
        return f"git not runnable: {exc}"
    if p.returncode != 0:
        return f"{ref!r} is not a commit in the git repository at --root"
    return None


def check(c: Citation, src: Source, slack: int = 0, cache: Optional[dict] = None) -> Optional[Result]:
    """Check one citation. Returns None for 'soft' citations (host:port look-alikes)
    whose file does not exist, which are not file references at all."""
    cache = cache if cache is not None else {}
    paths = [c.path] + list(c.alts or [])
    chosen = None
    for pth in paths:
        if pth not in cache:
            try:
                cache[pth] = src.read(pth)
            except (OSError, ValueError) as exc:
                cache[pth] = (None, "unreadable", str(exc))
        if cache[pth][1] is None:
            chosen = pth
            break
    if chosen is None:
        first = cache[c.path]
        if c.soft and first[1] == "missing-file":
            return None
        # prefer the most informative error among the candidates
        return Result(c, first[1], first[2])
    lines = cache[chosen][0]
    n = len(lines)
    if c.start < 1 or c.end < c.start or c.end > n:
        return Result(c, "out-of-range", f"cites lines {c.start}-{c.end} but the file has {n} lines")
    if c.snippet is None or not c.snippet.strip():
        return Result(c, WEAK, "lines exist, but there is no quote to compare, so nothing is verified")
    hits = find_snippet(lines, c.snippet)
    if not hits:
        return Result(c, "quote-not-found", "snippet does not appear anywhere in the file")
    gapped = len(_segments(c.snippet)) > 1
    for a, b in hits:
        if not (c.start - slack <= a <= c.end + slack):
            continue
        if gapped and b > c.end + slack:
            continue  # an ellipsis quote must fit inside the cited lines
        return Result(c, "verified", f"snippet at lines {a}-{b}")
    a, b = min(hits, key=lambda h: abs(h[0] - c.start))
    where = f"{a}-{b}" if a != b else str(a)
    more = f" (+{len(hits) - 1} more)" if len(hits) > 1 else ""
    cited = f"{c.start}-{c.end}" if c.start != c.end else str(c.start)
    why = " (a quote with '...' must fit inside the cited lines)" if gapped and c.start - slack <= a <= c.end + slack else ""
    return Result(c, "moved", f"snippet is at line {where}{more}, not {cited}{why}")
