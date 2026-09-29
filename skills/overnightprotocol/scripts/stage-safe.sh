#!/usr/bin/env bash
# overnightprotocol: stage the loop's work for a commit WITHOUT secret-shaped or huge new files.
#
#   stage-safe.sh [--dry-run] [project_dir]
#
# - Tracked files: every change is staged (they're already the project's) and listed as
#   "TRACKED <path>" so the kickoff preview shows everything that will be committed and
#   pushed. A tracked file that looks secret-shaped is still staged, but flagged "WARN".
#   Claude Code's .claude/settings.local.json is never staged, even when git tracks it.
# - New (untracked, not ignored) files: staged unless they look like a secret or a data
#   dump, are Claude Code's local settings, or are bigger than OVERNIGHT_MAX_FILE_MB
#   (default 10). Skipped files stay untracked and are listed so the agent can record them
#   under "Excluded from commits" in the report.
#
# Secret-shaped (case-insensitive), checked on the WHOLE path, folders included:
#   - anything inside .ssh .gnupg .aws .kube .azure .gcloud, and .docker/config.json; inside
#     a folder named secret(s) or credential(s), everything except source code
#   - exact names: .env .envrc .npmrc .pypirc .netrc .pgpass .git-credentials id_rsa
#     id_dsa id_ecdsa id_ed25519 .claude/settings.local.json
#   - .env.* (except .example/.sample/.template/.dist) and *.env
#   - extensions: pem key p8 p12 pfx ppk gpg pgp asc jks keystore kdbx tfstate tfvars
#     sqlite sqlite3 db dump (and *.tfstate.* backups, *.tfvars.json); SQL dumps
#     (*.sql.gz, *dump*.sql, *backup*.sql)
#   - a secret word (secret credential token passw(or)d api-key service-account
#     private-key adminsdk) in a CONFIG/DATA file name (json yaml yml txt env ini cfg conf
#     toml properties xml plist, or no extension). Source files like tokenizer.py or
#     SecretView.swift are NOT skipped.
#   - any small non-source file that contains an actual private key (a "-----BEGIN … PRIVATE
#     KEY-----" or PGP private block, or a JSON "private_key": "-----BEGIN…" value)
#   - files that were already staged before this ran get the same checks
#
# Output, one line per file: "STAGE <path>", "TRACKED <path>", "SKIP <path> (<reason>)",
# "WARN <path> (…)", "FAIL <path> (git add failed)", then a summary line. --dry-run stages
# nothing. Exit 0 ok · 1 a git add failed · 2 usage / not a git repository.

set -u
DRY=""
PROJ="$PWD"
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    -*) echo "usage: stage-safe.sh [--dry-run] [project_dir]" >&2; exit 2 ;;
    *) PROJ="$a" ;;
  esac
done
case "$PROJ" in "~") PROJ="$HOME" ;; "~/"*) PROJ="$HOME/${PROJ#\~/}" ;; esac
cd "$PROJ" 2>/dev/null || { echo "not a directory: $PROJ" >&2; exit 2; }
git rev-parse --git-dir >/dev/null 2>&1 || { echo "not a git repository: $PROJ" >&2; exit 2; }

exec python3 - "${DRY:-}" "${OVERNIGHT_MAX_FILE_MB:-10}" <<'PY'
import os, re, subprocess, sys

dry = sys.argv[1] == "1"
try:
    max_bytes = int(sys.argv[2]) * 1024 * 1024
except ValueError:
    max_bytes = 10 * 1024 * 1024

CRED_DIRS = {".ssh", ".gnupg", ".aws", ".kube", ".azure", ".gcloud"}   # skip anything inside
SECRET_DIRS = {"secret", "secrets", "credential", "credentials"}         # inside: stage source code only
CODE_EXT = {"py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "swift", "m", "mm", "h", "hpp", "c", "cc",
            "cpp", "cs", "go", "rs", "java", "kt", "kts", "rb", "php", "scala", "dart", "lua",
            "vue", "svelte"}
DOC_EXT = {"md", "rst", "html", "css", "scss"}
SOURCE_EXT = CODE_EXT | DOC_EXT   # what may be staged from inside a secrets folder
EXACT = {".env", ".envrc", ".npmrc", ".pypirc", ".netrc", ".pgpass", ".git-credentials",
         "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
ENV_OK = {".env.example", ".env.sample", ".env.template", ".env.dist"}
EXT = {"pem", "key", "p8", "p12", "pfx", "ppk", "gpg", "pgp", "asc", "jks", "keystore", "kdbx",
       "tfstate", "tfvars", "sqlite", "sqlite3", "db", "dump"}
DATA_EXT = {"", "json", "yaml", "yml", "txt", "env", "ini", "cfg", "conf", "toml",
            "properties", "xml", "plist"}
WORD = re.compile(r"secret|credential|token|passw(or)?d|api[-_]?key|service[-_]?account|private[-_]?key|adminsdk")
KEY_RE = re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----|-----BEGIN PGP PRIVATE KEY BLOCK-----|"private_key"\s*:\s*"-----BEGIN')

def secret_reason(path):
    low = path.lower()
    parts = low.split("/")
    base = parts[-1]
    root, ext = os.path.splitext(base)
    ext = ext[1:]
    if any(p in CRED_DIRS for p in parts[:-1]):
        return "inside a credentials folder"
    if len(parts) > 1 and parts[-2] == ".docker" and base == "config.json":
        return "Docker credentials"
    if any(p in SECRET_DIRS for p in parts[:-1]) and ext not in SOURCE_EXT:
        return "non-source file inside a secrets folder"
    if low.endswith(".claude/settings.local.json") or low == ".claude/settings.local.json":
        return "Claude Code local settings"
    if base in EXACT:
        return "secret-shaped name"
    if base.startswith(".env.") and base not in ENV_OK:
        return "secret-shaped name"
    if ext == "env":
        return "secret-shaped name"
    if ext in EXT or ".tfstate." in base or base.endswith(".tfvars.json"):
        return "secret/data file type"
    if base.endswith(".sql.gz") or (ext == "sql" and re.search(r"dump|backup", root)):
        return "database dump"
    if ext in DATA_EXT and WORD.search(root if ext else base):
        return "secret word in a config/data file"
    if ext not in CODE_EXT:   # code may hold fake test keys; everything else (docs too) is checked
        try:
            if os.path.getsize(path) <= 1048576:
                with open(path, "rb") as f:
                    if KEY_RE.search(f.read(1048576)):
                        return "contains a private key"
        except OSError:
            pass
    return None

def show(p):
    return p if p.isprintable() else repr(p)

def git_lines(*args):
    out = subprocess.run(["git", *args], capture_output=True).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]

failed = 0
LOCAL = (".claude/settings.local.json", ":(exclude,glob)**/.claude/settings.local.json")
def is_local_settings(p):
    return p.lower() == ".claude/settings.local.json" or p.lower().endswith("/.claude/settings.local.json")

# 1) anything already staged before this ran: new files get the same filter as untracked ones
pre_new = git_lines("diff", "--cached", "-z", "--name-only", "--diff-filter=A")
pre_other = [p for p in git_lines("diff", "--cached", "-z", "--name-only") if p not in pre_new]

# 2) changes to tracked files (never Claude Code's local settings, even when tracked)
tracked = git_lines("diff", "-z", "--name-only") + git_lines("ls-files", "-z", "--deleted") + pre_other
tracked = list(dict.fromkeys(tracked))
if not dry:
    r = subprocess.run(["git", "add", "-u", "--", ".", ":(exclude).claude/settings.local.json", ":(exclude,glob)**/.claude/settings.local.json"], capture_output=True)
    if r.returncode != 0:
        print("FAIL tracked changes (git add -u failed)"); failed += 1
for p in tracked:
    if is_local_settings(p):
        if not dry:
            subprocess.run(["git", "reset", "-q", "--", p], capture_output=True)
        print(f"SKIP {show(p)} (Claude Code local settings — not committed even though git tracks it)")
        continue
    print(f"TRACKED {show(p)}")
    why = secret_reason(p)
    if why:
        print(f"WARN {show(p)} ({why} — already tracked by the repo, so it is committed anyway)")

staged = skipped = 0
reported = set()
for p in pre_new:
    reported.add(p)
    why = secret_reason(p)
    if why:
        if not dry:
            subprocess.run(["git", "rm", "-q", "--cached", "--", p], capture_output=True)
        note = "it was already staged; would be unstaged, file kept" if dry else "it was already staged; unstaged, file kept"
        print(f"SKIP {show(p)} ({why} — {note})"); skipped += 1
    else:
        print(f"STAGE {show(p)}"); staged += 1
for p in git_lines("ls-files", "-z", "-o", "--exclude-standard"):
    if p in reported:
        continue
    why = secret_reason(p)
    if not why:
        try:
            if os.path.getsize(p) > max_bytes:
                why = f"larger than {max_bytes // 1048576} MB"
        except OSError:
            pass
    if why:
        print(f"SKIP {show(p)} ({why})"); skipped += 1
        continue
    if not dry:
        r = subprocess.run(["git", "add", "--", p], capture_output=True)
        if r.returncode != 0:
            print(f"FAIL {show(p)} (git add failed)"); failed += 1
            continue
    print(f"STAGE {show(p)}"); staged += 1

print(f"TRACKED {len(tracked)} changed · STAGED {staged} new file(s) · SKIPPED {skipped}"
      + (" · dry run: nothing staged" if dry else ""))
sys.exit(1 if failed else 0)
PY
