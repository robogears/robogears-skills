# .NET / native Windows self-updater (Inno Setup reinstall + rename-aside)

This is the **native Windows** branch of the `github-updater` skill: C#/.NET apps (the
primary case), any installer-based native Windows app, and installer-less exes/CLIs that
must replace themselves. Derived from a real production updater — VirtualMirage's .NET 8
WinForms tray app, shipped as a single-file self-contained win-x64 exe inside an Inno
Setup per-user installer (`Update/Updater.cs`, `Update/UpdateController.cs`,
`UI/UpdateForm.cs`, `installer/VirtualMirage.iss`).

Read the core spec in `SKILL.md` first — everything there (ETag'd discovery, version
compare, asset contract, SHA-256 gate, the state machine) applies unchanged. This file is
the .NET last mile. The Electron/NSIS twin is `references/updater-windows.md`; its
SmartScreen/AV section is shared reality for this file too. Cutting the release this
updater consumes is `github-ship`; testing without a public release is
`updater-e2e-harness`.

## Contents

- [Core spec → .NET (transport, ETag, JSON)](#core-spec--net-transport-etag-json)
- [Version: assembly vs tag — the dual-version wrinkle](#version-assembly-vs-tag--the-dual-version-wrinkle)
- [The asset-name contract (fixed unversioned installer)](#the-asset-name-contract-fixed-unversioned-installer)
- [Download to temp + the SHA-256 gate](#download-to-temp--the-sha-256-gate)
- [The swap: Inno Setup silent reinstall](#the-swap-inno-setup-silent-reinstall)
- [What the .iss must contain (relaunch + per-user)](#what-the-iss-must-contain-relaunch--per-user)
- [The state machine on WinForms (UI-thread gotchas)](#the-state-machine-on-winforms-ui-thread-gotchas)
- [Velopack — weigh it first for NEW .NET apps](#velopack--weigh-it-first-for-new-net-apps)
- [No installer? Rename-aside self-replace (exes + CLIs)](#no-installer-rename-aside-self-replace-exes--clis)
- [SmartScreen, logging, and the harness contract](#smartscreen-logging-and-the-harness-contract)

---

## Core spec → .NET (transport, ETag, JSON)

The mapping is pleasant — .NET's stack gives you for free what Electron has to opt into:

| Core spec requirement | .NET |
|---|---|
| Transport honoring system proxy + OS cert store | `HttpClient` (default handler: system proxy via `HttpClient.DefaultProxy`, SChannel → Windows cert store). No `net`-module workaround needed. |
| JSON parsing, no deps | `System.Text.Json` — `JsonDocument.Parse` for a read-only walk |
| ETag caching (mandatory, §2) | `HttpRequestMessage` + `Headers.IfNoneMatch`; `HttpStatusCode.NotModified` |
| SHA-256 | `System.Security.Cryptography.SHA256` over the file stream |
| Detached swap process | `Process.Start` — a started process is independent on Windows, period |

One **static** `HttpClient` for the app (per-request instantiation exhausts sockets), with
the two headers GitHub requires/expects:

```csharp
private static readonly HttpClient Http = new(new HttpClientHandler
{
    AllowAutoRedirect = false,   // hardened: follow hops MANUALLY so every hop is host-checked (§5.1)
})
{ Timeout = TimeSpan.FromMinutes(10) };            // generous: this client also streams the installer

static Updater()
{
    Http.DefaultRequestHeaders.UserAgent.ParseAdd($"MyApp/{CurrentVersion()}");  // GitHub 403s UA-less requests
    Http.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
}
```

**Host pinning across redirects:** `AllowAutoRedirect = true` silently follows the
`api.github.com → objects.githubusercontent.com` hop with no chance to inspect it. The
hardened shape sets it `false` and loops over `Location` yourself (bounded, ≤5 hops),
resolving relative locations against the current URL and re-checking the host on **every**
hop:

```csharp
private static bool HostAllowed(Uri u) =>
    u.Scheme == Uri.UriSchemeHttps &&
    (u.Host is "api.github.com" or "github.com" or "objects.githubusercontent.com"
     || u.Host.EndsWith(".githubusercontent.com", StringComparison.OrdinalIgnoreCase));

// per hop:  var next = new Uri(current, resp.Headers.Location!);   // resolves relative Location
//           if (!HostAllowed(next)) throw ...;
```

**The ETag'd check** (core spec §2 — a `304` costs zero quota, and the 60/hr
unauthenticated budget is shared by every app on the machine):

```csharp
var req = new HttpRequestMessage(HttpMethod.Get, $"{ApiBase}/repos/{Owner}/{Repo}/releases/latest");
if (cache?.ETag is { Length: > 0 } etag)
    req.Headers.IfNoneMatch.Add(EntityTagHeaderValue.Parse(etag));   // Parse handles GitHub's weak W/"…" tags

using var resp = await Http.SendAsync(req, ct);
if (resp.StatusCode == HttpStatusCode.NotModified) return cache!.Result;   // free; reuse cached verdict
if (resp.StatusCode == HttpStatusCode.NotFound)    return NoReleases();    // brand-new repo — NOT an error
if (resp.StatusCode is HttpStatusCode.Forbidden or (HttpStatusCode)429)
    return RateLimited(RetryAfterSeconds(resp));    // parse Retry-After / X-RateLimit-Reset, surface it
resp.EnsureSuccessStatusCode();
// persist resp.Headers.ETag?.ToString() + the parsed result to %APPDATA%\<App>\update-cache.json
```

Parse the body with `JsonDocument`; keep `tag_name`, `html_url`, `body` (show "What's
new" — §9), and walk `assets[]` for the matcher below. Check on launch + every 12h + on
resume (`SystemEvents.PowerModeChanged`, `PowerModes.Resume` — a laptop that sleeps
nightly never fires a naive 12h timer).

## Version: assembly vs tag — the dual-version wrinkle

The current version comes from the assembly, not a file on disk:

```csharp
public static string CurrentVersion()
{
    var v = Assembly.GetExecutingAssembly().GetName().Version ?? new Version(0, 0, 0);
    return $"{v.Major}.{v.Minor}.{v.Build}";     // AssemblyVersion is 4-part; releases are 3-part
}
```

**The dual-version wrinkle.** The version exists in two places: the csproj
(`<Version>X.Y.Z</Version>`) and the CI stamp
(`dotnet publish -p:Version=$v` where `$v` is the tag with the `v` stripped). **The CI
stamp wins in the shipped binary** — the released exe always self-identifies as its tag,
whatever the csproj said. Keep the csproj bumped in lockstep anyway, in the same commit as
the tag (that's `github-ship`'s bump contract), because:

- a local/dev build otherwise self-reports a stale version and nags about "updating" to
  the version it already is;
- `github-ship`'s mechanical old-version grep keys on the csproj;
- if CI ever loses the `-p:Version` stamp, the csproj silently becomes the shipped truth —
  identical values make that a non-event instead of a broken updater.

**`Version.Parse` pitfalls — never feed it a raw tag:**

- `Version.Parse("0.2.0-rc.1")` **throws** `FormatException`. Strip the prerelease suffix
  at the first `'-'` before any numeric work.
- `Version.Parse("3")` throws too (needs ≥2 components). Tags are user input; parse
  defensively.
- The stamp side is safe: `-p:Version=0.2.0-rc.1` yields `AssemblyVersion 0.2.0.0` (the
  suffix lands in `InformationalVersion` only) — so the *local* side of the compare is
  always purely numeric; only the remote tag needs the `'-'` strip.

The compare itself is the core-spec §3 algorithm — segment-wise numeric, prerelease ranks
below its own release, garbage tags logged and ignored (not compared as zeros):

```csharp
public static bool IsNewer(string remoteTag, string current)
{
    string r = remoteTag.TrimStart('v', 'V');
    int dash = r.IndexOf('-');                    // "0.2.0-rc.1" → compare "0.2.0"; suffix noted
    if (dash >= 0) r = r[..dash];
    if (!Regex.IsMatch(r, @"^\d+(\.\d+)*$")) { Log.Warn($"ignoring garbage tag '{remoteTag}'"); return false; }
    int[] a = r.Split('.').Select(int.Parse).ToArray();
    int[] b = current.Split('.').Select(int.Parse).ToArray();
    for (int i = 0; i < Math.Max(a.Length, b.Length); i++)
    {
        int x = i < a.Length ? a[i] : 0, y = i < b.Length ? b[i] : 0;
        if (x != y) return x > y;
    }
    return false;    // equal numerics: a prerelease NEVER beats its own final release
}
```

## The asset-name contract (fixed unversioned installer)

Same law as everywhere in this skill: **the updater finds its download by file name, and
the artifact name and the matcher are one contract split across two files** — here the
`.iss`'s `OutputBaseFilename`, the C# matcher constant, *and* the CI workflow's `files:`
list (three legs, one contract). Change any one, change all in the same commit.

Both patterns from `updater-windows.md` work here too:

| Pattern | Produces | Matcher | Trade-off |
|---|---|---|---|
| **Fixed** — `OutputBaseFilename=MyApp-Setup` | `MyApp-Setup.exe`, every release | exact literal (or `EndsWith`) | predictable `releases/latest/download/` URL; but downloaded installers from two releases are indistinguishable, no version in the filename |
| **Versioned** — `OutputBaseFilename=MyApp-{#AppVersion}-Setup` | `MyApp-1.2.3-Setup.exe` | `EndsWith("-Setup.exe")` | self-describing in the Releases UI and in Downloads; matcher keys on the stable suffix |

The VirtualMirage-style release carries **two** exes: the installer (`MyApp-Setup.exe`,
primary — what the updater consumes) and a portable single-file build
(`MyApp-win-x64.exe`, secondary manual download). That forces matcher discipline:

- **Match with `EndsWith`, not `Contains`.** A substring like `"Setup.exe"` also matches
  the sidecar `MyApp-Setup.exe.sha256` — depending on asset order you "download" a
  100-byte text file as your installer. `EndsWith("Setup.exe", OrdinalIgnoreCase)` (or the
  exact literal) can't be shadowed.
- The portable exe must not match either — keep `Setup` out of its name.

**The no-fallback rule (core spec §4).** No matching asset ⇒ the check result carries
only the release URL (`Available` without a `DownloadUrl`); the UI degrades to "Get vX" →
opens the release page. **Never** fall back to "any `.exe`" — the portable build is not an
installer, and if you ever ship arm64 Windows, a wrong-arch fallback silently installs a
broken app. Belt-and-braces: before spawning, re-check the downloaded thing is the asset
you matched, not a page-URL fallback (VirtualMirage guards with an `.EndsWith(".exe")`
check and opens the release page otherwise).

## Download to temp + the SHA-256 gate

Stream to a per-user temp path (`Path.GetTempPath()` = `%TEMP%`, per-user on Windows) as
`<name>.part`, rename only on success, verify the sidecar **before** anything gets the
real `.exe` name:

```csharp
public static async Task<string?> DownloadVerifiedAsync(string url, string? shaUrl,
    IProgress<(long done, long total)>? progress, CancellationToken ct = default)
{
    string dest = Path.Combine(Path.GetTempPath(), $"MyApp-update-{DateTime.Now:yyyyMMddHHmmss}.exe");
    string part = dest + ".part";
    try
    {
        using var resp = await Http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, ct);
        resp.EnsureSuccessStatusCode();
        long total = resp.Content.Headers.ContentLength ?? 0;
        await using (var src = await resp.Content.ReadAsStreamAsync(ct))
        await using (var dst = new FileStream(part, FileMode.Create, FileAccess.Write, FileShare.None))
        {
            var buf = new byte[1 << 16]; long done = 0; int n, lastPct = -1;
            while ((n = await src.ReadAsync(buf, ct)) > 0)
            {
                await dst.WriteAsync(buf.AsMemory(0, n), ct);
                done += n;
                int pct = total > 0 ? (int)(done * 100 / total) : -1;
                if (pct != lastPct) { progress?.Report((done, total)); lastPct = pct; } // integer-% throttle (§6)
            }
        }

        if (shaUrl is not null)
        {
            string body = await Http.GetStringAsync(shaUrl, ct);        // "<hex>  <filename>" (shasum format)
            string expected = body.Trim().Split(' ', '\t')[0];
            string actual = await Sha256HexAsync(part, ct);
            if (!actual.Equals(expected, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("checksum mismatch — refusing to run the download");
        }
        else Log.Warn("release has no .sha256 sidecar — proceeding on the TLS floor");

        File.Move(part, dest);              // only a COMPLETE, verified file gets the runnable name
        return dest;
    }
    catch (Exception ex)
    {
        Log.Error("update download failed", ex);
        try { File.Delete(part); } catch { }
        return null;
    }
}

static async Task<string> Sha256HexAsync(string path, CancellationToken ct)
{
    await using var fs = File.OpenRead(path);
    using var sha = SHA256.Create();
    return Convert.ToHexString(await sha.ComputeHashAsync(fs, ct)).ToLowerInvariant();
}
```

Same honesty as everywhere in this skill: **the sidecar travels the same channel as the
artifact, so it detects corruption/truncation, not tampering** — anyone who can swap the
exe can swap the digest. Real tamper resistance is a detached minisign signature (core
spec §5.3). A missing sidecar (old release) is a warning, not a failure. Sweep stale
`MyApp-update-*.exe` / `*.part` older than ~24h from `%TEMP%` on startup.

## The swap: Inno Setup silent reinstall

The .NET installer story is the shortest last mile in this skill — hand everything to the
installer and get out of the way:

```csharp
public static bool ApplyUpdate(string setupPath)
{
    try
    {
        File.AppendAllText(AttemptsLog,        // %APPDATA%\<App>\attempts.log — see logging section
            $"{DateTime.UtcNow:o} apply {CurrentVersion()} -> {pendingVersion} via {setupPath}\r\n");
        Process.Start(new ProcessStartInfo
        {
            FileName = setupPath,
            Arguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART",
            UseShellExecute = false,
        });
        return true;                            // caller now calls Application.Exit()
    }
    catch (Exception ex)
    {
        Log.Error("failed to launch the update installer", ex);
        return false;                           // caller STAYS in Ready — "Restart to apply" survives for a retry
    }
}
```

Why each piece:

- **The installer outlives the app — for free.** On Windows a `Process.Start`ed process
  is fully independent of its parent; there is no detach/unref dance (that's an
  Electron/libuv concern, not a .NET one). Order still matters: **spawn first, exit
  second** — `Application.Exit()` is what releases the lock on your own exe so the
  installer's file copy can succeed.
- **`UseShellExecute = false`** (the .NET Core+ default): direct `CreateProcess`. If the
  installer *would* need elevation it fails fast with a `Win32Exception`
  (`ERROR_ELEVATION_REQUIRED`, 740) — a clean typed failure your Ready state survives —
  instead of a surprise UAC consent dialog in the middle of a "silent" update. With a
  `PrivilegesRequired=lowest` installer (next section) elevation is never needed, so this
  is purely a fail-fast guard.
- **`/VERYSILENT`**, not `/SILENT` — `/SILENT` still shows a progress window.
  `/SUPPRESSMSGBOXES` auto-answers any message box; `/NORESTART` forbids a machine
  reboot. This is Inno's counterpart to NSIS's `/S`.
- **Launch failed ⇒ don't exit.** `ApplyUpdate` returning `false` leaves the state
  machine in Ready so the user can retry — never `Application.Exit()` after a failed
  spawn, or the app just quits and nothing updates.
- **`Application.Exit()` runs `FormClosing` handlers.** A tray app's close-to-tray
  interceptor must cancel **only** `CloseReason.UserClosing` — if it cancels
  `ApplicationExitCall` too, the app never exits and the update stalls. The installer's
  `CloseApplications=yes` (Restart Manager) is the backstop that closes a lingering
  process, but a clean self-exit is the happy path.

## What the .iss must contain (relaunch + per-user)

Two load-bearing decisions live in the Inno script, and both differ from NSIS.

**1. Per-user install — the UAC-free requirement:**

```iss
[Setup]
; lowest => Setup NEVER elevates: no UAC prompt on install OR on silent auto-update.
; {autopf} then resolves to {userpf} = %LocalAppData%\Programs, a user-writable location.
PrivilegesRequired=lowest
DefaultDirName={autopf}\MyApp
CloseApplications=yes                 ; Restart Manager closes a still-running app before file copy
CloseApplicationsFilter=MyApp.exe
RestartApplications=no                ; relaunch is handled explicitly in [Run], below
```

**`PrivilegesRequired=lowest` is what makes unattended silent updates possible.** An
admin-installing setup pops UAC (or fails) in the middle of every silent update — the
Inno analogue of `updater-windows.md`'s `perMachine: false` rule. Per-user installs land
in `%LocalAppData%\Programs\<App>\`.

**2. Relaunch — authored in the script, not passed on the command line.** This is the key
NSIS↔Inno difference: **NSIS relaunch after a silent install is a command-line flag
(`--force-run` — see `updater-windows.md`); Inno relaunch is whatever the `.iss` `[Run]`
section says.** `postinstall` entries are Finished-page checkboxes and never run in a
silent install (`skipifsilent` makes that explicit); the silent-update relaunch must be
its own plain entry gated on `WizardSilent`:

```iss
[Run]
; Fresh interactive install only: one-time side effects (e.g. autostart registration).
Filename: "{app}\MyApp.exe"; Parameters: "--set-autostart"; Flags: runhidden waituntilterminated; Tasks: autostart; Check: not WizardSilent
; Interactive install: the optional "Launch MyApp" checkbox on the Finished page.
Filename: "{app}\MyApp.exe"; Description: "Launch MyApp"; Flags: nowait postinstall skipifsilent
; Silent (auto-update) install: THE RELAUNCH. Without this line a /VERYSILENT update
; leaves the app closed — the Inno analogue of NSIS missing --force-run.
Filename: "{app}\MyApp.exe"; Flags: nowait; Check: WizardSilent
```

Three entries, three jobs: fresh-install side effects (`Check: not WizardSilent` so
updates don't re-run them), the interactive launch checkbox, and the silent relaunch.
Because Setup itself runs as the signed-in user (never elevated), the relaunched app and
any HKCU writes land in the right user context with no `runasoriginaluser` gymnastics.

CI compiles the installer with the tag's version:
`ISCC.exe /DAppVersion=$v installer\MyApp.iss` after
`dotnet publish -r win-x64 --self-contained -p:PublishSingleFile=true -p:Version=$v`
(see the VirtualMirage-shaped workflow in `github-ship`; draft-first, `body_path:
RELEASE_NOTES.md`, and remember the ISCC step can fail independently of the publish —
check its exit code).

## The state machine on WinForms (UI-thread gotchas)

The core-spec §6 machine, with the WinForms specifics that were learned the hard way:

- **One lock-guarded state enum** (`Idle → Checking → Available → Downloading → Ready →
  Restarting`) in a single controller; clicks during transitional states are ignored —
  that's the re-entrancy guard.
- **Tray context menus close the instant they're clicked**, which hides a
  check→download→install sequence mid-flight. Pair the at-a-glance tray menu item with a
  **persistent modeless status form** the user opens from it; the form's `FormClosing`
  cancels `CloseReason.UserClosing` and hides instead (and *only* that reason — see
  above).
- **`Progress<T>` captures the SynchronizationContext it's constructed on** — build it on
  the UI thread and its callbacks marshal back automatically. Run the download itself via
  `Task.Run` so a multi-MB transfer never blocks the UI thread.
- **Discard late progress reports once the state has left `Downloading`** — a trailing
  100% callback otherwise overwrites "Restart to apply" right after it appears.
- Launch check is silent; only an actual `Available` surfaces a notification. Only the
  manual "Check for updates" shows a "you're up to date" confirmation (§9).

## Velopack — weigh it first for NEW .NET apps

For a **new** .NET desktop app, evaluate **Velopack** before hand-rolling (verified
2026-07: active, v1.2.x, Squirrel.Windows' successor — same author lineage as
Clowd.Squirrel, migrates Squirrel apps automatically):

- Client side is a few lines: `UpdateManager` with
  `Velopack.Sources.GithubSource(repoUrl, accessToken, prerelease)`. The `prerelease`
  flag is your channel switch — `false` filters prereleases (stable channel), `true` is
  the beta channel (core spec §9's `channel` pref, built in).
- **Delta updates and rollback are built in** — the answer to core spec §9's "artifact
  over ~100 MB" threshold that whole-file hand-rolled downloads can't match.
- Ship side slots straight into `github-ship`'s CI: `vpk download github` (fetches the
  previous release so the delta can be computed) → `vpk pack` → `vpk upload github
  --repoUrl … --tag vX.Y.Z`. **Omit `--publish`** — it flips the release public at upload
  time; leaving it off keeps the house draft-first flow, and publishing stays
  `github-ship`'s deliberate step.
- **Token warning:** a `GithubSource` access token compiled into a client binary is
  extractable by anyone with the exe. Private-repo update feeds are only viable for
  trusted/internal users — **not** for public distribution. Public apps: public repo, no
  token (which the unauthenticated `releases/latest` check requires anyway).

**House rule: existing apps keep the hand-rolled Inno flow** — it matches the unsigned
trust model, has zero infrastructure beyond GitHub Releases, and is what these references
document. New .NET apps should weigh Velopack first and fall back to this file's recipe
when a framework dependency isn't wanted.

## No installer? Rename-aside self-replace (exes + CLIs)

For a bare exe or CLI distributed without an installer, the enabling fact is:
**Windows locks a running exe against write and delete — but not against rename.** The
lock binds the open file object, not its directory entry. So the swap is: rename the
running exe aside, put the verified new one at the original path, clean up on next start.
This is exactly how rustup self-updates on Windows, and the pattern under the
go-selfupdate-style libraries behind many single-binary CLIs.

```csharp
// newExe must ALREADY be SHA-verified, and staged on the SAME volume as the target
// (write it beside the target, not in %TEMP% — a cross-volume File.Move is a copy,
// not a rename, and loses the atomicity this trick depends on).
static void SelfReplace(string newExe)
{
    string exe = Environment.ProcessPath!;     // .NET 6+: the real running image.
                                               // (Assembly.Location is EMPTY in single-file publish — don't use it.)
    string old = exe + ".old";
    File.Delete(old);                          // stale leftover from a previous update; no-op if absent
    File.Move(exe, old);                       // legal on a RUNNING exe — the lock is on the file, not the name
    try { File.Move(newExe, exe); }
    catch { File.Move(old, exe); throw; }      // rollback — never leave nothing at the exe path (§6)
    Process.Start(exe);                        // GUI: relaunch. CLI: skip and print "update applied — restart".
    Environment.Exit(0);
}

// Call at EVERY startup. Deleting .old often fails on the first post-update launch
// (the old process is still winding down and holds its image) — that's fine; swallow
// it and the next launch gets it. Retry-tolerant by design.
static void SweepOld()
{
    try { File.Delete(Environment.ProcessPath! + ".old"); }
    catch (IOException) { } catch (UnauthorizedAccessException) { }
}
```

Preflight writability of the exe's directory before offering self-install (core spec §6):
an exe run out of `Program Files` without elevation can't stage beside itself — degrade
to "Get vX" → release page, same as every other platform's `canSelfInstall` gate.

## SmartScreen, logging, and the harness contract

**SmartScreen / AV:** everything in `updater-windows.md`'s Gotchas applies verbatim to
unsigned Inno installers and bare exes — the *"Windows protected your PC"* dialog on first
manual install, AV quarantining the downloaded installer before it runs, the whole
reputation story. Read it there; nothing is .NET-specific. The paid fix is Authenticode —
**Azure Artifact Signing** (formerly Azure Trusted Signing; verified 2026-07) is the
subscription route with baseline SmartScreen reputation; if adopted, sign **both** the app
exe and the Setup.exe in CI.

**Log like the app is already gone (core spec §8).** The install runs after your process
exits — a file is the only debuggability. Log to `%APPDATA%\<App>\` and append an
`attempts.log` line **before** `Application.Exit()` (timestamp, from-version, to-version,
installer path — the `ApplyUpdate` sketch above does this). When "the update did nothing"
reports arrive, that line plus Inno's own log (`/LOG="path"` if you want one) is the whole
story.

**Harness contract (core spec §10)** — same env-override semantics, mapped to .NET. One
predicate gates the API base override *and* any http/host-pin relaxation, at **every**
request site (check, artifact download, sidecar fetch):

```csharp
static bool OverridesAllowed()
{
#if DEBUG
    return true;                               // dev builds: harness always allowed
#else
    return Environment.GetEnvironmentVariable("UPDATER_ALLOW_INSECURE_OVERRIDE") == "1";
#endif
}

static string ApiBase =>
    OverridesAllowed() && Environment.GetEnvironmentVariable("UPDATER_API_BASE") is { Length: > 0 } o
        ? o
        : "https://api.github.com";
```

(`Debugger.IsAttached` is an acceptable extra OR-term for interactive debugging.) In a
Release build without the explicit env var, every override is dead — exactly the
`!app.isPackaged || env` semantics on Electron and `#if DEBUG || env` on Swift.

**What to verify on a real Windows box** (via `updater-e2e-harness` — never a throwaway
public release):

1. Fresh install lands in `%LocalAppData%\Programs\<App>\` with **zero** UAC prompts.
2. N → N+1 in-app update: quits, installs, **relaunches by itself** (that's the
   `Check: WizardSilent` `[Run]` line doing its job), no dialogs.
3. The relaunched app's version stamp reads N+1 — a "successful" silent install that
   replaced nothing is the failure mode to watch for.
4. The checksum gate fires: serve a fixture whose bytes don't match the `.sha256` and
   confirm the updater aborts instead of spawning it.
5. An unpackaged/dev build degrades to opening the release page.
