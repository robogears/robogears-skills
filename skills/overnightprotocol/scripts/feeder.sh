#!/usr/bin/env bash
# overnightprotocol: real-number FEEDER.
# Run this in a terminal to feed the REAL 5h/7d usage numbers into the shared snapshot
# (~/.claude/overnight-usage.json) that an app-based loop reads — with ZERO extra requests.
#
# How it works: the Claude DESKTOP app, while open, samples the true usage percentages to
# ~/Library/Application Support/Claude/plan-usage-history.json every ~5 min. This feeder
# harvests the latest fresh sample every ~3 min and writes it as source:"real-app".
#
#   REQUIREMENT: keep the Claude Desktop app OPEN while this runs (that's what keeps the
#   cache fresh). If the sample goes stale (Desktop closed), the feeder does NOTHING and
#   leaves the snapshot alone — it never writes a stale number as if it were live.
#
# Usage:
#   feeder.sh            watch: harvest every ~3 min until you Ctrl-C (run in tmux for overnight)
#   feeder.sh --once     harvest once and exit
#   feeder.sh --status   show what real source is available right now
#
# Env: same as usage_daemon.sh (OVERNIGHT_USAGE_INTERVAL, OVERNIGHT_PLAN_USAGE_TTL, …)

HERE="$(cd "$(dirname "$0")" && pwd)"
DAEMON="$HERE/usage_daemon.sh"
[ -x "$DAEMON" ] || DAEMON="$HOME/.claude/overnight-loop/usage_daemon.sh"

case "${1:-}" in
  --once)   exec bash "$DAEMON" --harvest ;;
  --status) exec bash "$DAEMON" --status ;;
  "" )      echo "feeder: harvesting real usage from the Claude Desktop cache every ~3 min (Ctrl-C to stop)."
            echo "        keep the Claude Desktop app OPEN so its numbers stay fresh."
            exec bash "$DAEMON" --feed ;;
  *)        echo "usage: feeder.sh [--once|--status]"; exit 2 ;;
esac
