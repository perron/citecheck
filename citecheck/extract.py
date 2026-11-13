"""Pull citations out of Markdown text and JSON findings."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Citation:
    source: str            # where the citation was found, e.g. "report.md:12" or "report.json:$.findings[0]"
    path: str              # path as written
    start: int
    end: int
    snippet: Optional[str] = None
    text: str = ""         # the citation text as written
    alts: Optional[List[str]] = None   # other candidate paths (GitHub URLs with slashed refs)
    soft: bool = False     # looks like host:port or similar; dropped if the file does not exist


_EXT = r"[A-Za-z][A-Za-z0-9]{0,7}"
_NAMES = ("Dockerfile", "Makefile", "Rakefile", "Gemfile", "Procfile", "Justfile", "Jenkinsfile",
          "Vagrantfile", "Brewfile", "Containerfile", "LICENSE", "CODEOWNERS")
_PATH = (r"(?:\.{0,2}/)?(?:[\w.@+\-]+/)*(?:[\w.@+\-]+\." + _EXT + r"|\.[A-Za-z][\w.\-]*|(?:"
         + "|".join(_NAMES) + r")(?![\w.\-]))")
_LEAD = r"(?<![\w/.@+\-:])"
_COLON = re.compile(_LEAD + "(" + _PATH + r")(?::|#L)(\d+)(?:(?:-|\u2013)L?(\d+))?(?::\d+)?(?![\w])")
_GH_BLOB = re.compile(
    r"https?://github\.com/[^/\s]+/[^/\s]+/blob/([^\s)#`]+)#L(\d+)(?:-L?(\d+))?", re.I)
_URL = re.compile(r"https?://[^\s)>\]`]+")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
# A bare name such as redis.svc:6379 is treated as a file reference only if its
# extension is a common source/config extension, or the file exists.
_KNOWN_EXT = set("""py pyi js jsx mjs cjs ts tsx go rs java kt kts scala c h cc cpp cxx hpp hh cs rb php swift m mm
sh bash zsh fish ps1 bat md mdx rst txt json jsonc yaml yml toml ini cfg conf env html htm css scss sass less sql lua
pl pm r dart ex exs erl hs ml vue svelte xml gradle lock mk tf proto graphql gql zig nim jl clj groovy tex csv
cmake bzl nix dockerfile""".split())


def _mask(text: str, m: "re.Match[str]") -> str:
    return text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]


def _line_citations(line: str):
    """Yield (path, start, end, text) for each citation on one Markdown line."""
    found = []
    work = line
    for m in list(_GH_BLOB.finditer(work)):
        s = int(m.group(2))
        e = int(m.group(3)) if m.group(3) else s
        segs = m.group(1).split("/")
        # the ref may itself contain slashes: try every possible split, shortest ref first
        cands = ["/".join(segs[k:]) for k in range(1, len(segs))] or [m.group(1)]
        found.append((m.start(), cands, s, e, m.group(0), False))
        work = _mask(work, m)
    for m in list(_URL.finditer(work)):
        work = _mask(work, m)
    for m in _COLON.finditer(work):
        path = m.group(1)
        base = path.rsplit("/", 1)[-1]
        soft = "/" not in path and not base.startswith(".") and (
            base in _NAMES or base.rsplit(".", 1)[-1].lower() in _KNOWN_EXT) is False
        s = int(m.group(2))
        e = int(m.group(3)) if m.group(3) else s
        found.append((m.start(), [path], s, e, m.group(0), soft))
    found.sort(key=lambda t: t[0])
    for _, cands, s, e, text, soft in found:
        yield cands, s, e, text, soft


_BQ = re.compile(r"^(?:\s*>)+ ?")


def _unq(line: str) -> str:
    """Strip blockquote markers so fences inside a quote are still seen."""
    return _BQ.sub("", line)


def _fence_open(line: str):
    m = _FENCE.match(_unq(line))
    return m.group(1) if m else None


def _is_close(line: str, fence: str) -> bool:
    t = _unq(line).strip()
    return len(t) >= len(fence) and set(t) <= {fence[0]}


def extract_markdown(text: str, name: str) -> List[Citation]:
    lines = text.splitlines()
    out: List[Citation] = []
    i = 0
    n = len(lines)
    while i < n:
        fence = _fence_open(lines[i])
        if fence:  # skip a fenced block that was not claimed as a snippet by the line above
            i += 1
            while i < n and not _is_close(lines[i], fence):
                i += 1
            i += 1
            continue
        cites = list(_line_citations(lines[i]))
        if cites:
            snippet = None
            if len(cites) == 1:
                j = i + 1
                while j < n and not _unq(lines[j]).strip():
                    j += 1
                if j < n and _fence_open(lines[j]):
                    fence = _fence_open(lines[j])
                    body = []
                    k = j + 1
                    while k < n and not _is_close(lines[k], fence):
                        body.append(_unq(lines[k]))
                        k += 1
                    snippet = "\n".join(body)
            for cands, s_, e_, ctext, soft in cites:
                out.append(Citation(f"{name}:{i + 1}", cands[0], s_, e_,
                                    snippet if len(cites) == 1 else None, ctext,
                                    cands[1:] or None, soft))
        i += 1
    return out


_FILE_KEYS = ("file", "path", "filename", "file_path", "filepath")
_START_KEYS = ("line", "start_line", "startLine", "line_start", "lineStart")
_END_KEYS = ("end_line", "endLine", "line_end", "lineEnd")
_SNIP_KEYS = ("snippet", "code", "quote", "excerpt", "evidence", "code_snippet")


def _num(v):
    """int, integral float or digit string -> int; anything else -> None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v == int(v):
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"\s*L?\d+\s*", v):
        return int(v.strip().lstrip("L"))
    return None


def _parse_lines(d: dict):
    """Return (start, end) or None when the object has no line key. A line key whose
    value cannot be read yields (0, 0), which is reported as out-of-range."""
    start = end = None
    seen = False
    for k in _START_KEYS:
        if k in d:
            seen = True
            v = d[k]
            if isinstance(v, str):
                m = re.fullmatch(r"\s*L?(\d+)(?:\s*[-\u2013]\s*L?(\d+))?\s*", v)
                if m:
                    start = int(m.group(1))
                    if m.group(2):
                        end = int(m.group(2))
            elif isinstance(v, list) and len(v) >= 1 and _num(v[0]) is not None:
                start = _num(v[0])
                if len(v) > 1:
                    end = _num(v[1])
            else:
                start = _num(v)
            if start is not None:
                break
    if start is None and isinstance(d.get("lines"), list) and d["lines"]:
        seen = True
        v = d["lines"]
        start = _num(v[0])
        if len(v) > 1:
            end = _num(v[1])
    for k in _END_KEYS:
        if k in d and end is None:
            e = _num(d[k])
            if e is None:
                seen, start = True, None
            else:
                end = e
    if not seen:
        return None
    if start is None:
        return 0, 0
    return start, (end if end is not None else start)


def _is_location_key(k) -> bool:
    k = str(k).lower()
    return "location" in k or k in ("loc", "locs")


def extract_json(data, name: str) -> List[Citation]:
    out: List[Citation] = []

    def walk(node, pointer, inherited):
        if isinstance(node, dict):
            snip = inherited
            for k in _SNIP_KEYS:
                if isinstance(node.get(k), str) and node[k].strip():
                    snip = node[k]
                    break
            fpath = next((node[k] for k in _FILE_KEYS if isinstance(node.get(k), str)), None)
            rng = _parse_lines(node) if fpath else None
            if fpath and rng:
                out.append(Citation(f"{name}:{pointer}", fpath, rng[0], rng[1], snip,
                                    f"{fpath}:{rng[0]}" + (f"-{rng[1]}" if rng[1] != rng[0] else "")))
            for k, v in node.items():
                # a parent's snippet is inherited only by children whose key says 'location'
                walk(v, f"{pointer}.{k}", snip if _is_location_key(k) else None)
        elif isinstance(node, list):
            for idx, v in enumerate(node):
                walk(v, f"{pointer}[{idx}]", inherited)

    walk(data, "$", None)
    return out


def extract(text: str, name: str, force_json: Optional[bool] = None) -> List[Citation]:
    is_json = force_json
    if is_json is None:
        is_json = name.lower().endswith(".json") or text.lstrip()[:1] in ("{", "[") and _looks_json(text)
    if is_json:
        return extract_json(json.loads(text), name)
    return extract_markdown(text, name)


def _looks_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except ValueError:
        return False
