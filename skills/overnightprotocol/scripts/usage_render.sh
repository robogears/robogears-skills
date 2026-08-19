#!/bin/sh
# overnightprotocol: shared visual renderer (Tier 1-3).
# ONE coherent visual language on three surfaces, AUTO-DETECTING which run is active:
#   loop     → flag ~/.claude/overnight-loop-active   (unbounded; stop = END OVERNIGHT LOOP)
#   protocol → flag ~/.claude/overnight-active         (bounded; shows its end time)
#
#   usage_render.sh statusline   -> one loop/protocol-aware status-line string (spinner +
#                                   usage bar + state + stop/end). EMPTY if neither run is
#                                   active, so the global statusline falls back to normal.
#   usage_render.sh cockpit      -> the multi-line dashboard BODY (caller clears + loops).
#   usage_render.sh banner       -> the one-time kickoff box.
#
# Reads (all optional, all degrade gracefully):
#   the active flag (JSON: cwd, plan, [end_epoch], [threshold])
#   ~/.claude/overnight-usage.json                usage snapshot (source, five_hour, seven_day)
#   ~/.claude/overnight-loop-status | overnight-status   agent-written "state|detail|reset_epoch"
#   <plandir>/OVERNIGHT_LOOP_PLAN.md | OVERNIGHT_PLAN.md  checkbox progress (done/total)
#   <plandir>/OVERNIGHT_LOOP_REPORT.md                    STATUS line (ladder cycles, loop only)
#
# Colour via ANSI; disable with OVERNIGHT_NO_COLOR=1 or TERM=dumb. jq used when present.

MODE="${1:-statusline}"
SNAP="${OVERNIGHT_USAGE_FILE:-$HOME/.claude/overnight-usage.json}"
GLYPH="${OVERNIGHT_GLYPH:-🌙}"

# --- detect which run is active ---------------------------------------------
if [ -f "$HOME/.claude/overnight-loop-active" ]; then
  KIND=loop;     FLAG="$HOME/.claude/overnight-loop-active"; STATUSF="$HOME/.claude/overnight-loop-status"; LABEL="OVERNIGHT LOOP"
elif [ -f "$HOME/.claude/overnight-active" ]; then
  KIND=protocol; FLAG="$HOME/.claude/overnight-active";      STATUSF="$HOME/.claude/overnight-status";      LABEL="OVERNIGHT"
else
  KIND=""; FLAG=""; STATUSF=""; LABEL="OVERNIGHT"
fi

E=$(printf '\033')
if [ -n "${OVERNIGHT_NO_COLOR:-}" ] || [ "${TERM:-}" = "dumb" ]; then
  RST=""; BOLD=""; DIM=""; GRN=""; YEL=""; RED=""; BLU=""; CYA=""; GRY=""
else
  RST="$E[0m"; BOLD="$E[1m"; DIM="$E[2m"; GRN="$E[32m"; YEL="$E[33m"
  RED="$E[31m"; BLU="$E[34m"; CYA="$E[36m"; GRY="$E[90m"
fi

jqr() { [ -f "$SNAP" ] && command -v jq >/dev/null 2>&1 && jq -r "$1 // empty" "$SNAP" 2>/dev/null; }

source=$(jqr '.source')
fh=$(jqr '.five_hour.used_percentage')
wk=$(jqr '.seven_day.used_percentage')
ctx=$(jqr '.context_pct')
reset=$(jqr '.five_hour.resets_at')
fh_i=$(printf '%s' "$fh" | cut -d. -f1 2>/dev/null); case "$fh_i" in ''|*[!0-9]*) fh_i="" ;; esac

# flag-derived: plan, done/total checkboxes, end time
cycles=""; done_n=""; total_n=""; build=""; last=""; end_hm=""; end_left=""
if command -v jq >/dev/null 2>&1 && [ -n "$FLAG" ]; then
  plan=$(jq -r '.plan // empty' "$FLAG" 2>/dev/null)
  end=$(jq -r '.end_epoch // empty' "$FLAG" 2>/dev/null)
  if [ -n "$plan" ] && [ -f "$plan" ]; then
    done_n=$(grep -c '\[x\]' "$plan" 2>/dev/null)
    total_n=$(grep -cE '^[[:space:]]*-[[:space:]]*\[' "$plan" 2>/dev/null)
    if [ "$KIND" = loop ]; then report="$(dirname "$plan")/OVERNIGHT_LOOP_REPORT.md"; else report="$(dirname "$plan")/OVERNIGHT_REPORT.md"; fi
    if [ -f "$report" ]; then
      sl=$(grep -m1 -E '^STATUS:|^TL;DR:' "$report" 2>/dev/null)
      [ "$KIND" = loop ] && cycles=$(printf '%s' "$sl" | grep -oE '[0-9]+ ladder cycles' | grep -oE '^[0-9]+')
      build=$(printf '%s' "$sl"  | grep -oE 'build\+tests (GREEN|RED)' | awk '{print $2}')
      last=$(printf '%s' "$sl"   | awk -F'last: ' 'NF>1{print $2}' | awk -F' · monitor' '{print $1}')
    fi
  fi
  case "$end" in ''|*[!0-9]*) : ;; *)
    now=$(date +%s)
    if [ "$end" -gt "$now" ] 2>/dev/null; then
      end_hm=$(date -r "$end" '+%H:%M' 2>/dev/null || date -d "@$end" '+%H:%M' 2>/dev/null)
      lm=$(( (end - now) / 60 )); lh=$(( lm / 60 )); lmm=$(( lm % 60 ))
      [ "$lh" -gt 0 ] && end_left="${lh}h${lmm}m" || end_left="${lmm}m"
    fi ;;
  esac
fi

# agent-written status file wins for state + detail
state=""; detail=""; sreset=""
if [ -n "$STATUSF" ] && [ -f "$STATUSF" ]; then
  line=$(head -n1 "$STATUSF" 2>/dev/null)
  state=$(printf '%s' "$line" | cut -d'|' -f1); detail=$(printf '%s' "$line" | cut -d'|' -f2); sreset=$(printf '%s' "$line" | cut -d'|' -f3)
fi
[ -n "$sreset" ] && reset="$sreset"
[ -z "$detail" ] && detail="$last"
if [ -z "$state" ]; then
  # Fallback inference only (the status file above is authoritative when present).
  # Use the run's own threshold from the flag JSON when recorded; default 95 — the
  # guardrail reads real percentages, so "paused" must not be painted at a mere 80%.
  fthr=""
  [ -n "$FLAG" ] && command -v jq >/dev/null 2>&1 && fthr=$(jq -r '.threshold // empty' "$FLAG" 2>/dev/null)
  case "$fthr" in ''|*[!0-9]*) fthr=95 ;; esac
  if [ -n "$fh_i" ] && [ "$fh_i" -ge "$fthr" ] 2>/dev/null; then state="paused"; else state="working"; fi
fi

reset_hm=""
if [ -n "$reset" ]; then ri=$(printf '%s' "$reset" | cut -d. -f1); case "$ri" in ''|*[!0-9]*) : ;; *) reset_hm=$(date -r "$ri" '+%H:%M' 2>/dev/null || date -d "@$ri" '+%H:%M' 2>/dev/null) ;; esac; fi

spin_frame() { f="$HOME/.claude/.op-spin-$1"; n=$(cat "$f" 2>/dev/null); case "$n" in ''|*[!0-9]*) n=0 ;; esac
  printf '%s' "$((n+1))" > "$f" 2>/dev/null; set -- '◐' '◓' '◑' '◒'; i=$(( n % 4 )); eval "printf '%s' \"\${$((i+1))}\""; }

bar() { p="$1"; w="$2"; case "$p" in ''|*[!0-9]*) p="" ;; esac
  if [ -z "$p" ]; then i=0; else i=$(( (p * w + 50) / 100 )); [ "$i" -gt "$w" ] && i="$w"; fi
  col="$GRN"
  if [ "$state" = "paused" ]; then col="$DIM$BLU"
  elif [ -n "$p" ] && [ "$p" -ge 90 ] 2>/dev/null; then col="$RED"
  elif [ -n "$p" ] && [ "$p" -ge 70 ] 2>/dev/null; then col="$YEL"; fi
  printf '%s' "$col"; k=0; while [ "$k" -lt "$i" ]; do printf '▓'; k=$((k+1)); done
  printf '%s' "$GRY"; while [ "$k" -lt "$w" ]; do printf '░'; k=$((k+1)); done; printf '%s' "$RST"; }

state_token() { case "$state" in
    paused)  printf '%s⏸ paused%s' "$DIM$BLU" "$RST"; [ -n "$reset_hm" ] && printf '%s·%s%s' "$DIM$BLU" "$reset_hm" "$RST" ;;
    blocked) printf '%s⚠ %.20s%s' "$YEL" "${detail:-blocked}" "$RST" ;;
    wrapup)  printf '%s✔ wrapping up%s' "$CYA" "$RST" ;;
    starting) printf '%s✦ starting%s' "$CYA" "$RST" ;;
    *)       printf '%s▶ %.22s%s' "$GRN" "${detail:-building}" "$RST" ;;
  esac; }

pct_disp() { [ -n "$fh_i" ] && printf '%s%%' "$fh_i" || printf '?'; }
wk_disp()  { case "$wk" in ''|null) printf '?' ;; *) printf '%s%%' "$(printf '%s' "$wk" | cut -d. -f1)" ;; esac; }

# tail (statusline) + bottom line (cockpit/banner), per kind
if [ "$KIND" = "protocol" ]; then
  tail_seg="${DIM}⌛ ends ${end_hm:-?}${RST}"
  stop_line="${DIM}⌛ ends ${end_hm:-?}${end_left:+ · $end_left left} · rm ~/.claude/overnight-active to stop early${RST}"
  banner_line="working until ${end_hm:-the window closes}. removing ~/.claude/overnight-active stops early."
else
  tail_seg="${DIM}⌨ END OVERNIGHT LOOP${RST}"
  stop_line="${DIM}⌨ type  ${BOLD}END OVERNIGHT LOOP${RST}${DIM}  in chat to stop${RST}"
  banner_line="I'll keep building & improving until you stop."
fi

case "$MODE" in
  statusline)
    [ -n "$KIND" ] || exit 0
    sp=$(spin_frame statusline); src_mark=""; [ "$source" = "estimate-ccusage" ] && src_mark="${DIM}~${RST}"
    printf '%s%s%s %s%s%s 5h ' "$BOLD" "$GLYPH" "$RST" "$CYA" "$sp" "$RST"; bar "$fh_i" 8
    printf ' %s%s wk %s  ' "$src_mark" "$(pct_disp)" "$(wk_disp)"
    [ -n "$cycles" ] && printf '%scyc %s%s ' "$GRY" "$cycles" "$RST"
    [ -n "$total_n" ] && [ "$total_n" -gt 0 ] 2>/dev/null && printf '%s✓%s/%s%s  ' "$GRY" "${done_n:-0}" "$total_n" "$RST"
    state_token; printf '  %s' "$tail_seg"
    ;;
  cockpit)
    up="${OVERNIGHT_UPTIME:-}"
    printf '  %s%s %s%s  %s%s%s' "$BOLD" "$GLYPH" "$LABEL" "$RST" "$CYA" "$(spin_frame cockpit)" "$RST"
    [ -n "$up" ] && printf '%*sup %s' 18 '' "$up"
    printf '\n  %s────────────────────────────────────────────────%s\n' "$GRY" "$RST"
    printf '  5h  '; bar "$fh_i" 8; printf ' %s   ' "$(pct_disp)"
    printf 'wk '; bar "$(printf '%s' "$wk" | cut -d. -f1)" 6; printf ' %s   ' "$(wk_disp)"
    printf 'ctx '; bar "$(printf '%s' "$ctx" | cut -d. -f1)" 6; printf ' %s%%\n' "${ctx%%.*}"
    printf '  '
    [ -n "$cycles" ] && printf 'cycle %s · ' "$cycles"
    [ -n "$total_n" ] && [ "$total_n" -gt 0 ] 2>/dev/null && printf '%s/%s done · ' "${done_n:-0}" "$total_n"
    case "$build" in GREEN) printf 'build %s✔ GREEN%s' "$GRN" "$RST" ;; RED) printf 'build %s✗ RED%s' "$RED" "$RST" ;; *) printf 'build —' ;; esac
    printf '\n  now '; state_token; printf '\n'
    [ -n "$last" ] && printf '  %slast%s ✔ %.44s\n' "$GRY" "$RST" "$last"
    printf '  %s────────────────────────────────────────────────%s\n' "$GRY" "$RST"
    printf '  %s\n' "$stop_line"
    ;;
  banner)
    printf '%s──────────────────────────────────────────────%s\n' "$CYA" "$RST"
    printf '%s%s%s  %s%s%s — %sACTIVE%s\n' "$BOLD" "$GLYPH" "$RST" "$BOLD" "$LABEL" "$RST" "$GRN" "$RST"
    printf '%s%s%s\n' "$DIM" "$banner_line" "$RST"
    if [ "$KIND" = "protocol" ]; then printf '⌛  ends %s%s%s\n' "$BOLD" "${end_hm:-—}" "$RST"
    else printf '⌨  type  %sEND OVERNIGHT LOOP%s  to end\n' "$BOLD" "$RST"; fi
    printf '%s──────────────────────────────────────────────%s\n' "$CYA" "$RST"
    ;;
esac
