# Native Swift / SwiftUI macOS self-updater (the zip-swap recipe)

This is the core spec (SKILL.md §1–10) mapped onto a native Swift 6 / SwiftUI macOS
app — no Electron, no Node, no framework dependency. It is derived from a real
production implementation (the VibeLight pattern: `@Observable` update service,
zip-swap install, `.bak` rollback, translocation-aware destination), upgraded to the
full core-spec contract. Read the core spec first; this file is the verbatim last
mile for Swift.

The headline difference from the Electron path: **the updater consumes a `.zip`, not
the `.dmg`** — every release ships both (the two-asset contract below). The swap
itself is the **same bash relauncher as the Electron reference** — the script has
zero Electron and zero Swift in it, and it is deliberately kept byte-for-byte
portable between the two implementations.

> Cross-references: cutting the release this updater reads is **github-ship** (these
> repos typically release via its **no-CI Path B** — local build + `gh release
> create`); testing the flow without a public release is **updater-e2e-harness**.
> The Electron counterpart of every section here is `references/updater-main.ts`.

## Contents

- [Core spec → Swift, at a glance](#core-spec--swift-at-a-glance)
- [The two-asset release contract (dmg for humans, zip for the updater)](#the-two-asset-release-contract-dmg-for-humans-zip-for-the-updater)
- [The service: an @Observable state machine](#the-service-an-observable-state-machine)
- [Release discovery: URLSession + ETag (§2)](#release-discovery-urlsession--etag-2)
- [Version compare (§3)](#version-compare-3)
- [Asset matcher + SHA-256 sidecar (§4, §5)](#asset-matcher--sha-256-sidecar-4-5)
- [Download: host-pinned, capped, .part semantics (§6)](#download-host-pinned-capped-part-semantics-6)
- [Stage: unpack with ditto, validate identity, codesign-verify](#stage-unpack-with-ditto-validate-identity-codesign-verify)
- [The relauncher — shared logic with the Electron reference](#the-relauncher--shared-logic-with-the-electron-reference)
- [Destination, translocation, canSelfInstall, aftermath (§7)](#destination-translocation-canselfinstall-aftermath-7)
- [The harness override guard (§10)](#the-harness-override-guard-10)
- [Cadence, wake re-check, logging (§8, §9)](#cadence-wake-re-check-logging-8-9)
- [Sparkle 2 — the framework alternative (§11)](#sparkle-2--the-framework-alternative-11)
- [Testing with updater-e2e-harness](#testing-with-updater-e2e-harness)

---

## Core spec → Swift, at a glance

| Core spec | Swift construct |
|---|---|
| Transport (§2, §5) | `URLSession` — system proxy settings and the OS trust store for free (the same reason the Electron path uses `net`, not raw `node:https`; never shell out to `curl`) |
| Release model | `Codable` structs + a **pure, `nonisolated static` parse function** — unit-testable against fixture JSON, no network in tests |
| ETag cache (§2) | `URLRequest.setValue(etag, forHTTPHeaderField: "If-None-Match")`, explicit 304 handling, cache persisted as JSON in Application Support (or `UserDefaults`) |
| Version compare (§3) | pure `static func isNewer(_:than:)`, unit-tested |
| State machine (§6) | `@MainActor @Observable final class UpdateService` with a `Phase` enum; SwiftUI observes `phase` directly — no IPC layer exists or is needed |
| Progress (§6) | `URLSessionDownloadDelegate.didWriteData`, clamped to integer-percent changes |
| Swap (§7) | the shared bash relauncher, written with `FileManager`, launched detached with `Process` |
| Logging (§8) | `~/Library/Logs/<App>/` + `attempts.log` before terminating |
| Cadence (§9) | launch check + 12 h `Timer` + `NSWorkspace.didWakeNotification` |
| Harness (§10) | `#if DEBUG \|\| UPDATER_ALLOW_INSECURE_OVERRIDE=1` gating `UPDATER_API_BASE` |

## The two-asset release contract (dmg for humans, zip for the updater)

**Every release ships two artifacts plus their sidecars, by contract:**

| Asset | Consumer | Purpose |
|---|---|---|
| `App-1.2.3-arm64.dmg` | humans, first install | styled drag-to-Applications installer; the one-time Gatekeeper "Open Anyway" dance happens here |
| `App-1.2.3-arm64.zip` | the in-app updater | in-place swap: extract, verify, replace the bundle |
| `<asset>.sha256` ×2 | the updater (and careful humans) | §5 corruption gate |

**Why the updater eats a zip and not the dmg it already ships:**

- **No mount step.** `hdiutil attach` is the flakiest link in the Electron dmg path:
  mount points, `-nobrowse -noverify` flags, detach that fails while anything holds
  the volume, stale mounted volumes left behind after a crash. A zip has none of
  that — extract to a private staging dir, done, nothing to unmount on any error
  path.
- **`ditto -x -k` preserves the signature.** ditto keeps extended attributes,
  resource forks, and symlinks intact, so the code-signature seal survives
  extraction. A naive unzip can mangle framework symlinks/xattrs and make a
  perfectly good build fail `codesign --verify`.
- **The dmg still exists because a first-time human wants it** — drag-to-
  Applications with the arrow, not a bare zip in Downloads. Machines and humans get
  different artifacts; that's the whole contract.

**Pack the zip with ditto too** (the same recipe Xcode's Organizer and Sparkle use),
so extraction round-trips losslessly:

```bash
ditto -c -k --sequesterRsrc --keepParent App.app "App-${VERSION}-arm64.zip"
```

**Ship side:** repos on this pattern typically have no CI — github-ship's **Path B**
(local build, then `gh release create --draft`). The release script builds the dmg
(`create-dmg`-style script) and the zip is the one `ditto` line above; emit a
`.sha256` for each. **Upload both assets every release, or the updater breaks**: a
dmg-only release means the matcher finds no zip, and every installed copy either
errors ("no macOS download") or routes to the release page — the self-update feature
is dead for that release. Both filenames follow §4: the packer's names and the
matcher below are **one contract split across two places — change one, change the
other in the same commit.**

## The service: an @Observable state machine

One `@MainActor @Observable` class is the whole updater; SwiftUI views observe
`phase` and render the §6 state machine directly (the same states the Electron
`UpdateButton` renders, minus the IPC hop).

```swift
import AppKit
import Foundation
import Observation

@MainActor
@Observable
final class UpdateService {

    struct Release: Equatable, Sendable {
        var version: String       // tag with leading v stripped, e.g. "1.2.3"
        var notes: String         // release body — render "What's new" (§9), zero extra requests
        var releaseURL: URL       // html_url — the route when we can't/shouldn't self-install
        var assetURL: URL?        // nil = no asset for this arch (§4) → UI routes to releaseURL
        var sha256URL: URL?       // nil = old release without a sidecar → TLS floor (§5)
        var assetSize: Int64
    }

    enum Phase: Equatable {
        case idle
        case checking
        case upToDate
        case noReleases                        // 404: brand-new repo, not an error (§2)
        case rateLimited(retryAfterSeconds: Int?)
        case offline                           // distinct from error (§9)
        case available                         // details in `available`
        case downloading(Double)               // 0…1
        case verifying                         // sha gate + unpack + codesign
        case readyToInstall                    // staged; waiting for "Restart to apply"
        case applying
        case failed(String)
    }

    private(set) var phase: Phase = .idle
    private(set) var available: Release?
    @ObservationIgnored private var stagedApp: URL?      // the verified bundle awaiting swap
    @ObservationIgnored private var lastCheck: Date?
    var isQuittingForUpdate = false            // quit interceptors MUST respect this

    var currentVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0"
    }

    /// Silent launch/periodic checks swallow errors (offline is normal); only an
    /// explicit "Check for Updates" surfaces a "you're current" or a failure (§9).
    func check(silent: Bool) async { /* re-entrancy guard, fetchLatest, isNewer */ }

    /// "Update now": download → verify → stage, stop at .readyToInstall.
    /// Re-entrancy guard: .downloading/.verifying/.readyToInstall return early (§6).
    func downloadAndInstall() async { /* below */ }

    /// "Restart to apply": re-validate the stage EXISTS (temp dirs get purged —
    /// if it vanished, reset to .available so the UI re-offers the download,
    /// never hang on "Updating…"), write + launch the relauncher, terminate.
    func installStagedUpdate() { /* below */ }
}
```

Re-entrancy guards are enum-cheap in Swift: `switch phase { case .downloading,
.verifying, .applying: return; default: break }` at the top of each entry point —
one download in flight, one apply in flight (§6).

## Release discovery: URLSession + ETag (§2)

The Codable model and a pure parser (fixture-testable):

```swift
struct GitHubAsset: Codable { let name: String; let browser_download_url: String; let size: Int64? }
struct GitHubRelease: Codable {
    let tag_name: String
    let html_url: String?
    let body: String?
    let assets: [GitHubAsset]
}
```

The check, with the mandatory ETag cache and typed non-200 handling:

```swift
struct CheckCache: Codable { var etag: String; var payload: Data }

private static func fetchLatest() async throws -> GitHubRelease? {
    var req = URLRequest(url: URL(string: "\(apiBase)/repos/\(owner)/\(repo)/releases/latest")!)
    // WE manage the ETag, not URLCache — see the gotcha below.
    req.cachePolicy = .reloadIgnoringLocalCacheData
    req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
    req.setValue("\(appName)-Updater", forHTTPHeaderField: "User-Agent")
    req.timeoutInterval = 15
    let cache = loadCheckCache()               // Application Support/<bundle-id>/update-check.json
    if let etag = cache?.etag { req.setValue(etag, forHTTPHeaderField: "If-None-Match") }

    let (data, response) = try await URLSession.shared.data(for: req)
    guard let http = response as? HTTPURLResponse else { throw UpdateError.network }
    switch http.statusCode {
    case 304:
        // Free (doesn't count against the 60/h quota); reuse the cached body.
        guard let cache else { throw UpdateError.malformed }
        return try JSONDecoder().decode(GitHubRelease.self, from: cache.payload)
    case 200:
        if let etag = http.value(forHTTPHeaderField: "ETag") {
            saveCheckCache(CheckCache(etag: etag, payload: data))
        }
        return try JSONDecoder().decode(GitHubRelease.self, from: data)
    case 404:
        return nil                              // no published release yet → .noReleases, NOT .failed
    case 403, 429:
        throw UpdateError.rateLimited(retryAfterSeconds: retryAfter(from: http))
    default:
        throw UpdateError.http(http.statusCode)
    }
}
```

- **The URLCache gotcha:** by default `URLSession` revalidates cached responses
  *transparently* — it sends `If-None-Match` itself and hands you the cached body
  with a 200, so your 304 branch never runs and your persisted ETag never updates.
  Set `cachePolicy = .reloadIgnoringLocalCacheData` and own the ETag explicitly, as
  above. The explicit cache is also what survives across launches deterministically.
- `retryAfter(from:)` parses `Retry-After` (seconds) or `X-RateLimit-Reset` (epoch →
  seconds from now). Surface `.rateLimited(retryAfterSeconds:)`, never a generic
  "couldn't reach GitHub" (§2).
- **Offline is not an error** (§9): catch `URLError` codes
  `.notConnectedToInternet` / `.networkConnectionLost` / `.dataNotAllowed` →
  `.offline`; everything else → `.failed(message)`.
- Drafts are invisible to `releases/latest` — publishing is github-ship's deliberate
  flip. Never test against a draft; use the harness (§10).

## Version compare (§3)

The full §3 rules, pure and unit-tested. The classic Swift trap is the same as the
JS one: `Int("3-beta".prefix { $0.isNumber })` → 3, which silently offers a
prerelease as an upgrade over its own final release. Split the suffix off first:

```swift
nonisolated static func isNewer(_ candidate: String, than current: String) -> Bool {
    func split(_ s: String) -> (nums: [Int], pre: String?) {
        let v = s.trimmingCharacters(in: CharacterSet(charactersIn: "vV "))
        let halves = v.split(separator: "-", maxSplits: 1)
        let nums = (halves.first ?? "").split(separator: ".").map { Int($0) ?? 0 }
        return (nums, halves.count > 1 ? String(halves[1]) : nil)
    }
    let a = split(candidate), b = split(current)
    for i in 0..<max(a.nums.count, b.nums.count) {
        let x = i < a.nums.count ? a.nums[i] : 0    // missing segments = 0
        let y = i < b.nums.count ? b.nums[i] : 0
        if x != y { return x > y }                   // numeric, never string compare
    }
    switch (a.pre, b.pre) {                          // equal numerics:
    case (nil, .some): return true                   // 1.2.3 beats 1.2.3-rc.1
    default: return false                            // prerelease never beats its release
    }
}
```

Caveat: at equal numerics two prerelease suffixes compare as equal (`rc.2` is never
offered over `rc.1`) — fine for the stable channel; if you enable the §9 prerelease
channel, extend the compare to handle dot-separated prerelease identifiers per
SemVer §11 or use a real semver library.

Gate garbage first: a tag not matching `^v?\d+(\.\d+)*` (`wholeMatch(of:)` with a
regex literal) is logged and ignored, not compared as zeros.

## Asset matcher + SHA-256 sidecar (§4, §5)

Arch is a compile-time fact for a single-arch build:

```swift
nonisolated static var arch: String {
    #if arch(arm64)
    "arm64"
    #else
    "x86_64"
    #endif
}

nonisolated static func matchAsset(in release: GitHubRelease) -> (asset: GitHubAsset?, sha: GitHubAsset?) {
    let exact = release.assets.first { $0.name.hasSuffix("-\(arch).zip") }
    // The ONLY acceptable fallback (§4): a universal binary contains both slices.
    let universal = release.assets.first { $0.name.hasSuffix("-universal.zip") }
    guard let asset = exact ?? universal else { return (nil, nil) }
    let sha = release.assets.first { $0.name == "\(asset.name).sha256" }
    return (asset, sha)
}
```

- **No wrong-arch fallback, ever.** No match → `Release.assetURL = nil` and the UI
  routes to `releaseURL` ("Get vX" → release page), same as the Electron
  `available`-without-`downloadUrl` shape. Do **not** "prefer `-arm64.zip`, else any
  `.zip`": in an arm64-only portfolio that fallback looks harmless right up until an
  x86_64 build exists, and then it silently installs a wrong-arch bundle that
  Gatekeeper reports as "damaged." The harness checklist tests this invariant.
- Sidecar parse is exact-format (`shasum -a 256` output: `<64-hex>  <filename>`):
  take the first whitespace-delimited token, require 64 hex chars, lowercase it.
  Anything else = unreadable sidecar → log a warning and proceed on the TLS floor,
  same policy as a missing sidecar (old release).

Hash with CryptoKit, streaming (never `Data(contentsOf:)` a multi-hundred-MB file):

```swift
import CryptoKit

nonisolated static func sha256(of file: URL) throws -> String {
    var hasher = SHA256()
    let handle = try FileHandle(forReadingFrom: file)
    defer { try? handle.close() }
    while let chunk = try handle.read(upToCount: 4 << 20), !chunk.isEmpty {
        hasher.update(data: chunk)
    }
    return hasher.finalize().map { String(format: "%02x", $0) }.joined()
}
```

**Be honest about what the gate buys (§5):** the sidecar travels the same channel as
the artifact, so a mismatch means **corruption or truncation, not tampering** —
anyone who can substitute the zip can substitute the digest. On mismatch, delete the
download and abort **before staging**, with the suite's canonical message "Checksum
mismatch — download corrupted or tampered" (in docs/UI copy, be honest that a
same-origin sidecar mainly proves corruption). Real tamper resistance is the
minisign/EdDSA rung of the ladder —
which, for Swift, is one of the two things Sparkle actually sells (see below).

## Download: host-pinned, capped, .part semantics (§6)

Use a `URLSessionDownloadDelegate` (it streams to disk; a `data(for:)` call would
buffer the whole artifact in RAM). Key mechanics:

- **Host pin, re-validated on every redirect hop.** Only `https` to `github.com` or
  `*.githubusercontent.com` (http/other hosts only under the §10 guard). GitHub
  bounces asset downloads to its CDN, so the initial pin alone is not enough:

  ```swift
  func urlSession(_ s: URLSession, task: URLSessionTask,
                  willPerformHTTPRedirection response: HTTPURLResponse,
                  newRequest request: URLRequest,
                  completionHandler: @escaping (URLRequest?) -> Void) {
      if let url = request.url, Self.isAllowedDownloadURL(url) {
          completionHandler(request)
      } else {
          completionHandler(nil)     // cancel — refuse an off-GitHub redirect
      }
  }
  ```

- **The `.part` rule, URLSession edition:** the session's own temp file *is* your
  `.part` — it never carries the final name. But it is deleted the moment
  `didFinishDownloadingTo` returns, so `FileManager.moveItem` it to your staging
  name **synchronously inside that callback**, nowhere else. A file under the final
  name is therefore complete by construction.
- **Byte cap.** Kill the task when `totalBytesWritten` exceeds
  `min(max(advertisedSize * 1.1, 500_000_000), 2_000_000_000)`. The advertised size
  rides the same channel as the payload, so clamp the cap — a hostile size field
  must not be able to inflate its own ceiling.
- `config.timeoutIntervalForResource = 300` so a stalled transfer fails instead of
  hanging forever.
- **Progress:** forward `didWriteData` to the `@MainActor` phase only on
  integer-percent changes.
- **Preflights (§6):** before downloading, check ~2.5× the asset size is free
  (`volumeAvailableCapacityForImportantUsageKey` on the temp volume) and that the
  install destination's parent is writable — fail typed ("need ~X MB free"), don't
  quit into a swap that can't succeed.
- **Startup sweep:** on launch, delete `<tmp>/<app>-update-*` staging dirs and zips
  older than ~24 h.

## Stage: unpack with ditto, validate identity, codesign-verify

```swift
private static func unpackAndValidate(zipURL: URL, expectedVersion: String) throws -> URL {
    let fm = FileManager.default
    let dir = fm.temporaryDirectory
        .appendingPathComponent("\(appName)-update-\(UUID().uuidString)", isDirectory: true)
    try fm.createDirectory(at: dir, withIntermediateDirectories: true)
    var success = false
    defer { if !success { try? fm.removeItem(at: dir) } }   // clean up on any throw

    try runTool("/usr/bin/ditto", ["-x", "-k", zipURL.path, dir.path])
    try? fm.removeItem(at: zipURL)

    let apps = try fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)
        .filter { $0.pathExtension == "app" }
    guard let app = apps.first(where: { $0.lastPathComponent == "\(appName).app" }) else {
        throw UpdateError.badPackage("The update didn't contain \(appName).")
    }
    // Identity gate: it must actually be this app, and not older than promised.
    let info = NSDictionary(contentsOf: app.appendingPathComponent("Contents/Info.plist"))
    guard (info?["CFBundleIdentifier"] as? String) == Bundle.main.bundleIdentifier else {
        throw UpdateError.badPackage("The update has an unexpected identity.")
    }
    if let v = info?["CFBundleShortVersionString"] as? String, isNewer(expectedVersion, than: v) {
        throw UpdateError.badPackage("The downloaded build is older than expected.")
    }
    // INTEGRITY ONLY for an ad-hoc app: the seal must be internally consistent
    // (catches a mangled extraction). Any internally-consistent bundle passes —
    // this does NOT establish authenticity, and we re-sign ad-hoc after the swap
    // anyway. Do not relax the TLS/host pin believing codesign backstops it (§1).
    try runTool("/usr/bin/codesign", ["--verify", "--deep", app.path])
    success = true
    return app                                   // held as stagedApp until "Restart"
}
```

If you are Developer-ID-signed + notarized instead of ad-hoc, this is where the §1
upgrade lands: `codesign --verify --deep --strict`, `spctl -a`, and assert your Team
ID — the verify becomes a real authenticity check.

`runTool` is a ~15-line `Process` wrapper: capture stdout+stderr through one `Pipe`,
`waitUntilExit`, throw on non-zero status with the output in the message.

## The relauncher — shared logic with the Electron reference

**This script is the same one `updater-main.ts` writes.** It contains no Electron
and no Swift — it is byte-for-byte portable between the two implementations, and it
is maintained as one piece of shared logic: if you improve it here, port the change
to the Electron reference in the same sitting (and vice versa). Divergence between
the two copies is a bug. Only the *writer* differs: `FileManager` +
`posixPermissions` + `Process` instead of `writeFileSync` + `chmodSync` + `spawn`.

The contract (§6/§7, all mandatory): double-fork so it survives the app quitting;
wait for the old PID and **abort + reopen the old app if it never exits**; back up
to `.bak` and **reopen the intact old app if even the backup step fails** (the part
everyone forgets — never proceed without a rollback path); strip quarantine; re-sign
ad-hoc **and verify, and only then** delete the backup; roll back and relaunch the
old version on any failure; `open` the result. The user must never be left with no
app.

```bash
#!/bin/bash
# <App> self-update relauncher. argv: <old-pid> <staged-app> <target-app>
if [ "$1" != "--daemonized" ]; then
  nohup "$0" --daemonized "$@" </dev/null >/dev/null 2>&1 &
  disown
  exit 0
fi
shift
PID="$1"; NEW_APP="$2"; TARGET="$3"
LOGDIR="$HOME/Library/Logs/<App>"; mkdir -p "$LOGDIR"
exec >>"$LOGDIR/update-$(date +%s).log" 2>&1
set -x
trap "" HUP TERM
BACKUP="${TARGET}.bak"
# 1. Wait <=30s for the app to actually exit. Quit interceptors are the classic
#    cause of a hang here — NEVER swap under a live process; abort and reopen.
for i in $(seq 1 30); do ps -p "$PID" >/dev/null 2>&1 || break; sleep 1; done
if ps -p "$PID" >/dev/null 2>&1; then
  echo "ERROR: old app never exited; aborting"
  open "$TARGET"; rm -f "$0"; exit 1
fi
xattr -dr com.apple.quarantine "$NEW_APP" 2>/dev/null || true
mkdir -p "$(dirname "$TARGET")"
# 2. Back up the old install (a fresh install to /Applications has none). If even
#    the BACKUP fails, reopen the still-intact old app and stop.
if [ -d "$TARGET" ]; then
  rm -rf "$BACKUP" 2>/dev/null
  if ! mv "$TARGET" "$BACKUP"; then
    echo "ERROR: backup failed; aborting"; open "$TARGET"; rm -f "$0"; exit 1
  fi
fi
# 3. Move the new bundle in; restore the backup on failure.
if ! mv "$NEW_APP" "$TARGET"; then
  echo "ERROR: move-in failed, rolling back"
  [ -d "$BACKUP" ] && [ ! -d "$TARGET" ] && mv "$BACKUP" "$TARGET"
  [ -d "$TARGET" ] && open "$TARGET"
  rm -f "$0"; exit 1
fi
# 4. Re-sign ad-hoc, then VERIFY. Moving + de-quarantining invalidated the seal,
#    and an unsigned bundle is "damaged" on Apple Silicon. A failed re-sign must
#    not be swallowed — only a verified bundle earns deleting the backup.
if codesign --force --deep --sign - "$TARGET" && codesign --verify --deep --strict "$TARGET"; then
  rm -rf "$BACKUP" 2>/dev/null
  open "$TARGET"
else
  echo "ERROR: re-sign/verify failed, restoring backup"
  rm -rf "$TARGET" 2>/dev/null
  [ -d "$BACKUP" ] && mv "$BACKUP" "$TARGET"
  [ -d "$TARGET" ] && open "$TARGET"
fi
rm -f "$0"
```

The Swift writer — paths go in as **argv, never string-interpolated into the
script** (spaces and quotes in paths stay safe, and nothing user-controlled can
inject shell):

```swift
private func launchRelauncher(replacing target: URL, with stagedApp: URL) throws {
    let fm = FileManager.default
    let scriptURL = fm.temporaryDirectory
        .appendingPathComponent("\(Self.appName)-relaunch-\(UUID().uuidString).sh")
    try Self.relauncherScript.write(to: scriptURL, atomically: true, encoding: .utf8)
    try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: scriptURL.path)

    let p = Process()                      // posix_spawn under the hood
    p.executableURL = URL(fileURLWithPath: "/bin/bash")
    p.arguments = [scriptURL.path,
                   String(ProcessInfo.processInfo.processIdentifier),
                   stagedApp.path,
                   target.path]
    p.standardInput = FileHandle.nullDevice
    p.standardOutput = FileHandle.nullDevice
    p.standardError = FileHandle.nullDevice
    try p.run()          // the script re-execs itself daemonized (nohup+disown);
                         // that copy is orphaned to launchd and outlives us
    appendAttemptLog(to: target)           // §8 — the line goes in BEFORE we quit
    isQuittingForUpdate = true
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { NSApp.terminate(nil) }
}
```

`NSApp.terminate` runs your `applicationShouldTerminate` /
`windowShouldClose` interceptors — a "close hides the window" or "confirm quit"
interceptor stalls the PID wait and triggers the abort-and-reopen path. Give the
service an explicit `isQuittingForUpdate` flag and make every interceptor respect
it (the Swift analogue of the Electron tray-app gotcha).

## Destination, translocation, canSelfInstall, aftermath (§7)

```swift
var installDestination: URL {
    let fm = FileManager.default
    let bundle = Bundle.main.bundleURL
    // App Translocation: a quarantined app launched from Downloads runs from a
    // READ-ONLY randomized /private/var/.../AppTranslocation/ path. Updating
    // "in place" there is impossible and meaningless — install to /Applications.
    let translocated = bundle.path.contains("/AppTranslocation/")
    if !translocated, fm.isWritableFile(atPath: bundle.deletingLastPathComponent().path) {
        return bundle                                            // normal in-place update
    }
    if fm.isWritableFile(atPath: "/Applications") {
        return URL(fileURLWithPath: "/Applications/\(Self.appName).app")
    }
    return fm.homeDirectoryForCurrentUser
        .appendingPathComponent("Applications/\(Self.appName).app")   // no-admin fallback
}

var canSelfInstall: Bool {
    #if DEBUG
    return false        // dev builds NEVER self-install — degrade to the release page
    #else
    return FileManager.default
        .isWritableFile(atPath: installDestination.deletingLastPathComponent().path)
    #endif
}
```

**The aftermath notice (§7):** after a translocated install lands in
`/Applications`, the stale quarantined copy in Downloads still opens the *old*
version — a support ticket generator. Persist a one-shot flag and show "Moved to
Applications — delete the old copy in Downloads" on the next launch.

When `canSelfInstall` is false, the update UI degrades to "Get vX" →
`NSWorkspace.shared.open(release.releaseURL)` — same routing as the
no-asset-for-arch case. Never attempt a swap from a dev build.

## The harness override guard (§10)

The one predicate, covering **every** request site — the check, the artifact
download, and the sidecar fetch — plus the https/host-pin relaxation:

```swift
nonisolated static var overridesAllowed: Bool {
    #if DEBUG
    return true         // "not a production build"
    #else
    return ProcessInfo.processInfo.environment["UPDATER_ALLOW_INSECURE_OVERRIDE"] == "1"
    #endif
}

nonisolated static var apiBase: String {
    if overridesAllowed,
       let base = ProcessInfo.processInfo.environment["UPDATER_API_BASE"],
       !base.isEmpty {
        return base                          // the updater-e2e-harness mock
    }
    return "https://api.github.com"
}
```

There is deliberately **no** repo override — the harness mock answers
`releases/latest` for any owner/repo, so the shipped owner/repo constants never
change. In a Release
build without the explicit env var, all overrides are **dead** — `#if DEBUG` is
Swift's "not-a-production-build" arm, and `UPDATER_ALLOW_INSECURE_OVERRIDE=1` is the
escape hatch for pointing a *real Release build* at the mock. Never gate on the
presence of `UPDATER_API_BASE` alone; that turns an env var into a remote-code
vector on production installs.

| Stack | `overridesAllowed()` |
|---|---|
| Electron | `!app.isPackaged \|\| process.env.UPDATER_ALLOW_INSECURE_OVERRIDE === '1'` |
| Swift | `#if DEBUG` → true, else `env["UPDATER_ALLOW_INSECURE_OVERRIDE"] == "1"` |

## Cadence, wake re-check, logging (§8, §9)

- **Check on launch (silent) + every 12 h + on wake.** The wake check exists
  because a laptop that sleeps nightly never hits a naive 12 h timer. The gotcha:
  sleep/wake notifications post on **`NSWorkspace.shared.notificationCenter`**, not
  `NotificationCenter.default` — an observer on the default center compiles fine
  and never fires.

  ```swift
  NSWorkspace.shared.notificationCenter.addObserver(
      forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
  ) { _ in
      Task { @MainActor in await updateService.recheckIfStale() }   // > 12h since lastCheck
  }
  ```

  (Optionally record the timestamp on `NSWorkspace.willSleepNotification`;
  `didWake` + a `lastCheck` staleness test is the part that must exist.)
- **Silent when up-to-date**; only the manual "Check for Updates" menu item shows a
  "you're current" confirmation or an error (§9). Optional prefs (`autoCheck`,
  `skippedVersion`, channel) per §9 — a tiny Codable JSON next to the check cache.
- **Log like the app is already gone (§8).** The relauncher writes its step log to
  `~/Library/Logs/<App>/` (survives the app; visible in Console.app), and the app
  appends one `attempts.log` line **before** terminating: timestamp, from-version,
  to-version, staged path, target. When a "the update did nothing" report arrives,
  these two files are the entire investigation.

## Sparkle 2 — the framework alternative (§11)

> **What it buys:** EdDSA (ed25519)-signed appcasts and archives — **real tamper
> resistance**, the §5.3 rung, independent of Apple code signing; **binary delta
> updates** (users download only what changed — decisive for large apps, §9's
> ~100 MB signal); a polished, localized, battle-tested update UI and years of
> edge-case hardening.
>
> **What it costs:** a framework dependency inside your bundle; **EdDSA key
> custody** — lose the private key and existing installs can never verify another
> update; leak it and an attacker can feed your users updates; and appcast
> generation in the release flow. It does work against plain GitHub Releases:
> `generate_appcast --download-url-prefix
> https://github.com/<owner>/<repo>/releases/download/vX.Y.Z/` points enclosure
> URLs at release assets (flag verified in the `generate_appcast` source, 2026-07),
> with `appcast.xml` hosted on GitHub Pages or a raw URL. Dev caveat: debug/ad-hoc
> builds need care with sandboxing/library validation per Sparkle's docs.
>
> **House recommendation:** the hand-rolled recipe in this file stays primary for
> ad-hoc-signed apps — it matches the trust model (TLS floor, no keys to guard,
> §1), needs zero infrastructure beyond GitHub Releases, and is a few hundred lines
> you own end to end. Choose Sparkle when deltas, the polished UI, or genuine
> signature verification matter enough to accept the framework and the key custody.

## Testing with updater-e2e-harness

The harness works for Swift unchanged — **the mock is stack-agnostic** (a
dependency-free local server faking `releases/latest` and serving real asset
files; nothing Electron in it). Never test against a real release, and never
against a draft (draft assets aren't downloadable — §2).

1. **Wire the override** exactly as in the guard section above, then run a Debug
   build with `UPDATER_API_BASE=http://127.0.0.1:8787` (from Xcode: scheme →
   Run → Environment Variables), or a Release build with
   `UPDATER_ALLOW_INSECURE_OVERRIDE=1` added. Caveat: a Debug build covers only
   the check/notify rows — `canSelfInstall` is false under `#if DEBUG`, so every
   case routes to the release page. All install-machinery rows (download, checksum
   gate, rollback, abort-if-alive, stage-vanish, translocation) need a Release
   build launched with `UPDATER_ALLOW_INSECURE_OVERRIDE=1 UPDATER_API_BASE=...`
   from a terminal.
2. **Build the vNext artifact.** The harness's `make-test-dmg.sh` manipulates any
   built `.app` — it is not Electron-specific (bumps `CFBundleShortVersionString`,
   re-signs ad-hoc) — but it deletes its scratch dir on exit; its only outputs are
   the `.dmg` and `.sha256`. For the Swift zip contract, either mount the emitted
   dmg and pack the zip from the `.app` inside it (`ditto -c -k --sequesterRsrc
   --keepParent App.app App-<vNext>-arm64.zip`), or bump/re-sign a copy of the
   `.app` yourself with the same two commands the script uses (PlistBuddy +
   codesign), then pack — plus `shasum -a 256` for the sidecar, named per §4.
3. **Walk the checklist rows** against the mock: exact-arch match; the
   no-fallback routing (serve a release whose only zip is wrong-arch → the UI must
   route to the release page, not download); checksum abort (corrupt the zip, keep
   the sidecar → abort before staging); rollback (break the staged bundle so
   `codesign` re-sign/verify fails → `.bak` restored and the old version reopens);
   abort-if-alive (add a temporary quit interceptor → relauncher must reopen the
   old app, not swap); stage-vanish (delete the staged app before "Restart" → phase
   resets to `.available`); translocation (launch a quarantined copy from
   Downloads → install lands in `/Applications` + aftermath notice).
4. **The release side is github-ship's no-CI Path B**: local build, dmg via the
   release script, zip via ditto, `.sha256` for each, `gh release create --draft
   --verify-tag` with **all four files**. Both assets or the updater breaks —
   that's the two-asset contract, and it's enforced by a human eyeball at
   github-ship step 5 (verify the draft's asset list) every single ship.
