#!/bin/sh
# Everyday Claude Code status line: <model> | 5h:N% wk:N% ctx:N%
# Installed to ~/.claude/ by uninstall-legacy.sh when it retires the v0.2.x status line,
# so a terminal's status line looks exactly as before. Display only.
input=$(cat)
command -v jq >/dev/null 2>&1 || { printf 'Claude'; exit 0; }
five=$(printf '%s' "$input" | jq -r '.rate_limits.five_hour.used_percentage // "?"' 2>/dev/null)
week=$(printf '%s' "$input" | jq -r '.rate_limits.seven_day.used_percentage // "?"' 2>/dev/null)
ctx=$(printf '%s' "$input" | jq -r '.context_window.used_percentage // "?"' 2>/dev/null)
model=$(printf '%s' "$input" | jq -r '.model.display_name // "Claude"' 2>/dev/null)
printf '%s | 5h:%s%% wk:%s%% ctx:%s%%' "$model" "$five" "$week" "$ctx"
