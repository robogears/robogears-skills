---
name: github-updater
version: 0.1.1
description: >-
  github-updater (v0.1.1) — Battle-tested recipes for adding a self-updater to ANY app distributed through GitHub
  Releases — Electron (macOS .dmg self-install + Windows/NSIS), Swift/SwiftUI macOS,
  C#/.NET Windows (Inno/Velopack), Linux AppImage, self-hosted Python/server apps, and
  single-binary CLIs. One platform-agnostic core spec (ETag-cached release discovery,
  version compare, asset-name contract, SHA-256 gate,
  download→verify→stage→swap→relaunch with rollback) plus per-stack implementations. Use
  when building, wiring, or debugging an in-app "Check for updates / Update now /
  Restart to apply" feature — especially ad-hoc-signed, un-notarized apps
  self-installing without the App Store or a Developer ID. Trigger on auto-update,
  "update from GitHub releases", in-app updater, dmg self-install, NSIS silent
  reinstall, AppImage self-update, App Translocation, or replicating the
  Folderify/VibeLight updaters. NOT for iOS/Android — stores own mobile updates. To TEST
  an updater see updater-e2e-harness; to cut the release see github-ship.
---

# GitHub Releases self-updaters — core spec + per-stack recipes

This skill has two halves. The **core spec** below is stack-agnostic: every updater,
in any language, follows the same protocol, contracts, and state machine — they were
extracted from five working production implementations (Electron, Swift, C#, C++,
and this skill's own reference code). The **per-stack references** then give you the
verbatim last mile for your platform. Read the core spec first, always; then read
exactly one implementation file.

## Router — which reference to read

| Your app | Read |
|---|---|
| Electron, macOS and/or Windows | `references/updater-main.ts` (heart of the feature) + `references/renderer-and-ipc.md` + `references/packaging-and-ci.md`; Windows deltas in `references/updater-windows.md` |
| Native Swift / SwiftUI macOS app | `references/impl-swift-macos.md` (zip-swap recipe from VibeLight; Sparkle 2 trade-off included) |
| C# / .NET Windows app, or any installer-based native Windows app | `references/impl-windows-native.md` (Inno Setup silent reinstall from VirtualMirage; Velopack for new apps; rename-aside for installer-less exes) |
| Linux desktop app (AppImage) or C++ app | `references/impl-linux-appimage.md` (AppImage swap + re-exec from VibeDolphin) |
| Self-hosted server app (Python/Node/etc.), Docker or bare-metal | `references/impl-server-python.md` (notify-first tiers; the app must not silently mutate a server) |
| Single-binary CLI tool | the CLI sections of `impl-linux-appimage.md` (POSIX atomic rename) and `impl-windows-native.md` (rename-aside trick) |
| iOS / Android / mobile | **Stop.** Mobile apps cannot self-update — the App Store / Play Store owns updates, and sandboxed mobile apps can't overwrite their own bundle. Ship a hard-coded no-op updater stub on mobile targets. |

---

# The core spec (every implementation follows this)

## 1. Trust model (read first — it shapes everything)

Two tiers, decided by whether you have a paid signing identity:

- **Ad-hoc signed / unsigned (no Developer ID, no Authenticode cert)** — the default
  for this portfolio. There is no code signature to verify against a trusted
  identity; **TLS to GitHub is the integrity floor**. You trust that the bytes came
  from your release, over HTTPS, from `api.github.com` / `github.com` /
  `objects.githubusercontent.com`, and nothing else. Host-pin every request and
  re-validate on every redirect hop.
- **Properly signed (Developer ID + notarization, or Authenticode)** — additionally
  verify the downloaded artifact's signature before staging (`codesign --verify
  --deep --strict` + `spctl -a` and Team ID on macOS; Authenticode on Windows), and
  consider a framework updater instead (see §11).

On macOS, ad-hoc apps must **re-sign after install** (`codesign --force --deep
--sign -`) because moving a bundle and stripping quarantine can invalidate the
ad-hoc signature — an unsigned bundle is rejected by Apple Silicon Gatekeeper as
"damaged." A fresh install still needs one Gatekeeper "Open Anyway" (on Sequoia and
Tahoe that's **System Settings → Privacy & Security → Open Anyway** — the old
right-click→Open bypass no longer works), but an in-place self-update relaunched via
`open` inherits trust and launches cleanly.

## 2. Release discovery — the check protocol

Poll `GET https://api.github.com/repos/<owner>/<repo>/releases/latest`.

Facts every implementation must respect:

- **Drafts and prereleases are invisible** to `releases/latest`. CI creates a
  *draft*; users see the update only after `github-ship`'s publish step flips it
  public. Draft **assets** are genuinely undownloadable too (no working
  `browser_download_url`, even with a PAT) — so a draft is a safe unlisted staging
  area, and updater tests must use the `updater-e2e-harness` mock, never a draft.
- **404 means "no published releases yet"** (brand-new repo) — return an
  `up-to-date`/`no-releases` result, not an error. Every user of a fresh app hits
  this until the first publish. (A repo gone private also 404s; unauthenticated
  checks only work on public repos — keep the repo public.)
- **The unauthenticated quota is 60 requests/hour per IP, shared** across every
  app polling GitHub from that machine/NAT — several of this portfolio's apps
  coexist on one Mac. Therefore:
  - **ETag caching is mandatory, not optional.** Persist the last response's `ETag`
    and send `If-None-Match` on every check; a `304 Not Modified` reply **does not
    count against the quota** and means "reuse your cached result." This makes the
    steady-state cost of update checks zero.
  - On `403`/`429`, parse `Retry-After` / `X-RateLimit-Reset` and surface a
    `rate-limited` status with the retry time — never a generic "couldn't reach
    GitHub."
  - Cadence: check on launch, every 12h while running, and on wake-from-sleep
    (a laptop that sleeps nightly never hits a naive 12h timer). Never poll by the
    minute.
- **Zero-API-cost alternative (optional pattern):** the permanent URL
  `https://github.com/<owner>/<repo>/releases/latest/download/<asset>` serves the
  named asset of the latest published release without touching `api.github.com`.
  Publishing a tiny `latest.json` manifest asset with every release (version, notes,
  per-platform URLs — Tauri's pattern) and fetching it via that URL bypasses the API
  quota entirely. Adopt it only if you also change the ship side to emit the
  manifest; the ETag'd API check is the default here.
- **Keep the release `body`.** The JSON you already fetched contains the release
  notes — parse them out and show a "What's new" in the update UI instead of asking
  users to update blind (§9).

## 3. Version comparison

Strip a leading `v` **case-insensitively**, split on `.`, compare numeric segments
left-to-right (missing segments = 0). Rules learned the hard way:

- Never string-compare (`"10" < "9"`).
- A prerelease suffix ranks **below** its own release: split off `-rc.1`/`-beta.2`
  at the first `-`; at equal numerics, the un-suffixed version wins. (A naive
  `parseInt('3-beta')` → 3 silently offers a prerelease as an upgrade over its own
  final release.)
- A tag that doesn't match `^v?\d+(\.\d+)*` is garbage — log and ignore it, don't
  compare it as zeros.
- Don't pull in a semver library for `vMAJOR.MINOR.PATCH` tags; the ~20-line compare
  in `references/updater-main.ts` (`isNewerVersion`) is the reference. If you use
  full semver prerelease/build semantics, use a real semver compare.

## 4. The asset-name contract (the one contract you must not break)

The updater finds its download by **file name**. The packager's artifact name and
the updater's matcher are one contract split across two files — **change one, change
the other in the same commit.**

| Platform | Artifact pattern | Matcher |
|---|---|---|
| macOS Electron / Swift (installer) | `App-1.2.3-arm64.dmg` | name contains `-${arch}.dmg` |
| macOS Swift (in-place update) | `App-1.2.3-arm64.zip` | prefer `-${arch}.zip` |
| Windows NSIS (versioned) | `App-1.2.3-setup.exe` | name ends with `-setup.exe` |
| Windows NSIS / Inno (fixed) | `app-setup.exe` (literal) | exact literal |
| Linux | `App.AppImage` or `App-x86_64.AppImage` | ends with `.AppImage` (arch token if multi-arch) |
| Integrity sidecar (all) | `<asset>.sha256` | exact `<asset name>.sha256` |

**The no-wrong-arch-fallback invariant:** when no asset matches the running
machine's arch, the check returns `available` **without** a `downloadUrl`
(`reason: 'no-asset-for-arch'`) and the UI routes to the release page — it must
**never** fall back to "any `.dmg`/`.exe`," which silently installs a wrong-arch
build and leaves the user with a "damaged" app. The **only** acceptable fallback is
`-universal.dmg` after the exact-arch miss (a universal binary contains both
slices). This invariant is tested by the harness (checklist Row 2/8).

## 5. Integrity — an honest ladder

1. **TLS + host pinning (the floor).** HTTPS only; allow only `github.com`,
   `api.github.com`, `objects.githubusercontent.com` (and `*.githubusercontent.com`);
   re-check the host on **every redirect hop**, resolving relative `Location`
   headers against the current URL.
2. **SHA-256 sidecar (the default).** CI publishes `<asset>.sha256` (format:
   `<hex>  <filename>`, `shasum -a 256` output) per artifact; the updater fetches
   it, hashes the download, and **aborts before staging on mismatch**. Be honest
   about what this buys: the sidecar travels the same channel as the artifact, so it
   detects **corruption/truncation, not tampering** — anyone who can substitute the
   artifact can substitute the digest. A missing sidecar (old release) logs a
   warning and proceeds on the TLS floor.
3. **Detached signature (real tamper resistance, optional).** Sign artifacts in CI
   with **minisign** (key in a GitHub secret), embed the public key in the app,
   verify before staging. ~30 lines, no paid cert, and it's exactly what Sparkle
   (EdDSA appcast) and Velopack provide built-in. Recommended if you ever care about
   a compromised GitHub account not being able to push code to your users silently.

## 6. The state machine (download → verify → stage → swap → relaunch)

Every implementation is this machine; only the "swap" differs per platform:

```
idle → checking → { up-to-date | no-releases | rate-limited | offline | error
                  | available(downloadUrl?) }
available + user consent → downloading(progress) → verifying(sha) → staged/ready
staged + user consent → applying → [app exits] → swap → relaunch new version
any failure → typed error state the UI can recover from (never a dead end)
```

Non-negotiable mechanics, all platforms:

- **Preflight disk space** before downloading: the flow can need ~2.5× the artifact
  size at once (download + staged copy + backup during swap). Fail with a typed
  "need ~X MB free" error, not a generic retry that fails identically forever.
- **Preflight writability** of the install target before quitting into a swap that
  cannot succeed.
- **Download to `<dest>.part`, rename on success.** A partial file must never be
  mistaken for a complete one. Destroy streams on error paths; sweep stale `*.part`
  and stale staged/temp files older than ~24h on startup.
- **Re-entrancy guards.** One download in flight, one apply in flight; further
  requests return `busy`.
- **Throttle progress events** (integer-percent changes or ≥150 ms) — per-chunk
  events flood the UI for zero benefit.
- **The swap runs after your process is gone**, executed by something that outlives
  it (detached script, installer process, or the OS's rename semantics). It must:
  wait for the old process to actually exit (and **abort + reopen the old app if it
  never does** — quit interceptors are common); back up the old install; move the
  new one in; **roll back and relaunch the old version on any failure** (the user
  must never be left with no app — including when the *backup* step itself fails);
  relaunch the new version.
- **Stage can vanish.** OS temp dirs get purged; if the staged artifact is gone at
  apply time, reset to `available` (re-download) instead of hanging on "Updating…".
- **`canSelfInstall` gate.** Only offer download/apply from a real installed build
  (packaged, right platform, writable target). When false, the button degrades to
  "Get vX" → opens the release page. Dev builds never self-install.

## 7. The last mile, per platform (details in each impl file)

| Platform | Swap mechanism |
|---|---|
| macOS (Electron or Swift) | mount dmg / unzip → `ditto` the `.app` out → detached double-forked bash relauncher: wait for PID, strip quarantine, mv old → `.bak`, mv new in, **re-sign ad-hoc**, `open`, rollback on failure |
| Windows installer apps | spawn installer **detached** (NSIS: `['/S', '--force-run']` — bare `/S` never relaunches; Inno: `/VERYSILENT`, relaunch authored in the `.iss` `[Run]` `Check: WizardSilent` entry) → quit so the file lock releases → installer replaces files and relaunches |
| Linux AppImage | rename running AppImage aside (legal — inode stays open) → move verified new file to the original path → `chmod +x` → re-exec |
| CLI binaries | POSIX: atomic `rename()` over the resolved real path of the running binary. Windows: rename running exe → `.old`, write new exe at original path, delete `.old` on next start |
| Server apps | **don't silently self-mutate** — notify in the UI; apply = an operator-run script (git checkout tag / `docker compose pull`) + service restart |

## 8. Log like the app is already gone

The swap happens after your process exits; a log file is the only debuggability.
Write a step-by-step log from the relauncher/installer phase to a location that
survives the app (`~/Library/Logs/<App>/` on macOS, `%APPDATA%\<App>\` on Windows,
the service's journal for servers), plus an `attempts.log` line *before* quitting
(timestamp, from-version, to-version, artifact path). When "the update did nothing"
reports arrive, this log is the whole story.

## 9. User-facing behavior (QoL that separates good updaters from nagware)

- **Show what's new.** Render the release `body` (already in the check response —
  zero extra requests) under the update pill, collapsed.
- **Check on launch + every 12h + on wake** (`powerMonitor` resume on Electron,
  `NSWorkspace.didWakeNotification` on Swift, a timestamp check on request for
  servers). Silent when up-to-date; only the manual "Check for updates" shows a
  "you're current" confirmation.
- **Offline is not an error.** Detect offline distinctly ("You're offline", retry
  when connectivity returns) from GitHub-unreachable.
- **Optional prefs** (persist a small JSON): `autoCheck` toggle (the check pings
  GitHub with your IP — some users care), `skippedVersion` (stop nagging for a
  release the user declined; clear on next newer version), `channel:
  'stable'|'prerelease'` (prerelease channel switches discovery to
  `/releases?per_page=10` filtered on `!draft` — requires the §3 prerelease-aware
  compare).
- **Whole-file downloads are the deliberate trade-off** of rolling your own. If your
  artifact exceeds ~100 MB or users are bandwidth-constrained, that's the signal to
  prefer a framework updater with delta support (§11) — the GitHub-Releases
  contracts in this spec still apply to all of them.

## 10. Testing contract (→ `updater-e2e-harness`)

You can't unit-test process-lifetime-and-file-lock machinery convincingly — test the
real thing, against the local mock, never against throwaway public releases (that
habit produced real releases literally titled "updater test build" in this
portfolio).

Every implementation must honor the harness's env-override contract:

- `UPDATER_API_BASE` — override the GitHub API base URL (points at the mock).
- Both the override **and** any http/host-allowlist relaxation are gated by ONE
  predicate: **`overridesAllowed()` = not-a-production-build OR
  `UPDATER_ALLOW_INSECURE_OVERRIDE=1`** (Electron: `!app.isPackaged || env`; Swift:
  `#if DEBUG || env`; etc.). The gate must cover **every** request site — the check,
  the artifact download, and the sidecar fetch. In a production build without the
  explicit env var, all overrides are dead.
- The full round trip to verify per platform is in each impl file; the harness
  checklist rows (arch matching, no-fallback routing, checksum abort, rollback,
  translocation) are the acceptance tests.

## 11. When to use a framework instead of this recipe

| Situation | Use |
|---|---|
| Ad-hoc-signed / unsigned Electron (no Developer ID) | **This recipe.** `electron-updater` is a non-option: Squirrel.Mac validates the code signature, so electron-builder's docs flatly require a signed app for auto-update on macOS. |
| Developer-ID-signed + notarized Electron | `electron-updater` with the GitHub provider (zip target + `latest-mac.yml`) — deltas and battle-tested swap for free. |
| Native Swift app, you want deltas/UI/EdDSA and accept a framework dep + key custody | **Sparkle 2** — works against GitHub Releases (`generate_appcast --download-url-prefix` pointing at the release download URLs; appcast on GitHub Pages/raw). Trade-off discussion in `impl-swift-macos.md`. |
| New .NET (or C++/Electron-alt) Windows app | **Velopack** (Squirrel's successor): first-class GitHub Releases source, deltas, rollback; `vpk upload github` slots into `github-ship` CI. Details in `impl-windows-native.md`. |
| Tauri app | The official Tauri updater plugin (its `latest.json` + minisign pattern is the model for §2's manifest option). |
| Existing hand-rolled portfolio apps | Keep the hand-rolled recipe — it matches the ad-hoc trust model, has zero infra beyond GitHub Releases, and is the house pattern these references document. |

---

# The Electron implementation (the original recipe)

Drawn from production implementations (`Folderify`, `robogearsDownloader`,
`3rdPlayerScreen4Rekordbox`, `MusicSorter`, `FLACtoiPod`) that ship arm64 dmgs and
NSIS installers via GitHub Actions, ad-hoc signed and un-notarized.

## Architecture & data flow

Three Electron processes; the network lives **only in main** (zero renderer CORS/CSP
concern — do NOT add GitHub to `connect-src`). Transport uses Electron's `net`
module so system proxies and the OS cert store work (raw `node:https` fails forever
behind corporate proxies); the dev-mock http path is the one exception, behind the
§10 guard.

```
  MAIN process                             RENDERER UI
  ───────────                              ───────────────────
  registerUpdater(getWindow)               useUpdates() store
    ├ ipcMain.handle:                        ├ init(): subscribe onUpdateAvailable /
    │   app:version                          │   onUpdateProgress, fetch version +
    │   update:check          ◄──invoke──    │   canSelfInstall, then fire a SILENT
    │   update:get-pending                   │   check() and get-pending replay
    │   update:can-self-install              ├ check(): checkForUpdates()
    │   update:download   (no args)          ├ startDownload(): if !canSelfInstall →
    │   update:apply                         │   openExternal(releaseUrl)
    │   shell:open-external                  │   else downloadUpdate() → ready|failed
    ├ pushes: update:available               ├ apply(): applyUpdate()
    │         update:download-progress       └ openRelease(): openExternal(releaseUrl)
    ├ ETag-cached check; 12h timer;
    │   powerMonitor resume re-check       UpdateButton renders the state machine:
    └ temp sweep on register                 "Get vX" | "Update to vX" (+ What's new)
                                             | "Downloading N%" | "Restart to apply"
  Preload (contextBridge): fixed              | "Updating…" | "Download failed — retry"
  window.api surface; never raw ipcRenderer.  | "You're offline" | "Rate-limited, retry in Ns"
```

**Who runs the first check:** the renderer does, at the end of its `init()` — a
silent `check()` plus a `update:get-pending` replay for any result that landed
before it subscribed. Main's `checkAndNotify()` on window-ready is optional
belt-and-suspenders; the renderer path is the one that must exist. (Do not
reintroduce the old `setTimeout(2500)` launch hack.)

Happy path: check finds newer → pill "Update to vX" with collapsible notes → click →
main downloads to `*.part` (progress events), verifies sidecar, renames, mounts the
dmg, `ditto`s the `.app` out, detaches, stages → "Restart to apply" → main writes
the detached double-forked relauncher, logs an attempt line, quits → script waits
for the PID (aborts + reopens if it never exits), strips quarantine, swaps with
`.bak` rollback, re-signs ad-hoc, `open`s the new version.

## The IPC contract (exact channels)

Raw string channels in the preload — intentionally not part of any typed IPC map.
Reproduce exactly:

| Direction | Channel | Payload → Result |
|---|---|---|
| invoke | `app:version` | → `string` |
| invoke | `update:check` | → `UpdateCheck` (also pushes `update:available` if newer) |
| invoke | `update:get-pending` | → `UpdateCheck \| null` (replays last available) |
| invoke | `update:can-self-install` | → `boolean` ((darwin \|\| win32) && `app.isPackaged`) |
| invoke | `update:download` | **no args** → `{ ok: boolean; error?: string }` |
| invoke | `update:apply` | → `{ ok: boolean; code?: 'stage-missing' \| 'busy'; error?: string }` |
| invoke | `shell:open-external` | `url: string` → opens http(s) URLs only |
| push | `update:available` | `UpdateCheck` (the `available` variant) |
| push | `update:download-progress` | `{ downloaded: number; total: number }` |

`update:download` deliberately takes **no URL**: main downloads the asset + sidecar
pair from its own last check state. (The old renderer-supplied URL was both a
security hole — any GitHub-hosted URL was accepted — and a race: a periodic re-check
could bump main's sha256 state while the renderer held a stale URL, guaranteeing a
spurious checksum mismatch.)

The full `UpdateCheck` union (single source of truth: `references/renderer-and-ipc.md`):

```ts
export type UpdateCheck =
  | { status: 'available'; version: string; notes?: string; publishedAt?: string;
      downloadUrl?: string; sha256Url?: string; releaseUrl: string;
      reason?: 'no-asset-for-arch' }
  | { status: 'up-to-date'; version: string }
  | { status: 'no-releases' }
  | { status: 'rate-limited'; retryAfterSeconds?: number }
  | { status: 'offline' }
  | { status: 'error'; message: string }
```

`available` with **no** `downloadUrl` (`reason: 'no-asset-for-arch'`) means the UI
must route to `releaseUrl` — never self-install (§4).

## Build order

1. **Packaging** — electron-builder emits `MyApp-<version>-<arch>.dmg` (and
   `-setup.exe` if you ship Windows), ad-hoc signed via `build/after-pack.js`.
   Copy `references/packaging-and-ci.md`. Verify `npm run build:mac` yields a dmg.
2. **CI + releases** — the tag-triggered workflow that builds, emits `.sha256`
   sidecars for **every** artifact, and creates a **draft** release with the body
   from `RELEASE_NOTES.md`. Same file. Verify a tag push produces a draft with
   correctly-named assets + sidecars.
3. **Main-process updater** — drop in `references/updater-main.ts` (whole file; it
   platform-dispatches darwin/win32 internally). Set `OWNER`/`REPO`. Wire it:
   ```ts
   import { registerUpdater } from './updater'
   const updater = registerUpdater(() => mainWindow)   // getter, not the window
   app.on('before-quit', () => updater.stop())
   ```
4. **Preload bridge + renderer UI** — `references/renderer-and-ipc.md` (window.api
   surface, zustand store, UpdateButton). Call `useUpdates().init()` once at start.
5. **Test the round trip** with the `updater-e2e-harness` mock (§10) before any
   real release.

## The gotchas that make it actually work

1. **Ad-hoc sign after packing** (`build/after-pack.js`: `xattr -cr` then `codesign
   --force --deep --sign -`; `mac.identity: null`) — a fully-unsigned Apple Silicon
   app is rejected as "damaged."
2. **App Translocation guard.** A quarantined app launched from Downloads runs from
   a read-only translocated path — detect `'/AppTranslocation/'` and install to
   `/Applications/<App>.app` instead. Aftermath: the stale quarantined copy in
   Downloads still opens the old version; surface a one-time "moved to
   Applications — delete the old copy" notice after such an install.
3. **Re-sign after install** (in the relauncher, before `open`).
4. **The relauncher must survive the app quitting** — double-fork, `trap "" HUP
   TERM`, wait ≤30 s for the old PID, **and if the PID never exits, abort and
   reopen the old app** rather than swapping under a live process. Tray apps that
   intercept `before-quit` to hide-instead-of-quit are the classic cause — set an
   explicit `isQuittingForUpdate` flag your quit interceptors respect.
5. **Strip quarantine before opening the moved app.**
6. **Roll back on failure — including backup failure.** `.bak` the old bundle; if
   the move-in fails, restore; if even the *backup* step fails, reopen the still-
   intact old app before exiting. Never leave the user app-less.
7. **Guard self-install** (`(darwin || win32) && app.isPackaged`).
8. **Per-user temp dir** (`os.tmpdir()`, mode 0700 on macOS) — not `/tmp`.
9. **Log to `~/Library/Logs/<App>/`** + `attempts.log` (§8).
10. **`releases/latest` skips drafts** — publishing is `github-ship`'s deliberate
    flip, not a CI step.
11. **Recover from a vanished stage** (`stage-missing` → back to "Update to vX").
12. **Windows sequencing** — spawn the installer detached first, quit second; see
    `references/updater-windows.md` for the `/S` + relaunch-argument specifics,
    SmartScreen, and per-user-install constraints.

## Hardening

The reference `updater-main.ts` ships hardened **by default** — these are the
baseline, not TODOs:

- **Host pinning** on every request incl. redirect hops (relative `Location`
  resolved, host re-checked); http and API-base overrides exist only behind the §10
  `overridesAllowed()` guard — dead in packaged builds without the explicit env var.
- **SHA-256 sidecar verification** before staging; main re-derives both URLs from
  its own check state (`update:download` takes no args).
- **ETag-cached checks** persisted to userData; `rate-limited` status on 403/429.
- **`.part` downloads**, stream destruction on error, startup temp sweep,
  re-entrancy guards, disk-space and writability preflights.

Still your call, per trust level: minisign detached signatures (§5.3); notarized
apps should `codesign --verify` + `spctl -a` the staged bundle; for hygiene, pass
staged paths to the relauncher as `argv` rather than interpolating into the script,
or assert the extracted `.app` name equals your product name.

## Reference files

- `references/updater-main.ts` — the entire main-process updater, verbatim and
  genericized: ETag'd check, version compare, asset match, `net`-based streaming
  download with redirect re-validation + progress, sidecar verify, dmg
  mount/extract, the double-forked self-installing relauncher, the NSIS branch,
  and every §6 mechanic. **Read it in full before writing your own.**
- `references/renderer-and-ipc.md` — preload surface, zustand store, UpdateButton,
  shared types (the `UpdateCheck` single source of truth), wiring.
- `references/packaging-and-ci.md` — electron-builder.yml (artifactName contract,
  `identity: null`), after-pack ad-hoc signing, sidecar generation for all
  artifacts, and the tag-triggered draft-release workflow (whose release job
  **includes `actions/checkout`** so `body_path` isn't empty). RELEASE_NOTES format
  is owned by `github-ship`.
- `references/updater-windows.md` — the NSIS variant: asset contract, download,
  detached silent install + relaunch semantics, SmartScreen/AV reality.
- `references/impl-swift-macos.md` — native Swift/SwiftUI recipe (VibeLight) +
  Sparkle 2 decision.
- `references/impl-windows-native.md` — .NET + Inno Setup (VirtualMirage), Velopack
  for new apps, rename-aside self-replace for installer-less exes/CLIs.
- `references/impl-linux-appimage.md` — AppImage swap + re-exec (VibeDolphin), CLI
  binary self-replace.
- `references/impl-server-python.md` — self-hosted server tiers: notify-only,
  operator-run update script, and (rarely) supervised auto-update; Docker and
  systemd variants.
