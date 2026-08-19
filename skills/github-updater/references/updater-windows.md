# Windows / NSIS self-updater (the Windows path)

This is the Windows half of the Electron implementation. The macOS path — mount the
`.dmg`, `ditto` the `.app` out, double-fork a bash relauncher that swaps the bundle in
`/Applications` — lives in `references/updater-main.ts`. The Windows path is structurally
simpler but has its own sharp edges: it hands the whole install off to the **NSIS
installer** and gets out of the way.

Everything here stays **symmetric** with the macOS reference on purpose — same ETag'd
`fetchLatestRelease`, same `isNewerVersion`, same host-pinned `.part`-then-rename
`downloadToFile`, same SHA-256 gate, same "download to temp → stage → quit" shape. Only
the last mile (what you do with the downloaded artifact) differs. Read `updater-main.ts`
first; this file documents the Windows **semantics and gotchas**.

> Cross-references: cutting the release the updater reads is **github-ship**; testing this
> flow end-to-end without a public release is **updater-e2e-harness**. This skill is
> **github-updater** (formerly `electron-github-updater` — do not use the old name).

## Contents

- [The one contract you must not break (Windows edition)](#the-one-contract-you-must-not-break-windows-edition)
- [Where the code lives (one file, platform-dispatched)](#where-the-code-lives-one-file-platform-dispatched)
- [Download to temp (same integrity story as macOS)](#download-to-temp-same-integrity-story-as-macos)
- [The install / relaunch sequence](#the-install--relaunch-sequence)
- [Self-swap works — the trap is the relaunch](#self-swap-works--the-trap-is-the-relaunch)
- [If the app never comes back (the 60-second rule)](#if-the-app-never-comes-back-the-60-second-rule)
- [Gotchas](#gotchas)
- [What to verify on a real Windows box](#what-to-verify-on-a-real-windows-box)
- [Code sketch (the win32 branch)](#code-sketch-the-win32-branch)

---

## The one contract you must not break (Windows edition)

The macOS updater finds its download by name: it wants an asset whose name contains
`-${process.arch}.dmg` (e.g. `-arm64.dmg`), and that suffix is a contract with
electron-builder's `dmg.artifactName`. **Windows has the exact same contract, just a
different string.** electron-builder's `nsis.artifactName` names the `.exe`, and your
updater's matcher must agree with it byte-for-byte or updates silently break.

Two real naming patterns exist in the source apps — both work, and the difference matters
for how you write the matcher:

| App | `nsis.artifactName` | Produces | Updater matches on |
|---|---|---|---|
| MusicSorter | `MusicSorter-${version}-setup.${ext}` | `MusicSorter-0.3.1-setup.exe` | `endsWith('-setup.exe')` |
| FLACtoiPod | `flac-to-ipod-setup.exe` (literal, no vars) | `flac-to-ipod-setup.exe` | exact literal name |

Pick one and be deliberate:

- **Versioned name** (`YourApp-${version}-setup.exe`, MusicSorter's style) is the direct
  analogue of the macOS `-${arch}.dmg` contract. The filename changes per release, which
  is why the matcher keys on the **stable suffix** — `endsWith(WIN_ASSET_SUFFIX)` with
  `WIN_ASSET_SUFFIX = '-setup.exe'` — not the whole name. Prefer this — a versioned
  artifact is self-describing in the Releases UI and in a user's Downloads folder.
- **Fixed name** (`your-app-setup.exe`, FLACtoiPod's style) never changes across releases.
  The matcher is the whole literal, and download URLs are predictable. The cost: two
  releases' installers are indistinguishable once downloaded, and you lose the version
  from the filename.

Windows is **x64-only** in both source apps (`win.target: [{ target: nsis, arch: [x64] }]`),
so there is no per-arch fan-out the way macOS has arm64/x64 dmgs. If you ever ship arm64
Windows too, you inherit the macOS rule (SKILL.md §4): match `-x64-setup.exe` vs
`-arm64-setup.exe`, and on a miss return `available` **without** a `downloadUrl` so the
UI routes to the release page — **never** fall back to "any `.exe`", which silently
installs a wrong-arch build. Until then, one `.exe` per release keeps this simple.

**The rule, same as macOS:** if you change `nsis.artifactName`, change the matcher in the
same commit. They are one contract split across two files.

As with macOS, `releases/latest` **ignores drafts** — a user sees the Windows update only
after you flip the release to published (that's a github-ship step, not a code step).

## Where the code lives (one file, platform-dispatched)

The reference `updater-main.ts` is **not** darwin-only — it platform-dispatches
internally, and the win32 branch described in this file lives there:

- `canSelfInstall()` = `(darwin || win32) && app.isPackaged` — one shared gate for both
  platforms; dev sessions and unsupported platforms degrade to "Get vX" → release page.
- `update:download` takes **no args**; main downloads the asset + sidecar pair from its
  own last check state (renderer-supplied URLs were a security hole and a re-check race).
  On win32 the "stage" is simply the verified temp `.exe` — no mount/extract step.
- `update:apply` branches on `process.platform` **inside the handler**: darwin runs the
  dmg relauncher, win32 runs the spawn-detached-installer-and-quit sequence below.

So there is nothing to hand-merge: drop in `updater-main.ts`, and this file plus the
[code sketch](#code-sketch-the-win32-branch) explain what the win32 branch does and why.

## Download to temp (same integrity story as macOS)

Nothing about the download changes on Windows. The same shared `downloadToFile` runs
(Electron `net` transport for proxies/OS certs, host-pinned with redirect re-validation,
`.part` then rename):

- **TLS to GitHub is the integrity floor.** These builds are unsigned (no Authenticode
  certificate — the Windows equivalent of "ad-hoc signed, un-notarized" on macOS), so
  there is no code signature to verify against a trusted identity. The only thing
  guaranteeing the bytes are yours is HTTPS to `github.com` /
  `objects.githubusercontent.com`. Keep the same host pin, reject `http://` and
  non-GitHub hosts, and re-validate on every redirect hop.
- **Apply the SHA-256 sidecar gate identically.** Fetch `<artifact>.sha256`, hash the
  downloaded `.exe`, refuse to spawn it on mismatch. Be honest about what this buys
  (SKILL.md §5): the sidecar rides the **same channel** as the artifact, so it detects
  **corruption/truncation, not tampering** — it turns a truncated or bit-flipped
  download into a clean abort instead of a run of a broken installer with your app's
  privileges. If the sidecar is missing (old release), log a warning and proceed on the
  TLS floor — same policy as macOS. For genuine tamper resistance, that's the minisign
  rung of the ladder (§5.3), not a bigger hash.
- **Sidecar encoding cross-reference:** the `.exe`'s sidecar must be plain ASCII in
  `<hex>  <filename>` format. If your CI generates it on a Windows runner, PowerShell
  `>` writes UTF-16LE and the updater's parse fails every time — see the
  Windows-runner gotcha in `packaging-and-ci.md` (the reference workflow sidesteps it by
  generating all sidecars in bash on a Linux runner).

Write the installer to a **per-user temp path** — `path.join(os.tmpdir(), \`${TMP_PREFIX}-update-${Date.now()}.exe\`)`.
On Windows `os.tmpdir()` is `%TEMP%` (typically `C:\Users\<you>\AppData\Local\Temp`), which
is per-user, so a predictable name there isn't a shared-tmp race. The `.exe` extension is
load-bearing on Windows: you're about to execute this file, and the shell/loader keys off
the extension.

One Windows-specific difference from the macOS path: **there is no mount/extract step.**
On macOS the downloaded `.dmg` is not the thing you run — you mount it and copy the `.app`
out. On Windows the downloaded `.exe` **is** the thing you run. So the staged path is
just the temp `.exe` directly; there is no `mountAndExtractMacDmg` stage.

## The install / relaunch sequence

This is the whole Windows story, and it's short:

1. `spawn(installerPath, ['/S', '--force-run'], { detached: true, stdio: 'ignore', windowsHide: true })`
2. `child.unref()`
3. `app.quit()` (a small `setTimeout` before quit gives the spawn time to fully detach)

Then get out of the way and let NSIS do the rest.

**Why detached + quit, in that order — the core sequencing gotcha.** On Windows you
**cannot overwrite a running `.exe`**: while your app's process holds its own image open,
the file is locked and the installer's file-copy over it fails (`ERROR_SHARING_VIOLATION`).
So the installer process must **outlive the app process** — it has to still be running
after your app has exited so it can replace the now-unlocked files. That is exactly what
`detached: true` + `.unref()` buys you: the installer becomes its own independent process,
not a child that dies when Electron tears down. Then `app.quit()` releases the lock on your
executable. Order matters — spawn the detached installer **first**, quit **second**. If you
quit before the installer is spawned and detached, there's nothing left running to do the
install.

**`/S` is NSIS's silent-install switch** — no wizard, no clicking Next, no user
interaction. The installer runs headless and replaces the app files in place.

**`--force-run` is what brings the app back — `/S` alone does not.** This is the single
most-misdocumented fact on this page's subject, so here is the mechanism (verified
against electron-builder's NSIS templates, 2026-07): the oneClick installer's run-the-app
step is guarded by

```
${ifNot} ${Silent} ${orIf} ${isForceRun}
```

(`installSection.nsh`). Read it literally: a **non-silent** install auto-runs the app —
that is all `runAfterFinish: true` enables — and a **silent** (`/S`) install runs it
**only** when `${isForceRun}` is true, which happens iff the literal `--force-run`
parameter is on the installer command line. `electron-updater` itself appends
`--force-run` to its silent-install args for exactly this reason. Assisted
(non-oneClick) installers are stricter still: they auto-run only when **both** silent
**and** `--force-run` are present.

So the **minimal correct invocation is `['/S', '--force-run']`**. An updater that spawns
bare `['/S']` still swaps the files correctly — but the app stays closed until the user
relaunches it by hand. That relaunch is the counterpart of the macOS relauncher's final
`open "$TARGET"`; skipping `--force-run` is the Windows equivalent of a relauncher that
forgets the `open`.

**How NSIS handles the still-running app.** electron-builder's NSIS installer includes
logic that detects a running instance of the app being replaced and closes it before
copying files (it will, if needed, prompt or force-close). In practice your `app.quit()`
has already exited cleanly by the time the installer reaches the copy step, so there's
nothing to kill — but the installer is robust to the app still lingering. This is why the
flow works even though it looks like you're replacing a program while it's open: you're
not — you quit, and a **separate** process that you deliberately kept alive does the
replacement a beat later.

**The optional marker flag.** You may append `--updated`
(`['/S', '--force-run', '--updated']`): it's purely a signal your own app can read on
next launch (e.g. to show a "You're now on vX" toast) — NSIS ignores unknown args. It is
**not** what triggers the relaunch (only `--force-run` does) and it is not required for
the swap. Include it only if your app does something with it.

## Self-swap works — the trap is the relaunch

Two contradictory beliefs about NSIS self-update show up in real codebases, sometimes in
the same portfolio, and both are wrong in opposite directions:

- *"NSIS can't self-swap — return `false` from `canSelfInstall()` on win32 and open the
  release page instead."* **False as a technical claim.** The sequencing above is the
  whole mechanism: the installer is a separate detached process that outlives the app;
  the app quits, the file lock releases, and the installer overwrites files that belong
  to a process that no longer exists. There is no rule against "reinstalling over a
  running app" because by copy time the app is **not** running. Choosing the
  release-page fallback anyway is a legitimate conservative design (zero moving parts,
  no detached-process lifetime to reason about) — but it's a **choice**, and any comment
  claiming impossibility mislabels it. Don't copy such a comment forward.
- *"Spawn `['/S']` detached, quit, and `runAfterFinish` brings the app back."* **False
  on the relaunch half.** The swap succeeds; the relaunch never happens, because silent
  installs only auto-run with `--force-run` (previous section). This failure is nasty
  precisely because it's invisible in the wrong test: a **manual** (non-silent) install
  auto-runs fine, so the flow looks correct in development — then every real in-app
  update ends with the app quitting and never coming back. The field signature: the
  update *did* apply (the next manual launch is the new version, no errors in any log),
  but the user watched their app close and had to reopen it themselves.

**What to actually do:** self-swap with `['/S', '--force-run']` — one click, no browser
detour, the direct Windows analogue of the macOS self-install this skill is built
around. Keep the release-page fallback as the `canSelfInstall() === false` branch for
builds that genuinely can't self-install (unpackaged dev sessions) and for the
`no-asset-for-arch` routing.

## If the app never comes back (the 60-second rule)

A successful silent update relaunches the app within a few seconds of quit. **If ~60
seconds pass and the app hasn't reappeared, stop waiting** — the installer was
AV-quarantined, hung, or never spawned. There is no in-app process left to watch, so the
watchdog is a *next-launch* protocol built on the §8 logging rule:

- **Before quitting**, append an `attempts.log` line to `app.getPath('userData')`
  (`%APPDATA%\<AppName>\`): timestamp, from-version, to-version, installer temp path.
- **On every launch**, read the last attempt. If one exists and `app.getVersion()` still
  equals its *from*-version, the attempt failed — surface a one-time notice ("The last
  update didn't complete — retry, or download it from the Releases page") instead of
  silently re-showing the same update pill as if nothing happened.
- If `app.getVersion()` equals the *to*-version but the user reports "it quit and never
  came back", that's the **missing `--force-run` signature**: the swap worked and only
  the relaunch was skipped.
- Manual triage for a failed attempt: Task Manager for a lingering installer process,
  the AV quarantine history for the temp `.exe`, and whether the temp `.exe` still
  exists and isn't zero-length.

## Gotchas

These are the Windows-specific non-obvious things, in the same spirit as the macOS gotchas
list. Skip one and it "works on my machine" but fails for a real user.

1. **SmartScreen on unsigned installers.** Without an Authenticode signature (and without
   accumulated download reputation), Microsoft Defender SmartScreen shows a blue
   *"Windows protected your PC"* dialog when the `.exe` runs. The user must click
   **More info → Run anyway**. This hits your users on the **first** install and can
   recur. A silent `/S` spawn from inside the app generally does **not** surface this
   dialog the way a manual double-click does (the mark-of-the-web/reputation context
   differs), which is part of why the in-app self-update path is smoother than "download
   and run it yourself" — but you cannot rely on that, and a fresh manual install
   absolutely will show it. Document the "Run anyway" step for first-time users.
   The only real fix is code signing, and there are two paid routes (verified 2026-07):
   a classic OV/EV certificate (EV gets instant SmartScreen reputation, OV builds it
   over time; org-oriented, hundreds/yr) — or **Azure Artifact Signing** (formerly
   Trusted Signing): ~$9.99/mo on the Basic tier, **accepts individual developers**,
   plugs into `signtool`/CI, and reputation accrues durably to your validated identity
   rather than to a certificate that expires. For a solo developer shipping unsigned
   Electron apps, that subscription is the cheapest exit from SmartScreen-and-AV
   purgatory.

2. **Per-user vs per-machine (`perMachine`) elevation — and why silent + `/allusers`
   don't mix.** Both source patterns use `nsis.perMachine: false` (or the default) →
   **per-user** install into `%LOCALAPPDATA%\Programs\<AppName>\`. Per-user needs **no
   admin rights**, so the silent `/S` install runs without a UAC prompt — exactly what a
   fire-and-forget in-app updater needs. `perMachine: true` (or passing `/allusers` to
   an assisted installer) targets `Program Files` and **requires elevation**: the
   detached silent spawn then either throws a UAC consent dialog at a user with no idea
   why, or fails outright — there is no UAC-friendly path inside a headless `/S` run.
   **Keep `perMachine: false` for a self-updating app**, never pass `/allusers` from the
   updater, and don't change install scope across versions (a per-machine N and a
   per-user N+1 leave two installs side by side).

3. **The installer needs the app closed.** The sequencing gotcha restated as an
   operational fact: the file copy fails if your app still holds its `.exe` open. Your
   `app.quit()` handles this for the in-app flow — but quit interceptors are the classic
   saboteur (tray apps that hide-instead-of-quit on `before-quit`; `beforeunload`
   dialogs). Set an explicit `isQuittingForUpdate` flag your interceptors respect — the
   same rule as macOS gotcha 4 in SKILL.md. If a user manually runs the installer while
   the app is open, electron-builder's NSIS will try to close the running instance
   first; don't put blocking prompts in that path either.

4. **Antivirus false-positives on unsigned exes.** Unsigned Electron installers that spawn
   child processes and write to `AppData` are a common heuristic false-positive for
   consumer AV. Some AV will quarantine the downloaded `.exe` **before** it runs (so your
   spawn silently does nothing), or block the install mid-copy. There's no clean
   programmatic workaround; a code-signing route (gotcha 1) dramatically reduces it. In
   the meantime the [60-second rule](#if-the-app-never-comes-back-the-60-second-rule) is
   your detection story, and the Releases page is the manual fallback — this is where
   the `canSelfInstall() === false` branch earns its keep.

5. **`windowsHide: true` on the spawn.** Without it, the detached installer can briefly
   flash a console window. Set it. (It's harmless on non-Windows since the branch only runs
   on `win32`.)

6. **The `canSelfInstall()` gate, same as macOS.** The shared gate is
   `(darwin || win32) && app.isPackaged`. In dev (`app.isPackaged === false`) there is
   no installed app to replace and no NSIS installer to run — the renderer's button must
   degrade to "Get vX" so you don't try to self-update an `electron .` dev session.

7. **UTF-16 sidecars break the SHA gate.** Restating the cross-reference as a gotcha
   because it presents as a checksum bug: a `.sha256` generated with PowerShell `>` is
   UTF-16LE and the updater can't parse it — every Windows update then runs the
   "sidecar unreadable, skipping verify" branch (or aborts, depending on your parse).
   Generate sidecars in bash on a non-Windows runner, or use `certutil -hashfile` /
   `Out-File -Encoding ascii` with the `<hex>  <filename>` format. Details in
   `packaging-and-ci.md`.

## What to verify on a real Windows box

You cannot convincingly unit-test this — the whole thing is process lifetimes and file
locks. Test the real artifact on a real (or VM) Windows install:

1. Build and install version **N** via the NSIS `.exe` (past SmartScreen — *More info →
   Run anyway*). Confirm it lands in `%LOCALAPPDATA%\Programs\<AppName>\` (per-user).
2. Publish release **N+1** on GitHub with the correctly-named `…-setup.exe` **and its
   `.sha256` sidecar** attached (github-ship), **or** use **updater-e2e-harness** to
   serve a fake `releases/latest` plus a real vNext `.exe` so you don't cut a throwaway
   public release.
3. Launch version N. Within a few seconds the update pill should appear. Click through
   Download → the app should **quit and relaunch as N+1** with no wizard and no clicks.
   **The relaunch is the specific thing to watch** — files updating but the app staying
   closed means the spawn was bare `['/S']` without `--force-run`.
4. If nothing happens ~60 s after quit: the installer either never spawned (check the
   spawn didn't throw), was quarantined by AV, or hit a file lock because quit didn't
   fully exit. Follow the [60-second rule](#if-the-app-never-comes-back-the-60-second-rule)
   triage; confirm the old process is actually gone (Task Manager) and the temp `.exe`
   exists and isn't zero-length.
5. Verify the version stamp inside the relaunched app actually reads N+1 — a silent
   install that "succeeded" but replaced nothing is the failure mode to watch for.
6. Confirm the SHA gate fires: point the harness at a `.exe` whose bytes don't match
   the `.sha256` and confirm the updater aborts **before spawning it**. (Remember what
   this gate is: corruption detection, §5 — the abort-before-spawn is the behavior under
   test, not tamper-proofing.)

Because the install and relaunch happen **after your app is gone**, the pre-quit
`attempts.log` line (§8 and the 60-second rule above) is the only forensic record —
write it every time, not just when debugging.

## Code sketch (the win32 branch)

This is the shape of the win32 branch **inside `updater-main.ts`** — reproduced here so
the Windows semantics are readable in one place, not something you hand-merge.
`fetchLatestRelease`, `isNewerVersion`, `isAllowedDownloadUrl`, `downloadToFile`,
`sha256File`, and `fetchExpectedSha` are the same shared functions the darwin path uses
and are elided.

```ts
import { app } from 'electron'
import { spawn } from 'node:child_process'
import * as os from 'node:os'
import * as path from 'node:path'
import { existsSync, promises as fs } from 'node:fs'

// electron-builder nsis.artifactName contract. Match the STABLE suffix with
// endsWith, so per-version filenames (YourApp-1.2.3-setup.exe) still match.
// If you ship a literal fixed name (your-app-setup.exe), match that exact name.
const WIN_ASSET_SUFFIX = '-setup.exe'

interface GitHubAsset { name: string; browser_download_url: string }
interface GitHubRelease { tag_name: string; html_url: string; assets: GitHubAsset[] }

/**
 * Windows analogue of the macOS `-${process.arch}.dmg` matcher. No matching
 * asset -> return releaseUrl only (the `available`-without-downloadUrl shape,
 * reason 'no-asset-for-arch') so the UI routes to the release page. NEVER
 * fall back to "any .exe" — same invariant as macOS (SKILL.md §4).
 */
function matchWindowsAsset(release: GitHubRelease): {
  downloadUrl?: string
  sha256Url?: string
  releaseUrl: string
} {
  const assets = release.assets || []
  const asset = assets.find((a) => a.name && a.name.endsWith(WIN_ASSET_SUFFIX))
  // Windows is x64-only here, so no arch fan-out. If you ever ship arm64
  // Windows, require the arch token in the suffix — still no blind fallback.
  if (!asset) {
    return { releaseUrl: release.html_url }
  }
  const shaAsset = assets.find((a) => a.name === `${asset.name}.sha256`)
  return {
    downloadUrl: asset.browser_download_url,
    sha256Url: shaAsset?.browser_download_url,
    releaseUrl: release.html_url
  }
}

// canSelfInstall() is the SHARED gate in updater-main.ts:
//   (process.platform === 'darwin' || process.platform === 'win32') && app.isPackaged
// — not a Windows-local helper. Dev sessions never self-install.

/**
 * Download the installer to a per-user temp .exe, verifying its SHA-256 against
 * the sidecar (same gate as the macOS dmg — corruption detection, §5). Main
 * calls this with URLs derived from ITS OWN last check state: update:download
 * takes no args, so a stale renderer snapshot can never mismatch the sidecar.
 * downloadToFile writes `<dest>.part` and renames on success (§6). Returns the
 * temp .exe path — on Windows the download IS the installer; no mount/extract.
 */
async function downloadInstaller(
  url: string,
  sha256Url: string | undefined
): Promise<string> {
  const destPath = path.join(os.tmpdir(), `yourapp-update-${Date.now()}.exe`)
  await downloadToFile(url, destPath, (downloaded, total) => {
    // push 'update:download-progress' (throttled), same as macOS
  })
  if (sha256Url) {
    const expected = await fetchExpectedSha(sha256Url)
    if (expected) {
      const actual = await sha256File(destPath)
      if (actual !== expected) {
        await fs.unlink(destPath).catch(() => {})
        throw new Error('Checksum mismatch — download corrupted or tampered')
      }
    } // else: sidecar missing/unreadable -> proceed on the TLS floor (log a warning)
  }
  return destPath
}

/**
 * The whole Windows install: spawn the NSIS installer DETACHED and SILENT,
 * then quit so the running .exe unlocks and the installer can overwrite it.
 *
 * The installer MUST outlive this process — you cannot overwrite a running
 * .exe, so the file-copy only succeeds after app.quit() releases the lock.
 * detached + unref() makes the installer an independent process.
 *
 * ARGS — the load-bearing part:
 *   '/S'          NSIS silent install. On its own it swaps files but does NOT
 *                 relaunch the app: the oneClick run step is guarded by
 *                 `${ifNot} ${Silent} ${orIf} ${isForceRun}` (installSection.nsh),
 *                 so runAfterFinish only auto-runs NON-silent installs.
 *   '--force-run' sets ${isForceRun} -> the silent install relaunches the app.
 *                 electron-updater passes this for exactly this reason. REQUIRED.
 *   '--updated'   optional marker your own app can read on next launch; NSIS
 *                 ignores unknown args; it does NOT trigger the relaunch.
 */
function runInstallerAndQuit(installerPath: string): { ok: boolean; code?: 'stage-missing' | 'busy'; error?: string } {
  if (!installerPath || !existsSync(installerPath)) {
    return { ok: false, code: 'stage-missing' }
  }
  try {
    const child = spawn(installerPath, ['/S', '--force-run' /* , '--updated' */], {
      detached: true,
      stdio: 'ignore',
      windowsHide: true // no flashing console window
    })
    child.unref() // let the installer survive our exit
    // Append the attempts.log line (timestamp, from/to version, installerPath)
    // to userData BEFORE quitting — see "the 60-second rule" and §8.
    // Give the spawn a moment to fully detach before we release the file lock.
    setTimeout(() => app.quit(), 200)
    return { ok: true }
  } catch (err) {
    return { ok: false, error: (err as Error).message }
  }
}
```

This is what the shared `update:apply` handler in `updater-main.ts` dispatches to:
`darwin` runs the dmg relauncher, `win32` runs `runInstallerAndQuit`. The renderer never
knows the difference — `canSelfInstall()` and the `UpdateCheck` union carry the whole
per-platform story across the IPC boundary.
