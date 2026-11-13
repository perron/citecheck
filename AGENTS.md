# AGENTS.md

## What this repo is

`citecheck`: a dependency-free Python CLI that verifies `file:line` citations
and quoted snippets in a report (Markdown or JSON) against a repo or a git
revision.

## If you are an agent that wrote a report

Before handing a report to a human, run from the repo root:

```sh
citecheck YOUR_REPORT.md --require-quote
```

Fix or remove every line that starts with `FAIL`. For `moved`, correct the line
number to the one shown. For `quote-not-found`, re-read the code and quote what
is really there, or drop the finding. For `missing-file` and `out-of-range`,
the citation is wrong. Do not edit the repo to make a citation pass. A pass only
shows the quotes are accurate; it does not show your conclusions are.

## If you are changing this code

- Python 3.9+, standard library only. Do not add runtime dependencies.
- Layout: `citecheck/extract.py` (find citations), `citecheck/verify.py`
  (check one against the files), `citecheck/cli.py` (arguments, output, exit
  codes), `tests/test_citecheck.py`.
- Run the tests: `pip install -e '.[dev]' && python -m pytest -q`. All must pass.
- Add a test for every behavior change. Keep README examples exactly
  reproducible: if output changes, re-run the README commands and paste the
  real output.
- Never read outside `--root`. The `outside-root` checks are a security
  property; keep their tests.
- Exit codes: 0 clean, 1 problem or no citations, 2 usage error.
- Demo data stays labeled as invented.
