#!/usr/bin/env bash
# Tests for scripts/uninstall-legacy.sh (the v0.2.x → v0.3 cleanup).
# Run:  bash tests/test_uninstall_legacy.sh
#
# Fully isolated: every case runs against a throwaway $HOME, with the macOS `trash` command
# disabled (OVERNIGHT_TRASH_CMD="") so nothing reaches the real Trash. Touches nothing real.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
UN="$HERE/../scripts/uninstall-legacy.sh"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# A v0.2.x-shaped home: our two hooks + an unrelated user hook sharing the Stop event,
# our statusLine, a non-ASCII value, the env var, the runtime dir and state files.
mkhome() {  # $1 = dir, $2 = "trash" to give it a ~/.Trash
  local H="$1"
  mkdir -p "$H/.claude/overnight-loop"
  [ "${2:-}" = "trash" ] && mkdir -p "$H/.Trash"
  echo 'x' > "$H/.claude/overnight-loop/usage_daemon.sh"
  echo '{}' > "$H/.claude/overnight-usage.json"
  echo '[]' > "$H/.claude/overnight-usage-cal.json"
  echo '[]' > "$H/.claude/overnight-usage-cal.json.wrong-units-bak"
  echo 'working||' > "$H/.claude/overnight-loop-status"
  cat > "$H/.claude/settings.json" <<JSON
{
  "model": "opus",
  "note": "keep this dash — and this é",
  "env": {"BASH_MAX_TIMEOUT_MS": "18300000"},
  "statusLine": {"type": "command", "command": "sh \"$H/.claude/overnight-loop/statusline_usage_writer.sh\"", "padding": 0, "refreshInterval": 30000},
  "hooks": {
    "Stop": [
      {"hooks": [{"type": "command", "command": "bash \"$H/.claude/overnight-loop/overnightloop_stop.sh\""}]},
      {"hooks": [{"type": "command", "command": "say done"}]}
    ],
    "SessionStart": [
      {"matcher": "startup|resume|compact", "hooks": [{"type": "command", "command": "bash \"$H/.claude/overnight-loop/overnightloop_session_start.sh\""}]}
    ]
  }
}
JSON
}
run() { HOME="$1" OVERNIGHT_TRASH_CMD="" bash "$UN" "${@:2}" >"$1/out.txt" 2>&1; }
js()  { python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print($2)" "$1/.claude/settings.json" 2>/dev/null; }
leftovers() { ls -d "$1/.Trash/"overnightprotocol-v0.2-leftovers-* 2>/dev/null | head -1; }

echo "uninstall-legacy.sh"
T=$(mktemp -d); trap 'kill $(jobs -p) 2>/dev/null; rm -rf "$T"' EXIT

# --- full cleanup with a Trash present -------------------------------------------
H="$T/a"; mkhome "$H" trash; chmod 600 "$H/.claude/settings.json"
cp "$H/.claude/settings.json" "$T/a-original.json"
run "$H"; rc=$?
check "exits 0" '[ $rc -eq 0 ]'
check "our Stop hook removed, user's Stop hook kept" '[ "$(js "$H" "json.dumps(d[\"hooks\"][\"Stop\"])")" = "[{\"hooks\": [{\"type\": \"command\", \"command\": \"say done\"}]}]" ]'
check "empty SessionStart event dropped" '[ "$(js "$H" "\"SessionStart\" in d[\"hooks\"]")" = "False" ]'
check "unrelated keys preserved (model, env)" '[ "$(js "$H" "d[\"model\"]+\" \"+d[\"env\"][\"BASH_MAX_TIMEOUT_MS\"]")" = "opus 18300000" ]'
check "non-ASCII kept as real characters (no \\u escapes)" 'grep -q "keep this dash — and this é" "$H/.claude/settings.json"'
check "file permissions kept (600)" '[ "$(python3 -c "import os,sys;print(oct(os.stat(sys.argv[1]).st_mode & 0o777)[2:])" "$H/.claude/settings.json")" = "600" ]'
check "statusLine repointed to ~/.claude/statusline.sh" '[ "$(js "$H" "d[\"statusLine\"][\"command\"]")" = "sh \"$H/.claude/statusline.sh\"" ]'
check "statusLine padding and refreshInterval kept" '[ "$(js "$H" "(d[\"statusLine\"][\"padding\"], d[\"statusLine\"][\"refreshInterval\"])")" = "(0, 30000)" ]'
check "statusline.sh is the shipped everyday script" 'cmp -s "$H/.claude/statusline.sh" "$HERE/../scripts/statusline-everyday.sh"'
if command -v jq >/dev/null 2>&1; then
  line=$(printf '%s' '{"model":{"display_name":"Opus"},"rate_limits":{"five_hour":{"used_percentage":12},"seven_day":{"used_percentage":40}},"context_window":{"used_percentage":7}}' | sh "$H/.claude/statusline.sh")
  check "everyday status line output unchanged" '[ "$line" = "Opus | 5h:12% wk:40% ctx:7%" ]'
else
  echo "  skip everyday status line output (jq not installed)"
fi
B=$(ls "$H/.claude/"settings.json.bak.overnightprotocol-uninstall-* 2>/dev/null | head -1)
check "backup written and identical to the original" '[ -n "$B" ] && cmp -s "$B" "$T/a-original.json"'
check "no temp files left in ~/.claude" '[ -z "$(ls -A "$H/.claude" | grep -E "^\.tmp-overnight-|\.tmp$")" ]'
L=$(leftovers "$H")
check "runtime folder moved into the Trash (not deleted)" '[ -n "$L" ] && [ -f "$L/overnight-loop/usage_daemon.sh" ] && [ ! -e "$H/.claude/overnight-loop" ]'
check "state files moved into the Trash (incl. globbed backups)" '[ -f "$L/overnight-usage.json" ] && [ -f "$L/overnight-usage-cal.json.wrong-units-bak" ] && [ -f "$L/overnight-loop-status" ]'
check "restart advice printed" 'grep -q "Restart Claude Code" "$H/out.txt"'

# --- idempotent -------------------------------------------------------------------
run "$H"; rc=$?
check "second run exits 0" '[ $rc -eq 0 ]'
check "second run: nothing of ours left, status line recognised as converted" 'grep -q "nothing of ours left" "$H/out.txt" && grep -q "already converted" "$H/out.txt"'
check "second run says nothing to do (not 'removed')" 'grep -q "nothing to do" "$H/out.txt" && ! grep -q "install removed" "$H/out.txt"'

# --- no Trash → moved into a dated folder in ~/.claude, never deleted -------------------
H="$T/b"; mkhome "$H"
run "$H"; rc=$?
check "without ~/.Trash: exits 0 and keeps the files in ~/.claude/overnightprotocol-v0.2-leftovers-*" \
  '[ $rc -eq 0 ] && ls -d "$H/.claude/"overnightprotocol-v0.2-leftovers-*/overnight-loop >/dev/null 2>&1 && [ ! -e "$H/.claude/overnight-loop" ]'

# --- live loop: refused; dry run still works; stale flag / --force proceed ---------------
H="$T/c"; mkhome "$H" trash
echo '{"cwd":"/tmp/proj","mode":"loop"}' > "$H/.claude/overnight-loop-active"
before=$(cat "$H/.claude/settings.json")
run "$H"; rc=$?
check "live loop → refused (exit 1), settings untouched" '[ $rc -eq 1 ] && grep -q "REFUSED" "$H/out.txt" && [ "$(cat "$H/.claude/settings.json")" = "$before" ]'
run "$H" --dry-run; rc=$?
check "live loop + --dry-run → preview works, notes the live loop, changes nothing" \
  '[ $rc -eq 0 ] && grep -q "still LIVE" "$H/out.txt" && grep -q "would remove Stop hook" "$H/out.txt" && [ "$(cat "$H/.claude/settings.json")" = "$before" ]'
touch -t 202001010000 "$H/.claude/overnight-loop-active"
run "$H"; rc=$?
check "stale flag (dead run) → proceeds and removes the flag" '[ $rc -eq 0 ] && [ ! -e "$H/.claude/overnight-loop-active" ]'
H="$T/d"; mkhome "$H" trash
echo '{"cwd":"/tmp/proj","mode":"loop"}' > "$H/.claude/overnight-loop-active"
run "$H" --force; rc=$?
check "--force proceeds on a live flag" '[ $rc -eq 0 ] && [ ! -e "$H/.claude/overnight-loop-active" ]'

# --- dry run changes nothing and says so ------------------------------------------------
H="$T/e"; mkhome "$H" trash
before=$(cat "$H/.claude/settings.json")
run "$H" --dry-run; rc=$?
check "dry run exits 0" '[ $rc -eq 0 ]'
check "dry run labels every action" 'grep -q "\[dry-run\] would remove Stop hook" "$H/out.txt" && grep -q "\[dry-run\] would move to the Trash" "$H/out.txt" && grep -q "\[dry-run\] would keep your everyday status line" "$H/out.txt"'
check "dry run leaves settings, runtime and statusline.sh alone" '[ "$(cat "$H/.claude/settings.json")" = "$before" ] && [ -d "$H/.claude/overnight-loop" ] && [ ! -e "$H/.claude/statusline.sh" ]'

# --- custom statusLine / existing statusline.sh are never clobbered ----------------------
H="$T/f"; mkhome "$H" trash
python3 - "$H/.claude/settings.json" <<'PY'
import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["statusLine"]={"type":"command","command":"ccstatusline"}; json.dump(d,open(p,"w"))
PY
run "$H"; rc=$?
check "custom statusLine left as-is (and the hooks still removed)" '[ $rc -eq 0 ] && [ "$(js "$H" "d[\"statusLine\"][\"command\"]")" = "ccstatusline" ] && [ "$(js "$H" "\"SessionStart\" in d[\"hooks\"]")" = "False" ]'
H="$T/g"; mkhome "$H" trash
echo 'mine' > "$H/.claude/statusline.sh"
run "$H"; rc=$?
check "someone else's statusline.sh is not overwritten; statusline-everyday.sh used instead" \
  '[ $rc -eq 0 ] && [ "$(cat "$H/.claude/statusline.sh")" = "mine" ] && [ "$(js "$H" "d[\"statusLine\"][\"command\"]")" = "sh \"$H/.claude/statusline-everyday.sh\"" ] && cmp -s "$H/.claude/statusline-everyday.sh" "$HERE/../scripts/statusline-everyday.sh"'
H="$T/g2"; mkhome "$H" trash
cp "$HERE/../scripts/statusline-everyday.sh" "$H/.claude/statusline.sh"
run "$H"; rc=$?
check "an identical statusline.sh is reused" '[ $rc -eq 0 ] && [ "$(js "$H" "d[\"statusLine\"][\"command\"]")" = "sh \"$H/.claude/statusline.sh\"" ]'

# --- invalid settings.json → untouched, non-zero -----------------------------------------
H="$T/h"; mkhome "$H" trash
echo '{ not json' > "$H/.claude/settings.json"
run "$H"; rc=$?
check "invalid JSON → exit 1, file untouched, runtime not moved" '[ $rc -eq 1 ] && grep -q "not valid JSON" "$H/out.txt" && [ "$(cat "$H/.claude/settings.json")" = "{ not json" ] && [ -d "$H/.claude/overnight-loop" ]'

# --- a symlinked settings.json stays a symlink (dotfiles managers) -----------------------
H="$T/i"; mkhome "$H" trash
mkdir -p "$H/dotfiles"; mv "$H/.claude/settings.json" "$H/dotfiles/settings.json"
ln -s "$H/dotfiles/settings.json" "$H/.claude/settings.json"
run "$H"; rc=$?
check "symlinked settings.json: still a symlink, and the real file was cleaned" \
  '[ $rc -eq 0 ] && [ -L "$H/.claude/settings.json" ] && ! grep -q overnightloop_ "$H/dotfiles/settings.json"'

# --- unexpected hook shapes are preserved untouched ------------------------------------
H="$T/j"; mkhome "$H" trash
python3 - "$H/.claude/settings.json" <<'PY'
import json,sys; p=sys.argv[1]; d=json.load(open(p))
d["hooks"]["PreToolUse"]={"matcher":"Bash","hooks":[{"type":"command","command":"my-guard.sh"}]}
d["hooks"]["Stop"].append({"matcher":"*","command":"my-flat-hook.sh"})
d["hooks"]["Stop"][1]["hooks"].append("a-bare-string-hook")
json.dump(d,open(p,"w"))
PY
run "$H"; rc=$?
check "an event written as an object is kept" '[ $rc -eq 0 ] && [ "$(js "$H" "d[\"hooks\"][\"PreToolUse\"][\"hooks\"][0][\"command\"]")" = "my-guard.sh" ]'
check "a group without a hooks list is kept" '[ "$(js "$H" "any(g.get(\"command\")==\"my-flat-hook.sh\" for g in d[\"hooks\"][\"Stop\"])")" = "True" ]'
check "a non-object hook entry is kept" '[ "$(js "$H" "any(\"a-bare-string-hook\" in g.get(\"hooks\",[]) for g in d[\"hooks\"][\"Stop\"])")" = "True" ]'

# --- only processes running from ~/.claude/overnight-loop/ are stopped -------------------
H="$T/k"; mkhome "$H" trash
exec 3>&2 2>/dev/null   # silence bash's "Terminated" job notices for this section
( exec -a "bash $H/.claude/overnight-loop/usage_daemon.sh --watch" sleep 60 ) & OURS=$!
( exec -a "node /Users/someone/projects/rss-feeder/index.js --heartbeat" sleep 60 ) & OTHER=$!
sleep 0.3
echo "$OURS" > "$H/.claude/overnight-usage-daemon.pid"
echo "$OTHER" > "$H/.claude/overnight-usage-feeder.pid"
run "$H"; rc=$?; sleep 0.3
check "the old daemon (runs from ~/.claude/overnight-loop/) is stopped" '[ $rc -eq 0 ] && ! kill -0 $OURS 2>/dev/null'
check "an unrelated process whose name mentions feeder/heartbeat is NOT stopped" 'kill -0 $OTHER 2>/dev/null'
kill $OURS $OTHER 2>/dev/null; wait $OURS $OTHER 2>/dev/null
exec 2>&3 3>&-

# --- unknown argument ----------------------------------------------------------------
H="$T/l"; mkhome "$H" trash
run "$H" --bogus; rc=$?
check "unknown argument → exit 2, nothing changed" '[ $rc -eq 2 ] && [ -d "$H/.claude/overnight-loop" ]'

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
