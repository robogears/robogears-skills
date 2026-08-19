#!/bin/sh
# overnightprotocol: statusline hook.
# Claude Code pipes a JSON payload (model, context, rate_limits) to the
# configured statusLine command on every display refresh. This script:
#   1. Snapshots the official rate-limit data to ~/.claude/overnight-usage.json
#      (atomically), MERGING with the previous snapshot: a payload that carries only
#      one usage window must not null out a good reading for the other window
#      (audit DAT-weekly-nulled). Each freshly-delivered window is stamped with
#      observed_ts so downstream consumers can age readings honestly even when the
#      snapshot file itself is rewritten later (audit DAT-weekly-launder).
#   2. Prints a minimal visible status line so it's useful on its own.
#
# Requires: jq  (ships with recent macOS at /usr/bin/jq; else `brew install jq`)
# If you already run a custom statusline, you don't need this file — copy the
# guarded, atomic snapshot block below into your existing script instead.
#
# Env: OVERNIGHT_USAGE_FILE overrides the snapshot path (default ~/.claude/overnight-usage.json)

input=$(cat)
SNAP="${OVERNIGHT_USAGE_FILE:-$HOME/.claude/overnight-usage.json}"

# jq guard (V4): without jq we can't build the snapshot. Critically, do NOT run a
# `> "$SNAP"` redirection in this case — the shell truncates the file to empty
# BEFORE discovering jq is missing, destroying the last good snapshot on every
# refresh. Print a minimal line and exit, leaving any existing snapshot intact.
if ! command -v jq >/dev/null 2>&1; then
  printf 'Claude | usage: jq not found (snapshot disabled)'
  exit 0
fi

# Only snapshot when the payload actually carries rate-limit data (V2). Early-session
# refreshes (before the first API response) and some concurrent sessions send payloads
# with no rate_limits; writing those would clobber a good snapshot with nulls and a
# fresh timestamp, which check_usage.sh would read as "all clear". Guard on EITHER
# window being present (they can be independently absent).
# NOTE: rate percentages are account-wide (any session's write is equally valid), but
# context_pct is PER-SESSION and last-writer-wins — context_session records which
# session wrote it so the ctx reading can be attributed (audit DAT-ctx-wrong-session).
has_rl=$(printf '%s' "$input" | jq -r '
  if (.rate_limits.five_hour.used_percentage != null)
     or (.rate_limits.seven_day.used_percentage != null)
  then "yes" else "no" end' 2>/dev/null)

if [ "$has_rl" = "yes" ]; then
  # Merge base: the previous snapshot, if it parses (a corrupt/absent one merges as {}).
  prev='{}'
  if [ -f "$SNAP" ]; then
    if candidate=$(cat "$SNAP" 2>/dev/null) && printf '%s' "$candidate" | jq -e . >/dev/null 2>&1; then
      prev="$candidate"
    fi
  fi
  # Atomic write (V1): build into a temp file in the same directory, then rename.
  # rename() is atomic on one filesystem, so the checker never observes a half-written
  # file; and if jq errors, the previous good snapshot is left untouched.
  dir=$(dirname "$SNAP")
  mkdir -p "$dir" 2>/dev/null
  tmp="$SNAP.tmp.$$"
  # Provenance-aware merge: carrying a window forward under source:"real" is only
  # honest if the previous snapshot WAS real — a daemon estimate's five_hour must
  # never be relabeled as an authoritative reading (that would bypass the checker's
  # source-awareness AND poison the calibration with self-referential pairs).
  # seven_day merges from any prev: weekly data only ever originates real-sourced
  # and carries its own observed_ts.
  if printf '%s' "$input" | jq -c --argjson prev "$prev" '
        (now | floor) as $ts |
        (($prev.source // "") | test("^real")) as $prev_real |
        {
          ts: $ts,
          source: "real",
          five_hour: (if .rate_limits.five_hour.used_percentage != null
                      then (.rate_limits.five_hour + {observed_ts: $ts})
                      elif $prev_real then ($prev.five_hour // null)
                      else null end),
          seven_day: (if .rate_limits.seven_day.used_percentage != null
                      then (.rate_limits.seven_day + {observed_ts: $ts})
                      else ($prev.seven_day // null) end),
          context_pct: .context_window.used_percentage,
          context_session: (.session_id // null)
        }' > "$tmp" 2>/dev/null; then
    mv -f "$tmp" "$SNAP" 2>/dev/null || rm -f "$tmp" 2>/dev/null
  else
    rm -f "$tmp" 2>/dev/null
  fi
fi

# --- Tier 1: loop-aware RICH status line. Only when a loop is active; otherwise fall
# through to the exact minimal line below. This is the GLOBAL statusline, so a normal
# (non-loop) session's line must be byte-for-byte unchanged, and any render failure
# (empty output) falls back to minimal.
HERE=$(dirname "$0"); RENDER="$HERE/usage_render.sh"
if { [ -f "$HOME/.claude/overnight-loop-active" ] || [ -f "$HOME/.claude/overnight-active" ]; } && [ -f "$RENDER" ]; then
  rich=$(sh "$RENDER" statusline 2>/dev/null)
  [ -n "$rich" ] && { printf '%s' "$rich"; exit 0; }
fi

# Always print a minimal visible status line (works even when we didn't snapshot).
five=$(printf '%s' "$input" | jq -r '.rate_limits.five_hour.used_percentage // "?"' 2>/dev/null)
week=$(printf '%s' "$input" | jq -r '.rate_limits.seven_day.used_percentage // "?"' 2>/dev/null)
ctx=$(printf '%s' "$input" | jq -r '.context_window.used_percentage // "?"' 2>/dev/null)
model=$(printf '%s' "$input" | jq -r '.model.display_name // "Claude"' 2>/dev/null)

printf '%s | 5h:%s%% wk:%s%% ctx:%s%%' "$model" "$five" "$week" "$ctx"
