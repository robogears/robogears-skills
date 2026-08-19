#!/usr/bin/env bash
# overnightprotocol: one-time installer.
# Copies the scripts + LOOP hooks to a STABLE location (~/.claude/overnight-loop/)
# and wires the (gated) loop hooks into ~/.claude/settings.json — preserving every
# existing key and any unrelated hooks, validating
# the JSON before replacing, and backing up first (pruned to the newest 5 backups).
# Also sets env.BASH_MAX_TIMEOUT_MS=18300000 so the pause procedure's single long sleep
# is allowed (raised only, never lowered). Idempotent.
#
# Copies go via tmp+mv so re-running the installer while a daemon is live never
# truncates the script file that running bash still holds open.
#
# statusLine: only set if none is configured or the current one is already an overnight
# snapshot writer — a custom unrelated statusLine is left untouched (with a note).
#
# NOTE: there is deliberately NO PreCompact hook anymore (Claude Code ignores its
# additionalContext); the strip logic still cleans up entries from older versions.
# REMINDER: hooks added to settings.json take effect only after Claude Code restarts.

set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
OV="$HOME/.claude/overnight-loop"
SETTINGS="$HOME/.claude/settings.json"

command -v jq >/dev/null 2>&1      || { echo "FAIL: jq required (brew install jq)"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "FAIL: python3 required (xcode-select --install)"; exit 1; }

echo "→ installing loop scripts to $OV"
mkdir -p "$OV"
for f in statusline_usage_writer.sh check_usage.sh preflight.sh usage_daemon.sh feeder.sh usage_render.sh cockpit.sh; do
  cp "$ROOT/scripts/$f" "$OV/$f.tmp.$$" && mv -f "$OV/$f.tmp.$$" "$OV/$f"
done
for f in overnightloop_stop.sh overnightloop_session_start.sh; do
  cp "$ROOT/hooks/$f" "$OV/$f.tmp.$$" && mv -f "$OV/$f.tmp.$$" "$OV/$f"
done
# Clean up the retired PreCompact hook from older installs (dead pattern — see header).
rm -f "$OV/overnightloop_precompact.sh" 2>/dev/null || true
chmod +x "$OV"/*.sh
echo "  copied: $(ls "$OV" | tr '\n' ' ')"

# Treat a 0-byte settings file like a missing one — json.load would choke on it.
[ -s "$SETTINGS" ] || echo '{}' > "$SETTINGS"
BACKUP="$SETTINGS.bak.overnight-loop-$(date +%Y%m%d-%H%M%S)"
cp "$SETTINGS" "$BACKUP"
# Prune old backups to the newest 5 (they accumulate one per install run otherwise).
ls -t "$SETTINGS".bak.overnight-loop-* 2>/dev/null | tail -n +6 | while IFS= read -r old; do
  rm -f "$old"
done

WRITER="$OV/statusline_usage_writer.sh"
STOP="$OV/overnightloop_stop.sh"
SSTART="$OV/overnightloop_session_start.sh"

if ! python3 - "$SETTINGS" "$WRITER" "$STOP" "$SSTART" "$BACKUP" <<'PY'
import json, sys, os

settings_path, writer, stop, sstart, backup = sys.argv[1:6]
try:
    with open(settings_path) as f:
        cfg = json.load(f)
except (ValueError, OSError) as e:
    sys.stderr.write(
        f"settings.json is not valid JSON ({e}).\n"
        f"Nothing was changed. Fix or restore it — an untouched backup was just made at:\n"
        f"  {backup}\n")
    sys.exit(3)

# 1) statusline — only if absent or already an overnight writer (don't clobber a custom one)
sl = cfg.get("statusLine") or {}
cur = (sl.get("command") or "") if isinstance(sl, dict) else ""
if (not cur) or ("statusline_usage_writer.sh" in cur) or ("/.claude/overnight" in cur):
    # refreshInterval re-renders the statusLine every 30s → the writer snapshots the REAL
    # rate_limits organically between turns, not just on a turn (the actual usage-freshness fix).
    # Paths are quoted so a HOME containing spaces cannot silently break every hook.
    cfg["statusLine"] = {"type": "command", "command": f'sh "{writer}"', "padding": 0,
                         "refreshInterval": 30000}
    print("  statusLine wired to the loop snapshot writer (refreshInterval 30s)")
else:
    print("  statusLine left as-is (custom) — add the snapshot block from statusline_usage_writer.sh manually")

# 2) env — the pause procedure's single long sleep needs an 18300000ms max timeout.
#    Raise-only: never lower a higher value the user set themselves.
env = cfg.get("env")
if not isinstance(env, dict):
    env = {}
try:
    current = int(str(env.get("BASH_MAX_TIMEOUT_MS", "0")))
except ValueError:
    current = 0
if current < 18300000:
    env["BASH_MAX_TIMEOUT_MS"] = "18300000"
    print("  env.BASH_MAX_TIMEOUT_MS set to 18300000 (single 5h+margin pause sleep allowed)")
else:
    print(f"  env.BASH_MAX_TIMEOUT_MS left at {current} (already sufficient)")
cfg["env"] = env

# 3) hooks — preserve unrelated ones; replace only entries
#    that are THIS skill's installed artifacts. The matcher is deliberately narrow
#    ("overnight-loop/overnightloop_") so a user's own hook that merely mentions the
#    flag path is never silently deleted; anything stripped is printed.
hooks = cfg.get("hooks")
if not isinstance(hooks, dict):
    hooks = {}

def strip_loop(entries):
    out = []
    for group in entries or []:
        kept = []
        for h in group.get("hooks", []):
            cmd = h.get("command", "") or ""
            if "overnight-loop/overnightloop_" in cmd:
                print(f"  replacing prior loop hook entry: {cmd}")
            else:
                kept.append(h)
        if kept:
            g = dict(group); g["hooks"] = kept; out.append(g)
    return out

hooks["Stop"] = strip_loop(hooks.get("Stop")) + [
    {"hooks": [{"type": "command", "command": f'bash "{stop}"'}]}
]
# startup included so a fresh session in an armed project (post-reboot) also gets the
# recovery injection — the hook's own flag+cwd gates keep it inert everywhere else.
hooks["SessionStart"] = strip_loop(hooks.get("SessionStart")) + [
    {"matcher": "startup|resume|compact",
     "hooks": [{"type": "command", "command": f'bash "{sstart}"'}]}
]
# No PreCompact hook is added anymore; strip cleans up entries from older versions.
pre = strip_loop(hooks.get("PreCompact"))
if pre:
    hooks["PreCompact"] = pre
else:
    hooks.pop("PreCompact", None)
cfg["hooks"] = hooks

tmp = settings_path + ".tmp"
with open(tmp, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
with open(tmp) as f:
    json.load(f)  # validate before replacing
os.replace(tmp, settings_path)
print("  settings.json updated (loop Stop/SessionStart hooks + env)")
PY
then
  echo "FAIL: settings.json could not be updated — see the message above. Backup: $BACKUP"
  exit 1
fi

echo
echo "✓ loop install complete. IMPORTANT: hooks load at session start — restart Claude"
echo "  Code before arming a loop if this install (re)wired them. Then verify:"
echo "   1) open Claude Code, send one message"
echo "   2) cat ~/.claude/overnight-usage.json   # should show percentages"
echo "   3) bash $OV/preflight.sh \"\$PWD\"        # should say monitor:ok"
echo
echo "RECOMMENDED (one-time): enable the real-api source — true percentages every ~60s"
echo "  even when the loop runs in the Claude Desktop app:"
echo "   a) install the terminal CLI:  npm install -g @anthropic-ai/claude-code"
echo "      (or the native build if you have no node — see docs.claude.com)"
echo "   b) run \`claude\` in a terminal, log in once, then /exit"
echo "   c) bash $OV/usage_daemon.sh --status"
echo "      macOS: click \"Always Allow\" on the Keychain prompt this triggers."
echo "      It should then say: real-api: available"
echo "  Skipping this is fine in a TERMINAL loop (the status line feeds real numbers);"
echo "  a Desktop-app loop without it runs on a ccusage estimate — works, less exact,"
echo "  and its seed budgets assume a Max-20x plan until a real reading calibrates them"
echo "  (tune OVERNIGHT_CCUSAGE_100PCT_COST / _100PCT for other plans)."
echo
echo "OFF-SWITCH to stop a running loop:  rm ~/.claude/overnight-loop-active"
