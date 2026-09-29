#!/usr/bin/env bash
# overnightprotocol: preflight doctor (read-only).
# One non-interactive check of everything outside the session that decides whether an
# unattended loop survives the night. Prints PASS / WARN / FAIL per item with the fix,
# INFO lines that don't count, then a summary line the agent parses:
#   PREFLIGHT <pass>/<total> verdict:<OK|FAIL> route:<desktop|terminal>
#
# Nothing is installed or changed. The loop runs on Claude Code's built-in /loop. Usage
# limits: the Desktop app auto-resumes 5-hour limits by itself (can't be checked from here);
# the terminal CLI uses the autoContinueAtUsageLimit setting, which IS checked here.
# JSON is read with python3 (also required by the loop's helper scripts); jq is not needed.
#
# Usage: preflight.sh [project_dir]
# Exit code: 0 if no FAIL, 1 if any FAIL (WARN does not fail).
# Env (tests): OVERNIGHT_MANAGED_SETTINGS (default: macOS managed-settings.json path)

set -u
PROJ="${1:-$PWD}"
case "$PROJ" in "~") PROJ="$HOME" ;; "~/"*) PROJ="$HOME/${PROJ#\~/}" ;; esac
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SETTINGS="$HOME/.claude/settings.json"
MANAGED="${OVERNIGHT_MANAGED_SETTINGS:-/Library/Application Support/ClaudeCode/managed-settings.json}"
case "${CLAUDE_CODE_ENTRYPOINT:-}" in claude-desktop*) ROUTE=desktop ;; *) ROUTE=terminal ;; esac

if [ -t 1 ]; then G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; B=$'\033[36m'; N=$'\033[0m'; else G=""; Y=""; R=""; B=""; N=""; fi
pass=0; total=0; failed=0
ok(){   total=$((total+1)); pass=$((pass+1)); printf '  %sPASS%s  %s\n' "$G" "$N" "$1"; }
warn(){ total=$((total+1));                  printf '  %sWARN%s  %s\n     ↳ %s\n' "$Y" "$N" "$1" "$2"; }
fail(){ total=$((total+1)); failed=$((failed+1)); printf '  %sFAIL%s  %s\n     ↳ %s\n' "$R" "$N" "$1" "$2"; }
info(){ printf '  %sINFO%s  %s\n' "$B" "$N" "$1"; }
mtime(){ date -r "$1" +%s 2>/dev/null; }   # BSD and GNU date both accept -r FILE

echo "overnightprotocol preflight — $(date '+%Y-%m-%d %H:%M')"
echo "project: $PROJ · route: $ROUTE"
echo

# --- dependencies ---------------------------------------------------------------
command -v git >/dev/null 2>&1 && ok "git present" || fail "git missing" "xcode-select --install"
if python3 -c 1 >/dev/null 2>&1; then PY=1; ok "python3 works"
else PY=""; fail "python3 missing or not runnable" "xcode-select --install — the loop's helper scripts (stage-safe, deny-rules) need it"; fi
command -v caffeinate >/dev/null 2>&1 || warn "caffeinate missing" "part of macOS; without it the Mac may sleep"

# --- the engine: /loop must be enabled ---------------------------------------------
if [ -n "${CLAUDE_CODE_DISABLE_CRON:-}" ] && [ "${CLAUDE_CODE_DISABLE_CRON}" != "0" ]; then
  fail "CLAUDE_CODE_DISABLE_CRON is set — /loop is disabled" "unset it (shell profile or settings.json env) and restart Claude Code"
else
  ok "/loop engine enabled"
fi
[ "${CLAUDE_CODE_LOOP_KEEPALIVE:-}" = "0" ] && info "CLAUDE_CODE_LOOP_KEEPALIVE=0 — the terminal's one-shot retry for a missed wake-up is off"

# --- settings files: parse, auto-continue, ask rules, question timeout -----------------
if [ -n "$PY" ]; then
  python3 - "$ROUTE" "$SETTINGS" "$MANAGED" "$PROJ/.claude/settings.json" "$PROJ/.claude/settings.local.json" <<'PY'
import json, os, sys
route, user, managed, proj, local = sys.argv[1:6]
def load(p):
    if not os.path.exists(p) or os.path.getsize(p) == 0:
        return None, None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return (d, None) if isinstance(d, dict) else (None, "not a JSON object")
    except (ValueError, OSError) as e:
        return None, str(e).replace("\t", " ")
files = {"user": user, "managed": managed, "project": proj, "local": local}
data, bad = {}, []
for k, p in files.items():
    d, err = load(p)
    data[k] = d or {}
    if err: bad.append(f"{p} ({err})")
out = []   # (kind, message, fix)
if bad:
    out.append(("FAIL", "settings file(s) do not parse: " + "; ".join(bad),
                "fix the JSON — Claude Code ignores a broken file, and in the terminal it also turns usage-limit auto-continue OFF"))
else:
    out.append(("PASS", "settings files parse", ""))

KEY = "autoContinueAtUsageLimit"
if route == "desktop":
    out.append(("INFO", "usage limits (Desktop app): it auto-resumes the 5-HOUR limit only (setting 'Auto-continue when limits reset', on by default), "
                        "about 90 s after the reset, if THIS chat is open on screen with an empty message box. Weekly limits wait for you. Can't be checked from here.", ""))
    if any(KEY in data[k] for k in ("user", "project", "local")):
        out.append(("INFO", f"{KEY} is set in a settings file — it only affects terminal sessions, not this Desktop one", ""))
else:
    # Claude Code: the first value set in managed policy, then ~/.claude/settings.json, wins;
    # with neither set it is on only if NO settings file mentions the key and all of them parse.
    pol, usr = data["managed"].get(KEY), data["user"].get(KEY)
    present_elsewhere = [k for k in ("project", "local") if KEY in data[k]]
    if pol is not None:
        if pol is False:
            out.append(("FAIL", "usage-limit auto-continue is OFF by managed policy", "ask your admin; otherwise the loop stops at the first usage limit"))
        else:
            out.append(("PASS", "usage-limit auto-continue on (managed policy)", ""))
    elif usr is False:
        out.append(("FAIL", "usage-limit auto-continue is OFF in ~/.claude/settings.json", f"remove {KEY} or set it true there"))
    elif usr is True:
        out.append(("PASS", "usage-limit auto-continue on (set true in ~/.claude/settings.json)", ""))
    elif present_elsewhere:
        out.append(("FAIL", f"usage-limit auto-continue is OFF: {KEY} appears in the project's {' and '.join(present_elsewhere)} settings",
                    "a mention in a project file (even true) turns it off unless ~/.claude/settings.json sets it true — remove it there, or set it true in ~/.claude/settings.json"))
    elif bad:
        out.append(("FAIL", "usage-limit auto-continue is OFF because a settings file doesn't parse", "fix the JSON (see above)"))
    else:
        out.append(("PASS", "usage-limit auto-continue on (terminal: resumes after a reset up to 24 h away)", ""))
    try:
        gcfg = json.load(open(os.path.expanduser("~/.claude.json"), encoding="utf-8"))
        gcfg = gcfg if isinstance(gcfg, dict) else {}
    except Exception:
        gcfg = {}
    if data["user"].get("remoteControlAtStartup") is True or gcfg.get("remoteControlAtStartup") is True:
        out.append(("WARN", "Remote Control starts with every session — that disables terminal usage-limit auto-continue",
                    "set remoteControlAtStartup false for loop sessions, or run the loop in the Desktop app"))

asks = [f"{files[k]} ({len(data[k]['permissions']['ask'])} rule(s))"
        for k in ("user", "project", "local")
        if isinstance(data[k].get("permissions"), dict) and isinstance(data[k]["permissions"].get("ask"), list)
        and data[k]["permissions"]["ask"]]
if asks:
    out.append(("WARN", "explicit permission ask-rules present: " + ", ".join(asks),
                "they force a prompt even with permissions bypassed — one unanswered prompt stalls the night"))
else:
    out.append(("PASS", "no permission ask-rules", ""))

t = data["user"].get("askUserQuestionTimeout") or data["project"].get("askUserQuestionTimeout") or data["local"].get("askUserQuestionTimeout")
if t and t != "never":
    out.append(("PASS", f"askUserQuestionTimeout = {t} (a stray question auto-continues)", ""))
else:
    out.append(("INFO", 'askUserQuestionTimeout not set — a stray question dialog waits forever; consider "askUserQuestionTimeout": "5m" in ~/.claude/settings.json', ""))
for kind, msg, fix in out:
    print(f"{kind}\t{msg}\t{fix}")
PY
fi > "${TMPDIR:-/tmp}/preflight.$$" 2>/dev/null
while IFS=$'\t' read -r kind msg fix; do
  case "$kind" in PASS) ok "$msg" ;; WARN) warn "$msg" "$fix" ;; FAIL) fail "$msg" "$fix" ;; INFO) info "$msg" ;; esac
done < "${TMPDIR:-/tmp}/preflight.$$"
rm -f "${TMPDIR:-/tmp}/preflight.$$"

# --- leftover v0.2.x install (hooks, status line, runtime, flag, daemon) --------------
legacy=""; live=""
[ -d "$HOME/.claude/overnight-loop" ] && legacy="$legacy runtime-folder"
if [ -n "$PY" ] && [ -f "$SETTINGS" ]; then
  hits=$(python3 - "$SETTINGS" <<'PY' 2>/dev/null
import json, sys
try: d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception: sys.exit(0)
def walk(x):
    if isinstance(x, dict):
        for v in x.values(): yield from walk(v)
    elif isinstance(x, list):
        for v in x: yield from walk(v)
    elif isinstance(x, str): yield x
n = sum(1 for s in walk(d.get("hooks", {})) if "overnight-loop/overnightloop_" in s)
sl = d.get("statusLine"); sl = sl.get("command", "") if isinstance(sl, dict) else ""
print(f"{n} {1 if '/.claude/overnight-loop/' in (sl or '') else 0}")
PY
)
  set -- $hits
  [ "${1:-0}" -gt 0 ] 2>/dev/null && legacy="$legacy hooks($1)"
  [ "${2:-0}" = 1 ] && legacy="$legacy status-line"
fi
for f in overnight-usage.json overnight-usage-cal.json overnight-usage-daemon.pid overnight-loop-status; do
  [ -e "$HOME/.claude/$f" ] && { legacy="$legacy state-files"; break; }
done
pgrep -f "/.claude/overnight-loop/usage_daemon" >/dev/null 2>&1 && legacy="$legacy daemon-running"
FLAGF="$HOME/.claude/overnight-loop-active"
if [ -f "$FLAGF" ]; then
  hb=$(mtime "$FLAGF"); case "$hb" in ''|*[!0-9]*) hb=0 ;; esac
  fcwd="?"; [ -n "$PY" ] && fcwd=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("cwd","?"))' "$FLAGF" 2>/dev/null || echo "?")
  if [ "$(( $(date +%s) - hb ))" -le 1800 ]; then live="$fcwd"; else legacy="$legacy stale-flag"; fi
fi
if [ -n "$live" ]; then
  warn "an OLD v0.2.x loop is LIVE in: $live" "end it first (type END OVERNIGHT LOOP in its chat). If that is this project, its Stop hook will fight the new loop. Then clean up: bash \"$HERE/uninstall-legacy.sh\" --dry-run"
elif [ -n "$legacy" ]; then
  warn "old v0.2.x install still present:$legacy" "inert, but it runs on every turn of every session — preview then clean up: bash \"$HERE/uninstall-legacy.sh\" --dry-run   (then without --dry-run, then restart Claude Code)"
else
  ok "no leftover v0.2.x install"
fi

# --- git ------------------------------------------------------------------------
if git -C "$PROJ" rev-parse --git-dir >/dev/null 2>&1; then
  ok "git repository (on $(git -C "$PROJ" rev-parse --abbrev-ref HEAD 2>/dev/null))"
  if [ -z "$(git -C "$PROJ" status --porcelain)" ]; then
    ok "git tree clean"
  else
    warn "git tree has uncommitted changes" "the kickoff lists them (and which secret-shaped files it would skip) before snapshotting them on the loop branch"
  fi
  git -C "$PROJ" ls-files --error-unmatch .claude/settings.local.json >/dev/null 2>&1 && \
    warn "git tracks .claude/settings.local.json" "the loop's deny rules live there and must not be committed — untrack it (git rm --cached .claude/settings.local.json, add it to .gitignore), or the loop runs without deny rules"
  [ -f "$(git -C "$PROJ" rev-parse --absolute-git-dir 2>/dev/null)/overnight-loop/deny-added.json" ] && \
    info "deny rules from an earlier loop run are still in this project (a resume keeps them; to drop them: bash \"$HERE/deny-rules.sh\" remove \"$PROJ\")"
  [ "$(git -C "$PROJ" config --get commit.gpgsign 2>/dev/null)" = "true" ] && \
    warn "commits are signed (commit.gpgsign=true)" "a signing prompt at 2 a.m. stalls every commit — make sure the signer needs no touch/password, or disable signing for this repo"
  remote=$(git -C "$PROJ" remote 2>/dev/null | head -n1)
  if [ -n "$remote" ]; then
    if [ -n "$PY" ] && python3 - "$PROJ" "$remote" <<'PY' >/dev/null 2>&1
import os, subprocess, sys
proj, remote = sys.argv[1], sys.argv[2]
ssh = subprocess.run(["git", "-C", proj, "config", "--get", "core.sshCommand"], capture_output=True, text=True).stdout.strip()
ssh = os.environ.get("GIT_SSH_COMMAND") or ssh or "ssh"
env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND=ssh + " -o BatchMode=yes -o ConnectTimeout=10")
try:
    # a dry-run push authenticates for WRITE exactly like the real one, but changes nothing;
    # --no-verify keeps the project's pre-push hook (tests, lint) from running
    p = subprocess.run(["git", "-C", proj, "push", "--dry-run", "--no-verify", "--porcelain", remote,
                        "HEAD:refs/heads/overnightprotocol-preflight-check"],
                       env=env, capture_output=True, timeout=30)
    sys.exit(0 if p.returncode == 0 else 1)
except Exception:
    sys.exit(1)
PY
    then ok "remote '$remote' accepts a push without a password prompt (checked with push --dry-run)"
    else warn "a push to '$remote' fails or needs a prompt (checked with push --dry-run)" "pushes would fail overnight (no backup) — check the network, SSH agent or credential helper"; fi
  else
    warn "no git remote" "no off-machine backup — wrap-up writes a local git bundle instead"
  fi
else
  fail "not a git repository: $PROJ" "the loop needs git (branch, commits). Run from the project root, or git init it yourself first"
fi

# --- keep-awake: only a real assertion OWNER counts. The system-wide summary line is also
#     raised by powerd's "prevent sleep while display is on" (i.e. whenever someone is at the
#     Mac) and by coreaudiod while audio plays; other apps hold short-lived ones (Handoff,
#     video). Only known keep-awake owners count: caffeinate, the Claude app itself
#     (NoIdleSleepAssertion), Amphetamine, KeepingYouAwake, Lungo, Theine, Caffeine. -----
if command -v pmset >/dev/null 2>&1; then
  owners=$(pmset -g assertions 2>/dev/null | grep -E '^[[:space:]]*pid [0-9]+\(' \
             | grep -E 'Prevent(UserIdle)?SystemSleep|PreventUserIdleDisplaySleep|NoIdleSleepAssertion|NoDisplaySleepAssertion' \
             | sed -E 's/.*pid [0-9]+\(([^)]*)\).*/\1/' | sort -u)
  keepers=$(printf '%s\n' "$owners" | grep -Ex 'caffeinate|Claude|Amphetamine|KeepingYouAwake|Lungo|Theine|Caffeine' | tr '\n' ' ' | sed 's/ $//')
  others=$(printf '%s\n' "$owners" | grep -Evx 'caffeinate|Claude|Amphetamine|KeepingYouAwake|Lungo|Theine|Caffeine|powerd|coreaudiod|WindowServer|runningboardd' | grep . | tr '\n' ' ' | sed 's/ $//')
  [ -n "$others" ] && info "other short-lived sleep assertions present ($others) — not counted as keeping the Mac awake"
  if [ -n "$keepers" ]; then
    ok "system-sleep prevention active ($keepers)"
  else
    warn "nothing is keeping the Mac awake" "run  caffeinate -dis &  in a terminal (and keep Claude Code open) — a sleeping Mac stalls the loop and can't auto-resume after a limit"
  fi
fi

echo
[ "$failed" -gt 0 ] && verdict="FAIL" || verdict="OK"
printf 'PREFLIGHT %s/%s verdict:%s route:%s\n' "$pass" "$total" "$verdict" "$ROUTE"
[ "$failed" -gt 0 ] && exit 1 || exit 0
