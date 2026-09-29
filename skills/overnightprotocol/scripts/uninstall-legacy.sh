#!/usr/bin/env bash
# overnightprotocol: remove the v0.2.x install (one-time upgrade cleanup).
#
# v0.3 runs on Claude Code's built-in /loop and needs nothing in ~/.claude/settings.json.
# This removes what the old install.sh wired in:
#   - hook entries that run ~/.claude/overnight-loop/overnightloop_*.sh — every OTHER hook,
#     including ones in shapes this script doesn't recognise, is left exactly as it was
#   - the statusLine, IF it is the old overnight one: your everyday line
#     "<model> | 5h:N% wk:N% ctx:N%" is kept (scripts/statusline-everyday.sh is copied to
#     ~/.claude/statusline.sh, or statusline-everyday.sh if that name is taken by
#     something else); a custom statusLine is left alone
#   - the ~/.claude/overnight-loop/ runtime and the old state files
#   - a still-running v0.2 usage daemon / feeder (only processes running from
#     ~/.claude/overnight-loop/ — nothing else is ever signalled)
# env.BASH_MAX_TIMEOUT_MS is left as-is (harmless; other work may rely on it).
#
# Safety: refuses while a v0.2.x loop is LIVE (flag heartbeat < 30 min) unless --force;
# --dry-run always works and changes nothing. settings.json: follows a symlink to the real
# file, keeps its permissions and characters (no \u escapes), backs it up first, writes a
# temp file next to it, checks nobody changed it meanwhile, validates, then swaps.
# Leftover files go to the Trash (macOS `trash`), else ~/.Trash, else a dated folder in
# ~/.claude — never deleted. If a move fails the file stays put and the run still finishes.
#
# Usage: uninstall-legacy.sh [--dry-run] [--force]
# Env (tests): OVERNIGHT_TRASH_CMD (default /usr/bin/trash; empty = don't use it)
# Afterwards, restart Claude Code: already-open sessions keep the old hooks loaded and
# may report a missing-hook error at the end of each turn until restarted.

set -u
DRY=""; FORCE=""
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --force)   FORCE=1 ;;
    *) echo "usage: uninstall-legacy.sh [--dry-run] [--force]" >&2; exit 2 ;;
  esac
done

C="$HOME/.claude"
SETTINGS="$C/settings.json"
FLAG="$C/overnight-loop-active"
TS=$(date +%Y%m%d-%H%M%S)
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TRASH_CMD="${OVERNIGHT_TRASH_CMD-/usr/bin/trash}"
say() { if [ -n "$DRY" ]; then echo "  [dry-run] would $*"; else echo "  $*"; fi; }
mtime() { date -r "$1" +%s 2>/dev/null; }   # BSD and GNU date both accept -r FILE

python3 -c 1 >/dev/null 2>&1 || { echo "FAIL: python3 is required (xcode-select --install)"; exit 1; }

# 1) never pull the hooks out from under a live old loop
if [ -f "$FLAG" ]; then
  hb=$(mtime "$FLAG"); case "$hb" in ''|*[!0-9]*) hb=0 ;; esac
  if [ "$(( $(date +%s) - hb ))" -le 1800 ]; then
    cwd=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("cwd","?"))' "$FLAG" 2>/dev/null || echo "?")
    if [ -n "$DRY" ]; then
      echo "NOTE: a v0.2.x overnight loop is still LIVE in: $cwd — a real run would refuse until it ends."
    elif [ -z "$FORCE" ]; then
      echo "REFUSED: a v0.2.x overnight loop is still LIVE in: $cwd"
      echo "  Removing its hooks now would let it stop at its next turn. End it first"
      echo "  (type END OVERNIGHT LOOP in that chat), then re-run this. Or pass --force."
      exit 1
    fi
  fi
fi

echo "overnightprotocol: removing the v0.2.x install${DRY:+ (dry run — nothing changes)}"
changed=0

# 2) stop old background processes — only ones running from ~/.claude/overnight-loop/
for pf in "$C/overnight-usage-daemon.pid" "$C/overnight-usage-feeder.pid"; do
  [ -f "$pf" ] || continue
  pid=$(cat "$pf" 2>/dev/null || true)
  case "$pid" in ''|*[!0-9]*) continue ;; esac
  if ps -p "$pid" -o command= 2>/dev/null | grep -qF "/.claude/overnight-loop/"; then
    say "stop old background process (pid $pid)"
    [ -n "$DRY" ] || kill "$pid" 2>/dev/null || true
    changed=1
  fi
done

# 3) settings.json — strip our hook entries, convert our statusLine
if [ -f "$SETTINGS" ] && [ -s "$SETTINGS" ]; then
  python3 - "$SETTINGS" "$C" "$HERE/statusline-everyday.sh" "${DRY:-}" "$TS" <<'PY'
import json, os, shutil, sys, tempfile

settings, cdir, sl_src, dry, ts = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1", sys.argv[5]
MARK = "overnight-loop/overnightloop_"
real = os.path.realpath(settings)
pre = "  [dry-run] would " if dry else "  "
try:
    st0 = os.stat(real)
    with open(real, encoding="utf-8") as f:
        cfg = json.load(f)
except (ValueError, OSError) as e:
    print(f"FAIL: settings.json is not valid JSON ({e}) — left untouched.")
    sys.exit(3)
if not isinstance(cfg, dict):
    print("FAIL: settings.json is not a JSON object — left untouched.")
    sys.exit(3)

msgs, changed = [], False
hooks = cfg.get("hooks")
if isinstance(hooks, dict):
    for ev in list(hooks):
        groups = hooks[ev]
        if not isinstance(groups, list):
            continue                                   # unknown shape: leave it exactly as is
        out, ev_changed = [], False
        for g in groups:
            hs = g.get("hooks") if isinstance(g, dict) else None
            if not isinstance(hs, list):
                out.append(g); continue                # unknown shape: keep untouched
            kept = [h for h in hs if not (isinstance(h, dict) and MARK in str(h.get("command") or ""))]
            if len(kept) == len(hs):
                out.append(g); continue
            ev_changed = True
            for h in hs:
                if h not in kept:
                    msgs.append(f"{pre}remove {ev} hook: {h.get('command')}")
            if kept:
                g = dict(g); g["hooks"] = kept; out.append(g)
        if ev_changed:
            changed = True
            if out:
                hooks[ev] = out
            else:
                del hooks[ev]
    if changed and not hooks:
        del cfg["hooks"]

created = None
sl = cfg.get("statusLine")
cmd = (sl.get("command") or "") if isinstance(sl, dict) else ""
our = open(sl_src, encoding="utf-8").read()
if "/.claude/overnight-loop/" in cmd:
    target = None
    for name in ["statusline.sh", "statusline-everyday.sh"] + [f"statusline-everyday-{i}.sh" for i in range(2, 10)]:
        p = os.path.join(cdir, name)
        if os.path.lexists(p):
            try:
                if not os.path.islink(p) and open(p, encoding="utf-8").read() == our:
                    target = p; break                   # already ours: reuse
            except OSError:
                pass
            continue                                    # someone else's file: pick another name
        target = p; break
    if target is None:
        print("FAIL: no free name for the everyday status-line script in ~/.claude — nothing changed.")
        sys.exit(4)
    if not dry and not os.path.lexists(target):
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o755)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(our)
        created = target
    sl = dict(sl); sl["command"] = f'sh "{target}"'
    cfg["statusLine"] = sl
    changed = True
    msgs.append(f"{pre}keep your everyday status line, now {target}")
elif cmd:
    if "statusline" in cmd and os.path.join(cdir, "statusline") in cmd:
        msgs.append("  statusLine: already converted")
    else:
        msgs.append("  statusLine: custom, left as-is")

def undo_created():
    if created and os.path.exists(created):
        os.unlink(created)

if not changed:
    print("  settings.json: nothing of ours left in it")
    if msgs:
        print("\n".join(msgs))
    sys.exit(0)
if dry:
    print("\n".join(msgs)); print("  [dry-run] would rewrite settings.json (after a backup)")
    sys.exit(10)

backup = f"{settings}.bak.overnightprotocol-uninstall-{ts}"
tmp = None
try:
    shutil.copy2(real, backup)
    st1 = os.stat(real)
    if (st1.st_mtime_ns, st1.st_size) != (st0.st_mtime_ns, st0.st_size):
        undo_created()
        print("FAIL: settings.json changed while this ran (Claude Code writing to it?) — nothing changed; re-run.")
        sys.exit(4)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(real), prefix=".tmp-overnight-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write("\n"); f.flush(); os.fsync(f.fileno())
    with open(tmp, encoding="utf-8") as f:
        if json.load(f) != cfg:
            raise ValueError("re-read does not match")
    shutil.copymode(real, tmp)
    os.replace(tmp, real)
    tmp = None
except Exception as e:
    undo_created()
    print(f"FAIL: settings.json not changed ({e}). Backup, if made: {backup}")
    sys.exit(5)
finally:
    if tmp and os.path.exists(tmp):
        os.unlink(tmp)
print("\n".join(msgs))
print(f"  settings.json updated (backup: {backup})")
sys.exit(10)
PY
  rc=$?
  case "$rc" in
    0) : ;;
    10) changed=1 ;;
    *) echo "Stopped: nothing further was changed."; exit 1 ;;
  esac
fi

# 4) runtime dir + old state files → Trash (recoverable), never deleted
DEST_TRASH="$HOME/.Trash/overnightprotocol-v0.2-leftovers-$TS"
DEST_LOCAL="$C/overnightprotocol-v0.2-leftovers-$TS"
failed=""
move_away() {  # $1 = path; echo where it went, or return 1
  local p="$1" tries="" d
  if [ -n "$TRASH_CMD" ] && [ -x "$TRASH_CMD" ]; then
    "$TRASH_CMD" "$p" >/dev/null 2>&1 && { echo "the Trash"; return 0; }
  fi
  for d in "$DEST_TRASH" "$DEST_LOCAL"; do
    case "$d" in "$HOME/.Trash/"*) [ -d "$HOME/.Trash" ] || continue ;; esac
    if mkdir -p "$d" 2>/dev/null && mv "$p" "$d/" 2>/dev/null; then echo "${d/#$HOME/~}"; return 0; fi
  done
  return 1
}
for p in "$C/overnight-loop" "$FLAG" "$FLAG.wrapup" "$FLAG.tmp" "$C/overnight-loop-status" \
         "$C/overnight-usage.json" "$C/overnight-usage-cal.json" "$C"/overnight-usage-cal.json.* \
         "$C/overnight-usage-daemon.pid" "$C/overnight-usage-feeder.pid" \
         "$C/overnight-loop-hook.log" "$C"/.op-spin-* "$C"/.tmp-usage-*; do
  [ -e "$p" ] || [ -L "$p" ] || continue
  changed=1
  if [ -n "$DRY" ]; then say "move to the Trash: ${p/#$HOME/~}"; continue; fi
  if where=$(move_away "$p"); then
    echo "  moved ${p/#$HOME/~} → $where"
  else
    echo "  could not move ${p/#$HOME/~} — left in place"
    failed="$failed ${p/#$HOME/~}"
  fi
done

echo
if [ -n "$DRY" ]; then
  [ "$changed" = 1 ] && echo "dry run complete — re-run without --dry-run to apply." || echo "dry run complete — nothing to do."
elif [ "$changed" = 0 ]; then
  echo "nothing to do — no v0.2.x install found."
else
  [ -n "$failed" ] && echo "⚠ some leftovers could not be moved (macOS privacy settings?):$failed — move them to the Trash in Finder."
  echo "✓ v0.2.x install removed. Restart Claude Code (quit the app fully) so open"
  echo "  sessions drop the old hooks. Nothing else to install — v0.3 runs on /loop."
fi
