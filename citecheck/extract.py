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


_EXT = r"[A-Za-z][A-Za-z0-9]{0,7}"
_PATH = r"(?:\.{0,2}/)?(?:[\w.@+\-]+/)*[\w.@+\-]+\." + _EXT
_LEAD = r"(?<![\w/.@+\-:])"
_COLON = re.compile(_LEAD + "(" + _PATH + r")(?::|#L)(\d+)(?:(?:-|\u2013)L?(\d+))?(?::\d+)?(?![\w])")
_GH_BLOB = re.compile(
    r"https?://github\.com/[^/\s]+/[^/\s]+/blob/[^/\s]+/([^\s)#`]+)#L(\d+)(?:-L?(\d+))?")
_URL = re.compile(r"https?://[^\s)>\]`]+")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
# hosts that look like file.ext followed by :port
_TLDS = {"com", "org", "net", "io", "dev", "ai", "co", "app", "edu", "gov", "local", "localhost"}


def _mask(text: str, m: "re.Match[str]") -> str:
    return text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]


def _line_citations(line: str):
    """Yield (path, start, end, text) for each citation on one Markdown line."""
    found = []
    work = line
    for m in list(_GH_BLOB.finditer(work)):
        s = int(m.group(2))
        e = int(m.group(3)) if m.group(3) else s
        found.append((m.start(), m.group(1), s, e, m.group(0)))
        work = _mask(work, m)
    for m in list(_URL.finditer(work)):
        work = _mask(work, m)
    for m in _COLON.finditer(work):
        path = m.group(1)
        if "/" not in path and path.rsplit(".", 1)[-1].lower() in _TLDS:
            continue
        s = int(m.group(2))
        e = int(m.group(3)) if m.group(3) else s
        found.append((m.start(), path, s, e, m.group(0)))
    found.sort(key=lambda t: t[0])
    for _, path, s, e, text in found:
        yield path, s, e, text


def extract_markdown(text: str, name: str) -> List[Citation]:
    lines = text.splitlines()
    out: List[Citation] = []
    i = 0
    n = len(lines)
    while i < n:
        fm = _FENCE.match(lines[i])
        if fm:  # skip a fenced block that was not claimed as a snippet by the line above
            fence = fm.group(1)
            i += 1
            while i < n and not (lines[i].strip().startswith(fence[0] * len(fence))
                                 and set(lines[i].strip()) <= {fence[0]}):
                i += 1
            i += 1
            continue
        cites = list(_line_citations(lines[i]))
        if cites:
            snippet = None
            if len(cites) == 1:
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                if j < n and _FENCE.match(lines[j]):
                    fence = _FENCE.match(lines[j]).group(1)
                    body = []
                    k = j + 1
                    while k < n and not (set(lines[k].strip()) <= {fence[0]}
                                         and len(lines[k].strip()) >= len(fence)):
                        body.append(lines[k])
                        k += 1
                    snippet = "\n".join(body)
            for path, s, e, ctext in cites:
                out.append(Citation(f"{name}:{i + 1}", path, s, e,
                                    snippet if len(cites) == 1 else None, ctext))
        i += 1
    return out


_FILE_KEYS = ("file", "path", "filename", "file_path", "filepath")
_START_KEYS = ("line", "start_line", "startLine", "line_start", "lineStart", "start")
_END_KEYS = ("end_line", "endLine", "line_end", "lineEnd", "end")
_SNIP_KEYS = ("snippet", "code", "quote", "excerpt", "evidence", "code_snippet")


def _parse_lines(d: dict):
    start = end = None
    for k in _START_KEYS:
        if k in d:
            v = d[k]
            if isinstance(v, bool):
                continue
            if isinstance(v, int):
                start = v
            elif isinstance(v, str):
                m = re.fullmatch(r"\s*L?(\d+)(?:\s*[-\u2013]\s*L?(\d+))?\s*", v)
                if m:
                    start = int(m.group(1))
                    if m.group(2):
                        end = int(m.group(2))
            elif isinstance(v, list) and v and all(isinstance(x, int) for x in v[:2]):
                start = v[0]
                if len(v) > 1:
                    end = v[1]
            if start is not None:
                break
    if "lines" in d and start is None:
        v = d["lines"]
        if isinstance(v, list) and v and all(isinstance(x, int) for x in v[:2]):
            start = v[0]
            end = v[1] if len(v) > 1 else None
    for k in _END_KEYS:
        if end is None and isinstance(d.get(k), int) and not isinstance(d.get(k), bool):
            end = d[k]
    if start is None:
        return None
    return start, (end if end is not None else start)


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
                walk(v, f"{pointer}.{k}", snip)
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
