#!/usr/bin/env bash
# overnightprotocol: preflight doctor.
# One non-interactive check of everything that has to be true for a loop run to
# survive the night. Prints PASS / WARN / FAIL per item with the exact fix, then a
# summary line the agent can parse:
#   PREFLIGHT <pass>/<total> monitor:<ok|estimate|blind> verdict:<OK|FAIL>
#
# Colors are tty-gated: captured/piped output (what the agent parses) carries no
# ANSI escapes. The summary line never did.
#
# Usage: preflight.sh [project_dir] [threshold]
#   project_dir  repo to check (default: current directory)
#   threshold    usage threshold to test the monitor against (default 95)
# Exit code: 0 if no FAIL, 1 if any FAIL (WARN does not fail).

set -u
PROJ="${1:-$PWD}"
THRESH="${2:-95}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SETTINGS="$HOME/.claude/settings.json"

if [ -t 1 ]; then G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else G=""; Y=""; R=""; N=""; fi
pass=0; total=0; failed=0
ok(){   total=$((total+1)); pass=$((pass+1)); printf '  %sPASS%s  %s\n' "$G" "$N" "$1"; }
warn(){ total=$((total+1));                  printf '  %sWARN%s  %s\n     ↳ %s\n' "$Y" "$N" "$1" "$2"; }
fail(){ total=$((total+1)); failed=$((failed+1)); printf '  %sFAIL%s  %s\n     ↳ %s\n' "$R" "$N" "$1" "$2"; }

echo "overnightprotocol preflight — $(date '+%Y-%m-%d %H:%M')"
echo "project: $PROJ"
echo

# --- dependencies --------------------------------------------------------------
command -v jq      >/dev/null 2>&1 && ok "jq present ($(command -v jq))"            || fail "jq missing"      "brew install jq  (ships with recent macOS at /usr/bin/jq)"
command -v python3 >/dev/null 2>&1 && ok "python3 present ($(command -v python3))"  || fail "python3 missing" "xcode-select --install"
command -v caffeinate >/dev/null 2>&1 && ok "caffeinate present" || warn "caffeinate missing" "part of macOS; without it the Mac may sleep"

# --- statusline hook wired ------------------------------------------------------
# A custom (non-bundled) statusline is fine as long as it embeds the snapshot block —
# never FAIL just because the command isn't a .sh file (audit DAT-statusline-clobber
# chain: a false FAIL here used to push the agent into clobbering it via install.sh).
sl_cmd=""
if [ -f "$SETTINGS" ] && command -v jq >/dev/null 2>&1; then
  sl_cmd=$(jq -r '.statusLine.command // empty' "$SETTINGS" 2>/dev/null)
fi
if [ -z "$sl_cmd" ]; then
  fail "statusLine not configured in settings.json" "run scripts/install.sh, or add a statusLine block (see launch-guide.md)"
elif printf '%s' "$sl_cmd" | grep -q 'statusline_usage_writer\.sh'; then
  # Extract the script path: prefer a quoted path (install.sh writes sh "..."),
  # fall back to whitespace-split for older unquoted configs.
  sl_path=$(printf '%s' "$sl_cmd" | sed -n 's/.*"\([^"]*statusline_usage_writer\.sh\)".*/\1/p')
  [ -n "$sl_path" ] || sl_path=$(printf '%s\n' "$sl_cmd" | tr ' ' '\n' | grep '\.sh$' | head -1)
  sl_path="${sl_path/#\~/$HOME}"
  if [ -n "$sl_path" ] && [ -f "$sl_path" ]; then
    ok "statusLine points at the bundled snapshot writer"
  else
    fail "statusLine references a missing file: ${sl_path:-<none>}" "re-run install.sh to refresh ~/.claude/overnight-loop/"
  fi
else
  warn "custom statusline detected ($sl_cmd)" "fine IF it embeds the guarded snapshot block from statusline_usage_writer.sh — the snapshot check below is the real test"
fi

# --- LOOP hooks wired (this skill's OWN hook filenames — audit QUA-wrong-hook-names:
#     an earlier version grepped the retired sibling skill's names and could
#     false-FAIL a loop-only machine or false-PASS one missing the loop watchdog) -----
for hk in "Stop:overnightloop_stop.sh" "SessionStart:overnightloop_session_start.sh"; do
  ev="${hk%%:*}"; script="${hk#*:}"
  n=$(jq -r --arg ev "$ev" --arg s "$script" '[.hooks[$ev][]?.hooks[]?.command // "" | select(contains($s))] | length' "$SETTINGS" 2>/dev/null || echo 0)
  case "$n" in ''|*[!0-9]*) n=0 ;; esac
  if [ "$n" -gt 0 ]; then
    ok "$ev loop hook wired ($script)"
  else
    fail "$ev loop hook not wired in settings.json" "run scripts/install.sh, then RESTART Claude Code (hooks load at session start) — without it the watchdog/recovery is OFF even if preflight otherwise passes"
  fi
done

# --- permission ask-rules (the launch guide's #1 run-killer) ----------------------
# Explicit ask rules force a prompt even under --dangerously-skip-permissions; one
# unanswered prompt at 1:30am ends the night.
ask_hits=""
for sf in "$HOME/.claude/settings.json" "$HOME/.claude/settings.local.json" \
          "$PROJ/.claude/settings.json" "$PROJ/.claude/settings.local.json"; do
  [ -f "$sf" ] || continue
  n=$(jq -r '.permissions.ask // [] | length' "$sf" 2>/dev/null || echo 0)
  case "$n" in ''|*[!0-9]*) n=0 ;; esac
  [ "$n" -gt 0 ] && ask_hits="$ask_hits $sf ($n rule(s))"
done
if [ -n "$ask_hits" ]; then
  warn "explicit permission ask-rules present:$ask_hits" "park them before launching — they stall the run even with --dangerously-skip-permissions"
else
  ok "no permission ask-rules in settings files"
fi

# --- pause timeout (audit LIF-pause-timeout-unset) --------------------------------
bmt=$(jq -r '.env.BASH_MAX_TIMEOUT_MS // empty' "$SETTINGS" 2>/dev/null)
if [ -n "$bmt" ] && [ "$bmt" -ge 18120000 ] 2>/dev/null; then
  ok "BASH_MAX_TIMEOUT_MS=$bmt (single 5-hour pause sleep allowed — pass the explicit timeout on the sleep call itself)"
else
  warn "BASH_MAX_TIMEOUT_MS missing or too low (${bmt:-unset})" "re-run install.sh (sets 18300000); otherwise pauses fall back to repeated 9-min sleeps with an explicit timeout"
fi

# --- leftover LOOP flag (audit QUA-wrong-flag-path: this checks the LOOP's own flag;
#     the sibling overnight-oldversion's time-boxed flag is its own preflight's job) ----
FLAGF="$HOME/.claude/overnight-loop-active"
if [ -f "$FLAGF" ]; then
  if ! jq -e '.cwd' "$FLAGF" >/dev/null 2>&1; then
    fail "loop flag exists but is UNREADABLE ($FLAGF)" "half-armed state: hooks stand down but a daemon may keep running — rm -f $FLAGF"
  else
    fcwd=$(jq -r '.cwd // "?"' "$FLAGF" 2>/dev/null)
    fend=$(jq -r '.end_epoch // 0' "$FLAGF" 2>/dev/null); fnow=$(date +%s)
    hb=$(stat -f %m "$FLAGF" 2>/dev/null || stat -c %Y "$FLAGF" 2>/dev/null)
    if [ -n "${hb:-}" ] && [ "$((fnow - hb))" -gt 1800 ] 2>/dev/null; then
      warn "STALE loop flag present (cwd: $fcwd; heartbeat $((fnow - hb))s old — dead run)" "hooks already stand down on it; clean up with: rm -f $FLAGF"
    elif [ "$fend" -gt 0 ] 2>/dev/null && ! [ "$fend" -gt "$fnow" ] 2>/dev/null; then
      warn "loop flag present with a PASSED ceiling (cwd: $fcwd)" "the next Stop in that project triggers wrap-up; or rm -f $FLAGF"
    else
      warn "a loop is ALREADY ARMED (cwd: $fcwd$([ "$fend" -gt 0 ] 2>/dev/null && date -r "$fend" '+; ceiling %H:%M' 2>/dev/null))" "two simultaneous loops cannot both be guarded — stop that run first (rm -f $FLAGF) before arming another project"
    fi
  fi
else
  ok "no leftover loop flag"
fi

# --- snapshot fresh -------------------------------------------------------------
monitor="blind"
usage_line=$(OVERNIGHT_ESTIMATE=off bash "$HERE/check_usage.sh" "$THRESH" 2>/dev/null)
case "$usage_line" in
  OK*|RECHECK*) ok "usage snapshot live → $usage_line"; monitor="ok" ;;
  EST_OK*)      ok "usage snapshot live (ESTIMATE source) → $usage_line" ; monitor="estimate" ;;
  EST_PAUSE*)   warn "snapshot live (ESTIMATE source), currently at/over the estimate margin → $usage_line" "the run would start by pausing — plumbing works, timing may not"; monitor="estimate" ;;
  PAUSE*)       warn "snapshot live, but usage is currently PAUSED → $usage_line" "the run would start by sleeping until the reset — plumbing works, timing may not"; monitor="ok" ;;
  WEEKLY_CAP*)  warn "snapshot live, but the WEEKLY cap is already binding → $usage_line" "the loop would start by pausing until the weekly reset (can be days) — remove the flag instead if you'd rather not run this week"; monitor="ok" ;;
  NO_DATA*) warn "snapshot present but no rate-limit data yet" "send one message in Claude Code, then re-run" ;;
  STALE*)   warn "snapshot stale ($usage_line)" "statusline/daemon not ticking — is Claude Code open with this statusLine?" ;;
  MISSING*) fail "no usage snapshot ($usage_line)" "statusline hook not producing data — install.sh + open Claude Code once" ;;
  *)        warn "unexpected usage state: $usage_line" "inspect check_usage.sh" ;;
esac

# --- real-api credentials (the daemon's authoritative source; presence only,
#     token values are never read into shell state or printed) --------------------
if [ "$(printf '%s' "${OVERNIGHT_REAL_API:-auto}" | tr '[:upper:]' '[:lower:]')" = "off" ]; then
  warn "real-api source disabled (OVERNIGHT_REAL_API=off)" "the daemon will rely on statusline/estimate only"
elif python3 - <<'PY'
import json, os, subprocess, sys, time
def usable(raw):
    try: creds = json.loads(raw)
    except ValueError: return False
    o = creds.get("claudeAiOauth") if isinstance(creds, dict) else None
    o = o if isinstance(o, dict) else (creds if isinstance(creds, dict) else {})
    if not o.get("accessToken"): return False
    exp = o.get("expiresAt")
    return not (isinstance(exp, (int, float)) and exp / 1000.0 < time.time() + 60)
if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"): sys.exit(0)
try:
    p = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                       capture_output=True, text=True, timeout=10)
    if p.returncode == 0 and usable(p.stdout): sys.exit(0)
except Exception: pass
cred = os.path.expanduser("~/.claude/.credentials.json")
try:
    if os.path.exists(cred) and usable(open(cred).read()): sys.exit(0)
except Exception: pass
sys.exit(1)
PY
then
  ok "real-api credentials available (daemon will fetch TRUE percentages every ~60s)"
  monitor="ok"
else
  warn "real-api credentials unavailable" "for true % in Desktop-app runs: install + log in the terminal \`claude\` CLI, then run usage_daemon.sh --status once (macOS: click 'Always Allow' on the Keychain prompt). A terminal loop is fine without it (statusline feeds real numbers)."
fi

# --- estimate fallback warm (version-pinned; bounded — a cold npx cache must not
#     hang the doctor at the one moment the human is waiting) ----------------------
CCVER="${OVERNIGHT_CCUSAGE_VER:-20.0.17}"
printf '%s' "$CCVER" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || CCVER=20.0.17   # exact x.y.z only
if command -v npx >/dev/null 2>&1; then
  if python3 - "$CCVER" <<'PY'
import subprocess, sys
try:
    p = subprocess.run(["npx", "-y", "ccusage@" + sys.argv[1], "--version"],
                       capture_output=True, timeout=90)
    sys.exit(0 if p.returncode == 0 else 1)
except Exception:
    sys.exit(1)
PY
  then
    ok "ccusage estimate fallback reachable (pinned $CCVER)"; [ "$monitor" = "blind" ] && monitor="estimate"
  else
    warn "ccusage not reachable (or >90s)" "run once on network to warm the npx cache: npx -y ccusage@$CCVER --version"
  fi
else
  warn "npx not on PATH" "estimate fallback unavailable if the statusline snapshot fails"
fi

# --- git state ------------------------------------------------------------------
if git -C "$PROJ" rev-parse --git-dir >/dev/null 2>&1; then
  if [ -z "$(git -C "$PROJ" status --porcelain)" ]; then
    ok "git tree clean"
  else
    warn "git tree has uncommitted changes" "Phase 0 lists them at kickoff, then snapshots them on the overnight-loop BRANCH with secret-shaped files (*.pem, *.key, *.env*, *credential*, *token*, *secret* — at any depth) excluded — still eyeball the list yourself: everything committed gets PUSHED"
  fi
  if git -C "$PROJ" remote | grep -q .; then
    ok "git remote configured (branch can be pushed as backup)"
  else
    warn "no git remote" "no off-machine backup — wrap-up will write a local git bundle instead"
  fi
else
  fail "not a git repository: $PROJ" "git init, or run from the project root"
fi

# --- keep-awake ----------------------------------------------------------------
if command -v pmset >/dev/null 2>&1; then
  if pmset -g assertions 2>/dev/null | grep -qiE 'PreventUserIdleSystemSleep.*1|caffeinate'; then
    ok "system-sleep prevention active (caffeinate running)"
  else
    warn "no caffeinate assertion detected" "launch inside: caffeinate -is tmux new -s overnight"
  fi
fi

echo
mon_label=$monitor
[ "$failed" -gt 0 ] && verdict="FAIL" || verdict="OK"
printf 'PREFLIGHT %s/%s monitor:%s verdict:%s\n' "$pass" "$total" "$mon_label" "$verdict"
[ "$failed" -gt 0 ] && exit 1 || exit 0
