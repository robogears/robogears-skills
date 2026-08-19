# Project-type detection — signal files → release path

This is step 0 of `ship`. **Detection decides everything downstream:** which file(s) get
the version bump, which post-bump hook runs, whether you watch CI or build locally, and
whether "publish" includes a registry step. Get it wrong and you bump the wrong file,
invent a workflow the repo doesn't have, or ship an artifact the updater can't find.

## Table of contents
- [How to detect (30 seconds)](#how-to-detect-30-seconds)
- [The detection matrix](#the-detection-matrix)
- [False-signal traps (verified in this portfolio)](#false-signal-traps-verified-in-this-portfolio)
- [Tie-breakers — when several signals match](#tie-breakers--when-several-signals-match)
- [The portfolio, one line per repo (survey verified 2026-07)](#the-portfolio-one-line-per-repo-survey-verified-2026-07)
- [No-CI repos: what "ship" builds locally](#no-ci-repos-what-ship-builds-locally)
- [Monorepos vs subdir manifests](#monorepos-vs-subdir-manifests)

## How to detect (30 seconds)

```bash
ls -a                                    # signal files at the root
ls app ui src 2>/dev/null                # manifests hiding in subdirs
ls .github/workflows/ 2>/dev/null        # a tag-triggered release workflow → Path A; none → Path B
git fetch origin 2>/dev/null             # then ALSO check the remote release branch — local
git ls-tree -r --name-only origin/<default-branch> .github/workflows/ 2>/dev/null
                                         # trees go stale/partial; read any push-triggered
                                         # publish workflows found there
gh release list --limit 5                # the practiced release shape (assets? draft-first? none ever?)
git tag --list | head -5                 # tagged at all? per-package tag scheme?
```

Then confirm against the matrix below — **signals lie** (see the traps section), so a
signal file alone is never the verdict; the combination is. And if the remote check
surfaces a **push-triggered docker/publish workflow**, a plain branch push already
publishes somewhere — what "push" means for that repo has changed.

## The detection matrix

Release-path values map to `SKILL.md` step 4: **tag-CI** = Path A, **no-CI local build**
= Path B, **docker** = Path C, **source-only** = tag + `gh release create` with the auto
source tarball (legitimately zero assets), **no-releases-by-design** = push is the whole
story, don't invent more.

**A named row in the [portfolio table](#the-portfolio-one-line-per-repo-survey-verified-2026-07) below overrides this matrix and the tie-breakers.**

| Signals | Type | Version file(s) | Post-bump hook | Release path | Distribution step on publish |
|---|---|---|---|---|---|
| `electron` dep + builder config — `electron-builder.yml` **or** a `"build"` key embedded in package.json (easy to miss) | Electron app | `package.json` `"version"` (root **or subdir** — see tie-breakers) | `npm install --package-lock-only`; then grep `scripts` for sync helpers (`sync\|version\|ext`) and run them | tag-CI draft (house default) or no-CI local | none — GitHub Releases IS the channel |
| package.json with `"name"` + `"version"`, no electron, no `"private": true` | npm library | `package.json` | lockfile sync as above | tag-CI or local | `npm publish` |
| `pnpm-lock.yaml` / `yarn.lock` / `"packageManager"` field | pnpm/yarn flavor of the two rows above | `package.json` | **none for the lockfile** — pnpm/yarn lockfiles don't embed the project's own version (only npm's package-lock.json does) | as above | as above |
| `project.yml` (XcodeGen) | Swift/macOS app | `project.yml` → `MARKETING_VERSION` | `xcodegen generate` — the pbxproj is generated output, never hand-edit it | tag-CI or **no-CI local build** | none |
| `.xcodeproj` with **no** `project.yml` | raw Xcode app | pbxproj `MARKETING_VERSION` — bump it in **every** build config (Debug + Release) | none | tag-CI or local | none (App Store is out of scope here) |
| `.csproj` (+ an `.iss` Inno script = installer variant) | C#/.NET app | csproj `<Version>`; CI may **also** stamp `-p:Version=$tag` — keep csproj and tag in lockstep or the shipped assembly drifts from source | none | tag-CI (Windows runner; the installer-compile step fails independently of the build) | none |
| `CMakeLists.txt` | C++ CMake | `project(... VERSION)`, a repo-specific CMake var, **or a version-in-code string** — grep the old version, don't assume | none | tag-CI or no-CI local | none |
| `.pro` file (qmake) | C++ Qt | upstream `version.txt` or in-code | none | often **no-releases-by-design** (helper/fork built for embedding) | none |
| `pyproject.toml` **with a `[project]` table** | Python library | `[project].version` — or **dynamic** (setuptools-scm / hatch-vcs): **no file to bump, the tag is the version** (SKILL step 1's dynamic check) | none | tag-CI | PyPI — `uv publish` / twine |
| `requirements.txt` + app entry point, **no** `[project]` table | Python app/server | version constant(s) **in code** — grep `APP_VERSION` / `__version__` and bump **every** copy (see traps) | none | source-only, or docker if compose-deployed | none, or ghcr image |
| `Cargo.toml` with `[package]` | Rust | `Cargo.toml` `version` | `cargo check` refreshes `Cargo.lock` — commit both | tag-CI | crates.io `cargo publish` (libraries) |
| `go.mod` | Go | **none — the annotated tag IS the version.** Major bumps ≥ v2 also need the `/vN` module-path suffix in go.mod | none | tag-CI (goreleaser is the common shape) | none — the module proxy indexes the tag itself |
| `Dockerfile` + `docker-compose.yml`, **no packaging manifest** | Docker-first server | image tag (+ any in-code constant, bumped in lockstep) | none | docker (Path C) | ghcr `:latest` retag — that's the visibility flip |
| `manifest.json` with `manifest_version`, standalone repo | Browser extension | `manifest.json` `"version"` | none | tag-CI zip or local | store upload (Chrome Web Store / Safari-via-Xcode) — a documented manual/CI step; **self-update is out of scope, stores own it** |
| Manifest only in a subdir (`app/`, `ui/`) | subdir-manifest app | **the subdir manifest** — the root has none | confirm against CI's `working-directory` | per the rows above | per the rows above |
| Multiple independently-released packages | monorepo | per-package manifests | per package | per-package tags `<pkg>-vX.Y.Z` (see below) | per package |
| None of the above; nothing to build | source-only repo | in-code constants, if any | none | tag + `gh release create` — GitHub's auto source tarball is the deliverable; **zero `.assets` is correct** here | none |
| CI runs on **push**, zero releases ever, deliverable is embedded in another app | helper repo — **no releases by design** | upstream's scheme, untouched | none | **push-only.** "Ship" doesn't apply; don't scaffold a release path unprompted | none |

**CLI binaries** (Go/Rust/single-binary) follow their ecosystem's row; if one is also
distributed via a **Homebrew tap**, publish adds a formula bump (new `url` + `sha256`
pointing at the release asset). No portfolio instance yet (verified 2026-07) — treat the
brew step as designed-but-unproven.

## False-signal traps (verified in this portfolio)

Each of these has misdirected a naive detector at least once. **Check for them before
trusting any single signal file.**

1. **package.json without `"version"`/`"name"`** — exists only to pin JS deps inside a
   non-JS project (a Python server using a JS SDK, for example). **Presence of
   package.json does NOT mean npm project.** A real npm/Electron project has `name` +
   `version` and usually `scripts`/builder config; a dependency-pinning stub has only
   `dependencies`. Never bump a version field that isn't there — and never add one.
2. **pyproject.toml with no `[project]` table** — pure tool config (`[tool.pytest.*]`
   etc.). Not Python packaging, not a version source, no PyPI step. The `[project]`
   table is the discriminator, not the filename.
3. **setup.py that is a runtime setup script** — creates dirs/DB/admin user on first
   boot, never imports setuptools, never calls `setup()`. Not packaging. Read the first
   twenty lines before concluding "setuptools project."
4. **A tracked `build/` directory that is source** — electron-builder `buildResources`
   (`after-pack.js` signing hooks, icons) live there on purpose. The junk gate flags the
   path; confirm it's tracked source and re-stage. `dist/`, `out/`, `release/` output
   stays banned.
5. **Version constants duplicated across files that drift** — two hand-maintained copies
   of the same `APP_VERSION` in different modules will diverge the first time one bump
   misses one. `git grep -n 'APP_VERSION\|__version__'` and bump **every** hit in the
   same commit. Already drifted? Which number wins:
   `git log -S <CONSTANT_NAME> --oneline -- <each file>` (or blame) finds the most
   recent **deliberate** bump — that is the intended version. The endpoint-served copy
   tells you what running installs REPORT — decisive once an installed base/updater
   exists, not before (zero tags, zero releases → the deliberate bump wins). Then bump
   ALL copies to that one number in the release commit.

## Tie-breakers — when several signals match

| Conflict | Winner |
|---|---|
| electron-builder config (yml **or** embedded `"build"` key) vs bare package.json | **Electron app.** The builder config decides; the package.json version still drives `${version}` in artifact names. |
| Dockerfile + compose present, no packaging manifest anywhere | Usually **self-host deployment convenience, not a distribution channel**. Route Docker-first (Path C) only when images are actually **published** — a ghcr package exists, or CI/release history shows image pushes. |
| `.github/workflows/` has a tag-triggered release workflow vs none | Workflow present → **Path A** (watch CI). Absent → **Path B** (build locally, `gh release create`). **Never invent a workflow mid-ship** — offering one is a separate, later conversation. |
| `project.yml` and `.xcodeproj` both present | **project.yml.** The xcodeproj is XcodeGen output; edits to it are overwritten by the next generate. |
| pyproject `[project]` table vs requirements.txt | `[project]` table → Python **library** (PyPI step). Bare requirements.txt + app entry → **app/server**, version in code. |
| Root manifest vs subdir manifest | Wherever CI's `working-directory` points (or, no CI: where the build script `cd`s). The root often has nothing. |
| manifest.json inside an app repo (beside Electron sources) | **Companion payload, not the project type.** Its version is synced by a post-bump hook, not bumped independently. |
| Release history contradicts the signal files | **History wins.** `gh release list` shows the practiced shape (asset names, draft-first or not, two-asset contracts). Signals say what's possible; releases say what's real. |
| Still ambiguous | Read the repo's CLAUDE.md / ship.md, then **ask**. A wrong guess mutates files. |

## The portfolio, one line per repo (survey verified 2026-07)

Asset names are **API** — every updater matches by filename substring/suffix. Never
rename an asset without changing the matcher in the same commit.

| Repo | Type | Version file(s) | Post-bump hook | Release path | Assets |
|---|---|---|---|---|---|
| Folderify | Electron/TS (electron-vite, npm) | `package.json` — multi-file lockstep incl. iOS pbxproj + CLAUDE.md header (see `version-bump-table.md`) | lockfile sync | tag-CI draft (macos-14) — the golden path | `Folderify-{v}-arm64.dmg` |
| robogearsDownloader | Electron (vanilla JS; builder config **embedded in package.json**) | `package.json` **+** `chrome-extension/manifest.json` | **`npm run sync-ext`** — mandatory; skipping it recreates the extension version-mismatch bug | tag-CI draft (win + mac matrix). Empty-body failure mode lives here — a release job without `actions/checkout` ships a blank body; **verify step 5 every ship**, fix the workflow when touching it | `robogears-downloader-setup.exe`, `robogears-downloader-mac-arm64.dmg` — **fixed names, substring-matched** |
| 3rdPlayerScreen4Rekordbox | Electron/TS (electron-vite, native module) | `package.json` only | none | tag-CI draft (macos-14). **No `workflow_dispatch`** — a failed tag build re-runs only via draft-safe re-tag | `3rd Player Screen-{v}-mac-arm64.dmg` — productName has **spaces**; the name **must keep the `mac-arm64.dmg` substring** (updater contract) |
| FLACtoiPod | Electron (vanilla JS) | `package.json` | none | tag-CI draft (win + mac). Same empty-body failure mode as robogearsDownloader — **verify step 5** | `flac-to-ipod-setup.exe`, `flac-to-ipod-mac-arm64.dmg` — fixed names |
| MusicSorter | Electron/React/TS in **`app/` subdir**, pnpm | `app/package.json` | none (pnpm lock doesn't embed the version) | tag-CI draft, **lineage tag globs** `v0.2.*`, `v0.[3-9].*`, `v[1-9].*.*` — a `v0.1.x` tag builds **nothing** (legacy Python lineage) | `MusicSorter-{v}-arm64.dmg`, `MusicSorter-{v}-setup.exe` — versioned |
| cockpit-anchor | C++ OpenXR DLL + Electron UI in **`ui/`** | `ui/package.json` — repo root has none | none | tag-CI draft (Windows, two-stage native + Electron single job) | `CockpitAnchor-Setup.exe` — fixed |
| VirtualMirage | C#/.NET 8 WinForms + Inno Setup | `src/VirtualMirage/VirtualMirage.csproj` `<Version>` — CI also stamps `-p:Version=$tag`; **keep csproj and tag identical** | none | tag-CI draft (windows-latest; ISCC compile step) | `VirtualMirage-Setup.exe` — fixed, updater matches `Setup.exe` — + `VirtualMirage-win-x64.exe` portable |
| VibeLight | Swift 6/SwiftUI via XcodeGen | `project.yml` `MARKETING_VERSION` | `xcodegen generate` | **no CI — local build** (Path B), see next section | **TWO assets by contract:** `VibeLight-{v}-arm64.dmg` (first install) + `VibeLight-{v}-arm64.zip` (**the updater consumes the zip** — ship the dmg alone and every installed copy stops updating) |
| VibeDolphin | C++ Dolphin fork (CMake/Qt6) | `Source/Core/Common/Version.cpp` — the `"VibeDolphin X.Y.Z"` string in `GetScmRevStr()`; the updater parses the **last whitespace-delimited token** | none | **no CI — local build** (Path B) | `VibeDolphin.AppImage` — fixed, suffix-matched `.AppImage` |
| VibeEden | C++ yuzu/Eden fork (CMake) | `CMakeModules/GenerateSCMRev.cmake` `VIBEEDEN_VERSION` (single knob — the git-describe logic around it is dead) | none | tag-CI, **but publishes public immediately** (`make_latest: true`, no draft) — the house-rule deviation: a bad tag ships to every user the moment it builds. Treat every tag as a publish; **normalize to draft-first only when the user asks** | `Eden.AppImage` — fixed |
| vibelight-moonlight-helper | C++ Qt qmake fork of moonlight-qt | upstream `app/version.txt` — not fork-versioned | none | **no releases by design.** Push-only: CI builds artifacts on every push; the deliverable rides inside VibeLight.app via its embed script. "Ship" here = push | none |
| odysseus | Python 3.12 FastAPI server (source + docker-compose deploy) | **Dual in-code constants** — `core/constants.py` **and** `src/constants.py` both define `APP_VERSION` and drift when bumped singly; the one the app's version endpoint serves is ground truth. Also an independent `scripts/_lib/cli.py` `VERSION` trail. Reconcile/consolidate **before** the first bump | none | **dev (default) and main carry CI** incl. push-triggered `docker-publish.yml` publishing ghcr images (`:dev` on dev pushes; `:latest` + `:X.Y.Z` on main pushes) — **a plain branch push IS a Docker-channel publish for this repo**. Zero tags, no tag-triggered release workflow, no RELEASE_NOTES.md: first ship = first-release flow + **source-only** tag + `gh release create` | none — auto source tarball only |

**odysseus preflight flag — the multi-account / multi-branch case:** its owner is a
*different* GitHub account (`pewdiepie-archdaemon`) than the rest of the portfolio
(`robogears`), and its default branch is `dev`, not `main`. Step 0's `gh auth status` /
owner-match / release-branch checks exist for exactly this repo — `gh auth switch` to the
owning account and confirm which branch releases cut from **before** any mutating command.

## No-CI repos: what "ship" builds locally

Path B, step 4 of `ship`. Run the repo's documented build — do not improvise one:

| Repo | Local release build | Then |
|---|---|---|
| VibeLight | `xcodegen generate` → Release `xcodebuild` → `scripts/embed-helper.sh` (bundles the moonlight helper — skip it and the app ships hollow) → `scripts/make-dmg.sh` (its header documents the **two-asset contract**: emits the dmg; the zip comes via `ditto`) | `.sha256` sidecars for **both** assets → `gh release create --draft --verify-tag` with dmg **and** zip |
| VibeDolphin | The repo's documented local AppImage build (CMake/Qt6 → linuxdeploy/appimagetool — VibeEden's `appimage.yml` is the in-house template if a workflow is ever wanted, **on request only**). Output must be named exactly `VibeDolphin.AppImage` | sidecar → `gh release create --draft --verify-tag`. **Never cut a real release to test the updater** — that's the `updater-e2e-harness` skill's whole reason to exist |
| odysseus | Nothing to build — source-only. Tag + `gh release create --draft --verify-tag --notes-file RELEASE_NOTES.md` with no asset args; the auto source tarball is the release | verify step 5 expecting **zero assets** (correct for source-only) |

## Monorepos vs subdir manifests

Don't confuse the two:

- **Subdir manifest** (MusicSorter's `app/`, cockpit-anchor's `ui/`): **one** product,
  one `v*` tag stream — the version just lives below the root. Everything in `SKILL.md`
  applies unchanged; only the bump path differs.
- **True monorepo**: multiple independently-released packages. Per-package tags
  **`<pkg>-vX.Y.Z`**; scope every discovery command — `git tag --list '<pkg>-v*'`,
  `git describe --tags --abbrev=0 --match '<pkg>-v*'` — keep **per-package notes files**,
  and give each package's workflow its own tag filter. The single-`v*` recipes applied
  unchanged to a monorepo tag the wrong package's history into the release notes and
  trigger every workflow at once. No portfolio instance yet (verified 2026-07) — treat
  this row as designed-but-unproven and double-check against the repo's own docs.
