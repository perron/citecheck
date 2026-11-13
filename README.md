# citecheck

Check that the `file:line` citations and quoted code in an AI agent's report
match the code in your repo.

Audit and review agents write reports full of references like
`src/auth.py:42` plus a quoted snippet. Those references are sometimes
invented, and they go stale as soon as the file is edited. You usually find out
after you have filed the issue or forwarded the report. `citecheck` reads the
report, opens every cited file, and tells you which citations are real.

**Before:** you open each cited file by hand to see whether the agent's finding
points at real code.
**After:** `citecheck REPORT.md` prints one line per citation and exits `1` if
any is missing, past the end of the file, moved, quotes code that is not
there, or has no quote to check at all.

No model, no network, no dependencies. Python 3.9+ standard library only
(the test suite passes on Python 3.9 and 3.13).

## Quick start

```sh
git clone https://github.com/perron/citecheck
cd citecheck
python3 -m venv .venv
. .venv/bin/activate
pip install .
```

Then, from your repo root:

```sh
citecheck REPORT.md
```

## Try it on the demo repo

`examples/make-demo.sh` builds a throwaway git repo plus an **invented** report
about it. All of it is demo data; no real project or finding is involved. The
report has one correct citation and four deliberately wrong ones.

```sh
sh examples/make-demo.sh /tmp/citecheck-demo
cd /tmp/citecheck-demo
citecheck REPORT.md
```

```text
ok    REPORT.md:3  src/auth.py:6  verified: snippet at lines 6-6
FAIL  REPORT.md:9  src/auth.py:9  moved: snippet is at line 10, not 9
FAIL  REPORT.md:15  src/config.py:3  missing-file: no such file
FAIL  REPORT.md:17  src/auth.py:40-44  out-of-range: cites lines 40-44 but the file has 10 lines
FAIL  REPORT.md:19  src/auth.py:7  quote-not-found: snippet does not appear anywhere in the file

5 citations: 1 missing-file, 1 moved, 1 out-of-range, 1 quote-not-found, 1 verified. 4 problem(s).
```

Check the same report against the commit before the demo's last edit (the
report was written against the later version, so most citations now fail):

```sh
citecheck REPORT.md --ref HEAD~1
```

```text
FAIL  REPORT.md:3  src/auth.py:6  moved: snippet is at line 4, not 6
FAIL  REPORT.md:9  src/auth.py:9  out-of-range: cites lines 9-9 but the file has 8 lines
FAIL  REPORT.md:15  src/config.py:3  missing-file: not in HEAD~1: fatal: path 'src/config.py' does not exist in 'HEAD~1'
FAIL  REPORT.md:17  src/auth.py:40-44  out-of-range: cites lines 40-44 but the file has 8 lines
FAIL  REPORT.md:19  src/auth.py:7  quote-not-found: snippet does not appear anywhere in the file

5 citations: 1 missing-file, 1 moved, 2 out-of-range, 1 quote-not-found. 5 problem(s).
```

JSON findings work too (still in the demo repo; the path below is relative to
where you cloned `citecheck`, so adjust it):

```sh
citecheck /path/to/citecheck/examples/findings-demo.json --json
```

That prints `"total": 3`, `"problems": 1` and counts of 2 `verified` and 1
`quote-not-found`.

## What it reads

**Markdown.** Citations are `path:LINE`, `path:START-END`, `path:LINE:COL`,
`path#L10-L20`, and GitHub `blob` URLs ending in `#L10-L20` (the ref in the URL
is not used; every split of the URL into ref and path is tried, shortest ref
first, and the first path that exists under `--root` wins). The path needs a
file extension, a leading dot (`.gitignore:1`), or one of a few well-known
names (`Dockerfile`, `Makefile`, `Gemfile`, ...). Other extensionless paths such
as `bin/run:12` are not recognised. Other URLs are ignored. Bare names with an
uncommon extension, such as `redis.svc:6379`, are treated as `host:port` and
ignored unless a file of that name exists. Citations inside fenced code blocks
are ignored.

If a line has exactly one citation and the next non-blank line opens a fenced
code block (also inside a list item or a `>` blockquote), that block is the
quote to verify. A line with two or more citations gets no quote, so each of
them is `range-only`.

**JSON.** Any object with a `file`, `path`, `filename` or `file_path` string and
a line given as `line`, `start_line`/`end_line`, `lines: [a, b]` or similar,
anywhere in the document. A `snippet`, `code`, `quote`, `excerpt` or `evidence`
string on the same object (or a parent finding) is the quote.

## What each result means

| Status | Meaning | Counts as problem |
|---|---|---|
| `verified` | the quoted snippet starts inside the cited lines (and, if it uses `...`, ends inside them too) | no |
| `range-only` | the lines exist, but there was no quote to compare, so nothing about the claim is verified | yes, unless `--allow-range-only` |
| `moved` | the snippet exists in the file but not at the cited lines; the real line is shown | yes |
| `quote-not-found` | the snippet is not anywhere in the file | yes |
| `out-of-range` | cited lines are past the end of the file, or the range is invalid | yes |
| `missing-file` | no such file (or not present in the `--ref` revision) | yes |
| `outside-root` | absolute path, `..` or symlink that leaves `--root` (never read) | yes |
| `unreadable` | file over 5 MB or unreadable | yes |

Snippet matching compares whole lines. It ignores indentation, runs of
whitespace (so `"a  b"` and `"a b"` count as equal) and blank lines. A quote
that is only part of a line, such as `x =` for the line `x = 1`, is
`quote-not-found`. A line that is only `...` (or `// ...`, `# ...`) lets the
quote skip code; the pieces must still appear in order, and the whole quote
must fit inside the cited lines, so cite the whole range you are quoting.

Exit codes: `0` all clean, `1` at least one problem (or no citations found:
nothing verified is not a pass), `2` usage error such as an unreadable report,
invalid or too deeply nested JSON, or a `--ref` that is not a commit in the
repository at `--root`. Control characters in a report are escaped in the
output.

## Options

| Option | Effect |
|---|---|
| `--root DIR` | repo root the cited paths are relative to (default `.`) |
| `--ref REV` | read files from a git revision of the repo at `--root` instead of the working tree (`GIT_DIR` and similar variables are ignored) |
| `--slack N` | accept a snippet that starts up to N lines outside the cited range |
| `--allow-range-only` | do not fail citations that have no quote (they are still printed as `weak`) |
| `--json` | machine-readable output |

Reports can be given as several files, or `-` for stdin.

## What it does not do

- A `verified` citation proves the quoted code is where the report says. It does
  not prove the report's claim about that code is right. Read the findings.
- `range-only` proves almost nothing: a line number exists. That is why it fails
  by default; `--allow-range-only` is for reports you know carry no quotes.
- Only fenced blocks directly under a citation (and JSON snippet fields) are
  compared. Inline `` `code` `` in prose is not.
- It does not follow renamed or moved files and does not check function names.
- Quotes must be whole lines. In JSON, parent snippets are inherited only by
  children whose key contains `loc` (such as `locations`).

## Inspiration

Inspired by the independent record-verification phase in Cloudflare's
[security-audit-skill](https://github.com/cloudflare/security-audit-skill),
where fresh agents re-verify the source claims in an audit's findings. citecheck
is a clean-room, from-scratch tool; no code was taken from that project. It is
the cheap mechanical first pass you can run on any report, from any agent, in
CI, before spending a model on verification.

## Contributing / how to help

Bug reports with a small report plus the code it was checked against are the
most useful thing you can send. Citation styles that other agents produce but
`citecheck` misses are welcome: add a case to `tests/test_citecheck.py`, then
make it pass.

```sh
pip install -e '.[dev]'
python -m pytest -q
```

## Point your agent at it

Running an audit or review agent? Have it run `citecheck` on its own report
before it hands the report over, and fix or drop every `FAIL` line (a
citation with no quote is one). See
[AGENTS.md](AGENTS.md) for instructions written for agents.

## License

MIT. See [LICENSE](LICENSE).
