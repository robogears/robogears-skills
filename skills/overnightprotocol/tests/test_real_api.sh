#!/usr/bin/env bash
# Regression guard for the real-api usage source (usage_daemon.sh source:"real-api").
# Run:  bash tests/test_real_api.sh
#
# Fully isolated: throwaway $HOME, a fake `security` (Keychain) and fake `npx` (ccusage)
# on PATH, and a local mock of /api/oauth/usage via OVERNIGHT_API_BASE. No network, no
# real credentials — the mock token is the literal string "test-token-123". Locks in:
#   * valid Keychain token → snapshot source:"real-api" with true 5h% + WORST weekly window
#   * per-model weekly (limits[] weekly_scoped) can BE the binding weekly window
#   * expired token / missing Keychain item / HTTP 401 → fall through to the estimate
#   * a fresh real-api snapshot is protected from estimate overwrite (checker-trust)
#   * a fresh statusline real short-circuits BEFORE any API call (endpoint hit count 0)
#   * check_usage.sh answers a real-api snapshot with plain OK/PAUSE (not EST_*)
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
CK="$HERE/../scripts/check_usage.sh"
DM="$HERE/../scripts/usage_daemon.sh"
WORK=$(mktemp -d)
FAKEBIN="$WORK/bin"; mkdir -p "$FAKEBIN" "$WORK/.claude"

# ---- mock /api/oauth/usage server (records hits, checks the bearer token) -------------
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
cat > "$WORK/mock_server.py" <<'PYS'
import http.server, json, os, sys
FIX = sys.argv[1]; HITS = sys.argv[2]; PORT = int(sys.argv[3])
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        with open(HITS, "a") as f: f.write(self.path + "\n")
        if self.headers.get("Authorization") != "Bearer test-token-123":
            self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":"auth"}'); return
        body = open(FIX, "rb").read()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.end_headers(); self.wfile.write(body)
http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
PYS
HITS="$WORK/hits.log"; : > "$HITS"
python3 "$WORK/mock_server.py" "$WORK/usage.json" "$HITS" "$PORT" &
SRV=$!
trap 'kill "$SRV" 2>/dev/null; rm -rf "$WORK"' EXIT
for _ in 1 2 3 4 5 6 7 8 9 10; do
  python3 -c "import socket;s=socket.create_connection(('127.0.0.1',$PORT),0.3);s.close()" 2>/dev/null && break
  sleep 0.3
done

# ---- fakes ----------------------------------------------------------------------------
# fake security: prints the creds fixture, or fails when $KC_MODE says so
cat > "$FAKEBIN/security" <<'EOF'
#!/usr/bin/env bash
case "${KC_MODE:-ok}" in
  missing) exit 44 ;;
  *) cat "$KC_FIX" ;;
esac
EOF
# fake npx (ccusage): emits the estimate fixture so fall-through paths are observable
printf '#!/usr/bin/env bash\ncat "$FIXTURE"\n' > "$FAKEBIN/npx"
chmod +x "$FAKEBIN/security" "$FAKEBIN/npx"

NOWMS=$(python3 -c 'import time;print(int(time.time()*1000))')
GOOD_EXP=$((NOWMS + 6*3600*1000)); BAD_EXP=$((NOWMS - 3600*1000))
mkcreds() { printf '{"claudeAiOauth":{"accessToken":"%s","refreshToken":"r","expiresAt":%s}}' "$1" "$2" > "$WORK/creds.json"; }
FUTURE_ISO=$(python3 -c 'import time,datetime;print(datetime.datetime.utcfromtimestamp(time.time()+3600).strftime("%Y-%m-%dT%H:%M:%S.000Z"))')
mkusage() { # $1=fh% $2=seven_day% $3=fable_weekly%
  cat > "$WORK/usage.json" <<JSON
{"five_hour":{"utilization": $1, "resets_at": "$FUTURE_ISO"},
 "seven_day":{"utilization": $2, "resets_at": "$FUTURE_ISO"},
 "seven_day_oauth_apps":{"utilization": 99, "resets_at": "$FUTURE_ISO"},
 "limits":[{"kind":"weekly_scoped","percent": $3,"resets_at":"$FUTURE_ISO",
            "scope":{"model":{"display_name":"Fable"}}}],
 "extra_usage":{"is_enabled":false}}
JSON
}
# ccusage estimate fixture: cheap block → low estimate, distinguishable from real-api
cat > "$WORK/ccfix.json" <<JSON
{"blocks":[{"isActive": true, "isGap": false, "startTime":"2026-07-21T06:00:00.000Z",
  "endTime":"$FUTURE_ISO", "costUSD": 25, "tokenCounts":{"outputTokens": 100000}}]}
JSON
cat > "$WORK/.claude/overnight-usage-cal.json" <<'JSON'
[ {"ts": 9999999999, "pct": 80, "cost": 335} ]
JSON

run_dm() { # uses current $KC_MODE / creds fixture / usage fixture
  rm -f "$WORK/snap.json"
  env -i PATH="$FAKEBIN:/usr/bin:/bin:/usr/local/bin" HOME="$WORK" \
    KC_MODE="${KC_MODE:-ok}" KC_FIX="$WORK/creds.json" FIXTURE="$WORK/ccfix.json" \
    OVERNIGHT_API_BASE="http://127.0.0.1:$PORT" \
    OVERNIGHT_USAGE_FILE="$WORK/snap.json" bash "$DM" once 2>"$WORK/stderr.txt"; }
snap(){ python3 -c "import json;d=json.load(open('$WORK/snap.json'));print(d['source'], d['five_hour']['used_percentage'], (d.get('seven_day') or {}).get('used_percentage'), (d.get('seven_day') or {}).get('window'))" 2>/dev/null; }

PASS=0; FAIL=0
check(){ if printf '%s' "$3" | grep -q -- "$2"; then echo "  PASS  $1 → $3"; PASS=$((PASS+1));
  else echo "  FAIL  $1 → got [$3], want substring [$2]"; FAIL=$((FAIL+1)); fi; }

echo "=== valid token → real-api snapshot ==="
mkcreds test-token-123 "$GOOD_EXP"; mkusage 79 65 63
KC_MODE=ok run_dm >/dev/null; check "source real-api, fh 79, weekly 65 (all-models binds)" "real-api 79.0 65.0 seven_day" "$(snap)"

echo "=== per-model weekly can bind ==="
mkusage 40 50 88
KC_MODE=ok run_dm >/dev/null; check "weekly = Fable 88 (scoped window binds)" "real-api 40.0 88.0 weekly_fable" "$(snap)"

echo "=== oauth-apps window is detail-only, never binds ==="
python3 -c "import json;d=json.load(open('$WORK/snap.json'));w=d.get('windows') or {};print('kept' if 'seven_day_oauth_apps' in w else 'dropped', d['seven_day']['used_percentage'])" > "$WORK/oa.txt"
check "oauth_apps kept as detail, weekly stays 88" "kept 88.0" "$(cat "$WORK/oa.txt")"

echo "=== degraded paths fall through to the estimate ==="
mkcreds expired-token "$BAD_EXP"
KC_MODE=ok run_dm >/dev/null; check "expired token → estimate" "estimate-ccusage" "$(snap)"
check "expiry reason on stderr" "token-expired" "$(cat "$WORK/stderr.txt")"
KC_MODE=missing run_dm >/dev/null; check "no keychain item → estimate" "estimate-ccusage" "$(snap)"
mkcreds wrong-token "$GOOD_EXP"
KC_MODE=ok run_dm >/dev/null; check "HTTP 401 → estimate" "estimate-ccusage" "$(snap)"
check "401 reason on stderr" "HTTP 401" "$(cat "$WORK/stderr.txt")"

echo "=== Linux path: no Keychain, creds in ~/.claude/.credentials.json ==="
mkusage 55 30 20
printf '{"claudeAiOauth":{"accessToken":"test-token-123","refreshToken":"r","expiresAt":%s}}' "$GOOD_EXP" > "$WORK/.claude/.credentials.json"
KC_MODE=missing run_dm >/dev/null; check "credentials-file → real-api" "real-api 55.0" "$(snap)"
printf '{"claudeAiOauth":{"accessToken":"test-token-123","refreshToken":"r","expiresAt":%s}}' "$BAD_EXP" > "$WORK/.claude/.credentials.json"
KC_MODE=missing run_dm >/dev/null; check "expired credentials-file → estimate" "estimate-ccusage" "$(snap)"
rm -f "$WORK/.claude/.credentials.json"

echo "=== a fresh real-api snapshot is protected from estimate overwrite ==="
mkcreds test-token-123 "$GOOD_EXP"; mkusage 79 65 63
KC_MODE=ok run_dm >/dev/null
env -i PATH="$FAKEBIN:/usr/bin:/bin:/usr/local/bin" HOME="$WORK" KC_MODE=missing \
  KC_FIX="$WORK/creds.json" FIXTURE="$WORK/ccfix.json" OVERNIGHT_REAL_API=off \
  OVERNIGHT_USAGE_FILE="$WORK/snap.json" bash "$DM" once >/dev/null 2>&1
check "estimate did NOT clobber fresh real-api" "real-api 79.0" "$(snap)"

echo "=== fresh statusline real short-circuits before any API call ==="
: > "$HITS"
python3 -c "import json,time;json.dump({'ts':int(time.time()),'source':'real','five_hour':{'used_percentage':33.0,'resets_at':int(time.time()+3600)},'seven_day':None,'context_pct':None},open('$WORK/snap.json','w'))"
env -i PATH="$FAKEBIN:/usr/bin:/bin:/usr/local/bin" HOME="$WORK" KC_MODE=ok \
  KC_FIX="$WORK/creds.json" FIXTURE="$WORK/ccfix.json" \
  OVERNIGHT_API_BASE="http://127.0.0.1:$PORT" \
  OVERNIGHT_USAGE_FILE="$WORK/snap.json" bash "$DM" once >/dev/null 2>&1
check "statusline real kept" "real 33.0" "$(snap)"
check "endpoint hits == 0" "^0$" "$(wc -l < "$HITS" | tr -d ' ')"

echo "=== check_usage answers a real-api snapshot with PLAIN tokens ==="
python3 -c "import json,time;json.dump({'ts':int(time.time()),'source':'real-api','five_hour':{'used_percentage':85.0,'resets_at':int(time.time()+3600),'observed_ts':int(time.time())},'seven_day':{'used_percentage':65.0,'resets_at':int(time.time()+86400),'observed_ts':int(time.time())},'context_pct':None},open('$WORK/snap.json','w'))"
R=$(env -i PATH="/usr/bin:/bin" HOME="$WORK" OVERNIGHT_USAGE_FILE="$WORK/snap.json" OVERNIGHT_ESTIMATE=off bash "$CK" 80 80 2>/dev/null)
check "over threshold → plain PAUSE (authoritative)" "PAUSE 85" "$R"
R=$(env -i PATH="/usr/bin:/bin" HOME="$WORK" OVERNIGHT_USAGE_FILE="$WORK/snap.json" OVERNIGHT_ESTIMATE=off bash "$CK" 90 90 2>/dev/null)
check "under threshold → plain OK with weekly" "OK 85 65" "$R"

echo "=== DEFAULT threshold is 95 (real readings run close to the cap) ==="
python3 -c "import json,time;json.dump({'ts':int(time.time()),'source':'real-api','five_hour':{'used_percentage':94.0,'resets_at':int(time.time()+3600),'observed_ts':int(time.time())},'seven_day':{'used_percentage':60.0,'resets_at':int(time.time()+86400),'observed_ts':int(time.time())},'context_pct':None},open('$WORK/snap.json','w'))"
R=$(env -i PATH="/usr/bin:/bin" HOME="$WORK" OVERNIGHT_USAGE_FILE="$WORK/snap.json" OVERNIGHT_ESTIMATE=off bash "$CK" 2>/dev/null)
check "94% real, no args → OK (default 95)" "OK 94 60" "$R"
python3 -c "import json,time;json.dump({'ts':int(time.time()),'source':'real-api','five_hour':{'used_percentage':96.0,'resets_at':int(time.time()+3600),'observed_ts':int(time.time())},'seven_day':{'used_percentage':60.0,'resets_at':int(time.time()+86400),'observed_ts':int(time.time())},'context_pct':None},open('$WORK/snap.json','w'))"
R=$(env -i PATH="/usr/bin:/bin" HOME="$WORK" OVERNIGHT_USAGE_FILE="$WORK/snap.json" OVERNIGHT_ESTIMATE=off bash "$CK" 2>/dev/null)
check "96% real, no args → PAUSE (default 95)" "PAUSE 96" "$R"

echo; echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
