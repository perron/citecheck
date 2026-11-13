import io
import json
import os
import subprocess
import sys
import textwrap

import pytest

from citecheck.cli import main
from citecheck.extract import extract, extract_json, extract_markdown
from citecheck.verify import Source, check, find_snippet

CODE = textwrap.dedent("""\
    import os

    def load(path):
        with open(path) as fh:
            data = fh.read()
        return data.strip()

    def save(path, text):
        with open(path, "w") as fh:
            fh.write(text)
""")


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "io.py").write_text(CODE)
    return tmp_path


def run(repo, report, *extra, capsys=None, name="r.md"):
    p = repo / name
    p.write_text(report)
    code = main([str(p), "--root", str(repo), *extra])
    return code, capsys.readouterr().out


# ---- extraction -------------------------------------------------------

def cites(text):
    return [(c.path, c.start, c.end) for c in extract_markdown(text, "x.md")]


def test_extract_forms():
    t = ("a src/io.py:3 b `src/io.py:4-6` c src/io.py:7:12 d src/io.py#L8-L9 "
         "e https://github.com/o/r/blob/main/src/io.py#L1-L2 f src/io.py:5\u20137")
    assert sorted(cites(t)) == sorted([
        ("src/io.py", 3, 3), ("src/io.py", 4, 6), ("src/io.py", 7, 7), ("src/io.py", 8, 9),
        ("src/io.py", 1, 2), ("src/io.py", 5, 7)])


def test_extract_ignores_urls_ports_versions_times():
    t = "see https://example.com/a.js:10 and example.com:8080 and v1.2.3 at 12:30 and localhost:3000"
    assert cites(t) == []


def test_extract_ignores_fenced_code_citations():
    t = "text\n```\nsrc/io.py:3\n```\nafter src/io.py:4\n"
    assert cites(t) == [("src/io.py", 4, 4)]


def test_snippet_attached_only_for_single_citation_line():
    t = "bug at src/io.py:3\n\n```py\ndef load(path):\n```\n"
    c = extract_markdown(t, "x.md")
    assert c[0].snippet == "def load(path):"
    t2 = "bug at src/io.py:3 and src/io.py:8\n\n```py\ndef load(path):\n```\n"
    assert all(c.snippet is None for c in extract_markdown(t2, "x.md"))


def test_snippet_not_attached_when_text_intervenes():
    t = "bug at src/io.py:3\nsome words\n```py\ndef load(path):\n```\n"
    assert extract_markdown(t, "x.md")[0].snippet is None


def test_json_findings_shapes():
    data = {"findings": [
        {"file": "src/io.py", "line": 3, "snippet": "def load(path):"},
        {"path": "src/io.py", "start_line": 4, "end_line": 5, "code": "with open(path) as fh:"},
        {"title": "x", "evidence": "return data.strip()",
         "locations": [{"file": "src/io.py", "line": "6"}]},
        {"file": "src/io.py", "lines": [8, 9]},
        {"unrelated": {"path": "config/x", "note": "no line"}},
    ]}
    cs = extract_json(data, "r.json")
    assert [(c.path, c.start, c.end) for c in cs] == [
        ("src/io.py", 3, 3), ("src/io.py", 4, 5), ("src/io.py", 6, 6), ("src/io.py", 8, 9)]
    assert cs[2].snippet == "return data.strip()"
    assert cs[3].snippet is None


def test_extract_autodetects_json_stdin_name():
    cs = extract('[{"file": "a.py", "line": 1}]', "<stdin>")
    assert len(cs) == 1


# ---- snippet matching -------------------------------------------------

def test_find_snippet_ignores_whitespace_and_blank_lines():
    lines = CODE.splitlines()
    assert find_snippet(lines, "  def   load(path):\n\n      with open(path) as fh:") == [(3, 4)]


def test_find_snippet_ellipsis_in_order():
    lines = CODE.splitlines()
    assert find_snippet(lines, "def load(path):\n...\nreturn data.strip()") == [(3, 6)]
    assert find_snippet(lines, "return data.strip()\n// ...\ndef load(path):") == []


def test_find_snippet_multiple_hits():
    assert find_snippet(CODE.splitlines(), "with open(") == []  # partial line is not a match
    assert len(find_snippet(CODE.splitlines(), 'with open(path) as fh:')) == 1


# ---- verification -----------------------------------------------------

def test_verified_and_range_only(repo, capsys):
    code, out = run(repo, "x src/io.py:3\n\n```\ndef load(path):\n```\n\nand src/io.py:8\n", capsys=capsys)
    assert code == 0
    assert "1 range-only" in out and "1 verified" in out
    assert "weak" in out


def test_require_quote_fails_range_only(repo, capsys):
    code, out = run(repo, "src/io.py:8\n", "--require-quote", capsys=capsys)
    assert code == 1


def test_moved_reports_actual_line(repo, capsys):
    code, out = run(repo, "src/io.py:8\n\n```\nreturn data.strip()\n```\n", capsys=capsys)
    assert code == 1
    assert "moved" in out and "line 6" in out


def test_slack_accepts_small_shift(repo, capsys):
    rep = "src/io.py:5\n\n```\nreturn data.strip()\n```\n"
    assert run(repo, rep, capsys=capsys)[0] == 1
    assert run(repo, rep, "--slack", "1", capsys=capsys)[0] == 0


def test_snippet_inside_range_start_counts(repo, capsys):
    code, out = run(repo, "src/io.py:3-6\n\n```\nwith open(path) as fh:\n```\n", capsys=capsys)
    assert code == 0


def test_invented_quote(repo, capsys):
    code, out = run(repo, "src/io.py:3\n\n```\ndef load(path, mode):\n```\n", capsys=capsys)
    assert code == 1 and "quote-not-found" in out


def test_out_of_range_and_zero(repo, capsys):
    assert "out-of-range" in run(repo, "src/io.py:99\n", capsys=capsys)[1]
    assert "out-of-range" in run(repo, "src/io.py:0\n", capsys=capsys)[1]
    assert "out-of-range" in run(repo, "src/io.py:9-3\n", capsys=capsys)[1]


def test_missing_file_and_directory(repo, capsys):
    out = run(repo, "nope/x.py:1 and src/io.py:1 and src.d/x.py:1\n", capsys=capsys)[1]
    assert out.count("missing-file") >= 2


def test_path_traversal_and_absolute_outside(repo, tmp_path_factory, capsys):
    outside = tmp_path_factory.mktemp("out") / "secret.txt"
    outside.write_text("hello\n")
    code, out = run(repo, f"../x/secret.txt:1 and {outside}:1\n", capsys=capsys)
    assert code == 1 and out.count("FAIL") == 2 and "2 outside-root" in out


def test_symlink_escape_blocked(repo, tmp_path_factory, capsys):
    outside = tmp_path_factory.mktemp("o2") / "s.txt"
    outside.write_text("secret\n")
    os.symlink(outside, repo / "link.txt")
    code, out = run(repo, "link.txt:1\n", capsys=capsys)
    assert code == 1 and "outside-root" in out


def test_absolute_path_inside_root_ok(repo, capsys):
    code, out = run(repo, f"{repo}/src/io.py:3\n", capsys=capsys)
    assert code == 0


def test_no_citations_is_not_a_pass(repo, capsys):
    code, out = run(repo, "nothing cited here\n", capsys=capsys)
    assert code == 1 and "no citations found" in out


def test_json_report_and_output(repo, capsys):
    rep = json.dumps({"findings": [
        {"file": "src/io.py", "line": 3, "snippet": "def load(path):"},
        {"file": "src/io.py", "line": 8, "snippet": "def load(path):"}]})
    code, out = run(repo, rep, "--json", capsys=capsys, name="r.json")
    d = json.loads(out)
    assert code == 1 and d["total"] == 2 and d["problems"] == 1
    assert d["counts"] == {"verified": 1, "moved": 1}


def test_crlf_and_tabs(repo, capsys):
    (repo / "w.txt").write_bytes(b"a\r\n\tfoo(1)\r\nb\r\n")
    code, out = run(repo, "w.txt:2\n\n```\nfoo(1)\n```\n", capsys=capsys)
    assert code == 0


def test_bad_args_exit_2(repo, capsys):
    p = repo / "r.md"
    p.write_text("src/io.py:1\n")
    assert main([str(p), "--root", str(repo / "nope")]) == 2
    assert main([str(repo / "missing.md"), "--root", str(repo)]) == 2
    assert main([str(p), "--root", str(repo), "--slack", "-1"]) == 2
    bad = repo / "bad.json"
    bad.write_text("{not json")
    assert main([str(bad), "--root", str(repo)]) == 2


def test_stdin(repo, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("see src/io.py:3\n"))
    assert main(["-", "--root", str(repo)]) == 0


# ---- git revision mode ------------------------------------------------

def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid",
                    "-c", "user.name=t", *a], check=True, capture_output=True)


def test_ref_mode_checks_old_revision(repo, capsys):
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "one")
    (repo / "src" / "io.py").write_text("# new header\n" + CODE)
    git(repo, "commit", "-q", "-am", "two")
    rep = "src/io.py:3\n\n```\ndef load(path):\n```\n"
    assert run(repo, rep, capsys=capsys)[0] == 1            # working tree: moved to line 4
    assert run(repo, rep, "--ref", "HEAD~1", capsys=capsys)[0] == 0
    out = run(repo, "new.py:1\n", "--ref", "HEAD", capsys=capsys)[1]
    assert "missing-file" in out and "not in HEAD" in out


def test_module_entry_point(repo):
    (repo / "r.md").write_text("src/io.py:3\n")
    p = subprocess.run([sys.executable, "-m", "citecheck", "r.md"], cwd=repo, capture_output=True,
                       text=True, env={**os.environ, "PYTHONPATH": os.path.dirname(os.path.dirname(__file__))})
    assert p.returncode == 0, p.stderr
