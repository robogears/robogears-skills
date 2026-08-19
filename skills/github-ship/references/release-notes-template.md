# RELEASE_NOTES.md — the exact structure

CI feeds this file **verbatim** as the GitHub Release body (`body_path`). The headings,
ordering, and changelog link are a contract: they're what users scan to decide whether to
update, and (for empty-body recovery) what `gh release edit --notes-file` re-applies.

> **This file is THE single source of truth for the release body.** Three consumers read
> it, so there is exactly one place to get it right: (1) CI's `body_path` publishes it as
> the GitHub Release body; (2) the empty-body recovery (`gh release edit --notes-file`)
> re-applies it; (3) the **`github-updater`** skill's "Install / update" copy comes from
> here — that skill no longer keeps a duplicate template of its own, it defers to this one.
> Don't fork this content into other docs; edit it here.

**Rewrite the whole file each release** — do not append. Old versions stay on the Releases
page; repeating them just bloats every body.

## Raw material — draft from the real commit range, not memory

```bash
prev=$(git describe --tags --abbrev=0 2>/dev/null)
git log "${prev:?no previous tag — first release, see below}"..HEAD --oneline
```

Optional extra raw material — GitHub's server-side draft (PR titles, or plain commit
lines on PR-less repos, plus its auto compare link). It's a *drafting aid*; the bullets
are still hand-written user-facing prose, never pasted verbatim:

```bash
gh api "repos/{owner}/{repo}/releases/generate-notes" -f tag_name="vX.Y.Z" \
  -f target_commitish="$(git rev-parse --abbrev-ref HEAD)" --jq .body
```

**First release ever** (no previous tag — the guarded command above aborts, which is the
point; the unguarded composition silently prints *nothing*):

- Raw material is the **full history**: `git log --oneline`.
- There is **no compare link**. Use the commit list instead:
  `**Full Changelog**: https://github.com/<owner>/<repo>/commits/vX.Y.Z`
- Drop the "Already on a self-updating version?" line — nobody has a prior version.

## Template

```md
# What's new in vX.Y.Z

## <Short heading for a feature group>
- Tight bullet: what changed and why it matters. No paragraphs.
- Another bullet if needed.

## <Another distinct area, if any>
- ...

## Under the hood
- Non-user-facing changes worth noting (perf, deps, security), if any.

---

# Install / update

<Pick the stanza for the project type — see "Install stanzas by project type" below.>

<Optional one line on where config/data lives.>

## Requirements

- <OS / arch / account / API-key / subscription requirements>
- <Any hard limits — rate caps, etc.>

---

**Full Changelog**: https://github.com/<owner>/<repo>/compare/vX.Y.(Z-1)...vX.Y.Z
```

Rules:
- Keep bullets short — verbs first, no walls of text. Users skim this.
- The `## Under the hood` block is optional; include it when there are meaningful
  non-visible changes (dependency bumps, security fixes, groundwork) so the changelog
  isn't silent about real work.
- The **Full Changelog** compare link is not optional — it's the one-click diff between
  the previous tag and this one. Fill in the real previous version. (First release:
  the `/commits/vX.Y.Z` form above instead — there is nothing to compare against.)
- Use the **exact artifact filename** in the Install section (e.g.
  `MyApp-1.2.3-arm64.dmg`). It must match what CI actually uploads — the in-app
  updaters match assets **by filename**.

## Install stanzas by project type

Short, copy-paste, one per project type. Pick exactly one (or combine for multi-platform
desktop apps); fill in the real names and versions.

### Desktop app (the house default — dmg / setup.exe, in-app updater)

```md
# Install / update

- **Already on a self-updating version?** Click the in-app **Update** button — it
  downloads and self-installs. No download needed. *(Only if the app has an updater.)*
- **Fresh install (macOS, Apple Silicon):** download `MyApp-X.Y.Z-arm64.dmg`, open it,
  and drag MyApp to Applications. First launch on macOS 15+: open it once, then
  **System Settings → Privacy & Security → "Open Anyway"** — once only (the app isn't
  notarized). Right-click→Open no longer bypasses Gatekeeper on Sequoia/Tahoe
  (verified 2026-07).
- **Fresh install (Windows):** download `MyApp-X.Y.Z-setup.exe` and run it. SmartScreen
  may warn on an unsigned installer — **More info → Run anyway**.
```

### Library (registry package)

```md
# Install / update

- npm: `npm install <pkg>@X.Y.Z`
- pip: `pip install <pkg>==X.Y.Z`
- cargo: `cargo add <pkg>@X.Y.Z`
```

Keep only the registry the project actually publishes to — and remember the GitHub
Release alone installs nothing; the registry publish is `ship and publish`'s
distribution step.

### CLI binary

```md
# Install / update

- Download `<tool>-X.Y.Z-<os>-<arch>` below, then:
  `chmod +x <tool>-* && mv <tool>-* /usr/local/bin/<tool>`
- Homebrew (if the project has a tap): `brew upgrade <tool>`
```

The brew line only after the tap formula is actually bumped (url + sha256) — that's
part of publish, not ship.

### Docker image

```md
# Install / update

- `docker pull ghcr.io/<owner>/<repo>:vX.Y.Z`
- Compose: bump the image tag in `docker-compose.yml`, then
  `docker compose pull && docker compose up -d`
```

Pin the version tag in the notes; `:latest` moves only on publish (that's the
visibility flip for images).

### Self-hosted server (source deploy)

```md
# Install / update

- `git fetch --tags && git checkout vX.Y.Z`, then run the update script:
  `./scripts/update.sh` (deps + migrations + service restart)
```

Name the repo's real update script; if there isn't one, spell out the two or three
commands it would contain. Servers never silently self-update — the operator runs this.

### Browser extension

```md
# Install / update

- Store: <store listing URL> — updates roll out automatically after review.
- Manual: download `<ext>-X.Y.Z.zip` below, unzip, then
  `chrome://extensions` → Developer mode → **Load unpacked**.
```

## Worked example 1 (macOS Electron app, arm64 dmg, with an in-app updater)

```md
# What's new in v0.1.6

## Now Playing on macOS
- Folderify now plugs into the macOS **Now Playing** widget (Control Center / menu bar),
  your **keyboard's media keys**, and AirPods — real title, artist, album, and album art,
  a live scrubber, and working play / pause / next / previous.

## Shuffle & repeat stick
- Your **shuffle and repeat** choices now persist across launches instead of resetting.

## Under the hood
- Trimmed the shipped build by moving dev-only tooling out of the app bundle.

---

# Install / update

- **Already on v0.1.2 or later?** Just click the in-app **Update** button (top bar or
  Settings → Updates) — it downloads and self-installs. No DMG needed.
- **Fresh install (macOS, Apple Silicon):** download `Folderify-0.1.6-arm64.dmg`, open it,
  and drag Folderify to Applications. First launch on macOS 15+: open it once, then go to
  **System Settings → Privacy & Security → Open Anyway** — once only (it isn't notarized;
  right-click→Open no longer works).

Your settings, cache, and thumbnails live in `~/Library/Application Support/Folderify/`.

## Requirements

- macOS 11 (Big Sur) or later, on an Apple Silicon Mac.
- No account, API key, or subscription.

---

**Full Changelog**: https://github.com/robogears/Folderify/compare/v0.1.5...v0.1.6
```

## Worked example 2 (native Swift macOS app, no CI, TWO assets)

VibeLight ships **two assets per release** and the notes must name **both**: the `.dmg`
is what a human opens for a fresh install; the `-arm64.zip` is what the in-app updater
downloads and swaps in place (the asset-name contract from `github-updater`). Drop or
rename either and you break either every fresh install or every existing user's Update
button. Both get `.sha256` sidecars. VibeLight releases via ship's Path B (local build +
`gh release create --draft --verify-tag`) — that changes nothing about the notes.

```md
# What's new in v1.4.0

## Scenes
- **Scenes** save a full lighting setup — colors, brightness, transition speed — and
  restore it in one click from the menu bar.

## Fixes
- Screen-sync no longer drops to black when the display sleeps and wakes.
- The brightness slider tracks smoothly instead of stepping.

## Under the hood
- Update checks now use ETag caching — zero API quota cost when nothing changed.

---

# Install / update

- **Already on v1.1 or later?** Click **Check for Updates** in the menu bar — the app
  downloads `VibeLight-1.4.0-arm64.zip`, verifies it, and swaps itself in place, then
  relaunches.
- **Fresh install (macOS, Apple Silicon):** download `VibeLight-1.4.0-arm64.dmg`, open
  it, and drag VibeLight to Applications. First launch on macOS 15+: open it once, then
  **System Settings → Privacy & Security → Open Anyway** (it isn't notarized;
  right-click→Open no longer works).

## Requirements

- macOS 13 (Ventura) or later, on an Apple Silicon Mac.

---

**Full Changelog**: https://github.com/robogears/VibeLight/compare/v1.3.2...v1.4.0
```
