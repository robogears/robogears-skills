#!/usr/bin/env bash
# Tests for scripts/deny-rules.sh.
# Run:  bash tests/test_deny_rules.sh
#
# 1) Pattern behaviour: every rule is run through a Python copy of Claude Code's own Bash
#    wildcard matcher (2.1.274 `C4`: escape regex specials, `*` → `.*`, a single trailing
#    " *" also matches the bare command, whole-command anchored, dot matches newlines).
#    Dangerous commands must be blocked; the loop's normal commands must NOT be.
# 2) add / remove / status against throwaway project folders — touches nothing real.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
DR="$HERE/../scripts/deny-rules.sh"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

echo "deny-rules.sh"

# --- 1) which commands the rules block (base branch: develop, work branch: overnight-loop/x)
bash "$DR" list develop overnight-loop/2026-09-29-0600 > /tmp/deny-rules-list.$$
python3 - /tmp/deny-rules-list.$$ <<'PY'
import re, sys
rules = [l.strip() for l in open(sys.argv[1]) if l.strip()]
def c4(pattern, command):
    p = pattern.strip()
    body = re.sub(r"[.+?^${}()|[\]\\'\"]", lambda m: "\\" + m.group(0), p).replace("*", ".*")
    if body.endswith(" .*") and p.count("*") == 1:
        body = body[:-3] + "( .*)?"
    return re.match("^" + body + "$", command, re.S) is not None
def blocked(cmd):
    return [r for r in rules if c4(r[len("Bash("):-1], cmd)]
BLOCK = [
    "git push --force", "git push origin feat --force", "git push --force-with-lease origin x",
    "git push -f", "git push -f origin x", "git push origin x -f", "git push origin +x",
    "git push origin main", "git push -u origin main", "git push origin HEAD:main",
    "git push origin x:refs/heads/main", "git push origin master", "git push origin develop",
    "git push origin main --dry-run", "git push origin --delete x", "git push origin -d x",
    "git push origin :x", "gh pr merge 12 --squash", "gh pr merge", "git reset --hard HEAD~1",
    "git reset --hard", "git branch -D x", "git branch -f -D x", "git clean -fdx", "git clean",
    "git push --mirror origin", "git push --all origin", "git push --prune origin",
    "git push origin refs/heads/main", "git push origin 'main'", 'git push origin "master"',
    "git -C ../lane push --force", "git -C ../lane push -f origin x", "git -C /x push origin main",
    "git -C /x push origin HEAD:main", "git -C ../lane clean -fd", "git -C . reset --hard HEAD",
    "git branch --delete --force x", "git branch --force --delete x",
    "git -c core.x=y push --force", "git push origin 'HEAD:main'", 'git push origin "HEAD:master"',
    "git push origin +overnight-loop/x", "git push --all",
]
ALLOW = [
    "git push -u origin overnight-loop/2026-09-29-0600", "git push origin overnight-loop/2026-09-29-0600",
    "git push", "git push --follow-tags origin overnight-loop/x", "git push origin fix-main",
    "git push origin main-cleanup", "git push origin feature-fix", "git push origin loop/015-shell",
    "git reset --soft HEAD~1", "git reset -q", "git restore .", "git branch -d merged-branch",
    "git status", "git commit -m 'docs: explain push --force policy'", "gh pr create --base main",
    "gh pr view 12", "git checkout -b overnight-loop/x", "git switch -c overnight-loop/shelved/foo",
    "git -C ../lane push -u origin loop/015-shell", "git -C ../lane commit -m 'add retry to push'",
    "git push origin refs/heads/overnight-loop/x", "git -C ../lane status", "git branch --delete merged",
    "git -C . commit -m 'tidy: clean up the parser'", "git push origin overnight-loop/c++-port",
    "git push origin loop/fix--all-flag", "git commit -m 'fix push +1 bug'", "git -C ../lane clean",
]
fails = 0
for c in BLOCK:
    if not blocked(c):
        print(f"  FAIL blocks: {c}"); fails += 1
    else:
        print(f"  ok   blocks: {c}")
for c in ALLOW:
    b = blocked(c)
    if b:
        print(f"  FAIL allows: {c}   (hit {b})"); fails += 1
    else:
        print(f"  ok   allows: {c}")
sys.exit(1 if fails else 0)
PY
pattern_rc=$?
rm -f /tmp/deny-rules-list.$$
check "pattern table (above) all as expected" '[ $pattern_rc -eq 0 ]'
check "the work branch itself is never protected" '! bash "$DR" list main main | grep -q "git push \* main)"'
check "base branch protected alongside main/master" 'bash "$DR" list develop feat | grep -q "git push \* develop)"'

# --- 2) add / remove / status ---------------------------------------------------------
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
P="$T/proj"; mkdir -p "$P/.claude"; git -C "$P" init -q
REC="$P/.git/overnight-loop/deny-added.json"
cat > "$P/.claude/settings.local.json" <<'JSON'
{"permissions": {"allow": ["Bash(npm test *)"], "deny": ["Bash(git reset --hard*)"]}, "note": "é — kept"}
JSON
( cd /; bash "$DR" add "$P" main overnight-loop/x ) > "$T/out.txt" 2>&1; rc=$?
S="$P/.claude/settings.local.json"
jsn() { python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print($2)" "$1" 2>/dev/null; }
check "add exits 0 from any current folder (absolute paths)" '[ $rc -eq 0 ]'
check "add wrote the rules into the project's settings.local.json" '[ "$(jsn "$S" "\"Bash(git push *--force*)\" in d[\"permissions\"][\"deny\"]")" = "True" ]'
check "existing allow rules and other keys kept (non-ASCII intact)" '[ "$(jsn "$S" "json.dumps(d[\"permissions\"][\"allow\"])")" = "[\"Bash(npm test *)\"]" ] && grep -q "é — kept" "$S"'
check "the user's own identical rule was NOT recorded as loop-added" '[ "$(jsn "$REC" "\"Bash(git reset --hard*)\" in d[\"added\"]")" = "False" ]'
n1=$(jsn "$S" "len(d[\"permissions\"][\"deny\"])")
( cd "$T"; bash "$DR" add "$P" main overnight-loop/x ) >/dev/null 2>&1
check "add twice is idempotent (no duplicates)" '[ "$(jsn "$S" "len(d[\"permissions\"][\"deny\"])")" = "$n1" ]'
check "status reports the loop-added rules" 'bash "$DR" status "$P" | grep -q "present"'
bash "$DR" remove "$P" > "$T/out.txt" 2>&1; rc=$?
check "remove exits 0 and deletes the record" '[ $rc -eq 0 ] && [ ! -e "$REC" ]'
check "remove kept the user's own rule and removed only the loop's" '[ "$(jsn "$S" "json.dumps(d[\"permissions\"][\"deny\"])")" = "[\"Bash(git reset --hard*)\"]" ]'
bash "$DR" remove "$P" > "$T/out.txt" 2>&1; rc=$?
check "remove again → nothing recorded, nothing removed, exit 0" '[ $rc -eq 0 ] && grep -q "nothing recorded" "$T/out.txt"'

P2="$T/proj2"; mkdir -p "$P2/.claude"; git -C "$P2" init -q; echo '{ broken' > "$P2/.claude/settings.local.json"
bash "$DR" add "$P2" main x > "$T/out.txt" 2>&1; rc=$?
check "invalid settings.local.json → exit 3, file untouched" '[ $rc -eq 3 ] && [ "$(cat "$P2/.claude/settings.local.json")" = "{ broken" ]'

P3="$T/proj3"; mkdir -p "$P3/.claude" "$T/dot"; git -C "$P3" init -q; echo '{}' > "$T/dot/local.json"
ln -s "$T/dot/local.json" "$P3/.claude/settings.local.json"
bash "$DR" add "$P3" main x >/dev/null 2>&1; rc=$?
check "a symlinked settings.local.json stays a symlink; the real file gets the rules" \
  '[ $rc -eq 0 ] && [ -L "$P3/.claude/settings.local.json" ] && grep -q "git push" "$T/dot/local.json"'

P4="$T/proj4"; mkdir -p "$P4"; git -C "$P4" init -q
bash "$DR" add "$P4" main x >/dev/null 2>&1; rc=$?
check "no .claude folder yet → created with the rules" '[ $rc -eq 0 ] && [ -f "$P4/.claude/settings.local.json" ]'
bash "$DR" remove "$P4" >/dev/null 2>&1
check "a settings file the loop created is removed again at stop (nothing else in it)" '[ ! -e "$P4/.claude/settings.local.json" ]'

check "the record lives inside .git (never committed or pushed)" '[ -z "$(git -C "$P4" status --porcelain --ignored | grep overnight-loop)" ]'

P5="$T/proj5"; mkdir -p "$P5/.claude"; git -C "$P5" init -q
echo '{"permissions": {"deny": ["Bash(git push --force:*)", "Bash(git push -f:*)", "Bash(git clean:*)", "Bash(rm -rf /*)"]}}' > "$P5/.claude/settings.local.json"
bash "$DR" add "$P5" main x >/dev/null; bash "$DR" remove "$P5" >/dev/null 2>&1
check "without --adopt-legacy, identical colon rules are treated as the user's and kept" \
  '[ "$(jsn "$P5/.claude/settings.local.json" "len(d[\"permissions\"][\"deny\"])")" = "4" ]'
out=$(bash "$DR" add "$P5" main x --adopt-legacy); bash "$DR" remove "$P5" >/dev/null 2>&1
check "old-version colon rules are adopted and removed at stop; the user's own rule stays" \
  'printf "%s" "$out" | grep -q "3 from an older version" && [ "$(jsn "$P5/.claude/settings.local.json" "json.dumps(d[\"permissions\"][\"deny\"])")" = "[\"Bash(rm -rf /*)\"]" ]'

P6="$T/proj6"; mkdir -p "$P6/.claude"; git -C "$P6" init -q; echo '{}' > "$P6/.claude/settings.local.json"
git -C "$P6" add -f .claude/settings.local.json
bash "$DR" add "$P6" main x > "$T/out.txt" 2>&1; rc=$?
check "git TRACKS settings.local.json → refuse (exit 4), file untouched" '[ $rc -eq 4 ] && grep -q "REFUSED: git tracks" "$T/out.txt" && [ "$(cat "$P6/.claude/settings.local.json")" = "{}" ]'

P7="$T/proj7"; mkdir -p "$P7"; git -C "$P7" init -q
REL=$(python3 -c "import os,sys;print(os.path.relpath(sys.argv[1], os.path.expanduser('~')))" "$P7")
bash "$DR" add "~/$REL" main x >/dev/null 2>&1; rc=$?   # a ~/… path that points into the temp folder
check "a ~/ project path is expanded" '[ $rc -eq 0 ] && [ -f "$P7/.claude/settings.local.json" ]'

mkdir -p "$T/notgit"; bash "$DR" add "$T/notgit" main x >/dev/null 2>&1; rc=$?
check "not a git repository → exit 2" '[ $rc -eq 2 ]'

bash "$DR" bogus >/dev/null 2>&1; rc=$?
check "unknown mode → exit 2" '[ $rc -eq 2 ]'

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
