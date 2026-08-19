#!/usr/bin/env bash
# overnightprotocol: usage daemon + real-number feeder (v3 — audit-hardened).
# Keeps ~/.claude/overnight-usage.json fresh so the loop's guardrail reads a trustworthy
# number, and HEARTBEATS the loop flag (touch) every cycle so the hooks can tell a live
# run from a crashed one (flag untouched >30 min = dead run, hooks stand down).
#
# Sources, best first:
#   1. REAL (statusline)  source:"real"  — the true server %, written by the statusline hook.
#      Authoritative when fresh (age < REAL_TTL) and in-block (resets_at > now). A real
#      reading the CHECKER still trusts (age < 900s, in-block) is NEVER overwritten by a
#      lower-confidence source (audit DAT-real-overwritten / DAT-harvest-bypass).
#   1b. REAL (usage API)  source:"real-api" — GET /api/oauth/usage (what the app's usage
#      popup reads): the true 5h% + ALL weekly windows incl. per-model ones. Auth comes from
#      the TERMINAL CLI's own credential store — CLAUDE_CODE_OAUTH_TOKEN in our env, else the
#      macOS Keychain item "Claude Code-credentials" (user grants /usr/bin/security access
#      ONCE via the OS prompt → unattended reads thereafter). Requires `claude` to be
#      installed + logged in; an EXPIRED token is skipped with a note (we never refresh
#      tokens ourselves — rotating them could log the CLI out). The token is used only for
#      this one request and never touches disk, argv, or logs. While this source is
#      delivering, the watch loop tightens to a ~60s cadence (it is one cheap HTTPS GET).
#      Disable: OVERNIGHT_REAL_API=off. Test override: OVERNIGHT_API_BASE.
#   2. REAL (Desktop app) source:"real-app" — harvested from plan-usage-history.json IF a
#      fresh sample exists AND the sample does not predate the current 5h window (a
#      pre-reset 97% must never be re-armed with an invented future reset — audit
#      DAT-stale-app-pause). Weekly readings carry observed_ts so they age honestly
#      (audit DAT-weekly-launder).
#   3. ESTIMATE (ccusage) source:"estimate-ccusage" — `ccusage blocks --json --offline`
#      (ALL blocks: an empty block list means ccusage is data-blind → "unknown", never a
#      permissive 0%). TWO proxies are computed and the HIGHER is reported (fail-safe: a
#      guardrail that reads LOW hides real usage and blows the cap — the 44%-vs-real-79%
#      bug this file was patched for). (a) COST: the block's model-weighted costUSD — ccusage
#      prices Opus ~5x Fable, so cost tracks the 5h limit across a CHANGING model mix where a
#      raw token count cannot; this is the primary basis. (b) TOKENS: output tokens → % via
#      the older ccusage-basis calibration, kept as a fallback for when cost is unavailable.
#      Both go through the calibration file (real% ↔ cost and real% ↔ tokens). check_usage.sh
#      is source-aware and reports these as EST_OK/EST_PAUSE with an earlier threshold.
#   4. UNKNOWN — no real reading and ccusage unavailable → used_percentage:null (NO_DATA).
#
# Calibration (real % ↔ ccusage output tokens) is guarded against block-boundary
# poisoning (audit ERR-cal-poison): pairs are recorded only when the ccusage block is
# older than 1h (outside the hour-flooring skew zone), high percentages require a sane
# token mass, points are thinned to one per 15 min, and the file is reloaded at write
# time (audit DAT-cal-write-race).
#
# Modes: (default) one-shot refresh · --watch (loop while flag) · --feed (harvest-only loop) ·
#        --harvest (one-shot real-app only) · --status (includes daemon liveness)
# Env: OVERNIGHT_USAGE_FILE, OVERNIGHT_USAGE_INTERVAL(180), OVERNIGHT_REAL_TTL(210),
#      OVERNIGHT_PLAN_USAGE_FILE, OVERNIGHT_PLAN_USAGE_TTL(600 — below the checker's 900s
#        staleness cutoff so harvested readings are never born stale),
#      OVERNIGHT_CCUSAGE_VER(20.0.17 — exact x.y.z only; also honored by check_usage.sh),
#      OVERNIGHT_CCUSAGE_100PCT(2110000 — token seed budget until calibration learns the ratio),
#      OVERNIGHT_CCUSAGE_100PCT_COST(410 — cost($)-basis seed budget: ccusage active-block
#        costUSD at 100% of the 5h window, measured real ~80% ↔ ~$335 on a Max-20x Opus+Fable
#        mix, 2026-07-21; the model-weighted primary basis, refined by the calibration file),
#      OVERNIGHT_CCUSAGE_RESET_MARGIN(600 — ccusage floors block start to the hour, so its
#        reset can be up to ~59min early; margin added before trusting it to resume).

set -u
SNAP="${OVERNIGHT_USAGE_FILE:-$HOME/.claude/overnight-usage.json}"
FLAG="$HOME/.claude/overnight-loop-active"
INTERVAL="${OVERNIGHT_USAGE_INTERVAL:-180}"
case "$INTERVAL" in ''|*[!0-9]*) INTERVAL=180 ;; esac   # non-numeric must not hot-spin the loop
# Clamp to [5, 600]: 0 would hot-spin npx back-to-back, and anything above ~600 lets
# the flag heartbeat (touched once per cycle) exceed the hooks' 1800s dead-run cutoff
# once run_py's worst case (~185s) stacks on top — a live run must never read as dead.
[ "$INTERVAL" -ge 5 ] 2>/dev/null || INTERVAL=180
[ "$INTERVAL" -le 600 ] 2>/dev/null || INTERVAL=600
CALFILE="$HOME/.claude/overnight-usage-cal.json"
PLANUSAGE="${OVERNIGHT_PLAN_USAGE_FILE:-$HOME/Library/Application Support/Claude/plan-usage-history.json}"
CCVER="${OVERNIGHT_CCUSAGE_VER:-20.0.17}"
# Exact x.y.z only (supply-chain pin): "20" or "20.1" are npm semver RANGES that would
# silently float to the newest matching release; mirror check_usage.sh's strict match.
printf '%s' "$CCVER" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || CCVER=20.0.17
MODE="${1:-once}"

command -v python3 >/dev/null 2>&1 || { echo "MISSING python3-not-found"; exit 0; }

# $1 = full | harvest
run_py() {
python3 - "$SNAP" "$CALFILE" "$PLANUSAGE" "${OVERNIGHT_PLAN_USAGE_TTL:-600}" \
           "${OVERNIGHT_REAL_TTL:-210}" "$CCVER" "${OVERNIGHT_CCUSAGE_100PCT:-2110000}" \
           "${OVERNIGHT_CCUSAGE_RESET_MARGIN:-600}" "${OVERNIGHT_CCUSAGE_100PCT_COST:-410}" "$1" <<'PY'
import json, sys, os, time, subprocess, tempfile, math

(snap_path, cal_path, plan_path, plan_ttl_s, real_ttl_s,
 ccver, cc100_s, cc_margin_s, cc100cost_s, mode) = sys.argv[1:11]

def fnum(v, default=0.0):
    """Defensive float parse — payload/env fields are not under our control and a
    format change must degrade, never crash every cycle (audit ERR-resets-at-float,
    ERR-env-float-crash)."""
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return default

plan_ttl = fnum(plan_ttl_s, 600.0) or 600.0
real_ttl = fnum(real_ttl_s, 210.0) or 210.0
cc100 = fnum(cc100_s, 2110000.0)
if cc100 <= 0: cc100 = 2110000.0
cc100cost = fnum(cc100cost_s, 410.0)
if cc100cost <= 0: cc100cost = 410.0
cc_margin = fnum(cc_margin_s, 600.0)
now = time.time(); WINDOW = 5 * 3600
CHECKER_TRUST = 900.0   # check_usage.sh trusts a real reading this long — never
                        # overwrite one inside that window with a guess
harvest_only = (mode == "harvest")

def parse_ts(s):
    try:
        import datetime
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None

def load(p):
    try:
        with open(p) as f: return json.load(f)
    except Exception:
        return None

def atomic_write(p, obj):
    d = os.path.dirname(p) or "."; os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-usage-")
    try:
        with os.fdopen(fd, "w") as f: json.dump(obj, f)
        os.replace(tmp, p)
    except Exception:
        try: os.unlink(tmp)
        except Exception: pass

def real_still_trusted():
    """True while the on-disk snapshot is a real reading the CHECKER still acts on
    (fresh within its 900s window, and either in-block or reset-UNKNOWN — the checker
    bounded-pauses on reset-unknown reals, so they deserve the same protection; only
    a PASSED reset, the "RECHECK 97" trap, stays replaceable). Re-loaded at call time
    so a real write landing during our up-to-90s ccusage stall is honored (audit
    DAT-real-overwritten: the race window shrinks to milliseconds)."""
    s = load(snap_path)
    if not isinstance(s, dict) or s.get("source") not in ("real", "real-api"):
        return False
    t = time.time()
    a = t - fnum(s.get("ts"), 0)
    if a >= CHECKER_TRUST:
        return False
    # Exact parity with check_usage.sh: a truly-UNKNOWN reset (absent/null/non-numeric)
    # is bounded-paused by the checker → protect it; a numeric reset protects only while
    # in the future (a passed reset — including a literal 0 — is the RECHECK trap the
    # checker wants refreshed).
    try:
        return float((s.get("five_hour") or {}).get("resets_at")) > t
    except (TypeError, ValueError):
        return True

def ccusage_active():
    """Return {out,start,reset} for the active block, {"none":True} if history exists
    but nothing is active (genuinely idle), or None if ccusage is unavailable OR shows
    no blocks at all (data-blind ≠ idle — audit ERR-zero-when-blind).
    --offline: token counts are local; only pricing needs network."""
    try:
        proc = subprocess.run(["npx", "-y", "ccusage@" + ccver, "blocks", "--json", "--offline"],
                              capture_output=True, text=True, timeout=90)
        if proc.returncode != 0 or not proc.stdout.strip(): return None
        data = json.loads(proc.stdout)
    except Exception:
        return None
    blocks = data.get("blocks") if isinstance(data, dict) else data
    if not isinstance(blocks, list) or not blocks: return None
    b = next((x for x in blocks if isinstance(x, dict) and x.get("isActive") and not x.get("isGap")), None)
    if not b: return {"none": True}
    tc = b.get("tokenCounts") or {}
    out = tc.get("outputTokens")
    if not isinstance(out, (int, float)): return None
    start = parse_ts(b.get("startTime")); end = parse_ts(b.get("endTime"))
    cost = b.get("costUSD")
    cost = float(cost) if isinstance(cost, (int, float)) and cost > 0 else None
    return {"out": int(out), "cost": cost, "start": start, "reset": end}

def load_cal():
    c = load(cal_path); return c if isinstance(c, list) else []

def record_cal(pct, cc):
    """Record a (real %, ccusage tokens) pair ONLY when both plausibly describe the
    same 5h window (audit ERR-cal-poison): the ccusage block must be older than 1h
    (outside the hour-flooring skew zone) and a high % demands a sane token mass.
    Thinned to <=1 point/15min so 20 points span ~5h, not ~1h (audit
    QUA-cal-short-memory). Reloads the file at write time (audit DAT-cal-write-race)."""
    if not cc or cc.get("none"): return
    out = cc.get("out"); start = cc.get("start")
    if not isinstance(out, (int, float)) or out <= 0 or pct < 10: return
    if start is None or (now - start) < 3600: return
    if pct >= 30 and out < 100000: return
    cal = [p for p in load_cal() if isinstance(p, dict)]
    if cal and (now - fnum(cal[-1].get("ts"), 0)) < 900: return
    point = {"ts": now, "pct": float(pct), "tok": int(out)}
    c = cc.get("cost")
    if isinstance(c, (int, float)) and c > 0: point["cost"] = float(c)   # model-weighted basis
    cal.append(point)
    atomic_write(cal_path, cal[-20:])

def carry_weekly(prev):
    """Carry the previous snapshot's weekly reading forward. A legacy reading with no
    observed_ts gets stamped from the previous snapshot's own ts — otherwise the
    checker's honest-aging falls back to snapshot age, which the daemon's 3-min
    re-stamps would launder fresh forever (audit DAT-weekly-launder, legacy path)."""
    sd = prev.get("seven_day") if isinstance(prev, dict) else None
    if not isinstance(sd, dict):
        return None
    if "observed_ts" not in sd:
        sd = dict(sd); sd["observed_ts"] = int(fnum(prev.get("ts"), now))
    return sd

API_BASE = (os.environ.get("OVERNIGHT_API_BASE") or "https://api.anthropic.com").rstrip("/")

def _creds_from_blob(raw, where):
    """Parse a credentials JSON blob (Keychain or ~/.claude/.credentials.json) →
    (token, reason). Shape: {claudeAiOauth:{accessToken, expiresAt(ms), ...}}."""
    try:
        creds = json.loads(raw)
    except ValueError:
        return None, where + "-parse-error"
    oauth = creds.get("claudeAiOauth") if isinstance(creds, dict) else None
    if not isinstance(oauth, dict):
        oauth = creds if isinstance(creds, dict) else {}
    tok = oauth.get("accessToken")
    if not isinstance(tok, str) or not tok:
        return None, "no-access-token-in-" + where
    exp = oauth.get("expiresAt")   # ms epoch
    if isinstance(exp, (int, float)) and exp / 1000.0 < time.time() + 60:
        # Expired (or about to). Deliberately NOT refreshed here: token rotation could
        # log the CLI out. Any terminal `claude` use refreshes it.
        return None, "token-expired (run `claude` once in a terminal to refresh)"
    return tok, None

def _oauth_token():
    """Access token for the usage endpoint, or (None, reason). Sources, in order:
    our own env (hook-spawned contexts) → macOS Keychain → ~/.claude/.credentials.json
    (Linux, and any setup with file-based creds). The value is returned in-memory
    only — callers must never echo, log, or persist it."""
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if tok:
        return tok, None
    reasons = []
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=10)
        if proc.returncode == 0 and proc.stdout.strip():
            tok, why = _creds_from_blob(proc.stdout, "keychain")
            if tok:
                return tok, None
            reasons.append(why)
        else:
            reasons.append("no-keychain-item")
    except FileNotFoundError:
        pass          # no `security` binary — not macOS; the file below is the normal home
    except Exception:
        reasons.append("keychain-error")
    cred_path = os.path.expanduser("~/.claude/.credentials.json")
    if os.path.exists(cred_path):
        try:
            with open(cred_path) as f:
                raw = f.read()
            tok, why = _creds_from_blob(raw, "credentials-file")
            if tok:
                return tok, None
            reasons.append(why)
        except Exception:
            reasons.append("credentials-file-unreadable")
    else:
        reasons.append("no-credentials-file")
    return None, "; ".join(reasons) + " (terminal `claude` not installed/logged in?)"

def _api_win(v):
    """One window from the usage response → {used_percentage, resets_at} or None.
    Schema (from the CLI's own validator): utilization 0-100 (nullable), resets_at
    ISO-8601 (nullable); limits[] entries use percent instead of utilization."""
    if not isinstance(v, dict):
        return None
    u = None
    for k in ("utilization", "used_percentage", "percent"):
        if isinstance(v.get(k), (int, float)):
            u = float(v[k]); break
    if u is None:
        return None
    rr = v.get("resets_at")
    rts = parse_ts(rr) if isinstance(rr, str) else (float(rr) if isinstance(rr, (int, float)) else None)
    return {"used_percentage": round(min(100.0, max(0.0, u)), 1),
            "resets_at": (int(rts) if rts else None)}

def real_api():
    """Fetch the TRUE percentages from /api/oauth/usage. Returns a parsed dict
    ({five_hour, weekly, weekly_name, windows, note}) or None (unavailable — caller
    falls through to the next source). Never raises; never leaks the token."""
    if (os.environ.get("OVERNIGHT_REAL_API") or "auto").lower() == "off":
        return None
    tok, why = _oauth_token()
    if not tok:
        if why and mode != "harvest":
            sys.stderr.write("real-api unavailable: %s\n" % why)
        return None
    import urllib.request, urllib.error
    req = urllib.request.Request(API_BASE + "/api/oauth/usage", headers={
        "Authorization": "Bearer " + tok,
        "Content-Type": "application/json",
        "anthropic-beta": "oauth-2025-04-20"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        sys.stderr.write("real-api HTTP %s%s\n" % (e.code,
            " (token invalid — run `claude` once in a terminal)" if e.code in (401, 403) else ""))
        return None
    except Exception as e:
        sys.stderr.write("real-api error: %s\n" % type(e).__name__)
        return None
    if not isinstance(data, dict):
        return None
    fh = _api_win(data.get("five_hour"))
    windows = {}
    for k, v in data.items():
        if k != "five_hour" and isinstance(v, dict):
            w = _api_win(v)
            if w: windows[k] = w
    lim = data.get("limits")
    for ent in (lim if isinstance(lim, list) else []):
        if not isinstance(ent, dict):
            continue
        w = _api_win(ent)
        if not w:
            continue
        try:
            name = "weekly_" + str(ent["scope"]["model"]["display_name"]).strip().lower().replace(" ", "_")
        except Exception:
            name = str(ent.get("kind") or "limit")
        windows.setdefault(name, w)
    # Guardrail weekly = WORST enforceable weekly window (all-models seven_day plus any
    # per-model weekly). Overage / oauth-apps accounting windows stay detail-only.
    best = None; best_name = None
    for k, w in windows.items():
        if not (k == "seven_day" or k.startswith("seven_day_") or k.startswith("weekly_")):
            continue
        if "overage" in k or "oauth_apps" in k:
            continue
        if best is None or w["used_percentage"] > best["used_percentage"]:
            best, best_name = w, k
    if fh is None and best is None:
        return None
    return {"five_hour": fh, "weekly": best, "weekly_name": best_name, "windows": windows}

def _median_per_pct(cal, key):
    pts = [p for p in cal if isinstance(p, dict) and (p.get("pct") or 0) >= 10 and (p.get(key) or 0) > 0]
    if not pts: return None
    recent = [p for p in pts if (now - (p.get("ts") or 0)) < 24 * 3600]
    use = recent if recent else pts[-3:]
    r = sorted(p[key] / p["pct"] for p in use); n = len(r)
    return r[n // 2] if n % 2 else (r[n // 2 - 1] + r[n // 2]) / 2

def tokens_per_pct(cal):  # ccusage output-tokens per 1% of the 5h window (model-BLIND)
    return _median_per_pct(cal, "tok")

def cost_per_pct(cal):    # ccusage costUSD per 1% of the 5h window (model-WEIGHTED)
    return _median_per_pct(cal, "cost")

snap = load(snap_path)
ssrc = (snap or {}).get("source") if isinstance(snap, dict) else None
sage = now - fnum((snap or {}).get("ts"), 0) if isinstance(snap, dict) else 1e9
sreset = fnum(((snap or {}).get("five_hour") or {}).get("resets_at"), 0) if isinstance(snap, dict) else 0
cal = load_cal()

# 1) FRESH statusline REAL snapshot → authoritative; calibrate against ccusage, don't
#    overwrite. A fresh real with UNKNOWN reset (sreset==0) is still authoritative —
#    just skip the calibration pairing (audit ERR-real-gate-resets-at); only a PASSED
#    reset (the "RECHECK 97" trap) falls through to be replaced.
if (not harvest_only and isinstance(snap, dict) and ssrc == "real"
        and sage < real_ttl and (sreset > now or sreset == 0)):
    fh = (snap.get("five_hour") or {}).get("used_percentage")
    if isinstance(fh, (int, float)) and sreset > now:
        record_cal(float(fh), ccusage_active())
    note = "in-block" if sreset > now else "reset-unknown"
    print(f"REAL {fh}% (statusline; authoritative, {note}) age={int(sage)}s")
    sys.exit(0)

# 1b) REAL from the official usage API. Runs BEFORE the aging-real guard on purpose:
#     a fresh server answer may replace an AGING statusline reading (it is never lower
#     confidence than a cached one). Skipped in harvest mode (the feeder's job is
#     narrow) and whenever a FRESH statusline real already exited above.
if not harvest_only:
    api = real_api()
    if api and api.get("five_hour"):
        fh_w = dict(api["five_hour"]); fh_w["observed_ts"] = int(now)
        prev = snap if isinstance(snap, dict) else {}
        sdw = None
        if api.get("weekly"):
            sdw = dict(api["weekly"]); sdw["observed_ts"] = int(now)
            if api.get("weekly_name"): sdw["window"] = api["weekly_name"]
        atomic_write(snap_path, {"ts": int(now), "source": "real-api",
            "five_hour": fh_w,
            "seven_day": (sdw if sdw else carry_weekly(prev)),
            "windows": api.get("windows"),
            "context_pct": prev.get("context_pct") if isinstance(prev.get("context_pct"), (int, float)) else None})
        # Feed the estimator's calibration from this real reading — but spawn the npx
        # ccusage probe at most once per thinning window (record_cal re-checks its gates).
        cal_pts = load_cal()
        if not cal_pts or (now - fnum(cal_pts[-1].get("ts"), 0)) >= 900:
            record_cal(float(fh_w["used_percentage"]), ccusage_active())
        wk_s = ("%s%% (%s)" % (sdw["used_percentage"], sdw.get("window"))) if sdw else "?"
        print(f"REAL-API {fh_w['used_percentage']}% wk={wk_s}")
        sys.exit(0)

# Checker-trust guard: even when the daemon would not treat the real reading as its
# own fast path (210s < age < 900s), the CHECKER still acts on it — leave it alone.
if not harvest_only and real_still_trusted():
    print(f"REAL (aging, within checker trust window) age={int(sage)}s — left in place")
    sys.exit(0)

# 2) REAL from the Claude Desktop app cache, if a FRESH sample from the CURRENT window
#    exists (audit DAT-stale-app-pause: a pre-reset % must never be written with an
#    invented future reset — that costs a full 5h false pause).
pu = load(plan_path)
if isinstance(pu, dict) and isinstance(pu.get("samples"), list) and pu["samples"]:
    last = pu["samples"][-1]; t_ms = last.get("t"); u = last.get("u") or {}
    fh = u.get("fh"); sd = u.get("sd"); xu = u.get("xu")
    if isinstance(t_ms, (int, float)) and isinstance(fh, (int, float)):
        sample_ts = t_ms / 1000.0
        fresh = 0 <= (now - sample_ts) < plan_ttl
        # Reject samples that predate the current window: if the last real reading's
        # reset has PASSED and this sample is older than that reset, it describes the
        # exhausted OLD block.
        if fresh and ssrc in ("real", "real-api") and 0 < sreset <= now and sample_ts < sreset:
            fresh = False
            if harvest_only:
                print("NOFEED (Desktop sample predates the current 5h window)"); sys.exit(0)
        cc = ccusage_active() if fresh else None
        if fresh and cc and not cc.get("none") and cc.get("start") and sample_ts < cc["start"]:
            fresh = False   # sample older than the active block == old window
            if harvest_only:
                print("NOFEED (Desktop sample predates the active block)"); sys.exit(0)
        if fresh:
            if harvest_only and real_still_trusted():
                print("NOFEED (fresh real statusline reading present)"); sys.exit(0)
            if cc and not cc.get("none"): record_cal(float(fh), cc)
            # Honest reset: known ccusage end + margin, else null — the checker's
            # bounded "PAUSE <pct> 1800 ?" default handles unknown resets safely;
            # never fabricate now+5h (audit DAT-stale-app-pause).
            reset = int(cc["reset"] + cc_margin) if (cc and cc.get("reset")) else None
            if not real_still_trusted():
                atomic_write(snap_path, {"ts": int(sample_ts), "source": "real-app",
                    "five_hour": {"used_percentage": fh, "resets_at": reset,
                                  "observed_ts": int(sample_ts)},
                    "seven_day": ({"used_percentage": sd, "observed_ts": int(sample_ts)}
                                  if isinstance(sd, (int, float)) else
                                  carry_weekly(snap if isinstance(snap, dict) else {})),
                    "context_pct": (snap or {}).get("context_pct") if isinstance(snap, dict) else None,
                    "extra_usage": xu})
                print(f"REAL-APP fh={fh}% sd={sd}% (Desktop cache, sample age {int(now - sample_ts)}s)")
                sys.exit(0)

if harvest_only:
    st = "stale" if isinstance(pu, dict) else "missing"
    print(f"NOFEED (plan-usage-history {st}) — snapshot left as-is"); sys.exit(0)

# 3) ESTIMATE from ccusage's deduped, block-aligned active block.
cc = ccusage_active()
prev = snap if isinstance(snap, dict) else {}
if cc is None:
    # ccusage unavailable or data-blind → be honest: UNKNOWN, no fabricated %.
    if not real_still_trusted():
        atomic_write(snap_path, {"ts": int(now), "source": "unknown",
            "five_hour": {"used_percentage": None, "resets_at": None},
            "seven_day": carry_weekly(prev),
            "context_pct": prev.get("context_pct") if isinstance(prev.get("context_pct"), (int, float)) else None,
            "note": "ccusage unavailable/data-blind and no fresh real reading"})
    print("UNKNOWN (ccusage unavailable or data-blind, no fresh real reading) — check_usage will read NO_DATA")
    sys.exit(0)
if cc.get("none"):
    # history exists, no active block == fresh/idle window ≈ 0%
    if not real_still_trusted():
        atomic_write(snap_path, {"ts": int(now), "source": "estimate-ccusage",
            "five_hour": {"used_percentage": 0.0, "resets_at": int(now + WINDOW)},
            "seven_day": carry_weekly(prev),
            "context_pct": prev.get("context_pct") if isinstance(prev.get("context_pct"), (int, float)) else None,
            "window_output_tokens": 0, "cc_calibrated": bool(tokens_per_pct(cal))})
    print("ESTIMATE-CCUSAGE 0.0% (no active block — fresh/idle window)")
    sys.exit(0)

out = cc["out"]; cost = cc.get("cost")
# COST is model-WEIGHTED (ccusage prices Opus ~5x Fable), so it tracks the 5h limit in
# BOTH regimes — high during Opus-heavy work, low during cheap Fable work — and is the
# SOLE basis whenever ccusage reports it. The output-token proxy is model-BLIND: it
# under-reads Opus-heavy windows (the 44%-vs-79% bug) AND over-reads cheap Fable-heavy
# ones (false pauses), so it is only a fallback for the rare case where cost is absent.
# (A max(cost,token) "fail-safe" is WRONG here: the token over-read would win on cheap
# work and false-pause the loop — the very bug this whole subsystem exists to prevent.)
cpp = cost_per_pct(cal)
if isinstance(cost, (int, float)) and cost > 0:
    pct = (cost / cpp) if (cpp and cpp > 0) else (cost / cc100cost * 100.0)
    cal_ok = bool(cpp and cpp > 0)
    calnote = "cost-weighted" + ("" if cal_ok else " seed")
else:
    tpp = tokens_per_pct(cal)
    pct = (out / tpp) if (tpp and tpp > 0) else (out / cc100 * 100.0)
    cal_ok = bool(tpp and tpp > 0)
    calnote = "token-fallback " + ("calibrated" if cal_ok else "seed (no cost from ccusage)")
pct = min(100.0, math.ceil(min(100.0, pct) * 10) / 10.0)   # round UP within the basis
reset = cc.get("reset")
reset = int(reset + cc_margin) if reset else int(now + WINDOW)   # +margin: ccusage reset can be ~59min early
if not real_still_trusted():   # re-checked at write time — closes the 90s stall race
    atomic_write(snap_path, {"ts": int(now), "source": "estimate-ccusage",
        "five_hour": {"used_percentage": round(pct, 1), "resets_at": reset},
        "seven_day": carry_weekly(prev),
        "context_pct": prev.get("context_pct") if isinstance(prev.get("context_pct"), (int, float)) else None,
        "window_output_tokens": out, "window_cost_usd": cost,
        "cc_calibrated": cal_ok})
    print(f"ESTIMATE-CCUSAGE {round(pct, 1)}% ({out:,} out-tok · ${cost if cost else 0:.0f} · {calnote})")
else:
    print("REAL reading appeared during refresh — estimate write skipped")
PY
}

daemon_alive() {  # $1 = pidfile; echoes pid if a live usage_daemon owns it
  local pid
  [ -f "$1" ] || return 1
  pid=$(cat "$1" 2>/dev/null)
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  kill -0 "$pid" 2>/dev/null || return 1
  ps -p "$pid" -o command= 2>/dev/null | grep -q usage_daemon || return 1
  echo "$pid"
}

watch_loop() {  # $1=full|harvest  $2=pidfile  $3=gate(yes|no)
  local kind="$1" pidfile="$2" gate="$3" oldpid
  # Crash-safe single instance (audit LIF-pidfile-stale): validate digits, verify the
  # pid actually belongs to a usage_daemon (PID reuse after reboot must not block a
  # restart), reclaim stale files, and create with noclobber to close the TOCTOU.
  if oldpid=$(daemon_alive "$pidfile"); then
    echo "already running (pid $oldpid)"; exit 0
  fi
  rm -f "$pidfile" 2>/dev/null
  if ! ( set -C; echo $$ > "$pidfile" ) 2>/dev/null; then
    echo "already running (lost startup race)"; exit 0
  fi
  trap 'rm -f "$pidfile" 2>/dev/null' EXIT
  # Sweep temp litter from hard kills (audit FS-tmp-litter).
  find "$HOME/.claude" -maxdepth 1 -name '.tmp-usage-*' -mmin +60 -delete 2>/dev/null
  find "$(dirname "$SNAP")" -maxdepth 1 -name "$(basename "$SNAP").tmp.*" -mmin +60 -delete 2>/dev/null
  echo "watching every ${INTERVAL}s (${kind})"
  while :; do
    if [ "$gate" = "yes" ]; then
      [ -f "$FLAG" ] || break
      # A flag the hooks can't read is "not armed" — agree with them instead of
      # running forever on a corrupt file (audit LIF-corrupt-flag-split).
      if command -v jq >/dev/null 2>&1 && ! jq -e '.cwd' "$FLAG" >/dev/null 2>&1; then
        echo "flag unreadable — standing down (matches hook behavior)"; break
      fi
      # Heartbeat: hooks treat a flag untouched for 30+ min as a dead run.
      touch "$FLAG" 2>/dev/null
    fi
    run_py "$kind" >/dev/null 2>&1 || true
    # Adaptive cadence: while the real-api source is delivering (one cheap HTTPS GET),
    # refresh every ~60s so the % the user sees is at most a minute old; every other
    # source keeps the full INTERVAL (the estimate path spawns npx — 60s would thrash).
    local step="$INTERVAL"
    if [ "$kind" = "full" ]; then
      if python3 -c 'import json,sys,time;d=json.load(open(sys.argv[1]));exit(0 if d.get("source")=="real-api" and time.time()-float(d.get("ts") or 0)<300 else 1)' "$SNAP" 2>/dev/null; then
        step=60; [ "$INTERVAL" -lt 60 ] && step="$INTERVAL"
      fi
    fi
    local slept=0
    while [ "$slept" -lt "$step" ]; do
      [ "$gate" = "yes" ] && [ ! -f "$FLAG" ] && break
      sleep 5; slept=$((slept+5)); done
    [ "$gate" = "yes" ] && [ ! -f "$FLAG" ] && break
  done
  echo "exiting"
}

case "$MODE" in
  --status)
    echo "snapshot: $SNAP"
    [ -f "$SNAP" ] && python3 -c 'import json,sys,time;d=json.load(open(sys.argv[1]));fh=(d.get("five_hour") or {});print("  source:",d.get("source"),"| 5h:",fh.get("used_percentage"),"% | reset_in:",(int((fh.get("resets_at") or 0)-time.time())//60 if fh.get("resets_at") else "?"),"min | age:",int(time.time()-(d.get("ts") or 0)),"s")' "$SNAP" 2>/dev/null || echo "  (none / unreadable)"
    echo "calibration (ccusage basis): $CALFILE"
    [ -f "$CALFILE" ] && python3 -c 'import json,sys;c=json.load(open(sys.argv[1]));print("  points:",len(c));[print("   %s%% <-> %s"%(p.get("pct"), (("$%s cost"%p["cost"]) if p.get("cost") else "") + ((" · %s out-tok"%p["tok"]) if p.get("tok") else "") or "(empty)")) for p in c[-3:]]' "$CALFILE" 2>/dev/null || echo "  (none yet / unreadable)"
    echo "real-api source:"
    python3 - <<'PST'
import json, subprocess, time, os

def probe(raw, where):
    creds = json.loads(raw)
    oauth = creds.get("claudeAiOauth") if isinstance(creds, dict) else None
    oauth = oauth if isinstance(oauth, dict) else (creds if isinstance(creds, dict) else {})
    exp = oauth.get("expiresAt")
    if not oauth.get("accessToken"):
        return "  UNAVAILABLE — %s present but no access token" % where
    if isinstance(exp, (int, float)) and exp / 1000.0 < time.time() + 60:
        return ("  UNAVAILABLE — %s token EXPIRED %s (run `claude` once in a terminal to refresh)"
                % (where, time.strftime("%H:%M", time.localtime(exp / 1000.0))))
    ok = ("valid until %s" % time.strftime("%a %H:%M", time.localtime(exp / 1000.0))
          if isinstance(exp, (int, float)) else "no expiry recorded")
    return "  available (%s token, %s)" % (where, ok)

if (os.environ.get("OVERNIGHT_REAL_API") or "auto").lower() == "off":
    print("  disabled (OVERNIGHT_REAL_API=off)")
elif os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
    print("  available (token from environment)")
else:
    msg = None
    try:
        p = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                           capture_output=True, text=True, timeout=10)
        if p.returncode == 0 and p.stdout.strip():
            msg = probe(p.stdout, "Keychain")
    except Exception:
        pass
    if msg is None or "UNAVAILABLE" in msg:
        cred = os.path.expanduser("~/.claude/.credentials.json")
        if os.path.exists(cred):
            try:
                m2 = probe(open(cred).read(), "credentials-file")
                if msg is None or "available" in m2.split("—")[0]:
                    msg = m2
            except Exception:
                msg = msg or "  unknown (credentials file unreadable)"
    print(msg or "  UNAVAILABLE — no credentials found (install + log in the terminal `claude` CLI; "
                 "on macOS also click 'Always Allow' on the Keychain prompt)")
PST
    echo "processes:"
    if pid=$(daemon_alive "$HOME/.claude/overnight-usage-daemon.pid"); then
      echo "  daemon (--watch): RUNNING (pid $pid)"
    else
      [ -f "$HOME/.claude/overnight-usage-daemon.pid" ] && echo "  daemon (--watch): NOT RUNNING (stale pidfile)" || echo "  daemon (--watch): not running"
    fi
    if pid=$(daemon_alive "$HOME/.claude/overnight-usage-feeder.pid"); then
      echo "  feeder (--feed):  RUNNING (pid $pid)"
    else
      [ -f "$HOME/.claude/overnight-usage-feeder.pid" ] && echo "  feeder (--feed):  NOT RUNNING (stale pidfile)" || echo "  feeder (--feed):  not running"
    fi
    ;;
  --watch)   watch_loop full    "$HOME/.claude/overnight-usage-daemon.pid" yes ;;
  --feed)    watch_loop harvest "$HOME/.claude/overnight-usage-feeder.pid" no ;;
  --harvest) run_py harvest ;;
  *)         run_py full ;;
esac
