#!/usr/bin/env bash
# overnightprotocol: usage checker (source-aware).
# Reads the snapshot written by statusline_usage_writer.sh / usage_daemon.sh and prints
# exactly ONE machine-readable line for the agent to act on. First token is the state:
#
#   OK <5h%> <7d%|?> ctx:<n>      REAL reading under threshold, keep working (ctx =
#                                 context-window %; "?" = that window's data absent —
#                                 do NOT read as 0%)
#   PAUSE <5h%> <secs> <hh:mm>    REAL reading at/over threshold — wait until reset. When
#                                 the reset time is unknown/unparseable a bounded default
#                                 is used: "PAUSE <pct> 1800 ?" (never an unbounded loop)
#   RECHECK <5h%>                 over threshold but reset time already passed;
#                                 take one cheap turn to refresh the snapshot, then re-check
#   WEEKLY_CAP <7d%> <secs> <hh:mm|?>  weekly limit binding — pause <secs> seconds, then
#                                 re-check and RESUME (loop semantics: this pause can last
#                                 days; it is still not a stop). Checked BEFORE staleness.
#   NO_DATA                       snapshot present but rate-limit fields are null/absent —
#                                 treat like STALE/MISSING (do NOT read as 0% used)
#   STALE <age_seconds>           snapshot too old, statusline/daemon not ticking
#   MISSING [reason]              no snapshot / unreadable / missing dependency / bad args
#
#   EST_OK <pct> wk:<7d%|?>       ESTIMATE under the estimate threshold. Emitted both for
#                                 daemon-written estimate snapshots (source-aware — a guess
#                                 NEVER prints as plain OK/PAUSE) and for this script's own
#                                 ccusage fallback.
#   EST_PAUSE <pct> <secs> <hh:mm>  ESTIMATE at/over the estimate threshold. Estimates
#                                 pause at OVERNIGHT_EST_THRESH (default: 10 points below
#                                 the main threshold, floor 50) — a proxy number must pause
#                                 EARLIER than a real one, never at the full cap.
#
# On any of {MISSING, STALE, NO_DATA} the script AUTOMATICALLY attempts the ccusage
# estimate and prints an EST_* line instead, so the agent's interface stays uniform.
# If the estimate itself fails, the PRIMARY state (MISSING/STALE/NO_DATA) is printed
# and a note goes to stderr — no EST_UNAVAILABLE token is ever printed on stdout.
# ccusage is version-PINNED because this runs unattended (supply chain) — bump it
# deliberately via OVERNIGHT_CCUSAGE_VER or in ALL THREE pin sites: this file,
# preflight.sh, and usage_daemon.sh.
#
# Usage: check_usage.sh [threshold_pct] [weekly_threshold_pct]
#   threshold_pct          default 95   (applies to the 5-hour window — real readings are
#                          trusted to run close to the cap; estimates still pause earlier)
#   weekly_threshold_pct   default = threshold_pct   (applies to the 7-day window)
#   (a single "95 90" argument is tolerated and split — but pass two arguments)
# Env:
#   OVERNIGHT_USAGE_FILE            override snapshot path (default ~/.claude/overnight-usage.json)
#   OVERNIGHT_ESTIMATE              "off" disables the ccusage fallback; anything else = auto
#   OVERNIGHT_EST_THRESH            estimate pause threshold (default max(50, threshold-10))
#   OVERNIGHT_CCUSAGE_RESET_MARGIN  seconds added to ccusage's early reset (default 600)
#   OVERNIGHT_CCUSAGE_VER           exact x.y.z ccusage pin (default 20.0.17; invalid → default)
#   OVERNIGHT_CCUSAGE_100PCT        output-token seed budget for 100% of the 5h window (default 2110000)
#   OVERNIGHT_CCUSAGE_100PCT_COST   cost($) seed budget for 100% of the 5h window (default 410) —
#                                   the model-weighted primary basis (see usage_daemon.sh header)

set -u
THRESH="${1:-95}"
WEEKLY_THRESH="${2:-}"
# Tolerate a single space-joined argument like "95 90" (docs say two args; belt-and-braces).
case "$THRESH" in
  *" "*) set -- $THRESH; THRESH="$1"; [ -z "$WEEKLY_THRESH" ] && WEEKLY_THRESH="${2:-}" ;;
esac
[ -n "$WEEKLY_THRESH" ] || WEEKLY_THRESH="$THRESH"
SNAP="${OVERNIGHT_USAGE_FILE:-$HOME/.claude/overnight-usage.json}"
ESTIMATE_MODE="${OVERNIGHT_ESTIMATE:-auto}"

# Dependency guard (V4): without python3 we cannot parse the snapshot; emit a
# parseable MISSING line (first token still routes the agent to the fallback path)
# instead of a bare 'command not found' + exit 127 that breaks the one-line contract.
if ! command -v python3 >/dev/null 2>&1; then
  echo "MISSING python3-not-found"
  exit 0
fi

python3 - "$SNAP" "$THRESH" "$WEEKLY_THRESH" "$ESTIMATE_MODE" <<'PY'
import json, sys, time, subprocess, shutil, os, re

snap_path, th_s, wth_s, est_mode = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    th = float(th_s)
    wth = float(wth_s)
except ValueError:
    # Bad argv must not break the one-line contract (audit ERR-argv-breaks-contract).
    print("MISSING bad-arguments")
    sys.exit(0)
now = time.time()

def envnum(name, default):
    """Read a numeric env var defensively — a config typo must never break the
    one-line contract (audit ERR-env-float-crash)."""
    try:
        v = float(os.environ.get(name, ""))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default

# Estimates pause EARLIER than real readings: default margin is 10 points below the
# main threshold (floor 50), never above it (audit ERR-est-margin-prose).
eth = envnum("OVERNIGHT_EST_THRESH", max(50.0, th - 10.0))
eth = min(eth, th)
RESET_MARGIN = envnum("OVERNIGHT_CCUSAGE_RESET_MARGIN", 600.0)
CCVER = os.environ.get("OVERNIGHT_CCUSAGE_VER", "20.0.17")
if not re.fullmatch(r"\d+\.\d+\.\d+", CCVER or ""):
    CCVER = "20.0.17"   # only exact versions are honored (supply-chain pin)

def hhmm(ts):
    try:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "?"

def load_snapshot(path):
    """Return parsed JSON, or a sentinel string if absent/unreadable. Retries once
    on a parse error to cover the rare read-during-write race (belt-and-braces on top
    of the writer's atomic rename)."""
    if not os.path.exists(path):
        return "MISSING_FILE"
    for attempt in (0, 1):
        try:
            with open(path) as f:
                return json.load(f)
        except (ValueError, OSError):
            if attempt == 0:
                time.sleep(0.3)
                continue
            return "CORRUPT"
    return "CORRUPT"

def pct(block):
    """Return the window's used_percentage as a float, or None if truly absent.
    Crucially does NOT coerce missing data to 0.0 — that would read as 'all clear'."""
    if not isinstance(block, dict):
        return None
    v = block.get("used_percentage")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _cal_median_per_pct(key):
    """Median (metric-per-percent) from the daemon's calibration file, using the SAME
    recency rule as the daemon (24h-recent points, else the last 3) so both estimator
    copies give one answer for one input (audit QUA-estimator-drift)."""
    try:
        cal = json.load(open(os.path.expanduser("~/.claude/overnight-usage-cal.json")))
        pts = [p for p in cal if isinstance(p, dict)
               and (p.get("pct") or 0) >= 10 and (p.get(key) or 0) > 0]
        if not pts:
            return None
        recent = [p for p in pts if (now - (p.get("ts") or 0)) < 24 * 3600]
        use = recent if recent else pts[-3:]
        r = sorted(p[key] / p["pct"] for p in use)
        n = len(r)
        return r[n // 2] if n % 2 else (r[n // 2 - 1] + r[n // 2]) / 2
    except Exception:
        return None

def cal_tokens_per_pct():  # ccusage output-tokens per 1% (model-BLIND)
    return _cal_median_per_pct("tok")

def cal_cost_per_pct():    # ccusage costUSD per 1% (model-WEIGHTED)
    return _cal_median_per_pct("cost")

# ---- ccusage estimate fallback -------------------------------------------------
def estimate():
    """Estimate from ccusage's DEDUPED, block-ALIGNED active block. Takes the HIGHER of a
    model-weighted COST proxy (primary) and a model-blind OUTPUT-token proxy (fallback),
    each converted via the daemon's calibration or its seed budget, and rounds UP —
    fail-safe so a guardrail never reads low (totalTokens is avoided: cache-dominated).
    Compared against the ESTIMATE threshold (margin below real). Never raises. Queries ALL
    blocks (not just --active) so 'genuinely idle' (history exists, none active) is
    distinguishable from 'ccusage is data-blind' (no blocks at all → EST_UNAVAILABLE,
    never a permissive 0% — audit ERR-zero-when-blind)."""
    if est_mode == "off":
        return "SKIP"
    if shutil.which("npx") is None:
        return "EST_UNAVAILABLE"
    try:
        proc = subprocess.run(
            ["npx", "-y", "ccusage@" + CCVER, "blocks", "--json", "--offline"],
            capture_output=True, text=True, timeout=90,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return "EST_UNAVAILABLE"
        data = json.loads(proc.stdout)
    except Exception:
        return "EST_UNAVAILABLE"

    blocks = data.get("blocks") if isinstance(data, dict) else data
    if not isinstance(blocks, list) or not blocks:
        return "EST_UNAVAILABLE"
    b = next((x for x in blocks
              if isinstance(x, dict) and x.get("isActive") and not x.get("isGap")), None)
    if b is None:
        return "EST_OK 0 wk:?"   # history exists, no active block == genuinely idle
    out = (b.get("tokenCounts") or {}).get("outputTokens")
    if not isinstance(out, (int, float)):
        return "EST_UNAVAILABLE"
    cost = b.get("costUSD")
    cost = float(cost) if isinstance(cost, (int, float)) and cost > 0 else None

    # COST is model-weighted (ccusage prices Opus ~5x Fable) so it tracks the 5h limit in
    # BOTH regimes — high on Opus-heavy work, low on cheap Fable work — and is the SOLE basis
    # whenever ccusage reports it. Raw output-tokens is model-blind: it under-reads Opus-heavy
    # windows (the 44%-vs-real-79% bug) AND over-reads cheap ones (false pauses), so it is only
    # a fallback for when cost is absent. (max(cost,token) would be wrong: the token over-read
    # would false-pause the loop on cheap work.)
    import math
    cpp = cal_cost_per_pct()
    if cost is not None:
        est_pct = (cost / cpp) if (cpp and cpp > 0) else (cost / envnum("OVERNIGHT_CCUSAGE_100PCT_COST", 410.0) * 100.0)
    else:
        tpp = cal_tokens_per_pct()
        est_pct = (out / tpp) if (tpp and tpp > 0) else (out / envnum("OVERNIGHT_CCUSAGE_100PCT", 2110000.0) * 100.0)
    est_pct = min(100.0, math.ceil(min(100.0, est_pct) * 10) / 10.0)   # round UP within the basis

    # reset = active block endTime + margin (ccusage floors the block start to the hour,
    # so its endTime can be up to ~59m early; the margin avoids resuming into a
    # still-capped window). Margin is env-tunable, same knob as the daemon.
    reset_epoch = None
    end = b.get("endTime")
    if isinstance(end, str):
        try:
            from datetime import datetime
            reset_epoch = datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp() + RESET_MARGIN
        except Exception:
            reset_epoch = None
    elif isinstance(end, (int, float)):
        reset_epoch = float(end) + RESET_MARGIN

    if est_pct >= eth:
        if reset_epoch and reset_epoch > now:
            return f"EST_PAUSE {est_pct:.0f} {int(reset_epoch - now)} {hhmm(reset_epoch)}"
        return f"EST_PAUSE {est_pct:.0f} 1800 ?"
    # wk:? — the fallback cannot see the 7-day window; weekly cap is UNKNOWN here.
    return f"EST_OK {est_pct:.0f} wk:?"

# ---- primary state from the snapshot ------------------------------------------
snap = load_snapshot(snap_path)

def primary():
    if snap == "MISSING_FILE":
        return "MISSING"
    if snap == "CORRUPT":
        return "MISSING corrupt-snapshot"
    if not isinstance(snap, dict):
        return "MISSING"

    age = now - float(snap.get("ts") or 0)
    src = str(snap.get("source") or "")
    fh = snap.get("five_hour")
    sd = snap.get("seven_day")
    f = pct(fh)
    w = pct(sd)

    # Both windows absent/null → data present but useless. Do NOT read as 0%.
    if f is None and w is None:
        return "NO_DATA"

    # Weekly cap is unrecoverable overnight — check it BEFORE staleness (audit
    # ERR-estimate-weekly-blind). Judge the trust window against the reading's OWN
    # observation time when present (audit DAT-weekly-launder: the daemon re-stamps
    # the snapshot every ~3 min, so snapshot age would launder a stale weekly reading
    # fresh forever); fall back to snapshot age for legacy snapshots. Trust up to 6h —
    # UNLESS its own reset time has already passed.
    if w is not None and w >= wth:
        try:
            wage = now - float((sd or {}).get("observed_ts"))
        except (TypeError, ValueError):
            wage = age
        if wage <= 21600:
            try:
                wr = float((sd or {}).get("resets_at"))
            except (TypeError, ValueError):
                wr = None
            if wr is None or wr > now:
                secs = int(wr - now) if (wr and wr > now) else 21600
                return f"WEEKLY_CAP {w:.0f} {secs} {hhmm(wr) if wr else '?'}"
            # else: the reading predates its own weekly reset — fall through (stale).

    if age > 900:
        return f"STALE {int(age)}"

    # If the pause-critical 5h window is absent, we cannot judge the guardrail even
    # when weekly data exists — route to the estimate fallback rather than reading 0%.
    if f is None:
        return "NO_DATA"

    # SOURCE-AWARE (audit ERR-est-as-real): a daemon-written ESTIMATE must never wear
    # the authoritative OK/PAUSE tokens — it gets EST_* and the earlier margin.
    if src.startswith("estimate"):
        try:
            r = float((fh or {}).get("resets_at"))
        except (TypeError, ValueError):
            r = None
        if f >= eth:
            if r is None:
                return f"EST_PAUSE {f:.0f} 1800 ?"
            if r > now:
                return f"EST_PAUSE {f:.0f} {int(r - now)} {hhmm(r)}"
            return f"RECHECK {f:.0f}"
        w_s = f"{w:.0f}" if w is not None else "?"
        return f"EST_OK {f:.0f} wk:{w_s}"

    if f >= th:
        try:
            r = float((fh or {}).get("resets_at"))
        except (TypeError, ValueError):
            r = None
        if r is None:
            # Reset time unknown/unparseable is NOT "reset already passed" (audit
            # ERR-unbounded-recheck): use a bounded default pause instead of an
            # unbounded RECHECK loop, mirroring the estimate path's 1800s default.
            return f"PAUSE {f:.0f} 1800 ?"
        if r > now:
            return f"PAUSE {f:.0f} {int(r - now)} {hhmm(r)}"
        # Over threshold and the reset genuinely elapsed → snapshot is effectively
        # stale; one cheap turn refreshes it (self-resolving).
        return f"RECHECK {f:.0f}"

    ctx = snap.get("context_pct")
    if isinstance(ctx, (int, float)):
        ctx_s = f"{float(ctx):.0f}"
    else:
        ctx_s = "?"
    # Never render an absent weekly window as 0% (audit ERR-ok-weekly-zero).
    w_s = f"{w:.0f}" if w is not None else "?"
    return f"OK {f:.0f} {w_s} ctx:{ctx_s}"

try:
    state = primary()
except Exception:
    # Whatever happens, never break the one-line contract.
    state = "MISSING internal-error"
first = state.split(" ", 1)[0]

# On an unavailable-data state, try the estimate fallback and prefer its answer.
if first in ("MISSING", "STALE", "NO_DATA") and est_mode != "off":
    est = estimate()
    if est.startswith("EST_") and est != "EST_UNAVAILABLE":
        print(est)
    elif est == "EST_UNAVAILABLE":
        # Truly blind: surface the primary state and note the fallback failed.
        print(state)
        sys.stderr.write("estimate fallback unavailable\n")
    else:
        print(state)
else:
    print(state)
PY
