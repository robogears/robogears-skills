#!/usr/bin/env bash
# overnightprotocol: cockpit (Tier 3) — a live dashboard for a tmux/terminal pane.
# Repaints the loop's status every few seconds (usage bars, cycle/done/build, current
# action, uptime) and exits when the loop flag is removed. Read-only: it never calls
# ccusage or touches the loop; it only reads the snapshot + report the loop already writes.
#
# Launch (in a tmux split next to the loop):
#   tmux split-window -v -p 22 'bash ~/.claude/overnight-loop/cockpit.sh'
# or just run it in any spare terminal:
#   bash ~/.claude/overnight-loop/cockpit.sh
#
# Env: OVERNIGHT_COCKPIT_INTERVAL (repaint seconds, default 3)

set -u
# auto-detect the active run (loop, or a legacy bounded overnight run)
if [ -f "$HOME/.claude/overnight-loop-active" ]; then FLAG="$HOME/.claude/overnight-loop-active"
elif [ -f "$HOME/.claude/overnight-active" ]; then FLAG="$HOME/.claude/overnight-active"
else FLAG="$HOME/.claude/overnight-loop-active"; fi
HERE="$(cd "$(dirname "$0")" && pwd)"
RENDER="$HERE/usage_render.sh"
INTERVAL="${OVERNIGHT_COCKPIT_INTERVAL:-3}"
case "$INTERVAL" in ''|*[!0-9]*) INTERVAL=3 ;; esac
[ "$INTERVAL" -ge 1 ] 2>/dev/null || INTERVAL=3

# start epoch from the plan header (falls back to now)
start=$(date +%s)
if command -v jq >/dev/null 2>&1 && [ -f "$FLAG" ]; then
  plan=$(jq -r '.plan // empty' "$FLAG" 2>/dev/null)
  if [ -n "$plan" ] && [ -f "$plan" ]; then
    se=$(grep -m1 '^START_EPOCH:' "$plan" 2>/dev/null | grep -oE '[0-9]+' | head -1)
    case "$se" in ''|*[!0-9]*) : ;; *) start="$se" ;; esac
  fi
fi

fmt_up() {  # seconds -> "2h14m" / "43m" / "12s"
  s="$1"; h=$((s/3600)); m=$(((s%3600)/60))
  if [ "$h" -gt 0 ]; then printf '%dh%02dm' "$h" "$m"
  elif [ "$m" -gt 0 ]; then printf '%dm' "$m"
  else printf '%ds' "$s"; fi
}

cleanup() { printf '\033[?25h\n'; exit 0; }   # restore cursor
trap cleanup INT TERM HUP

printf '\033[?25l'   # hide cursor while painting
while [ -f "$FLAG" ]; do
  now=$(date +%s); up=$(fmt_up "$((now - start))")
  # dynamic pane/tab title (glanceable even when the pane is hidden)
  if command -v jq >/dev/null 2>&1; then
    fh=$(jq -r '.five_hour.used_percentage // "?"' "$HOME/.claude/overnight-usage.json" 2>/dev/null)
  else fh="?"; fi
  printf '\033]2;🌙 %s%% · %s\033\\' "$fh" "$up"
  printf '\033[H\033[2J'   # cursor home + clear
  OVERNIGHT_UPTIME="$up" sh "$RENDER" cockpit 2>/dev/null || printf '  🌙 overnight loop — (waiting for first snapshot)\n'
  sleep "$INTERVAL"
done

printf '\033[?25h'                                   # restore cursor
printf '\033]2;loop ended\033\\'                     # reset title
printf '\n  🌙 overnight loop ended — safe to close this pane.\n'
