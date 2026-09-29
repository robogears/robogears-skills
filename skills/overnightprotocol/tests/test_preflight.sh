#!/usr/bin/env bash
# Tests for scripts/preflight.sh (read-only doctor).
# Run:  bash tests/test_preflight.sh
#
# Isolated: a throwaway $HOME, a throwaway git project with no remote (so no network), a
# fake managed-settings path, and a fake `pmset` / `pgrep` on PATH. Touches nothing real.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PF="$HERE/../scripts/preflight.sh"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

echo "preflight.sh"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
BIN="$T/bin"; mkdir -p "$BIN"
# fake pmset: prints $FAKE_PMSET; fake pgrep: never finds anything
printf '#!/bin/sh\ncat "$FAKE_PMSET"\n' > "$BIN/pmset"; printf '#!/bin/sh\nexit 1\n' > "$BIN/pgrep"; chmod +x "$BIN/pmset" "$BIN/pgrep"
DISPLAY_ONLY="$T/pmset-display"; CAFF="$T/pmset-caff"; CLAUDE_APP="$T/pmset-claude"
cat > "$DISPLAY_ONLY" <<'EOF'
Assertion status system-wide:
   PreventUserIdleSystemSleep     1
Listed by owning process:
   pid 386(powerd): [0x1] 00:06:53 PreventUserIdleSystemSleep named: "Powerd - Prevent sleep while display is on"
   pid 77(coreaudiod): [0x2] 00:01:00 PreventUserIdleSystemSleep named: "com.apple.audio.context.preventuseridlesleep"
EOF
cp "$DISPLAY_ONLY" "$CAFF"; echo '   pid 999(caffeinate): [0x3] 00:00:05 PreventUserIdleSystemSleep named: "caffeinate command-line tool"' >> "$CAFF"
cp "$DISPLAY_ONLY" "$CLAUDE_APP"; echo '   pid 222(Claude): [0x4] 22:00:00 NoIdleSleepAssertion named: "Electron"' >> "$CLAUDE_APP"
HANDOFF="$T/pmset-handoff"; cp "$DISPLAY_ONLY" "$HANDOFF"; echo '   pid 333(sharingd): [0x5] 00:00:03 PreventUserIdleSystemSleep named: "Handoff"' >> "$HANDOFF"

newhome() {  # fresh HOME + project repo
  H="$T/h$1"; P="$T/p$1"; rm -rf "$H" "$P"; mkdir -p "$H/.claude" "$P"
  ( cd "$P" && git init -q && git config user.email t@t && git config user.name t && echo x > a && git add a && git commit -qm i )
}
run() {  # $1 = route (terminal|desktop); rest = extra env assignments
  local route="$1"; shift
  env -i HOME="$H" PATH="$BIN:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin" \
      FAKE_PMSET="${PM:-$DISPLAY_ONLY}" OVERNIGHT_MANAGED_SETTINGS="$T/no-managed.json" \
      CLAUDE_CODE_ENTRYPOINT="$([ "$route" = desktop ] && echo claude-desktop || echo cli)" "$@" \
      bash "$PF" "$P" > "$T/out.txt" 2>&1
}
has() { grep -qF -- "$1" "$T/out.txt"; }
verdict() { tail -1 "$T/out.txt"; }

newhome 1; PM="$CAFF" run terminal; rc=$?
check "clean terminal setup: exit 0, verdict OK, parseable summary with route" '[ $rc -eq 0 ] && verdict | grep -qE "^PREFLIGHT [0-9]+/[0-9]+ verdict:OK route:terminal$"'
check "terminal: auto-continue reported on" 'has "usage-limit auto-continue on"'
check "no remote → WARN (no network touched)" 'has "no git remote"'

newhome 2; echo '{"autoContinueAtUsageLimit": false}' > "$H/.claude/settings.json"; PM="$CAFF" run terminal; rc=$?
check "terminal: user setting false → FAIL, exit 1" '[ $rc -eq 1 ] && has "OFF in ~/.claude/settings.json" && verdict | grep -q "verdict:FAIL"'

newhome 3; mkdir -p "$P/.claude"; echo '{"autoContinueAtUsageLimit": true}' > "$P/.claude/settings.local.json"; PM="$CAFF" run terminal
check "terminal: key in a PROJECT file (even true) → FAIL (Claude Code turns it off)" 'has "appears in the project" && verdict | grep -q "verdict:FAIL"'

newhome 4; mkdir -p "$P/.claude"; echo '{"autoContinueAtUsageLimit": true}' > "$H/.claude/settings.json"; echo '{"autoContinueAtUsageLimit": true}' > "$P/.claude/settings.json"; PM="$CAFF" run terminal
check "terminal: explicit true in ~/.claude/settings.json wins → on" 'has "usage-limit auto-continue on" && verdict | grep -q "verdict:OK"'

newhome 5; mkdir -p "$P/.claude"; echo '{ broken' > "$P/.claude/settings.json"; PM="$CAFF" run terminal
check "malformed settings file → FAIL naming the file" 'has "do not parse" && has "$P/.claude/settings.json" && verdict | grep -q "verdict:FAIL"'

newhome 6; echo '{"remoteControlAtStartup": true}' > "$H/.claude/settings.json"; PM="$CAFF" run terminal
check "terminal: Remote Control at startup → WARN" 'has "Remote Control starts with every session"'

newhome 7; echo '{"autoContinueAtUsageLimit": false}' > "$H/.claude/settings.json"; PM="$CAFF" run desktop; rc=$?
check "desktop: the CLI setting is only INFO there (no FAIL), Desktop 5-hour note shown" '[ $rc -eq 0 ] && has "5-HOUR limit only" && has "only affects terminal sessions" && verdict | grep -q "route:desktop"'

newhome 8; PM="$DISPLAY_ONLY" run terminal
check "keep-awake: display-on/audio assertions alone do NOT pass" 'has "nothing is keeping the Mac awake"'
newhome 9; PM="$CAFF" run terminal
check "keep-awake: a caffeinate assertion passes" 'has "system-sleep prevention active (caffeinate)"'
newhome 10; PM="$CLAUDE_APP" run terminal
check "keep-awake: the Claude app's NoIdleSleep assertion passes" 'has "system-sleep prevention active (Claude)"'

newhome 16; PM="$HANDOFF" run terminal
check "keep-awake: a short-lived Handoff assertion does NOT count (reported as info)" 'has "nothing is keeping the Mac awake" && has "other short-lived sleep assertions present (sharingd)"'

newhome 17; echo '{"autoContinueAtUsageLimit": true}' > "$T/managed17.json"; echo '{"autoContinueAtUsageLimit": false}' > "$H/.claude/settings.json"
PM="$CAFF" run terminal OVERNIGHT_MANAGED_SETTINGS="$T/managed17.json"
check "terminal: managed policy wins over the user setting" 'has "auto-continue on (managed policy)"'
newhome 18; echo '{"remoteControlAtStartup": true}' > "$H/.claude.json"; PM="$CAFF" run terminal
check "terminal: Remote Control set in ~/.claude.json is also caught" 'has "Remote Control starts with every session"'
newhome 19; echo '{"askUserQuestionTimeout": "never"}' > "$H/.claude/settings.json"; PM="$CAFF" run terminal
check "askUserQuestionTimeout never → only an INFO suggestion" 'has "askUserQuestionTimeout not set"'

newhome 11; PM="$CAFF" run terminal CLAUDE_CODE_DISABLE_CRON=1; rc=$?
check "CLAUDE_CODE_DISABLE_CRON set → FAIL (/loop disabled)" '[ $rc -eq 1 ] && has "/loop is disabled"'

newhome 12
cat > "$H/.claude/settings.json" <<JSON
{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "bash \"$H/.claude/overnight-loop/overnightloop_stop.sh\""}]}]},
 "statusLine": {"type": "command", "command": "sh \"$H/.claude/overnight-loop/statusline_usage_writer.sh\""}}
JSON
mkdir -p "$H/.claude/overnight-loop"; PM="$CAFF" run terminal
check "old v0.2 install → WARN listing hooks + status line + runtime, with a quoted --dry-run command" \
  'has "old v0.2.x install still present" && has "hooks(1)" && has "status-line" && has "runtime-folder" && has "uninstall-legacy.sh\" --dry-run"'
echo '{"cwd": "/tmp/elsewhere"}' > "$H/.claude/overnight-loop-active"; PM="$CAFF" run terminal
check "a LIVE old loop is called out (not 'inert')" 'has "an OLD v0.2.x loop is LIVE in: /tmp/elsewhere"'

newhome 13; echo '{"permissions": {"ask": ["Bash(rm *)"]}}' > "$H/.claude/settings.json"; PM="$CAFF" run terminal
check "permission ask-rules → WARN" 'has "explicit permission ask-rules present"'
echo '{"askUserQuestionTimeout": "5m"}' > "$H/.claude/settings.json"; PM="$CAFF" run terminal
check "askUserQuestionTimeout set → PASS" 'has "askUserQuestionTimeout = 5m"'

newhome 14; rm -rf "$P/.git"; PM="$CAFF" run terminal; rc=$?
check "not a git repo → FAIL, exit 1, summary line still printed" '[ $rc -eq 1 ] && has "not a git repository" && verdict | grep -q "^PREFLIGHT "'

newhome 15; ( cd "$P" && git config commit.gpgsign true ); PM="$CAFF" run terminal
check "signed commits → WARN" 'has "commits are signed"'

newhome 20; mkdir -p "$P/.claude"; echo '{}' > "$P/.claude/settings.local.json"; ( cd "$P" && git add -f .claude/settings.local.json && git commit -qm t ); PM="$CAFF" run terminal
check "git tracking .claude/settings.local.json → WARN" 'has "git tracks .claude/settings.local.json"'

newhome 21; BARE="$T/remote21.git"; git init -q --bare "$BARE"; ( cd "$P" && git remote add origin "$BARE" && mkdir -p .git/hooks && printf '#!/bin/sh\ntouch "%s/hook-ran"\nexit 1\n' "$T" > .git/hooks/pre-push && chmod +x .git/hooks/pre-push ); rm -f "$T/hook-ran"
PM="$CAFF" run terminal
check "push check passes against a reachable remote WITHOUT running the pre-push hook" 'has "accepts a push without a password prompt" && [ ! -e "$T/hook-ran" ]'
check "the dry-run push created nothing on the remote" '[ -z "$(git --git-dir="$BARE" for-each-ref)" ]'

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
