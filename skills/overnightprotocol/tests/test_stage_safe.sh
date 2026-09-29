#!/usr/bin/env bash
# Tests for scripts/stage-safe.sh — secret-shaped / huge NEW files are never staged,
# ordinary source files always are (even when their names contain "token" or "secret").
# Run:  bash tests/test_stage_safe.sh      (throwaway git repos only)
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SS="$HERE/../scripts/stage-safe.sh"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

echo "stage-safe.sh"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
R="$T/repo"; mkdir -p "$R"; cd "$R" || exit 1
git init -q; git config user.email t@t; git config user.name t; git config commit.gpgsign false
git config core.excludesFile /dev/null   # ignore the machine's global gitignore: test the script alone
echo v1 > tracked.txt; echo keep > .gitignore; git add -A; git commit -qm init
echo v2 > tracked.txt
mkdir -p src config dir
echo 'x' > src/app.py; echo 'x' > src/tokenizer.py; echo 'x' > src/SecretView.swift
echo 'x' > notes.md; echo 'x' > .env.example; echo 'x' > id_rsa.pub
echo 'K=1' > .env; echo 'K=1' > .env.local; echo '{}' > config/secrets.json; echo 't' > api_token.txt
echo 'k' > id_rsa; echo 'k' > server.pem; echo 'x' > data.sqlite; echo 'x' > dir/.npmrc
echo 'x' > infra.tfstate.backup; echo 'x' > prod.env
dd if=/dev/zero of=big.bin bs=1048576 count=11 2>/dev/null
echo 'x' > "name with spaces.txt"
mkdir -p secrets credentials k8s/secret src/secrets migrations .docker .claude
echo 'x' > secrets/prod.yaml; echo '{}' > credentials/gcp.json; echo 'x' > k8s/secret/db.yaml
echo 'x' > src/secrets/manager.py; echo 'x' > migrations/001_init.sql
echo 'x' > .envrc; echo 'x' > .pgpass; echo 'x' > dump.sql; echo 'x' > backup.sql.gz
echo '{}' > .docker/config.json; echo '{}' > firebase-adminsdk.json
printf '{"type": "service_account", "private_key": "-----BEGIN PRIVATE KEY-----\\nMIIE\\n-----END PRIVATE KEY-----\\n"}' > myproj-1a2b3c4d5e6f.json; echo '{}' > manifest-3f2a9c8d1e4b.json
echo x > AuthKey_ABC123.p8; echo x > deploy.ppk; echo x > backup.gpg; echo x > private.asc; echo '{}' > terraform.tfvars.json
echo x > secrets/id_rsa.bak; echo x > secrets/server.crt; echo x > credentials/backup.tar.gz
mkdir -p k8s/.docker; echo 'FROM x' > k8s/.docker/Dockerfile
echo 'K=9' > .env.staged; git add .env.staged
printf -- '-----BEGIN RSA PRIVATE KEY-----\nabc\n' > server.key.bak; mkdir -p certs docs schema bin
printf -- '-----BEGIN CERTIFICATE-----\nabc\n' > certs/ca-bundle.txt; echo 'see -----BEGIN header' > docs/SETUP.txt
echo '{"properties":{"private_key":{"type":"string"}}}' > schema/account.schema.json; printf '#!/bin/sh\necho BEGIN\n' > bin/gen-cert
echo 'export API_KEY=x' > secrets/export.sh; echo 'insert' > secrets/users.sql
printf -- '# setup\n-----BEGIN RSA PRIVATE KEY-----\nabc\n' > pasted-key.md; printf -- '-----BEGIN RSA PRIVATE KEY-----\n' > src/fixture_key.py
echo '{}' > .claude/settings.local.json
printf 'x' > "$(printf 'tab\there.txt')"

out=$(bash "$SS" --dry-run "$R"); rc=$?
check "dry run exits 0 and stages nothing new" '[ $rc -eq 0 ] && [ "$(git diff --cached --name-only)" = ".env.staged" ]'
check "dry run reports its summary" 'printf "%s" "$out" | grep -q "dry run: nothing staged"'
check "dry run lists changed TRACKED files too (they get committed and pushed)" 'printf "%s" "$out" | grep -qx "TRACKED tracked.txt"'
check "dry run also filters files that were ALREADY staged" 'printf "%s" "$out" | grep -qF "SKIP .env.staged ("'
check "dry run leaves the pre-staged file staged (changes nothing)" 'git diff --cached --name-only | grep -qx ".env.staged"'

cd "$T" && out=$(bash "$SS" "$R"); rc=$?; cd "$R"
staged=$(git diff --cached --name-only | sort | tr '\n' ' ')
check "real run exits 0 (from another folder)" '[ $rc -eq 0 ]'
for f in tracked.txt src/app.py src/tokenizer.py src/SecretView.swift notes.md .env.example id_rsa.pub "name with spaces.txt" src/secrets/manager.py migrations/001_init.sql manifest-3f2a9c8d1e4b.json k8s/.docker/Dockerfile certs/ca-bundle.txt docs/SETUP.txt schema/account.schema.json bin/gen-cert src/fixture_key.py; do
  check "staged: $f" 'git diff --cached --name-only | grep -qxF "$f"'
done
for f in .env .env.local config/secrets.json api_token.txt id_rsa server.pem data.sqlite dir/.npmrc infra.tfstate.backup prod.env big.bin \
         secrets/prod.yaml credentials/gcp.json k8s/secret/db.yaml .envrc .pgpass dump.sql backup.sql.gz .docker/config.json \
         firebase-adminsdk.json myproj-1a2b3c4d5e6f.json .claude/settings.local.json AuthKey_ABC123.p8 deploy.ppk backup.gpg \
         private.asc terraform.tfvars.json secrets/id_rsa.bak secrets/server.crt credentials/backup.tar.gz .env.staged \
         server.key.bak secrets/export.sh secrets/users.sql pasted-key.md; do
  check "skipped: $f" '! git diff --cached --name-only | grep -qxF "$f" && printf "%s" "$out" | grep -qF "SKIP $f ("'
done
check "big file skipped for its size" 'printf "%s" "$out" | grep -q "SKIP big.bin (larger than 10 MB)"'
check "a pre-staged secret is reported once, not twice" '[ "$(printf "%s" "$out" | grep -c "^SKIP .env.staged")" = "1" ]'
check "a file name containing a tab is staged safely" '[ -n "$(git diff --cached --name-only -z | tr "\0" "\n" | grep -F "here.txt")" ]'
git commit -qm "t" >/dev/null 2>&1; echo 'K=2' > .env.example; git add .env.example >/dev/null; git commit -qm t2; echo 'K=3' > .env.example
out2=$(bash "$SS" --dry-run "$R")
check "no false WARN on a tracked safe file" '! printf "%s" "$out2" | grep -q "^WARN .env.example"'

# a TRACKED .claude/settings.local.json is still never staged
R2="$T/repo2"; mkdir -p "$R2/.claude"; cd "$R2"; git init -q; git config user.email t@t; git config user.name t; git config core.excludesFile /dev/null
echo '{}' > .claude/settings.local.json; echo a > a.txt; git add -A; git commit -qm i
echo '{"permissions":{"deny":["Bash(x)"]}}' > .claude/settings.local.json; echo b > a.txt
out3=$(bash "$SS" "$R2")
check "a tracked .claude/settings.local.json is not staged (other tracked changes are)" \
  '! git diff --cached --name-only | grep -q settings.local.json && git diff --cached --name-only | grep -qx a.txt && printf "%s" "$out3" | grep -q "SKIP .claude/settings.local.json"'
out4=$(cd / && bash "$SS" --dry-run "~/$(python3 -c "import os,sys;print(os.path.relpath(sys.argv[1], os.path.expanduser(\"~\")))" "$R2")" 2>&1); rc=$?
check "a ~/ project path is expanded" '[ $rc -eq 0 ]'

cd "$T"; bash "$SS" "$T" >/dev/null 2>&1; rc=$?
check "not a git repo → exit 2" '[ $rc -eq 2 ]'

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
