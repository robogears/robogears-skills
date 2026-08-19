# Linux AppImage + single-binary CLI self-update (the Linux path)

This is the Linux half of the `github-updater` skill, derived from a production C++/Qt
implementation: VibeDolphin's `Source/Core/DolphinQt/VibeUpdater.{h,cpp}` (a Dolphin fork
shipped exclusively as a Steam Deck AppImage). The core spec in `SKILL.md` applies in
full; this file covers the Linux last mile — which is *simpler* than macOS or Windows,
because Linux lets you atomically replace a running executable. Read the core spec first.

> Cross-references: cutting the release this updater reads is **github-ship** (these
> repos use its no-CI Path B — local build + `gh release create --draft`); testing the
> flow without a public release is **updater-e2e-harness**; where the version number
> lives per repo is github-ship's `references/version-bump-table.md`. Windows CLI
> self-replace is in `references/impl-windows-native.md`.

## Contents

- [Mapping the core spec onto C++/Qt (or raw libcurl)](#mapping-the-core-spec-onto-cqt-or-raw-libcurl)
- [The asset contract (Linux edition)](#the-asset-contract-linux-edition)
- [Integrity: GitHub's per-asset digest, and/or the sidecar](#integrity-githubs-per-asset-digest-andor-the-sidecar)
- [The swap — Linux lets you replace a running file](#the-swap--linux-lets-you-replace-a-running-file)
- [Desktop-integration caveats](#desktop-integration-caveats)
- [Single-binary CLI self-update](#single-binary-cli-self-update)
- [Retargeting an inherited updater (the VibeEden pattern)](#retargeting-an-inherited-updater-the-vibeeden-pattern)
- [Release side + testing](#release-side--testing)

---

## Mapping the core spec onto C++/Qt (or raw libcurl)

No new dependencies. A Qt app almost certainly already links libcurl (Dolphin's
`Common::HttpRequest` wraps it) or has `QNetworkAccessManager`; JSON parsing is picojson
/ `QJsonDocument`. The whole updater is one header + one cpp.

| Core spec § | C++/Qt mechanism (from the production implementation) |
|---|---|
| §2 check | Blocking GET of `releases/latest` off the UI thread; ~10 s timeout, follow redirects (≤4); headers `User-Agent: App/<version> (+repo URL)` and `Accept: application/vnd.github+json` |
| §2 typed results | Tri-state enum: `UpToDate` / `UpdateAvailable` / `CheckFailed` — **a network or parse failure is `CheckFailed`, never "up to date"** |
| §3 compare | Strip one leading `v`/`V`, split on `.`, `std::from_chars` per segment (junk → 0), compare left-to-right zero-padding the shorter |
| §6 consent | Modal dialog: version + release `notes` as detailed text; "Update && Restart" or "Open release page" per `CanSelfInstall()` |
| §6 guard | `CanSelfInstall()` = running from an AppImage **and** target dir writable (below) |
| §8 logging | Append-only `<app>-update.log` **beside the AppImage** — it survives the re-exec |

Three hazards specific to this stack:

- **The version lives in code.** In this pattern the current version is parsed from a
  brand string compiled into the binary (e.g. `"VibeDolphin 0.1.17"` in
  `Source/Core/Common/Version.cpp` — the updater takes the last whitespace-delimited
  token). There is no manifest to read at runtime. Consequence: **if a release bumps the
  tag but not the string, every updated install still reports the old version, sees the
  same release as "newer," and offers itself the same update forever** — an infinite
  update loop, shipped. Bump the string and the tag in the same commit; the per-repo
  location is in github-ship's `references/version-bump-table.md`.
- **ETag caching (core spec §2) still applies.** A launch-time check is low-volume, but
  the 60 req/h unauthenticated quota is shared per IP across every app on the machine.
  Persist the last `ETag` + parsed result next to the app config, send `If-None-Match`
  on each check, and treat `304` as "reuse cached result" (it costs no quota). libcurl:
  add the header via `CURLOPT_HTTPHEADER` (`If-None-Match: "<etag>"` — keep the quotes
  GitHub sent) and read the response `ETag`. Qt:
  `request.setRawHeader("If-None-Match", etag)` on the `QNetworkRequest`.
- **Don't name the result enum `Status` in a Qt/X11 codebase.** `<X11/Xlib.h>` does
  `#define Status int` and is pulled in transitively by Qt platform headers —
  `MyUpdate::Status::Foo` becomes `MyUpdate::int::Foo` and the error points nowhere
  useful. Name it `CheckStatus`.

## The asset contract (Linux edition)

| Pattern | Matcher | When |
|---|---|---|
| `App.AppImage` (unversioned, one per release) | name ends with `.AppImage` (suffix compare, not substring) | Single-arch — the default here |
| `App-x86_64.AppImage` / `App-aarch64.AppImage` | ends with `-<arch token>.AppImage`, token from `uname -m` | The moment a second arch ships |
| `<asset>.sha256` sidecar | exact `<asset name>.sha256` | If using the sidecar convention (below) |

- **An unversioned single asset is fine.** The release *tag* carries the version; the
  updater already compared against `tag_name` before it ever looks at assets. Fixed
  names also make `releases/latest/download/App.AppImage` a permanent URL. The cost
  (two downloaded files indistinguishable in ~/Downloads) is the same trade-off as the
  Windows fixed-name pattern in `updater-windows.md`.
- **The no-fallback rule (core spec §4) applies unchanged**: if the release ships
  multiple AppImages and none carries the running machine's arch token, return
  available-without-URL and route to the release page. Never grab "any `.AppImage`" —
  a wrong-arch AppImage on Linux just fails to exec, but only *after* you've destroyed
  the working install.
- Suffix-match defensively: check `name.size() >= 9` before comparing the last 9 chars,
  or use `endsWith` — a naive `substr` on a short name throws.

## Integrity: GitHub's per-asset digest, and/or the sidecar

GitHub's release JSON includes a per-asset **`digest`** field — `"digest":
"sha256:<hex>"` — computed by GitHub at upload time (verified 2026-07). The production
implementation verifies against it:

1. From the matched asset object, read `digest`; if it starts with `sha256:`, strip the
   prefix and keep the hex.
2. After download, hash the bytes (`QCryptographicHash::Sha256` / OpenSSL EVP) and
   compare **case-insensitively**.
3. **Abort on mismatch *before* the file is made executable** — discard, log, tell the
   user, keep running the old version.
4. If the release provided no digest (older releases, some upload paths): warn +
   confirm on the TLS floor rather than hard-blocking your own release — same posture
   as a missing sidecar in the core spec.

Be honest about the tier (core spec §5): the digest is **API-provided over the same
channel as the artifact** — it detects corruption and truncation, not tampering. It's
the same rung of the ladder as the `.sha256` sidecar, minus any ship-side work (GitHub
computes it; nothing to generate or upload).

**Use both when consistency matters.** The rest of the portfolio publishes `.sha256`
sidecars per artifact; the harness's checksum-abort test (checklist) exercises the
sidecar path. Digest-only is acceptable for a repo that ships one AppImage; add the
sidecar the moment the repo grows a second artifact type. Real tamper resistance is
still minisign (core spec §5.3), unchanged on Linux.

## The swap — Linux lets you replace a running file

The Linux superpower: **renaming or replacing a running executable is legal.** The
process holds the old inode open (for an AppImage, through its live squashfs mount);
the directory entry is yours to swap. No detached relauncher script, no installer
process, no waiting-for-PID dance. The sequence:

1. **Resolve the running image's real path.** The AppImage runtime sets the
   **`APPIMAGE`** env var to the outer file's absolute path — that is the swap target.
   Empty/unset means you're not running as an AppImage (dev build, extracted tree):
   `CanSelfInstall()` = false, degrade to the release page. Bare binaries resolve via
   `readlink("/proc/self/exe")` instead (see the CLI section).
2. **Preflight writability of the parent directory, not the file.** Creating the
   staging file and renaming both happen in the *directory*; a file can be writable
   while its dir is read-only (and vice versa). `QFileInfo(dir).isWritable()` /
   `access(dir, W_OK)` is the real gate. Plus the core-spec ~2.5× disk preflight
   (download + staged copy + `.bak` coexist mid-swap).
3. **Stage beside the target — never in `/tmp`.** A staging dir next to the AppImage
   (`.myapp-update-XXXXXX`) guarantees same-filesystem, which makes the final rename
   **atomic**. `/tmp` is routinely a different filesystem (tmpfs; on the Steam Deck it
   is), turning the rename into `EXDEV` → a cross-device copy that, if interrupted,
   leaves a half-written binary where the app used to be. Download to
   `<staging>/App.AppImage.part` (or hash in memory first, as the production code does
   for a modest artifact — either way **no byte becomes executable before the digest
   check passes**), verify, write, rename off `.part`.
4. **`chmod +x` the staged file** (0755). AppImages must be executable; browsers and
   downloads aren't.
5. **Swap.** The canonical, rollback-capable sequence — both renames are same-dir,
   hence atomic:
   ```
   rename(App.AppImage      → App.AppImage.bak)     # old version parked
   rename(staged new image  → App.AppImage)          # new version live
   ```
   On failure of the second rename: `rename(.bak → App.AppImage)` and keep running —
   the user must never be left app-less (core spec §6). Delete the `.bak` on the next
   *successful* start of the new version; until then it is your rollback. Sweep stale
   `.bak`s and `.myapp-update-*` staging dirs on startup.

   The production implementation uses the shorter variant: a **single POSIX
   `rename()` directly over the target** — `rename()` atomically replaces an existing
   destination, and the running process is unaffected because it holds the old inode.
   No `.bak` means no on-disk rollback, which is acceptable *only because* the bytes
   were verified before the swap and an atomic rename cannot half-fail. Gotcha either
   way: **`QFile::rename()` refuses to overwrite an existing target** — use
   `std::rename` / `std::filesystem::rename` (POSIX overwrite semantics), not the Qt
   wrapper.
6. **Relaunch: `execv()` the new path as *this* process** — or prompt "restart to
   finish" if re-exec doesn't fit the app's lifecycle. The hard-won reason re-exec
   beats a detached helper on modern Linux: launchers (Steam, KDE's file manager) run
   the app in a **transient systemd scope that is torn down the instant the process
   exits — SIGKILLing any detached child before it can swap anything.** In production
   even `systemd-run --user --scope` failed to escape the scope (reported success; the
   command never ran). Swapping while still running and then `execv()`ing keeps the
   same PID and the same scope: nothing to kill mid-swap. Two sub-rules:
   - **Scrub the AppRun environment before `execv`.** The old AppImage's AppRun
     exported `LD_LIBRARY_PATH`, `QT_PLUGIN_PATH`, `QT_QPA_PLATFORM_PLUGIN_PATH`,
     `PYTHONPATH`, `APPDIR` pointing into the *old, soon-stale* mount — `unsetenv()`
     each so the new AppRun starts clean. Leave `DISPLAY` / `WAYLAND_DISPLAY` intact
     so the window comes back.
   - **`execv` returns only on failure — and by then the swap already happened.**
     Don't die silently: log it, tell the user "update installed — relaunch to
     finish," and quit. The next manual launch runs the new version.
7. **Make the call site respect the exit.** If the update flow ends in
   quit-and-re-exec, return that fact to the caller — code queued to run after the
   dialog (an emulator about to boot a title, a pending job) must bail rather than run
   underneath the relaunch.

Log every step (rename result + errno, execv failure) to `<app>-update.log` beside the
AppImage — after the re-exec, that file is the only witness (core spec §8).

## Desktop-integration caveats

- **No Gatekeeper equivalent.** No quarantine xattr, no translocation, no re-sign
  step. The entire post-download ritual is `chmod +x`. Enjoy it.
- **AppImageLauncher / appimaged can hold paths.** Those integrators move AppImages
  into `~/Applications` and write `.desktop` entries pointing at the *absolute path*.
  The in-place swap above is integration-safe precisely because the path never
  changes. If you ever "install to a new location" instead of swapping in place, the
  stale `.desktop` entry keeps launching the old file — the Linux cousin of macOS's
  stale-copy-in-Downloads problem.
- **Keep the AppImage (and staging) out of `/tmp`**: `noexec` mounts are common,
  tmpfs purges on reboot, and it's usually a different filesystem (breaks the atomic
  rename — step 3 above).

## Single-binary CLI self-update

Identical machine, minus the AppImage specifics. The whole recipe:

1. **Resolve the real path of the running binary**: `readlink("/proc/self/exe")` on
   Linux (`_NSGetExecutablePath` + `realpath` on macOS). **Never trust
   `realpath(argv[0])`** — argv[0] can be a bare name resolved via `PATH`, a symlink,
   or an outright lie from the parent process.
2. If the resolved path lives under a package manager's territory
   (`/usr/bin`, a Homebrew cellar, an apt/dpkg-owned file), **don't self-update** —
   print the package-manager upgrade command instead. Self-updating a packaged binary
   fights the package database and the next `apt upgrade` reverts it.
3. Check `releases/latest` (ETag'd), match the asset by arch token
   (`myapp-linux-x86_64`), verify digest/sidecar — all per the core spec.
4. Download to `<binary>.part` **in the same directory** (same-filesystem, and the
   dir-writability preflight doubles as the permission check), verify, `chmod +x`.
5. Atomic `rename()` over the resolved real path — the running process keeps its open
   inode and finishes normally. Want rollback? The `.bak`-aside sequence from step 5
   above, delete on next successful start.
6. Windows can't rename *over* a running exe, but it **can rename the running exe
   aside** — that variant is in `impl-windows-native.md`.

Don't hand-roll this in Rust or Go — embed a maintained implementation: Rust's
**`self_update`** crate (v0.44, actively maintained, verified 2026-07) and Go's
**`creativeprojects/go-selfupdate`** (v1.5.x, active, verified 2026-07) both speak
GitHub Releases natively, do the arch-matched asset selection and checksum
verification, and implement exactly this swap. The core-spec contracts (asset naming,
no-fallback, draft invisibility) still apply to what you feed them.

## Retargeting an inherited updater (the VibeEden pattern)

When a fork's upstream **already ships an update checker**, don't greenfield a second
one — repoint the existing one at your GitHub repo. The VibeEden (yuzu/Eden fork)
shape: endpoint, path, and repo are **CMake-baked constants** in
`CMakeModules/GenerateSCMRev.cmake`, templated into `scm_rev.cpp.in` at configure time:

```cmake
set(BUILD_AUTO_UPDATE_API      "api.github.com")
set(BUILD_AUTO_UPDATE_API_PATH "/repos/<owner>/<repo>/releases/latest")
set(BUILD_AUTO_UPDATE_REPO     "<owner>/<repo>")
set(BUILD_AUTO_UPDATE_WEBSITE  "https://github.com")
```

Rules for this move:

- **Verify the upstream's JSON parser understands GitHub's release schema** before
  assuming the swap works (Eden's `Release::FromJson` already read
  `assets[]`-shaped JSON; a Forgejo/Gitea-native parser may too — their APIs are
  GitHub-compatible — but confirm against a real response).
- **Audit which constants are read where before swapping.** Not every
  "updater-looking" upstream variable feeds the updater: in this codebase the
  `STABLE_*` variants feed an unrelated emulated-news service and must stay
  upstream-shaped — a naive swap-everything 404s a feature that has nothing to do
  with updates.
- **The fork's version lives in the same CMake file** (`VIBEEDEN_VERSION` →
  `BUILD_VERSION` → the compare in the upstream checker) — a fourth version-file
  format for the ship side; it's in github-ship's `references/version-bump-table.md`.
  Same infinite-loop hazard as the brand-string case above: tag and CMake constant
  move in the same commit.
- Everything the upstream checker *lacks* against the core spec (digest verification,
  typed rate-limit handling, ETag) is a gap to close incrementally, not a reason to
  rewrite — the retarget alone gets users onto your releases.

## Release side + testing

- **Ship:** the AppImage repos here release via github-ship's **no-CI Path B** — local
  AppImage build, then
  `gh release create vX.Y.Z --draft --verify-tag --notes-file RELEASE_NOTES.md App.AppImage`
  (plus `.sha256` sidecars if the repo uses the sidecar convention; the API `digest`
  costs nothing — GitHub computes it on upload). Draft releases are invisible to
  `releases/latest`, so the publish flip is the moment users' updaters see it.
- **Test with the harness, never with real releases.** Cutting a public release purely
  to see the updater fire is the exact habit `updater-e2e-harness` exists to kill —
  this portfolio has shipped real releases literally titled "updater test build."
  The env-override contract maps to C++ as: base URL from `UPDATER_API_BASE` when
  **`overridesAllowed()`** — a build-type predicate (`#ifdef` on a dev/CI define, or
  an `IS_DEV_BUILD`-style configure-time flag) **OR**
  `getenv("UPDATER_ALLOW_INSECURE_OVERRIDE")` — and the gate must cover **every**
  request site: the check, the asset download, and any sidecar fetch. In a release
  build without the explicit env var, the overrides are dead code.
