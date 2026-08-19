# Packaging, signing & release CI

This is what produces the `.dmg` (and, if you ship Windows, the NSIS `.exe`) the updater
downloads, and the GitHub release it reads. Get the **filename contract** and the
**ad-hoc signing** right or the updater can't work.

## `electron-builder.yml`

**arm64** dmg for macOS (plus an optional NSIS `.exe` block), no electron-builder
publishing (CI uploads instead), no electron-builder signing (the afterPack hook ad-hoc
signs). The `artifactName` here is the other half of the updater's asset matcher — keep
them in sync (SKILL.md §4: **change one, change the other in the same commit**).

```yaml
appId: com.example.myapp
productName: MyApp
# CI (softprops/action-gh-release) uploads to GitHub; electron-builder must not try to
# publish itself (avoids "GH_TOKEN is not set" on tag builds).
publish: null
# Ad-hoc sign the .app after packing (see build/after-pack.js) so Apple Silicon
# Gatekeeper doesn't reject the unsigned build as "damaged".
afterPack: build/after-pack.js
directories:
  buildResources: build
  output: release
files:
  - out/**/*
  - package.json
mac:
  category: public.app-category.utilities
  icon: build/icon.icns
  # Skip electron-builder's signing phase; after-pack.js ad-hoc signs instead.
  identity: null
  target:
    - target: dmg
      arch:
        - arm64
  # THE CONTRACT: -> MyApp-1.2.3-arm64.dmg  (the updater matches "-${process.arch}.dmg")
  artifactName: ${productName}-${version}-${arch}.${ext}
dmg:
  title: ${productName} ${version}
  artifactName: ${productName}-${version}-${arch}.${ext}
  contents:
    - x: 150
      y: 190
      type: file
    - x: 390
      y: 190
      type: link
      path: /Applications

# ── OPTIONAL: only if you ship Windows. Delete this block otherwise.
# See references/updater-windows.md for the semantics these settings feed.
win:
  target:
    - target: nsis
      arch:
        - x64
  # THE CONTRACT (Windows): -> MyApp-1.2.3-setup.exe
  # (the updater matches endsWith('-setup.exe') — WIN_ASSET_SUFFIX in updater-windows.md)
  artifactName: ${productName}-${version}-setup.${ext}
nsis:
  oneClick: true
  # Per-user install (%LOCALAPPDATA%\Programs) — no UAC prompt, which the silent
  # in-app update depends on. Do NOT set perMachine: true for a self-updating app.
  perMachine: false
  # Auto-run applies to NON-silent installs only; the silent (/S) self-update
  # relaunches the app ONLY when spawned with --force-run — updater-windows.md.
  runAfterFinish: true
```

> **Want Intel or universal too?** Add `x64` (or `universal`) to `mac.target[].arch`.
> The updater's `-${process.arch}.dmg` matcher picks the right dmg **per machine**; an
> arch with no matching asset returns `available` with **no** `downloadUrl`
> (`reason: 'no-asset-for-arch'`) and the UI routes to the release page — there is
> deliberately **no** any-`.dmg` fallback (a wrong-arch install reads as "damaged"; see
> SKILL.md §4). The reference matcher already falls back to `-universal.dmg` after
> the exact-arch miss (safe — a universal binary contains both slices); if you fork
> the matcher, keep that fallback, and change the matcher and the `mac.target` arch
> list **in the same commit**. Notarized builds: remove `identity: null`, set a real
> identity, and add a notarize step — then the after-pack ad-hoc sign is not needed.

## `build/after-pack.js` — ad-hoc signing

Without **any** signature, Apple Silicon Gatekeeper rejects the app as "damaged" (harsher
than the unidentified-developer warning). An ad-hoc signature is free and needs no
Developer ID.

```js
// Ad-hoc code-sign the packaged macOS .app.
const { execSync } = require('node:child_process')
const path = require('node:path')

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') return
  const appName = `${context.packager.appInfo.productFilename}.app`
  const appPath = path.join(context.appOutDir, appName)
  const q = JSON.stringify(appPath) // shell-quote the path
  // Strip extended attributes (FinderInfo/quarantine) that make codesign reject the
  // bundle as "detritus". Harmless on a clean CI runner.
  try {
    execSync(`xattr -cr ${q}`, { stdio: 'inherit' })
  } catch {
    /* ignore */
  }
  execSync(`codesign --force --deep --sign - ${q}`, { stdio: 'inherit' })
  console.log(`[after-pack] ad-hoc signed ${appName}`)
}
```

> **iCloud landmine (local builds only):** if your working copy lives in an
> iCloud-managed Documents folder, local `codesign` fails ("detritus not allowed" —
> FinderInfo xattrs can't be stripped). Build to `/tmp` for local signed builds
> (`electron-builder --mac --dir -c.directories.output=/tmp/build`). CI has no iCloud and
> signs fine.

## `.github/workflows/release.yml`

On a `v*` tag: build per platform, always upload the artifacts as CI artifacts (so manual
runs give test builds), and **only on tags** create a **draft** GitHub release with every
artifact + its `.sha256` sidecar attached and the body from `RELEASE_NOTES.md`.

All actions are **pinned to full commit SHAs** (verified 2026-07) — a mutable tag like
`@v4` can be re-pointed after a compromise; let Dependabot bump the pins.

```yaml
name: Release

on:
  push:
    tags:
      - 'v*'
  workflow_dispatch:

permissions:
  contents: write # only the release job needs this; consider scoping it per-job

jobs:
  build-mac:
    runs-on: macos-14
    steps:
      - name: Checkout
        uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

      - name: Setup Node
        uses: actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e # v6.4.0
        with:
          node-version: 22
          cache: npm

      - name: Install dependencies
        run: npm ci

      - name: Build renderer + main
        run: npm run build

      - name: Package macOS (.dmg, ad-hoc signed)
        run: npx --no-install electron-builder --mac --publish never
        env:
          # No Developer ID — electron-builder skips signing (mac.identity: null);
          # build/after-pack.js ad-hoc signs so Gatekeeper doesn't flag "damaged".
          CSC_IDENTITY_AUTODISCOVERY: 'false'

      # Upload ONLY the dmg — not the .dmg.blockmap electron-builder drops next to it
      # (electron-updater's differential-download index; this whole-file updater never
      # reads it, so it's release-page clutter). The path filter keeps it out.
      - name: Upload build artifacts
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: mac-artifacts
          path: release/*.dmg
          if-no-files-found: error

  # ── OPTIONAL: only if you ship Windows (win/nsis blocks in electron-builder.yml).
  # Delete this whole job AND its entry in the release job's `needs:` otherwise.
  # If you keep it, this job existing is what puts the .exe (and, via the release
  # job below, its .sha256 sidecar) on the release — an updater whose Windows SHA
  # gate "never fires" usually traces back to a workflow that only ever built macOS.
  build-win:
    runs-on: windows-latest
    steps:
      - name: Checkout
        uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

      - name: Setup Node
        uses: actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e # v6.4.0
        with:
          node-version: 22
          cache: npm

      - name: Install dependencies
        run: npm ci

      - name: Build renderer + main
        run: npm run build

      - name: Package Windows (NSIS .exe, unsigned)
        run: npx --no-install electron-builder --win --publish never

      # Same filter idea: *.exe only (the NSIS target emits an .exe.blockmap too).
      - name: Upload build artifacts
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: win-artifacts
          path: release/*.exe
          if-no-files-found: error

  # Only draft a GitHub Release on a version tag — manual/branch runs stop at the
  # CI artifacts above.
  release:
    if: startsWith(github.ref, 'refs/tags/v')
    needs: [build-mac, build-win] # drop build-win here if you deleted that job
    runs-on: ubuntu-latest
    # For the optional provenance step below, extend this job's permissions:
    #   permissions: { contents: write, id-token: write, attestations: write }
    steps:
      # REQUIRED — body_path reads RELEASE_NOTES.md from THIS job's workspace.
      # Without a checkout the release body ships empty, every release. See below.
      - name: Checkout
        uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

      - name: Download build artifacts
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          path: dist
          merge-multiple: true

      # ARTIFACT-AGNOSTIC on purpose: EVERY artifact in dist/ gets a sidecar — dmg,
      # exe, AppImage, whatever. A `for f in *.dmg`-shaped loop silently ships any
      # non-dmg artifact WITHOUT a sidecar and that platform's SHA gate never fires.
      # Runs in bash on Linux so sidecars are plain ASCII (see the Windows-runner
      # encoding gotcha in the notes). Format "<hex>  <filename>" is the contract
      # fetchExpectedSha parses.
      - name: Generate checksums
        run: |
          cd dist
          for f in *; do
            case "$f" in *.sha256) continue ;; esac
            [ -f "$f" ] && shasum -a 256 "$f" > "$f.sha256"
          done

      - name: Create draft release
        uses: softprops/action-gh-release@718ea10b132b3b2eba29c1007bb80653f286566b # v3.0.1
        with:
          draft: true
          name: ${{ github.ref_name }}
          body_path: RELEASE_NOTES.md
          files: dist/*
          # Fail LOUDLY when an expected artifact is missing — a draft with a silently
          # absent asset passes CI and only surfaces at github-ship's step-5 verify.
          fail_on_unmatched_files: true

      # ── OPTIONAL (public repos): signed build provenance for every artifact.
      # Needs the id-token/attestations permissions noted above. Users verify with:
      #   gh attestation verify <file> -R <owner>/<repo>
      # - name: Attest build provenance
      #   uses: actions/attest-build-provenance@0f67c3f4856b2e3261c31976d6725780e5e4c373 # v4.1.1
      #   with:
      #     subject-path: dist/*
```

> **Why the release job needs `actions/checkout` (above).** `body_path: RELEASE_NOTES.md`
> reads the file from the job's workspace. If the release job doesn't check out the repo,
> that file doesn't exist and the release body ships **empty** — the "softprops always
> blanks the body" myth that has cost real manual `gh release edit` steps on every ship.
> Keep the checkout step; `github-ship` documents this as the permanent one-line fix.

Notes:

- **Action pins** (verified 2026-07): the SHAs above are the current portfolio pins.
  softprops v3.x runs on the Node 24 runtime (v2.6.2 was the last Node-20 line); its
  inputs — including `draft` and `body_path` — are unchanged v2→v3, so bumping the pin
  is a no-op behaviorally. upload-artifact (v7) and download-artifact (v8) version
  independently on purpose; that major-number mismatch is not a mistake.
- **Windows-runner sidecar gotcha:** if you ever generate sidecars in the `build-win`
  job instead of the central release job, do **not** use PowerShell `>` redirection —
  it writes **UTF-16LE with a BOM**, and the updater's first-token hex parse then fails
  every sidecar as "unreadable" (or, worse, treats a valid download as corrupted). Use
  `certutil -hashfile <file> SHA256` reformatted to `<hex>  <filename>`, or
  `Get-FileHash … | Out-File -Encoding ascii`. This workflow avoids the trap entirely
  by generating all sidecars in bash on the Linux release job.
- **What the sidecar buys — be honest:** it detects **corruption/truncation, not
  tampering** (it rides the same channel as the artifact). That's rung 2 of the
  integrity ladder in SKILL.md §5; minisign detached signatures are the tamper-resistant
  rung 3.
- **`.blockmap` files** (dmg and exe): electron-builder emits them for
  electron-updater's differential downloads. This custom whole-file updater never uses
  them — the upload path filters above keep them off the release page. (If you were
  using `electron-updater` instead, you'd keep them.)

## `RELEASE_NOTES.md`

CI uses this file **verbatim** as the release body (`body_path`), so it must exist in the
repo and be rewritten per release. The **format and template are owned by the
`github-ship` skill** (`references/release-notes-template.md`) — the single source of
truth, so this skill doesn't keep a second copy that could drift. The one updater-specific
thing to keep in your notes: an **Install / update** section telling existing users to
click the in-app **Update** button and telling fresh users how to get past Gatekeeper on
the un-notarized `.dmg` (and past SmartScreen on the unsigned `.exe` — see
`updater-windows.md`).

## Release checklist (the sequence that ships a working update)

1. Bump the version in `package.json` (and `package-lock.json` via
   `npm install --package-lock-only`). Keep any platform version (e.g. iOS
   `MARKETING_VERSION`) in lockstep if you have one.
2. Rewrite `RELEASE_NOTES.md` for the new version.
3. Commit, push `main`, tag `v<version>`, push the tag. **The commit message must
   explicitly explain what changed** — see "Commit messages" below; a bare
   `vX.Y.Z` line is not enough.
4. Watch CI. **Verify the draft has a non-empty body and every expected artifact with
   its sidecar** — the correctly-named `*-arm64.dmg` (+ `*-setup.exe` if you ship
   Windows), each with an `<asset>.sha256` next to it. An empty body means the release
   job lost its checkout (see above); a missing sidecar means the checksum loop wasn't
   artifact-agnostic.
5. **Publish the draft** — `gh release edit v<version> --draft=false --latest`. Only now
   does `releases/latest` return it and does the in-app updater see it.
6. Existing users on a self-updating version get the "Update to v<version>" pill within a
   few seconds of their next launch (or immediately via a manual "Check for updates").

## Commit messages — always explain what changed

Every commit (both plain code commits and the release commit) must **describe what
actually changed and why**, not just restate the version. Someone reading `git log` — or
you, debugging a broken update six months later — should understand the change without
opening the diff. This matters doubly for updater work: when an update misbehaves in the
field, the commit history is often the fastest way to see which release introduced it.

Write a concise summary line, then a body of 2–4 short paragraphs (or tight bullets)
covering **what** changed and **why**; include the **root cause** if it's a bug fix. End
with the `Co-Authored-By` line for whatever agent/model is running.

```bash
git commit -m "$(cat <<'EOF'
vX.Y.Z: <one-line summary of the release's theme>

- <feature/fix>: what it does and why it matters.
- <another change>: the reasoning behind it.
- <bug fix>: the root cause and how this addresses it.

Co-Authored-By: <your agent/model name> <noreply@anthropic.com>
EOF
)"
```

**Bad** (says nothing): `git commit -m "v0.1.6"` or `git commit -m "updates"`.

**Good** (explains the change):
```
v0.1.6: macOS Now Playing + persistent shuffle/repeat

- Now Playing: wire the player into Control Center / media keys / AirPods via
  the Web MediaSession API — title, artist, album, artwork, scrubber, transport.
- Shuffle & repeat now persist across launches (localStorage) instead of
  resetting to off every time — they were in-memory only.
```

If a single release bundles distinct areas of work, prefer several well-described
commits over one vague one; the release tag ties them together.
