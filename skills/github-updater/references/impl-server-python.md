# Self-hosted server apps — notify-first update tiers (Python/FastAPI reference)

This is the server path of the `github-updater` skill. Designed around a FastAPI +
uvicorn app that deploys two ways — Docker Compose (`docker compose up -d --build`)
or bare-metal venv + a systemd unit — but the tiers apply to **any** self-hosted
server, any language. The core spec's *check* half (§2 discovery, §3 compare, §9 UX)
applies verbatim; the *swap* half deliberately does not. **Servers do not self-swap.**

> Cross-references: cutting the release this check reads is **github-ship** (its Path C
> covers Docker images); testing the check without a public release is
> **updater-e2e-harness**. Version-bump mechanics for Python constants live in
> github-ship's `references/version-bump-table.md` (Python section).

## Contents

- [The server principle (read first)](#the-server-principle-read-first)
- [Tier 1 — notify in-app](#tier-1--notify-in-app)
- [Tier 2 — the operator-run update script](#tier-2--the-operator-run-update-script)
- [Tier 3 — unattended auto-update (Docker only, opt-in)](#tier-3--unattended-auto-update-docker-only-opt-in)
- [Security notes](#security-notes)
- [Testing](#testing)

---

## The server principle (read first)

A desktop app self-installs because it has exactly one user and that user clicked
"Update." A server has none of that: users are mid-request, an update can change the
DB schema and `.env` expectations under them, a restart is downtime, and a process
that can rewrite its own code from the network turns a compromised GitHub account
into remote code execution on your box. Therefore:

**A server must NOT silently replace itself. Notify in-app; a human applies.**

| Tier | What it is | House position |
|---|---|---|
| **1 — notify** | ETag-cached `releases/latest` check + a banner in the web UI | **Always build this.** It's ~80 lines and zero risk. |
| **2 — operator script** | `update.sh` in the repo; the banner points at it; the operator runs it | **The apply path.** Deterministic tag checkout + restart. |
| **3 — unattended** | watchtower-fork / WUD auto-pull (Docker only) | Opt-in. Trades control for convenience; wrong for stateful apps without tested migrations + backups. |

The desktop state machine's download→verify→stage→swap half is intentionally absent
here — Tier 2's script *is* the swap, and a human runs it.

---

## Tier 1 — notify in-app

### One canonical `APP_VERSION` first (the dual-constant hazard)

The check is `APP_VERSION` vs the `releases/latest` tag — so there must be exactly
**one** `APP_VERSION`. Unpackaged server apps (no `[project]` table, no package
metadata) carry the version as a hand-maintained constant, and those repos can grow
**two** constants that drift. Worked example: odysseus carries `APP_VERSION` in both
`core/constants.py` and `src/constants.py`, and the two have drifted before — a
"v1.0" release commit bumped one file while `/api/version` kept serving the old
number from the other. A drifted constant makes the updater lie in both directions:
it offers the update forever, or never.

**Consolidate before wiring the check:** one constants module owns `APP_VERSION`;
the version endpoint and the update check import from *that* module; any duplicate
module re-exports it (`from core.constants import APP_VERSION`) or dies. Bump
mechanics and the discovery grep are in github-ship's `references/version-bump-table.md`
(Python section, "dual-constant hazard").

```python
@app.get("/api/version")
async def get_version():
    from core.constants import APP_VERSION      # THE module — same one the check uses
    return {"version": APP_VERSION}
```

This endpoint doubles as the post-update verification probe (Tier 2 curls it).

### The check — httpx, ETag-cached, typed statuses

Core spec §2 applies unchanged: ETag caching is mandatory (a `304` is quota-free),
`404` = no published releases yet, `403`/`429` = rate-limited with a retry time,
transport failure = offline — never a generic error. `httpx` is the transport
(stdlib `urllib.request` works for zero-dependency apps; same headers, same logic).

```python
# core/update_check.py — the whole Tier-1 check.
import asyncio, json, os, re, time
import httpx

from core.constants import APP_VERSION, DATA_DIR    # the ONE constants module

OWNER, REPO = "<owner>", "<repo>"
STATE_FILE = os.path.join(DATA_DIR, "update_check.json")   # etag + cached result
CHECK_INTERVAL = 6 * 3600   # server cadence — see note below

def _overrides_allowed() -> bool:
    # The §10 harness contract, mapped to a server: dev mode OR the explicit
    # escape hatch. In production without the env var, overrides are dead.
    return os.getenv("DEBUG", "").lower() in ("1", "true") \
        or os.getenv("UPDATER_ALLOW_INSECURE_OVERRIDE") == "1"

def _api_base() -> str:
    if _overrides_allowed() and os.getenv("UPDATER_API_BASE"):
        return os.environ["UPDATER_API_BASE"].rstrip("/")
    return "https://api.github.com"                 # host-pinned otherwise

def _is_newer(tag: str, current: str) -> bool:
    # Core spec §3: strip 'v' case-insensitively, numeric segments, missing = 0.
    # Garbage tags -> False (log, don't compare as zeros). If you run a
    # prerelease channel, port the suffix-aware compare from updater-main.ts.
    def parse(v):
        m = re.match(r"^v?(\d+(?:\.\d+)*)$", v.strip().split("-")[0], re.I)
        return [int(x) for x in m.group(1).split(".")] if m else None
    a, b = parse(tag), parse(current)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    a += [0] * (n - len(a)); b += [0] * (n - len(b))
    return a > b

def _load():
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except Exception: return {}

def _save(state):
    with open(STATE_FILE, "w") as f: json.dump(state, f)

async def check_for_update(force: bool = False) -> dict:
    state = _load()
    if not force and time.time() - state.get("checked_at", 0) < CHECK_INTERVAL:
        return state.get("result", {"status": "up-to-date", "version": APP_VERSION})

    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": f"{REPO}/{APP_VERSION}"}
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]    # 304s don't touch the quota
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_api_base()}/repos/{OWNER}/{REPO}/releases/latest",
                                 headers=headers)
    except httpx.TransportError:
        return {"status": "offline"}                # transient — don't cache, don't log as error

    if r.status_code == 304:
        result = state["result"]                    # unchanged; reuse cached verdict
    elif r.status_code == 404:
        result = {"status": "no-releases"}          # zero PUBLISHED releases — normal, not an error
    elif r.status_code in (403, 429):
        result = {"status": "rate-limited",
                  "retry_at": int(r.headers.get("x-ratelimit-reset", 0))}
    elif r.status_code != 200:
        result = {"status": "error", "message": f"HTTP {r.status_code}"}
    else:
        rel = r.json()
        tag = rel.get("tag_name", "")
        if _is_newer(tag, APP_VERSION):
            result = {"status": "available", "version": tag,
                      "notes": rel.get("body") or "",
                      "url": rel.get("html_url", "")}
        else:
            result = {"status": "up-to-date", "version": APP_VERSION}
        state["etag"] = r.headers.get("etag")

    state.update(checked_at=time.time(), result=result)
    _save(state)
    return result
```

Facts the code encodes — don't remove them:

- **`404` = "no releases yet."** A repo that has never published a release returns
  404 from `releases/latest`; every server hits this from first boot until its first
  `ship and publish`. Return `no-releases` and stay silent — don't error-log it every
  cycle. (Drafts are invisible too — the banner appears only after github-ship's
  publish flip.)
- **The timestamp guard is the server cadence.** The core spec's launch + 12 h +
  wake cadence assumes a machine that sleeps; a server doesn't, so a **≥ 6 h guard on
  a persisted `checked_at`** replaces the wake hook and caps the check at a few
  requests a day. Never check per page load without the guard.
- **ETag persisted to the data dir** — steady-state checks cost zero quota (§2).
- **Rate-limited and offline are distinct typed statuses** the UI can render
  honestly (§2), not exceptions bubbling into the journal.

Two scheduling shapes, pick one:

```python
# (a) background task — check on startup, then every interval
@app.on_event("startup")
async def start_update_checker():
    async def loop():
        while True:
            await check_for_update()
            await asyncio.sleep(CHECK_INTERVAL)
    # keep a strong ref — asyncio tasks with no reference get GC'd mid-flight
    app.state.update_task = asyncio.create_task(loop())

# (b) on-request — the guard makes this equivalent, no task to manage
@app.get("/api/update-status")
async def update_status():
    return await check_for_update()
```

Expose `/api/update-status` in either shape (shape (a) serves the cached state
through it) — the web UI reads it.

### The banner

On page load the frontend fetches `/api/update-status`; on `available` it renders a
dismissible banner:

- "**vX.Y.Z available**" + a collapsed "What's new" rendering the release `notes`
  (already in the check response — zero extra requests, core spec §9). **Escape or
  sanitize it** — the release body is remote content going into your DOM.
- A link to `url` (the release page) and a "How to update" link pointing at the
  repo's `update.sh` / update docs — **the banner links to Tier 2; it never runs it**
  (see Security).
- Persist a `skipped_version` (server-side settings or localStorage) so a declined
  release stops nagging; clear it when a newer version appears (§9).

Silent when `up-to-date` / `no-releases`. Show `rate-limited` and `offline` only on
a manual "check now" action, if you build one.

---

## Tier 2 — the operator-run update script

The banner points here. The script lives **in the repo** (versioned, reviewed,
diffs like any other code) and an operator runs it in a shell.

**Tags, not branch pulls.** `git pull` on a branch deploys "whatever main is right
now" — unreproducible, and rollback means archaeology. `git checkout vX.Y.Z` deploys
exactly what was released, and rollback is `git checkout` of the previous tag. This
is also why github-ship tags server repos even when the release has no artifacts.

```bash
#!/usr/bin/env bash
# update.sh — operator-run. The update banner LINKS here; nothing calls this automatically.
set -euo pipefail
cd "$(dirname "$0")"

TARGET="${1:?usage: ./update.sh vX.Y.Z}"
[[ "$TARGET" =~ ^v[0-9]+(\.[0-9]+)*$ ]] || { echo "not a version tag: $TARGET"; exit 1; }
PREV="$(git describe --tags --abbrev=0 2>/dev/null || echo none)"

# Refuse to clobber a hand-patched server — a dirty tree loses edits on checkout.
[ -z "$(git status --porcelain --untracked-files=no)" ] \
  || { echo "working tree has tracked changes — stash or commit first"; exit 1; }

echo "[update $(date -Is)] $PREV -> $TARGET"

# DB backup BEFORE anything that might migrate — migrations rarely reverse cleanly.
mkdir -p backups
if [ -f data/app.db ]; then cp data/app.db "backups/app.db.$PREV.$(date +%s)"; fi

git fetch --tags --prune origin
git checkout "$TARGET"        # a TAG — deterministic, rollback-friendly

## Bare-metal path (venv + systemd unit, install-service.sh style):
./venv/bin/pip install -r requirements.txt
# ./venv/bin/alembic upgrade head        # if the app has migrations
sudo systemctl restart <service>         # e.g. odysseus-ui

## Docker Compose path — replaces the three lines above:
# docker compose pull                    # only if the compose file runs registry images
# docker compose up -d --build          # `build: .` compose files rebuild from the checkout

# Verify: the running process must report the target version.
sleep 3
curl -fsS http://localhost:<port>/api/version
echo "[update $(date -Is)] done: $PREV -> $TARGET"
```

Mechanics that matter:

- **Bare-metal restart** assumes an install-service.sh-style systemd unit — a unit
  file with `User=`, `WorkingDirectory=`, `ExecStart=` pointing at
  `venv/bin/uvicorn`, `Restart=always`, installed to `/etc/systemd/system/` +
  `daemon-reload` + `enable`. If the repo ships an installer script for the unit,
  the update script depends on it having been run once.
- **Compose path:** `docker compose pull && docker compose up -d --build`. A
  `build: .` service has no registry image to pull — the `--build` rebuilds from the
  freshly checked-out tag, which is the whole update. `pull` matters only once the
  compose file references pushed images (Tier 3's prerequisite).
- **`.env` and data survive** because they're untracked — `git checkout` of a tag
  never touches untracked files. Keep secrets and state (`.env`, `data/`, `logs/`)
  untracked; the dirty-tree guard covers tracked files only, on purpose.
- **Rollback = `git checkout v<previous>` + the same install/restart steps.** If the
  new version migrated the schema, also restore the pre-update DB backup — that's why
  the backup happens *before* the checkout, unconditionally.
- **Log to the service journal** (core spec §8: log like the app is already gone —
  here, like the *operator* is already gone). The `echo` lines land in the journal
  when run via `systemd-cat -t update ./update.sh vX.Y.Z`, and the restart itself is
  in `journalctl -u <service>`. When "the update broke it" reports arrive, the
  journal line pair (`from -> to`, `done`) plus the uvicorn startup log is the story.
- **Verify with `/api/version`** — the same endpoint Tier 1 consolidated. If it
  still prints the old number after a restart, you have the dual-constant drift
  from Tier 1, live in production.

---

## Tier 3 — unattended auto-update (Docker only, opt-in)

**Say this plainly to whoever asks for it: Tier 3 trades control for convenience.**
The container restarts whenever the image tag moves — with no operator judgment
about active users, migrations, or backups. It is **wrong for stateful apps without
tested migrations and automated backups**. Default to Tier 1 + 2; wire Tier 3 only
when the operator explicitly wants a set-and-forget box and accepts the trade.

**Prerequisite:** the compose file must run a **registry image with versioned tags**
(`ghcr.io/<owner>/<repo>:vX.Y.Z`, `:latest` moved on publish) — that's github-ship's
Docker path (Path C). A `build: .` compose service has nothing to auto-pull; Tier 3
is unreachable until the ship side pushes images.

Tooling (verified 2026-07):

| Tool | Mode | Use when |
|---|---|---|
| **watchtower** — image `nickfedor/watchtower`, GitHub `nicholas-fedor/watchtower` | Full auto | The default full-auto choice. **The original `containrrr/watchtower` is archived — never use or recommend it**; the fork is the maintained drop-in successor (note the two different spellings: `nicholas-fedor` on GitHub, `nickfedor` on Docker Hub). Caveat: it recreates containers *outside* compose's knowledge — `docker-compose.yml` drifts from what's running. |
| **WUD** (`getwud/wud`), `dockercompose` trigger | Full auto, compose-aware | When compose-file sync matters: WUD updates the compose file, pulls, and recreates — the file stays truthful. |
| **diun** (`crazy-max/diun`) | Notify-only | The lean notify option — ~20 notifiers including **ntfy** (if your stack already runs an ntfy sidecar, diun→ntfy is a natural pairing). A human then runs `docker compose pull && docker compose up -d`. |
| **WUD** without a trigger | Notify-only | Same as diun with a web UI. |

Deployment notes:

- **Scope watchtower with labels** (`WATCHTOWER_LABEL_ENABLE=true` + the enable
  label on your app's service only). A compose stack usually carries sidecars pinned
  to `:latest` (search engine, vector DB, ntfy); unscoped watchtower will bump those
  too, on its schedule, not yours.
- Point auto-pull at a **channel tag you move deliberately** (`:latest`, flipped by
  github-ship's publish step) — never at `:vX.Y.Z` (immutable, nothing to pull) and
  never at a branch-build tag.
- Keep Tier 1's banner even with Tier 3 running — it tells you what the box *should*
  be on when you're debugging what it *is* on.
- The notify-only rows are the honest middle ground: machine watches, human applies.
  That's Tier 1 with a different transport, and it's the right ceiling for most
  stateful apps.

---

## Security notes

- **The check is outbound HTTPS to `api.github.com` only.** Host-pin it — the
  hardcoded base URL *is* the pin; the `UPDATER_API_BASE` override exists solely
  behind the `overridesAllowed()` guard and is dead in production without the
  explicit env var. A notify-only server check downloads no artifacts, so the
  allowlist is one host.
- **No token.** Public repos need none for `releases/latest`, and ETag caching makes
  the 60/h unauthenticated quota irrelevant. A PAT sitting in a server's env is a
  liability that buys nothing here.
- **Never auto-execute anything from the response.** The release `body` is displayed
  (escaped/sanitized — it's remote content in your web UI), never interpreted. The
  tag name reaches `git checkout` only after an operator typed it as an argument,
  and the script still pattern-validates it.
- **The update script is in-repo, versioned, and reviewed. The banner only LINKS to
  it.** Do not build an authenticated "Update now" button that shells out to
  `update.sh` — a web-reachable endpoint that runs `git checkout` + restart is an
  RCE primitive exactly one auth bug away from public. The desktop pattern
  (app applies its own update) is precisely what the server principle forbids.

## Testing

- **Check path — use the `updater-e2e-harness` mock.** The check code honors
  `UPDATER_API_BASE` behind the same §10 predicate as every other implementation
  (`DEBUG` dev-run OR `UPDATER_ALLOW_INSECURE_OVERRIDE=1`). Run the app with
  `DEBUG=1 UPDATER_API_BASE=http://127.0.0.1:<mock-port>` and walk the check
  states end to end: `available` (mock serves a newer tag + body → banner appears,
  notes render, skip persists), `up-to-date`, `no-releases` (mock 404s via
  `--status 404`), plus the ETag round trip (second check sends `If-None-Match`,
  mock 304s, cached verdict reused). No real releases were harmed.
- **Apply path — NOT the harness.** Tier 2 is git + pip/compose + systemd against a
  real deployment; test it as a staging exercise: a throwaway compose project or VM,
  install from the previous tag, run `update.sh` to the new tag, verify
  `/api/version` reports the target and the data survived — then run the **rollback
  drill** (checkout previous tag, restore the DB backup, restart, verify) once
  *before* you ever need it in anger.
- **Tier 3:** verify scoping by watching one full cycle — push a new image tag to a
  test registry/repo, confirm only the labeled service recreates and the sidecars
  stay put.
