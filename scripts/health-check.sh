#!/usr/bin/env bash
# Repo health checks — shared by pre-commit and CI. Exits non-zero on any failure.
# Usage: scripts/health-check.sh [--staged]
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
STAGED=0; [ "${1:-}" = "--staged" ] && STAGED=1
fail=0
ok(){ printf '\033[32m✓\033[0m %s\n' "$*"; }
bad(){ printf '\033[31m✗\033[0m %s\n' "$*"; fail=1; }

# Files to check: staged (pre-commit) or all tracked (CI).
if [ "$STAGED" = 1 ]; then
  mapfile -t FILES < <(git diff --cached --name-only --diff-filter=ACM)
else
  mapfile -t FILES < <(git ls-files)
fi

echo "== local-hermes health check ($([ $STAGED = 1 ] && echo staged || echo all) =="

# 1. Python files compile.
for f in "${FILES[@]}"; do
  case "$f" in
    *.py) python3 -m py_compile "$f" 2>/dev/null && ok "py $f" || bad "py compile failed: $f" ;;
  esac
done

# 2. Shell scripts pass syntax check.
for f in "${FILES[@]}"; do
  case "$f" in
    *.sh) bash -n "$f" 2>/dev/null && ok "sh $f" || bad "sh syntax failed: $f" ;;
  esac
done

# 3. prompts.jsonl is valid JSONL (one JSON object per line).
if printf '%s\n' "${FILES[@]}" | grep -qx 'eval/prompts.jsonl'; then
  if python3 -c "import json,sys; [json.loads(l) for l in open('eval/prompts.jsonl') if l.strip()]" 2>/dev/null; then
    ok "prompts.jsonl valid"
  else bad "prompts.jsonl has invalid JSON"; fi
fi

# 4. No secrets / tokens / private keys in tracked content.
SECRET_RE='(nvapi-[A-Za-z0-9]{20}|sk-[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,})'
leaks=$(git grep -nEI "$SECRET_RE" -- "${FILES[@]}" 2>/dev/null | grep -viE 'dummy|placeholder|example|your[_-]?key|xoxb-|test' || true)
[ -z "$leaks" ] && ok "no secrets" || { bad "possible secret(s):"; echo "$leaks"; }

# 5. No hardcoded user-home paths (portability).
home=$(git grep -nE '/home/[a-z]' -- "${FILES[@]}" 2>/dev/null | grep -vE 'README|LEARNINGS|EVAL_PLAN|research/' || true)
[ -z "$home" ] && ok "no hardcoded home paths" || { bad "hardcoded home path(s):"; echo "$home"; }

# 6. Required files present.
for req in README.md LICENSE serve.sh setup.sh eval/run_eval.py .gitignore; do
  [ -e "$req" ] && ok "have $req" || bad "missing $req"
done

echo "== $([ $fail = 0 ] && echo PASS || echo FAIL) =="
exit $fail