#!/bin/sh
# Builds a tiny throwaway git repo plus an invented "agent audit report" about it.
# ALL of this is demo data: the code, the report and the findings are made up for
# testing citecheck. The report deliberately contains one correct claim, one
# claim whose line moved, one invented quote, one line past end of file and one
# file that does not exist.
set -e
dir="${1:?usage: make-demo.sh DIR}"
rm -rf "$dir"
mkdir -p "$dir/src"
cd "$dir"
git init -q -b main
git config user.email demo@example.invalid
git config user.name demo
cat > src/auth.py <<'EOF'
import hashlib

def check_password(stored, given):
    digest = hashlib.md5(given.encode()).hexdigest()
    return digest == stored

def build_query(name):
    return "SELECT * FROM users WHERE name = '" + name + "'"
EOF
git add -A && git commit -q -m "demo: initial"
# a later edit pushes everything down two lines
printf '# helpers for login\n\n' | cat - src/auth.py > src/auth.tmp && mv src/auth.tmp src/auth.py
git add -A && git commit -q -m "demo: add header comment"
cat > REPORT.md <<'EOF'
# Demo audit report (invented, for testing citecheck)

1. Weak hash in `src/auth.py:6`

```python
digest = hashlib.md5(given.encode()).hexdigest()
```

2. SQL built by string concatenation, see src/auth.py:9

```python
return "SELECT * FROM users WHERE name = '" + name + "'"
```

3. Hard-coded secret at src/config.py:3

4. Timing leak at src/auth.py:40-44

5. Unsalted compare at src/auth.py:7

```python
return secrets.compare_digest(digest, stored)
```
EOF
echo "demo repo and REPORT.md written to $dir"
