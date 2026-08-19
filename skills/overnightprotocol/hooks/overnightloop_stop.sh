#!/usr/bin/env bash
# overnightprotocol: Stop-hook watchdog (LOOP variant).
# Unlike the retired single-pass skill's watchdog, this one does NOT release when the plan is
# empty — an empty plan is the trigger to GENERATE more work (the ladder), never a
# reason to stop. The ONLY clean releases are: the off-switch (flag file gone), a
# passed hard ceiling (after one wrap-up block), or a DEAD RUN (the flag's heartbeat
# — refreshed by the usage daemon every cycle — is older than 30 min; a crashed loop
# must never wedge tomorrow's normal sessions).
#
# Blocks the stop while: the loop flag is present AND fresh AND this session is in the
# loop's project. In every other case it allows the stop. Registered as a global Stop
# hook, so it MUST be inert and fast for normal sessions. Any error => exit 0 (allow
# stop) — but every fail-open exit leaves a one-line breadcrumb in
# ~/.claude/overnight-loop-hook.log so a dead loop is diagnosable in the morning.
#
# OFF-SWITCH (how a human stops the loop):  rm ~/.claude/overnight-loop-active
# The very next Stop is then allowed. This message is echoed on every block.

set -u
FLAG="$HOME/.claude/overnight-loop-active"
HOOKLOG="$HOME/.claude/overnight-loop-hook.log"
HB_MAX=1800   # seconds without a daemon heartbeat before the flag counts as a dead run

crumb() { echo "$(date '+%Y-%m-%d %H:%M:%S') stop-hook fail-open: $1" >> "$HOOKLOG" 2>/dev/null || true; }

# No active loop → allow stop (this is the off-switch: flag removed). Also sweep an
# orphaned wrap-up marker: the normal off-switch removes only the flag, and a stale
# marker would make a FUTURE run's ceiling release instantly without its wrap-up block.
if [ ! -f "$FLAG" ]; then
  rm -f "$FLAG.wrapup" 2>/dev/null
  exit 0
fi
command -v jq >/dev/null 2>&1 || { crumb "jq-missing"; exit 0; }

input=$(cat 2>/dev/null)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)

run_cwd=$(jq -r '.cwd // empty' "$FLAG" 2>/dev/null)
plan=$(jq -r '.plan // empty' "$FLAG" 2>/dev/null)

# Confine to the loop's project. If we cannot tell whose session this is (cwd absent),
# fail CLOSED for the loop's own project only by requiring an explicit match: an empty
# cwd is treated as "not this project" → allow stop (never wedge an unrelated session).
[ -n "$cwd" ] || { crumb "empty-session-cwd"; exit 0; }
[ -n "$run_cwd" ] || { crumb "flag-unreadable-or-no-cwd"; exit 0; }

# Canonicalize both sides before comparing (symlinks like /tmp -> /private/tmp,
# trailing slashes, and case-typed launches must not silently disarm the watchdog).
cwd=$(cd "$cwd" 2>/dev/null && pwd -P) || { crumb "session-cwd-unresolvable"; exit 0; }
run_cwd=$(cd "$run_cwd" 2>/dev/null && pwd -P) || { crumb "flag-cwd-unresolvable"; exit 0; }
case "$cwd" in
  "$run_cwd"|"$run_cwd"/*) : ;;
  *) exit 0 ;;
esac

now=$(date +%s)

# Dead-run failsafe: the usage daemon touches the flag every cycle (~3 min). A flag
# untouched for HB_MAX seconds means the run is dead (crash/reboot/force-quit) — stand
# down rather than conscripting whatever session wandered in later.
hb=$(stat -f %m "$FLAG" 2>/dev/null || stat -c %Y "$FLAG" 2>/dev/null)
if [ -n "${hb:-}" ] && [ "$((now - hb))" -gt "$HB_MAX" ] 2>/dev/null; then
  crumb "heartbeat-stale-$((now - hb))s (dead run; restart the daemon or rm the flag to fully clear)"
  exit 0
fi

# Optional user-set hard ceiling. Default is UNBOUNDED: end_epoch absent/0 → never expires.
end=$(jq -r '.end_epoch // empty' "$FLAG" 2>/dev/null)
if [ -n "$end" ] && [ "$end" != "0" ] && [ "$end" -gt 0 ] 2>/dev/null; then
  if ! [ "$end" -gt "$now" ] 2>/dev/null; then
    # Ceiling passed. Block ONCE so the agent runs Wrap-up (finalize report, push),
    # then allow the following Stop and clean everything up.
    if [ -f "$FLAG.wrapup" ]; then
      rm -f "$FLAG" "$FLAG.wrapup" 2>/dev/null
      exit 0
    fi
    touch "$FLAG.wrapup" 2>/dev/null
    printf '{"decision":"block","reason":"The overnight loop hard ceiling has passed — do NOT keep building. Run Wrap-up NOW: finalize OVERNIGHT_LOOP_REPORT.md with a TL;DR verdict, push the branch (or git bundle), remove the loop deny rules from .claude/settings.local.json, then delete BOTH ~/.claude/overnight-loop-active AND ~/.claude/overnight-loop-active.wrapup, and end. The next stop will be allowed either way."}\n'
    exit 0
  fi
fi

# Locate the plan file (informational only — its emptiness does NOT release the loop).
# Fallback derives from the loop project's ROOT, not the session cwd — a subdirectory
# session must not be told to recreate the plan in the wrong folder.
[ -n "$plan" ] || plan="$run_cwd/OVERNIGHT_LOOP_PLAN.md"
open_items="unknown"
if [ -f "$plan" ]; then
  if grep -qE '^[[:space:]]*-[[:space:]]*\[[[:space:]~]\]' "$plan" 2>/dev/null; then
    open_items="yes"       # unchecked [ ] or in-progress [~] items remain
  else
    open_items="no"        # official mission exhausted → enter the work-generation ladder
  fi
fi

if [ -n "$end" ] && [ "$end" != "0" ] && [ "$end" -gt 0 ] 2>/dev/null; then
  reset_h=$(date -r "$end" '+%H:%M' 2>/dev/null || echo "$end")
  ceiling=" A hard ceiling is set (until ${reset_h}); until then, keep going."
else
  ceiling=""
fi

if [ "$open_items" = "no" ]; then
  action="Your official mission items are all done — DO NOT STOP. Enter the work-generation ladder: (1) QA-harden, (2) run /audit and fix every Safe finding, (3) if audit is dry, research the project and plan + build QoL improvements. Append the new work to OVERNIGHT_LOOP_PLAN.md and continue."
elif [ "$open_items" = "unknown" ]; then
  action="OVERNIGHT_LOOP_PLAN.md was NOT found at $plan — recreate it from the skeleton in references/templates.md (see Context resilience), seed it from OVERNIGHT_LOOP_REPORT.md and git log, and continue the loop."
else
  action="OVERNIGHT_LOOP_PLAN.md still has open items — re-read OVERNIGHT_LOOP_PLAN.md and OVERNIGHT_LOOP_REPORT.md and continue the work loop from the top open item."
fi

printf '{"decision":"block","reason":"Overnight-protocol LOOP is active — this run does not stop on its own. %s If usage is at the cap, pause (sleep) until reset and then resume; pausing is not stopping.%s  Roughly every 20 turns, print ONE bold line: **OVERNIGHT ACTIVE — usage <5h%%>%% · cycle <n> · type END OVERNIGHT LOOP to stop**.  TO STOP: the user types END OVERNIGHT LOOP in chat (or says stop) — then delete ~/.claude/overnight-loop-active, run Wrap-up, and end."}\n' "$action" "$ceiling"
exit 0
