#!/usr/bin/env bash
# Regression guard for the cost-weighted usage estimator (usage_daemon.sh + check_usage.sh).
# Run:  bash tests/test_estimator.sh        (tests the skill's own scripts/ copies)
#
# Fully isolated: a throwaway $HOME plus a fake `npx` on PATH that prints a fixture instead
# of calling ccusage — touches nothing real, needs no network. Locks in the 2026-07-21 fix:
#   * Opus-heavy window PAUSES         (raw output-tokens under-read it → 44% vs real 79%)
#   * cheap Fable-heavy window does NOT false-pause (a max(cost,token) fail-safe would have)
#   * cost absent → token fallback · idle → 0 · data-blind → not a permissive 0%
#   * a daemon-written estimate never wears the plain OK/PAUSE tokens (source-awareness)
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
CK="$HERE/../scripts/check_usage.sh"
DM="$HERE/../scripts/usage_daemon.sh"
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
FAKEBIN="$WORK/bin"; mkdir -p "$FAKEBIN" "$WORK/.claude"

# Deterministic calibration (mirrors the shipped seed):
#   cost 335$ <-> 80%  => 4.1875 $/pct        token 1794035 <-> 85%  => 21106 tok/pct
# ts far-future so the "recent < 24h" filter always keeps both points.
cat > "$WORK/.claude/overnight-usage-cal.json" <<'JSON'
[ {"ts": 9999999999, "pct": 85, "tok": 1794035},
  {"ts": 9999999999, "pct": 80, "cost": 335} ]
JSON
printf '#!/usr/bin/env bash\ncat "$FIXTURE"\n' > "$FAKEBIN/npx"; chmod +x "$FAKEBIN/npx"

FUTURE=$(python3 -c 'import time;print(int(time.time()+3600))')
ENDISO=$(python3 -c "import datetime;print(datetime.datetime.utcfromtimestamp($FUTURE).strftime('%Y-%m-%dT%H:%M:%S.000Z'))")
mkfix() { # $1=out $2=cost|null $3=active $4=empty(1=no blocks)
  if [ "${4:-0}" = "1" ]; then echo '{"blocks":[]}' > "$WORK/fix.json"; return; fi
  local cf=""; [ "$2" != "null" ] && cf="\"costUSD\": $2,"
  cat > "$WORK/fix.json" <<JSON
{"blocks":[{"isActive": $3, "isGap": false, "startTime":"2026-07-21T06:00:00.000Z",
  "endTime":"$ENDISO", $cf "tokenCounts":{"outputTokens": $1}}]}
JSON
}
run_ck() { env -i FIXTURE="$WORK/fix.json" PATH="$FAKEBIN:/usr/bin:/bin:/usr/local/bin" \
  HOME="$WORK" OVERNIGHT_USAGE_FILE="$WORK/none.json" bash "$CK" 80 80 2>/dev/null; }
run_dm() { rm -f "$WORK/snap.json"; env -i FIXTURE="$WORK/fix.json" \
  PATH="$FAKEBIN:/usr/bin:/bin:/usr/local/bin" HOME="$WORK" \
  OVERNIGHT_USAGE_FILE="$WORK/snap.json" bash "$DM" once 2>/dev/null; }
snap_pct(){ python3 -c "import json;print(json.load(open('$WORK/snap.json'))['five_hour']['used_percentage'])" 2>/dev/null; }
snap_src(){ python3 -c "import json;print(json.load(open('$WORK/snap.json'))['source'])" 2>/dev/null; }

PASS=0; FAIL=0
check() { if printf '%s' "$3" | grep -q -- "$2"; then echo "  PASS  $1 → $3"; PASS=$((PASS+1));
  else echo "  FAIL  $1 → got [$3], want substring [$2]"; FAIL=$((FAIL+1)); fi; }

echo "=== check_usage.sh (own ccusage fallback, snapshot MISSING) ==="
mkfix 942974 344 true;   check "opus-heavy → pause"           "EST_PAUSE 82" "$(run_ck)"
mkfix 1500000 25 true;   check "fable-heavy → NO false-pause" "EST_OK 6"     "$(run_ck)"
mkfix 942974 null true;  check "cost-absent → token fallback" "EST_OK 4"     "$(run_ck)"
mkfix 500000 20 false;   check "idle → 0"                     "EST_OK 0"     "$(run_ck)"
mkfix 0 0 true 1;        check "data-blind → not permissive 0" "MISSING"     "$(run_ck)"

echo; echo "=== usage_daemon.sh (snapshot used_percentage) ==="
mkfix 942974 344 true;   run_dm >/dev/null; check "daemon opus-heavy 82.2"    "82.2"            "$(snap_pct)"
mkfix 1500000 25 true;   run_dm >/dev/null; check "daemon fable-heavy 6.0"     "6.0"            "$(snap_pct)"
mkfix 500000 20 false;   run_dm >/dev/null; check "daemon idle 0.0"            "0.0"            "$(snap_pct)"
mkfix 0 0 true 1;        run_dm >/dev/null; check "daemon data-blind=unknown"  "unknown"       "$(snap_src)"

echo; echo "=== source-awareness: estimate snapshot never wears plain PAUSE ==="
python3 -c "import json,time;json.dump({'ts':int(time.time()),'source':'estimate-ccusage','five_hour':{'used_percentage':82.0,'resets_at':int(time.time()+3600)},'seven_day':None,'context_pct':None},open('$WORK/snap2.json','w'))"
R=$(env -i PATH="/usr/bin:/bin" HOME="$WORK" OVERNIGHT_USAGE_FILE="$WORK/snap2.json" OVERNIGHT_ESTIMATE=off bash "$CK" 80 80 2>/dev/null)
check "estimate snapshot → EST_PAUSE" "EST_PAUSE 82" "$R"

echo; echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
