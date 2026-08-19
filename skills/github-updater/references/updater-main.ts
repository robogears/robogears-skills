// ============================================================================
// src/main/updater.ts — Electron self-updater from GitHub Releases (macOS + Windows).
//
// The entire feature's brain. Runs in the MAIN process (the only one with disk +
// unrestricted network — the renderer never talks to GitHub, so no CORS/CSP work).
// Registers all update:* IPC and returns { checkAndNotify, stop }.
//
// Platform dispatch lives IN THIS FILE, not in a fork of it: one shared
// check → download → SHA-256 verify pipeline, then a per-platform stage/apply.
//   darwin — mount the dmg, ditto the .app out, hand it to a detached
//            double-forked relauncher that swaps with .bak rollback.
//   win32  — the verified NSIS installer IS the stage; apply spawns it
//            detached with ['/S', '--force-run'] and quits.
// Everything else (transport, ETag cache, version compare, asset contract,
// integrity gate, re-entrancy, temp hygiene, re-check cadence) is identical.
//
// This is the HARDENED default:
//   - Electron `net.request` transport everywhere (system proxy + OS cert store;
//     raw node:https fails forever behind corporate proxies). Redirects are
//     MANUAL: the host is re-validated on every hop before followRedirect().
//   - Host-pinned downloads. http and API-base overrides exist ONLY behind
//     overridesAllowed() — dev build, or an explicit
//     UPDATER_ALLOW_INSECURE_OVERRIDE=1 for the packaged updater-e2e-harness
//     run. A shipped build without that env var can never take the loose path.
//   - ETag-cached checks persisted to userData: a 304 reply costs ZERO of the
//     shared 60/hr unauthenticated quota. 403/429 → 'rate-limited' with a retry
//     time; 404 → 'no-releases'; net.isOnline() false → 'offline'.
//   - SHA-256 sidecar gate before staging; downloads land in *.part and rename
//     on success; disk-space + install-target-writability preflights;
//     re-entrancy guards; startup temp sweep; throttled progress events.
//   - Arch-matched assets with NO wrong-arch fallback (-universal.dmg is the
//     one exception — it contains both slices). Staged-app existence re-checked
//     before apply. The relauncher aborts (and reopens the old app) if the old
//     process never exits, and rolls back on ANY failure — including a failed
//     backup step.
//
// Genericize: set the constants in the CONFIG block. Everything else is reusable.
// Wire it in your main entry (after app ready — `net` needs that too):
//     import { registerUpdater } from './updater'
//     const updater = registerUpdater(() => mainWindow)   // pass a GETTER
//     app.on('before-quit', () => updater.stop())
//     // The renderer invokes update:check itself once it has attached its
//     // listeners (see renderer-and-ipc.md) — no launch setTimeout race. If you
//     // still want a main-driven launch check, call updater.checkAndNotify().
// ============================================================================
import { app, ipcMain, net, powerMonitor, shell, type BrowserWindow } from 'electron'
import crypto from 'node:crypto'
import {
  accessSync,
  appendFileSync,
  chmodSync,
  constants as fsConstants,
  createReadStream,
  createWriteStream,
  existsSync,
  mkdirSync,
  promises as fs,
  readdirSync,
  readFileSync,
  rmSync,
  statfsSync,
  statSync,
  writeFileSync
} from 'node:fs'
import { spawn } from 'node:child_process'
import os from 'node:os'
import path from 'node:path'
import type { Readable } from 'node:stream'
import type { UpdateCheck } from '../shared/models'

// The UpdateCheck union this file returns (single source of truth:
// renderer-and-ipc.md / src/shared/models.ts — reproduced here for reference):
//
//   export type UpdateCheck =
//     | { status: 'available'; version: string; notes?: string; publishedAt?: string;
//         downloadUrl?: string; sha256Url?: string; releaseUrl: string;
//         reason?: 'no-asset-for-arch' }
//     | { status: 'up-to-date'; version: string }
//     | { status: 'no-releases' }
//     | { status: 'rate-limited'; retryAfterSeconds?: number }
//     | { status: 'offline' }
//     | { status: 'error'; message: string }
//
// 'available' with NO downloadUrl (reason: 'no-asset-for-arch') means the UI must
// route to releaseUrl — never self-install a wrong-arch build.

// ── CONFIG: set these for your app ──────────────────────────────────────────
const OWNER = 'your-github-owner' // e.g. 'robogears'
const REPO = 'YourRepo' //           GitHub repo name; releases live at OWNER/REPO
const TMP_PREFIX = 'myapp' //         lowercase slug for temp file/dir names
const LOG_DIR_NAME = 'MyApp' //       ~/Library/Logs/<LOG_DIR_NAME>/ (win32: %APPDATA%\<LOG_DIR_NAME>\)
const RECHECK_INTERVAL_MS = 12 * 60 * 60 * 1000 // periodic re-check cadence (12h)
// Windows asset contract: the NSIS artifact ends with this suffix
// (electron-builder artifactName `${productName}-${version}-setup.exe`). Change
// one side, change the other in the same commit.
const WIN_ASSET_SUFFIX = '-setup.exe'
// Hosts update bytes may come from. GitHub serves the API from api.github.com and
// release assets from github.com → objects.githubusercontent.com; nothing else
// should ever be a target. *.githubusercontent.com is also accepted (CDN shards).
const ALLOWED_HOSTS = new Set(['api.github.com', 'github.com', 'objects.githubusercontent.com'])
// ─────────────────────────────────────────────────────────────────────────────

type AvailableUpdate = Extract<UpdateCheck, { status: 'available' }>

// Staged update (darwin: extracted .app path; win32: verified installer .exe path).
let pendingUpdatePath: string | null = null
// Last "available" result, kept so the renderer can pull it after it finishes
// init() (update:get-pending) — this replaces the old "check 2.5s after launch
// and hope the listeners are attached" race. It is ALSO the only source of the
// {downloadUrl, sha256Url} pair: update:download takes no arguments, so the two
// URLs are always read together from one check result — no renderer-held stale
// URL can ever be verified against a newer check's digest.
let lastAvailable: AvailableUpdate | null = null
// Re-entrancy guards: one download in flight, one apply in flight. A second
// invoke (double-click, second window) returns busy instead of racing.
let downloadInFlight = false
let applying = false
// Set just before we quit for an update. If your app intercepts 'before-quit'
// (tray apps that hide instead of quitting are the classic), your interceptor
// MUST let the quit through when this is true — the relauncher aborts after 30s
// if the process never exits, but don't lean on that safety net.
let quittingForUpdate = false
export function isQuittingForUpdate(): boolean {
  return quittingForUpdate
}
// Epoch ms of the last check that actually reached the network. Drives the
// wake-from-sleep re-check (a laptop that sleeps nightly never hits a naive
// 12h setInterval).
let lastCheckedAtMs = 0

interface GitHubAsset {
  name: string
  browser_download_url: string
}
interface GitHubRelease {
  tag_name: string
  html_url: string
  body?: string //         release notes — shown as "What's new"; already paid for
  published_at?: string
  assets: GitHubAsset[]
}

// ── Override guard (the ONE predicate; see SKILL.md §10) ─────────────────────
/**
 * Every relaxation in this file — the UPDATER_API_BASE override, non-https
 * transport, and host-allowlist widening — is gated by THIS predicate and
 * nothing else. Dev builds get overrides for free; a packaged build must opt in
 * explicitly with UPDATER_ALLOW_INSECURE_OVERRIDE=1 (how the updater-e2e-harness
 * tests the real stage/swap/relaunch path against its local mock). A shipped
 * build without that env var ignores every override — always pinned, always
 * https. There is no other escape hatch (the old per-host
 * UPDATER_ALLOW_INSECURE_HOST mechanism is gone; do not reintroduce it).
 */
function overridesAllowed(): boolean {
  return !app.isPackaged || process.env.UPDATER_ALLOW_INSECURE_OVERRIDE === '1'
}

/** Host (host:port) of the UPDATER_API_BASE override, when the guard allows it. */
function overrideHost(): string | null {
  if (!overridesAllowed()) return null
  const base = process.env.UPDATER_API_BASE
  if (!base) return null
  try {
    return new URL(base).host
  } catch {
    return null
  }
}

/** API base for the release check. Overridable only behind the guard. */
function apiBase(): string {
  if (overridesAllowed() && process.env.UPDATER_API_BASE) {
    try {
      // Validate + normalize so a malformed env var can't throw mid-check.
      return new URL(process.env.UPDATER_API_BASE).toString().replace(/\/+$/, '')
    } catch {
      console.warn('[updater] UPDATER_API_BASE is not a valid URL; using api.github.com')
    }
  }
  return 'https://api.github.com'
}

/**
 * Reject any request target that isn't a pinned GitHub host over HTTPS. Applied
 * to the INITIAL url of all three request sites (check, artifact, sidecar) AND
 * to every redirect hop — a redirect could otherwise bounce us off the pinned
 * set. The one relaxation: when overridesAllowed(), the exact host of
 * UPDATER_API_BASE is accepted (http included) so the e2e-harness mock on
 * 127.0.0.1 can serve the whole flow. Defense-in-depth, not the main lock: main
 * derives every URL itself, so even a compromised renderer has nothing to inject.
 */
function isAllowedDownloadUrl(url: string): boolean {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  const ovr = overrideHost()
  if (ovr && parsed.host === ovr) return true // guard already applied inside overrideHost()
  if (parsed.protocol !== 'https:') return false
  if (ALLOWED_HOSTS.has(parsed.hostname)) return true
  if (parsed.hostname.endsWith('.githubusercontent.com')) return true
  return false
}

// ── Transport (shared by all three request sites) ────────────────────────────
/**
 * GET a URL with Electron's `net` (Chromium networking: system proxy resolution
 * + OS certificate store for free; also speaks plain http, which is what lets
 * the dev-mock override work with no second transport). Redirects are MANUAL:
 * Chromium hands the handler an ABSOLUTE redirect URL (relative Location
 * headers are already resolved against the current hop), we re-validate it
 * against the allowlist, then followRedirect() — which Electron requires to be
 * called synchronously inside the 'redirect' event — or abort.
 *
 * timeoutMs covers time-to-response-headers. Body-stall detection is the
 * caller's job (downloadToFile arms its own inactivity timer).
 */
function guardedGet(
  url: string,
  timeoutMs: number,
  headers: Record<string, string> = {}
): Promise<Electron.IncomingMessage> {
  return new Promise((resolve, reject) => {
    if (!isAllowedDownloadUrl(url)) {
      return reject(new Error('Blocked: host not allowed'))
    }
    const req = net.request({ url, method: 'GET', redirect: 'manual' })
    req.setHeader('User-Agent', `${app.getName()}/${app.getVersion()}`)
    for (const [k, v] of Object.entries(headers)) req.setHeader(k, v)
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      req.abort()
      reject(new Error('Request timed out'))
    }, timeoutMs)
    req.on('redirect', (_statusCode, _method, redirectUrl) => {
      // Re-validate the host on EVERY hop. GitHub's normal flow
      // (github.com → objects.githubusercontent.com) stays inside the pin.
      if (!isAllowedDownloadUrl(redirectUrl)) {
        if (!settled) {
          settled = true
          clearTimeout(timer)
          reject(new Error('Blocked: redirect to disallowed host'))
        }
        req.abort()
        return
      }
      req.followRedirect() // must be synchronous in this handler (Electron contract)
    })
    req.on('response', (res) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(res)
    })
    req.on('error', (err) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      reject(err)
    })
    req.end()
  })
}

/** Drain a (small) response body to a string. Not for artifacts — use downloadToFile.
 *  Arms its own stall timer: guardedGet's timeout only covers time-to-headers, so a
 *  server that sends headers then stalls the body would otherwise hang this forever
 *  (a stalled stream never rejects — it just never settles). */
function readBody(res: Electron.IncomingMessage, timeoutMs = 15_000): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = ''
    const timer = setTimeout(() => {
      // destroy(err) surfaces through the 'error' handler below → reject.
      ;(res as unknown as Readable).destroy(new Error('Body read timed out'))
    }, timeoutMs)
    res.on('data', (d) => (data += d))
    res.on('end', () => {
      clearTimeout(timer)
      resolve(data)
    })
    res.on('error', (e: Error) => {
      clearTimeout(timer)
      reject(e)
    })
  })
}

/** First value of a possibly-multi header, or undefined. */
function headerStr(res: Electron.IncomingMessage, name: string): string | undefined {
  const v = res.headers[name]
  if (Array.isArray(v)) return v[0]
  return typeof v === 'string' ? v : undefined
}

// ── ETag check cache (persisted; survives restarts) ──────────────────────────
// Why this exists: the unauthenticated GitHub API quota is 60 req/h PER IP,
// shared across every app checking from that machine/NAT. A conditional GET
// answered with 304 does NOT count against it — so steady-state checks are free.
// We cache the raw RELEASE (not the derived UpdateCheck): the derivation depends
// on app.getVersion(), and re-deriving on every 304 means a just-updated app
// can never resurrect a stale 'available' for the version it now runs.
interface CheckCache {
  etag?: string
  lastResult?: GitHubRelease
  lastCheckedAt?: number
}
let cacheMem: CheckCache | null = null
function cachePath(): string {
  return path.join(app.getPath('userData'), 'updater-check-cache.json')
}
function loadCache(): CheckCache {
  if (cacheMem) return cacheMem
  try {
    cacheMem = JSON.parse(readFileSync(cachePath(), 'utf8')) as CheckCache
  } catch {
    cacheMem = {}
  }
  return cacheMem
}
function saveCache(patch: Partial<CheckCache>): void {
  cacheMem = { ...loadCache(), ...patch }
  try {
    writeFileSync(cachePath(), JSON.stringify(cacheMem))
  } catch {
    /* a failed cache write only costs quota, never correctness */
  }
}

// getUpdateStatus consumes a discriminated union so every non-happy outcome has
// a distinct, typed rendering ("retry in Xs" ≠ "you're offline" ≠ "no releases").
type FetchResult =
  | { kind: 'ok'; release: GitHubRelease }
  | { kind: 'no-releases' }
  | { kind: 'rate-limited'; retryAfterSeconds?: number }
  | { kind: 'offline' }
  | { kind: 'error' }

/**
 * GET the latest PUBLISHED release (drafts and prereleases are invisible to
 * releases/latest — publishing is github-ship's deliberate flip, not a CI step).
 *
 *  - Sends If-None-Match; a 304 re-uses the cached release at zero quota cost.
 *  - 404 = "no published releases yet" (every fresh repo, every user, until the
 *    first publish) — a status, not an error. A repo gone private also 404s;
 *    unauthenticated checks only work on public repos.
 *  - 403/429 → 'rate-limited' with a retry time parsed from Retry-After /
 *    X-RateLimit-Reset (an unauthenticated check has no credentials to fix, so
 *    "wait and retry" is the only honest advice for any 403).
 *  - Offline is detected up front (net.isOnline()) and reported distinctly.
 */
function fetchLatestRelease(): Promise<FetchResult> {
  return (async (): Promise<FetchResult> => {
    if (!net.isOnline()) return { kind: 'offline' }
    const cached = loadCache()
    const headers: Record<string, string> = { Accept: 'application/vnd.github+json' }
    if (cached.etag) headers['If-None-Match'] = cached.etag
    let res: Electron.IncomingMessage
    try {
      res = await guardedGet(`${apiBase()}/repos/${OWNER}/${REPO}/releases/latest`, 10_000, headers)
    } catch {
      return { kind: 'error' }
    }
    const status = res.statusCode ?? 0
    const body = await readBody(res, 10_000).catch(() => '')
    lastCheckedAtMs = Date.now()
    saveCache({ lastCheckedAt: lastCheckedAtMs })

    if (status === 304) {
      // Not modified: our cached release is still the latest. Zero quota spent.
      if (cached.lastResult) return { kind: 'ok', release: cached.lastResult }
      // Cache file lost its body but kept its etag (corruption/manual edit):
      // drop the etag so the next check refetches unconditionally.
      saveCache({ etag: undefined })
      return { kind: 'error' }
    }
    if (status === 404) return { kind: 'no-releases' }
    if (status === 403 || status === 429) {
      const retryAfter = parseInt(headerStr(res, 'retry-after') ?? '', 10)
      const resetEpoch = parseInt(headerStr(res, 'x-ratelimit-reset') ?? '', 10)
      const retryAfterSeconds = Number.isFinite(retryAfter)
        ? Math.max(0, retryAfter)
        : Number.isFinite(resetEpoch)
          ? Math.max(0, resetEpoch - Math.floor(Date.now() / 1000))
          : undefined
      return { kind: 'rate-limited', retryAfterSeconds }
    }
    if (status !== 200) return { kind: 'error' }
    try {
      const release = JSON.parse(body) as GitHubRelease
      if (!release || !release.tag_name) return { kind: 'error' }
      saveCache({ etag: headerStr(res, 'etag'), lastResult: release })
      return { kind: 'ok', release }
    } catch {
      return { kind: 'error' }
    }
  })()
}

// ── Version compare ───────────────────────────────────────────────────────────
/**
 * Parse "v1.2.3" / "1.2.3-rc.1" (leading v stripped CASE-insensitively — an
 * uppercase 'V1.2.3' tag would otherwise compare as 0.2.3 and hide updates
 * forever). Returns null for garbage tags that don't match ^v?\d+(\.\d+)*.
 */
function parseVersionTag(tag: string): { nums: number[]; prerelease: string | null } | null {
  const s = String(tag).trim().replace(/^v/i, '')
  if (!/^\d+(\.\d+)*(-.+)?$/.test(s)) return null
  const dash = s.indexOf('-')
  const numPart = dash === -1 ? s : s.slice(0, dash)
  const prerelease = dash === -1 ? null : s.slice(dash + 1)
  return { nums: numPart.split('.').map((n) => parseInt(n, 10)), prerelease }
}

/**
 * Numeric segment compare, never string compare ("10" < "9"). Rules learned the
 * hard way:
 *  - A prerelease suffix ranks BELOW its own release at equal numerics: naive
 *    parseInt('3-beta') → 3 silently offers a prerelease as an "upgrade" over
 *    its own final release. We split at the first '-' and, at equal numerics,
 *    only release-over-prerelease counts as newer. (Two prereleases at equal
 *    numerics compare as not-newer — if you run a real prerelease channel, use a
 *    real semver compare instead of growing this one.)
 *  - A garbage tag is logged and IGNORED (returns false) — never compared as
 *    zeros, which would hide real updates behind one bad tag.
 */
function isNewerVersion(remote: string, current: string): boolean {
  const r = parseVersionTag(remote)
  const c = parseVersionTag(current)
  if (!r) {
    console.warn(`[updater] ignoring unparseable remote version tag: ${remote}`)
    return false
  }
  if (!c) {
    console.warn(`[updater] ignoring unparseable current version: ${current}`)
    return false
  }
  const len = Math.max(r.nums.length, c.nums.length)
  for (let i = 0; i < len; i++) {
    const a = r.nums[i] ?? 0 // missing segments = 0 (1.2 === 1.2.0)
    const b = c.nums[i] ?? 0
    if (a > b) return true
    if (a < b) return false
  }
  // Equal numerics: the un-suffixed release outranks its prerelease.
  return c.prerelease !== null && r.prerelease === null
}

// ── Asset matching (THE naming contract; SKILL.md §4) ─────────────────────────
/**
 * Pick the one asset this machine may self-install, by NAME. The packager's
 * artifactName and this matcher are one contract split across two files — change
 * one, change the other in the same commit.
 *
 * NO wrong-arch fallback: on an x64 Mac, "any .dmg" would silently self-install
 * an arm64 binary (or vice-versa) and produce a "damaged"/won't-launch app after
 * the swap. If nothing matches, the caller returns 'available' WITHOUT a
 * downloadUrl and the renderer routes to the release page. The ONLY acceptable
 * fallback is -universal.dmg after the exact-arch miss — a universal binary
 * contains both slices, so it can never be the wrong one.
 */
function matchAsset(assets: GitHubAsset[]): GitHubAsset | undefined {
  if (process.platform === 'darwin') {
    const wanted = `-${process.arch}.dmg` // e.g. "-arm64.dmg"
    const exact = assets.find((a) => a.name && a.name.includes(wanted))
    if (exact) return exact
    return assets.find((a) => a.name && a.name.includes('-universal.dmg'))
  }
  if (process.platform === 'win32') {
    // Fielded Windows apps here are x64-only, so no arch token — add one (and
    // update the packager in the same commit) only if arm64 Windows ever ships.
    return assets.find((a) => a.name && a.name.endsWith(WIN_ASSET_SUFFIX))
  }
  return undefined
}

async function getUpdateStatus(): Promise<UpdateCheck> {
  const fetched = await fetchLatestRelease()
  if (fetched.kind === 'offline') return { status: 'offline' }
  if (fetched.kind === 'no-releases') return { status: 'no-releases' }
  if (fetched.kind === 'rate-limited') {
    return { status: 'rate-limited', retryAfterSeconds: fetched.retryAfterSeconds }
  }
  if (fetched.kind === 'error') {
    return { status: 'error', message: 'Could not reach GitHub' }
  }
  const release = fetched.release
  if (!isNewerVersion(release.tag_name, app.getVersion())) {
    return { status: 'up-to-date', version: app.getVersion() }
  }

  const assets = release.assets || []
  const asset = matchAsset(assets)
  // The companion .sha256 asset (CI emits one per artifact) lets us verify bytes
  // before staging. Missing on old releases → we proceed with a logged warning.
  const shaAsset = asset ? assets.find((a) => a.name === `${asset.name}.sha256`) : undefined

  const version = release.tag_name.replace(/^v/i, '')
  if (!asset) {
    // No build for this platform+arch. Do not offer self-install; send the user
    // to the release page to pick the right build by hand.
    return {
      status: 'available',
      version,
      notes: release.body || undefined,
      publishedAt: release.published_at || undefined,
      releaseUrl: release.html_url,
      reason: 'no-asset-for-arch'
    }
  }

  return {
    status: 'available',
    version,
    // The release body IS the changelog — already fetched, zero extra requests.
    // The renderer shows it as a collapsible "What's new" under the pill.
    notes: release.body || undefined,
    publishedAt: release.published_at || undefined,
    downloadUrl: asset.browser_download_url,
    sha256Url: shaAsset?.browser_download_url,
    releaseUrl: release.html_url
  }
}

/**
 * Self-install is only possible from a packaged build on a platform this file
 * can actually swap (darwin dmg path, win32 NSIS path). Dev builds and other
 * platforms degrade to "Get vX" → open the release page.
 */
function canSelfInstall(): boolean {
  return (process.platform === 'darwin' || process.platform === 'win32') && app.isPackaged
}

// ── Download (shared: dmg and installer exe) ─────────────────────────────────
/**
 * Stream a URL to `destPath`, via `destPath + '.part'` — a partial file must
 * never be mistaken for a complete one, so the real name only exists after a
 * fully-successful rename. Every error path destroys both streams (an open
 * write handle leaks the fd everywhere and makes unlink fail EPERM/EBUSY on
 * Windows, stranding partial installers in %TEMP%).
 *
 * Disk preflight: the full flow can need ~2.5× the artifact at once (download +
 * staged copy + .bak during the swap). Checked against tmpdir's free space as
 * soon as content-length is known, so the failure is a typed message — not a
 * generic "retry" that fails identically forever, and not a post-quit swap
 * failure only the log ever sees.
 *
 * Progress: when the server omits content-length (total === 0) the renderer
 * must show an INDETERMINATE bar rather than a stuck "0%". GitHub's CDN
 * normally sends a length, but proxies occasionally strip it.
 */
async function downloadToFile(
  url: string,
  destPath: string,
  onProgress: (downloaded: number, total: number) => void
): Promise<void> {
  const partPath = destPath + '.part'
  const res = await guardedGet(url, 30_000)
  // Electron's IncomingMessage implements Node's Readable at runtime; the
  // typings only expose the EventEmitter surface, so cast once for pipe/destroy.
  const bodyStream = res as unknown as Readable
  if (res.statusCode !== 200) {
    bodyStream.destroy()
    throw new Error(`HTTP ${res.statusCode}`)
  }
  const total = parseInt(headerStr(res, 'content-length') ?? '0', 10) || 0
  if (total > 0) {
    let free = Infinity
    try {
      const st = statfsSync(os.tmpdir()) // Node ≥18.15
      free = st.bavail * st.bsize
    } catch {
      /* exotic tmpdir fs (container mounts) — skip the preflight rather than
         throw past bodyStream and leak the live response */
    }
    const needed = Math.ceil(total * 2.5)
    if (needed > free) {
      bodyStream.destroy()
      const needMb = Math.ceil(needed / (1024 * 1024))
      throw new Error(`not enough disk space (need ~${needMb} MB)`)
    }
  }
  await new Promise<void>((resolve, reject) => {
    const out = createWriteStream(partPath)
    let downloaded = 0
    let settled = false
    let stallTimer: NodeJS.Timeout | null = null
    const armStall = (): void => {
      if (stallTimer) clearTimeout(stallTimer)
      stallTimer = setTimeout(() => fail(new Error('Download stalled')), 60_000)
    }
    const fail = (err: Error): void => {
      if (settled) return
      settled = true
      if (stallTimer) clearTimeout(stallTimer)
      bodyStream.destroy()
      out.destroy()
      reject(err)
    }
    armStall()
    bodyStream.on('data', (chunk: Buffer) => {
      downloaded += chunk.length
      armStall()
      onProgress(downloaded, total) // total === 0 → renderer shows indeterminate
    })
    bodyStream.on('error', fail)
    out.on('error', fail)
    // Resolve on 'close', not 'finish': 'finish' fires before the fd is closed,
    // and renaming a not-yet-closed file races AV/indexer filesystem filters
    // (EBUSY/EPERM on Windows). A 'close' after destroy() is ignored — settled
    // is already true via fail() — and a close that arrives without
    // writableFinished is a silent truncation, so treat it as a failure.
    out.on('close', () => {
      if (settled) return
      if (out.writableFinished) {
        settled = true
        if (stallTimer) clearTimeout(stallTimer)
        resolve()
      } else {
        fail(new Error('Write stream closed before finishing'))
      }
    })
    bodyStream.pipe(out)
  }).catch(async (err) => {
    await fs.unlink(partPath).catch(() => {}) // stream is destroyed, so this can't race the handle
    throw err
  })
  try {
    await fs.rename(partPath, destPath)
  } catch (e) {
    // Keep the every-error-path-cleans-up contract: without this, the .part
    // survives and the caller's cleanup unlinks only destPath (which doesn't exist).
    await fs.unlink(partPath).catch(() => {})
    throw e
  }
}

/** Compute the hex SHA-256 of a file on disk. */
function sha256File(filePath: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256')
    const stream = createReadStream(filePath)
    stream.on('error', reject)
    stream.on('data', (chunk) => hash.update(chunk))
    stream.on('end', () => resolve(hash.digest('hex')))
  })
}

/**
 * Fetch the small .sha256 sidecar and return the bare hex digest, or null if it
 * doesn't exist / can't be parsed. Sidecar body is shasum format
 * ("`<hex>  <filename>`") — take the first whitespace-delimited token. Same
 * guarded transport as everything else: the sidecar fetch is a request site
 * too, and it must work against the harness mock (http, override host) under
 * the exact same guard as the artifact download.
 */
async function fetchExpectedSha(url: string): Promise<string | null> {
  try {
    const res = await guardedGet(url, 15_000)
    if (res.statusCode !== 200) {
      ;(res as unknown as Readable).destroy()
      return null
    }
    const body = await readBody(res, 15_000)
    const token = body.trim().split(/\s+/)[0]
    return /^[0-9a-f]{64}$/i.test(token) ? token.toLowerCase() : null
  } catch {
    return null
  }
}

// ── macOS: stage (mount dmg → ditto .app out → detach) ───────────────────────
/** Mount a .dmg, copy the .app out with ditto (preserves xattrs), detach. Returns staged .app path. */
function mountAndExtractMacDmg(dmgPath: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const ts = Date.now()
    const mountPoint = path.join(os.tmpdir(), `${TMP_PREFIX}-mount-${ts}`)
    const stagingDir = path.join(os.tmpdir(), `${TMP_PREFIX}-staged-${ts}`)
    try {
      mkdirSync(stagingDir, { recursive: true })
    } catch {
      /* ignore */
    }
    // Detach is best-effort but NOT fire-and-forget: "Resource busy" (Spotlight
    // indexing the fresh mount is the classic) would otherwise leave the volume
    // mounted indefinitely, and every later update mounts another. On a non-zero
    // exit, retry once after 2s with -force. Both attempts hit the log via set -x
    // in the relauncher era; here we just console.warn.
    const detach = (): void => {
      const attempt = (force: boolean): void => {
        const args = force
          ? ['detach', '-force', mountPoint]
          : ['detach', '-quiet', mountPoint]
        try {
          const p = spawn('hdiutil', args, { stdio: 'ignore' })
          p.on('error', () => {})
          p.on('close', (code) => {
            if (code !== 0 && !force) {
              console.warn(`[updater] hdiutil detach exit ${code}; retrying with -force in 2s`)
              setTimeout(() => attempt(true), 2_000)
            }
          })
          p.unref()
        } catch {
          /* ignore */
        }
      }
      attempt(false)
    }
    const attach = spawn(
      'hdiutil',
      ['attach', '-nobrowse', '-quiet', '-mountpoint', mountPoint, dmgPath],
      { stdio: 'ignore' }
    )
    attach.on('error', reject)
    attach.on('close', (code) => {
      if (code !== 0) return reject(new Error(`hdiutil attach exit ${code}`))
      let appName: string | undefined
      try {
        appName = readdirSync(mountPoint).find((n) => n.endsWith('.app'))
      } catch (e) {
        detach()
        return reject(new Error(`read mount: ${(e as Error).message}`))
      }
      if (!appName) {
        detach()
        return reject(new Error('No .app in DMG'))
      }
      const sourceApp = path.join(mountPoint, appName)
      const destApp = path.join(stagingDir, appName)
      const cp = spawn('ditto', [sourceApp, destApp], { stdio: 'ignore' })
      cp.on('error', (err) => {
        detach()
        reject(err)
      })
      cp.on('close', (cpCode) => {
        detach()
        if (cpCode !== 0) return reject(new Error(`ditto exit ${cpCode}`))
        resolve(destApp)
      })
    })
  })
}

// ── macOS: apply (detached relauncher swap) ──────────────────────────────────
/**
 * Where the swap must land. App Translocation guard: a quarantined app launched
 * from Downloads runs from a read-only, ephemeral
 * /private/var/.../AppTranslocation/... path — installing "over ourselves" there
 * is impossible AND pointless, so we target /Applications/<App>.app instead.
 */
function resolveDarwinTarget(): {
  runningAppBundle: string
  targetAppBundle: string
  isTranslocated: boolean
} {
  const exePath = app.getPath('exe')
  const runningAppBundle = exePath.replace(/\/Contents\/MacOS\/[^/]+$/, '')
  const isTranslocated = runningAppBundle.includes('/AppTranslocation/')
  const targetAppBundle = isTranslocated
    ? path.join('/Applications', path.basename(runningAppBundle))
    : runningAppBundle
  return { runningAppBundle, targetAppBundle, isTranslocated }
}

/** Per-platform log dir that SURVIVES the app (the swap runs after we're gone). */
function updaterLogDir(): string {
  return process.platform === 'win32'
    ? path.join(app.getPath('appData'), LOG_DIR_NAME)
    : path.join(os.homedir(), 'Library', 'Logs', LOG_DIR_NAME)
}

/** Append one line to attempts.log BEFORE quitting — when "the update did nothing"
 *  reports arrive, this line plus the relauncher log is the whole story. */
function logAttempt(line: string): void {
  const logDir = updaterLogDir()
  try {
    mkdirSync(logDir, { recursive: true })
    appendFileSync(path.join(logDir, 'attempts.log'), `[${new Date().toISOString()}] ${line}\n`)
  } catch {
    /* ignore */
  }
}

type ApplyResult = { ok: boolean; code?: 'stage-missing' | 'busy'; error?: string }

/**
 * Write a detached, double-forked relauncher that swaps the .app after we quit.
 *
 * Hard-won mechanics baked in:
 *  - Writability preflight: if the target's parent dir isn't writable (classic:
 *    non-admin user + /Applications entry owned by someone else), fail HERE with
 *    a typed error instead of quitting into a swap that cannot succeed.
 *  - PID re-check after the wait loop: if the old process is still alive after
 *    30s (tray apps intercepting 'before-quit' to hide-instead-of-quit are the
 *    classic cause), ABORT and reopen the target — never swap under a live app,
 *    which would delete the .bak the running process still executes from.
 *  - Backup failure ≠ silent exit: if `mv TARGET → .bak` fails, the old app is
 *    still intact — log AND reopen it before exiting. The user must never watch
 *    their app quit and nothing come back.
 *  - Re-sign then VERIFY: `codesign … || true` would mask re-sign failures and
 *    leave a damaged bundle with its .bak already deleted. We check codesign's
 *    exit status, verify, and only delete .bak after a good verify — otherwise
 *    roll back to .bak.
 *  - Translocation aftermath: after a translocated → /Applications install, a
 *    marker file is written so the app can show a one-time "moved to
 *    Applications — delete the old copy" notice on next launch (the quarantined
 *    copy in Downloads still opens the OLD version; without the notice the user
 *    is stuck in an endless-update loop from their habitual launch point).
 */
function applyDarwin(newPath: string): ApplyResult {
  const { targetAppBundle, isTranslocated } = resolveDarwinTarget()

  try {
    accessSync(path.dirname(targetAppBundle), fsConstants.W_OK)
  } catch {
    return {
      ok: false,
      error: `Install location not writable (${path.dirname(targetAppBundle)}) — move the app to a folder you own and try again`
    }
  }

  const ts = Date.now()
  const scriptPath = path.join(os.tmpdir(), `${TMP_PREFIX}-update-${ts}.sh`)
  const logDir = updaterLogDir()
  try {
    mkdirSync(logDir, { recursive: true })
  } catch {
    /* ignore */
  }
  const logPath = path.join(logDir, `update-${ts}.log`)
  logAttempt(`applyUpdate pid=${process.pid} new=${newPath} target=${targetAppBundle}`)

  if (isTranslocated) {
    // Consumed (shown once, then deleted) by the app on next launch.
    try {
      writeFileSync(
        path.join(app.getPath('userData'), 'moved-to-applications.marker'),
        targetAppBundle
      )
    } catch {
      /* ignore */
    }
  }

  // The script must outlive THIS process (it's replacing this very app), so it
  // double-forks (nohup + disown + re-exec as --daemonized) and ignores HUP/TERM.
  const script = [
    '#!/bin/bash',
    `LOG="${logPath}"`,
    'if [ "$1" != "--daemonized" ]; then',
    '  nohup "$0" --daemonized "$@" </dev/null >/dev/null 2>&1 &',
    '  disown',
    '  exit 0',
    'fi',
    'shift',
    'exec >>"$LOG" 2>&1',
    'set -x',
    'trap "" HUP TERM',
    'PID=$1',
    `NEW_APP="${newPath}"`,
    `TARGET="${targetAppBundle}"`,
    'BACKUP="${TARGET}.bak"',
    'for _ in $(seq 1 30); do',
    '  if ! ps -p "$PID" > /dev/null 2>&1; then break; fi',
    '  sleep 1',
    'done',
    '# Re-check after the loop: a quit interceptor can hold the process open past',
    '# the timeout. Swapping under a live app moves the bundle it is executing',
    '# from and then deletes it on codesign success — abort instead.',
    'if ps -p "$PID" > /dev/null 2>&1; then',
    '  echo "ERROR: old process still alive after 30s (quit interceptor?) — aborting swap"',
    '  open "$TARGET"',
    '  rm -f "$0"',
    '  exit 1',
    'fi',
    'xattr -dr com.apple.quarantine "$NEW_APP" 2>/dev/null || true',
    'if [ -d "$TARGET" ]; then',
    '  rm -rf "$BACKUP" 2>/dev/null',
    '  if ! mv "$TARGET" "$BACKUP"; then',
    '    # The old app is still intact at $TARGET — reopen it. Never leave the',
    '    # user app-less because a BACKUP step failed.',
    '    echo "ERROR: backup failed — reopening existing app"',
    '    [ -d "$TARGET" ] && open "$TARGET"',
    '    rm -f "$0"',
    '    exit 1',
    '  fi',
    'fi',
    'if ! mv "$NEW_APP" "$TARGET"; then',
    '  echo "ERROR: move-in failed, rolling back"',
    '  [ -d "$BACKUP" ] && [ ! -d "$TARGET" ] && mv "$BACKUP" "$TARGET"',
    '  [ -d "$TARGET" ] && open "$TARGET"',
    '  rm -f "$0"',
    '  exit 1',
    'fi',
    '# Re-sign ad-hoc, then VERIFY. A failed re-sign must not be swallowed — a',
    '# damaged bundle is worse than staying on the old version, so roll back.',
    'if codesign --force --deep --sign - "$TARGET" && codesign --verify --deep --strict "$TARGET"; then',
    '  rm -rf "$BACKUP" 2>/dev/null',
    '  open "$TARGET"',
    'else',
    '  echo "ERROR: re-sign/verify failed"',
    '  # Roll back ONLY if a backup exists. On a fresh-target install (translocated',
    '  # app → /Applications with no prior copy) there is no backup, and deleting',
    '  # the new bundle would leave the user with NO app at the target. A bundle',
    '  # that failed re-sign may still open (quarantine already stripped) — leaving',
    '  # it in place beats leaving nothing.',
    '  if [ -d "$BACKUP" ]; then',
    '    rm -rf "$TARGET" 2>/dev/null',
    '    mv "$BACKUP" "$TARGET"',
    '  fi',
    '  [ -d "$TARGET" ] && open "$TARGET"',
    'fi',
    'rm -f "$0"',
    ''
  ].join('\n')

  writeFileSync(scriptPath, script)
  chmodSync(scriptPath, 0o755)
  const child = spawn('/bin/bash', [scriptPath, String(process.pid)], {
    detached: true,
    stdio: 'ignore'
  })
  child.unref()
  quittingForUpdate = true
  setTimeout(() => app.quit(), 500)
  return { ok: true }
}

// ── Windows: apply (detached silent NSIS reinstall) ──────────────────────────
/**
 * Spawn the verified installer DETACHED, then quit so the file lock on our own
 * exe releases and the installer can replace files.
 *
 * '/S' alone does NOT relaunch the app after a silent install: electron-builder's
 * oneClick template runs the app only under `${ifNot} ${Silent} ${orIf}
 * ${isForceRun}` (installSection.nsh) — non-silent installs auto-run (that's what
 * runAfterFinish enables), silent installs run the app ONLY when the literal
 * `--force-run` parameter is on the installer command line. electron-updater
 * passes it for exactly this reason. Omit it and the field behavior is "the app
 * quits for the update and never comes back." (verified 2026-07 against
 * electron-builder's NSIS templates.) Add '--updated' only if your app reads it
 * as a first-run-after-update marker.
 */
function applyWin32(installerPath: string): ApplyResult {
  logAttempt(`applyUpdate(win32) pid=${process.pid} installer=${installerPath}`)
  try {
    const child = spawn(installerPath, ['/S', '--force-run'], {
      detached: true,
      stdio: 'ignore',
      windowsHide: true
    })
    // Gate the quit on 'spawn', not a fixed timer: EACCES/ENOENT arrive
    // asynchronously via 'error' (AV quarantining the temp-dir installer is the
    // classic), and quitting anyway would leave the user with no app running,
    // no installer running, and no relaunch coming. The installer exe itself is
    // cleaned up by the next launch's temp sweep (we're gone before we could
    // delete it).
    child.once('spawn', () => {
      child.unref()
      quittingForUpdate = true
      setTimeout(() => app.quit(), 200)
    })
    child.once('error', (err) => {
      applying = false // allow a retry; the renderer stays on its current state
      logAttempt(`applyUpdate(win32) spawn FAILED: ${err.message}`)
      console.error(`[updater] installer spawn failed: ${err.message}`)
    })
  } catch (e) {
    return { ok: false, error: `Could not start installer: ${(e as Error).message}` }
  }
  return { ok: true }
}

/**
 * Platform dispatch + the guards every apply shares. Returns a typed result so
 * the renderer can recover from every dead-end:
 *  - stage vanished (macOS purges per-user tmpdirs; if "Restart to apply" sits
 *    for days the stage can be gone) → reset state, { ok:false,
 *    code:'stage-missing' } — the button drops back to "Update to vX"
 *    (re-download) instead of hanging on "Updating…".
 *  - already applying → { ok:false, code:'busy' } (two relaunchers racing the
 *    same mv/backup sequence is how you corrupt an install).
 */
function applyUpdate(): ApplyResult {
  if (applying) return { ok: false, code: 'busy' }
  // Re-validate the stage right before we commit to quitting.
  if (!pendingUpdatePath || !existsSync(pendingUpdatePath)) {
    pendingUpdatePath = null
    return { ok: false, code: 'stage-missing' }
  }
  applying = true
  const result =
    process.platform === 'win32' ? applyWin32(pendingUpdatePath) : applyDarwin(pendingUpdatePath)
  if (!result.ok) applying = false // on success we're quitting; the flag dies with us
  return result
}

// ── Temp hygiene ─────────────────────────────────────────────────────────────
/**
 * Sweep our own temp detritus on startup: aborted flows strand partial *.part
 * files, downloaded-but-never-applied dmgs, -staged-* dirs, -mount-* mountpoint
 * dirs (hdiutil detach doesn't remove the created dir), -update-*.sh scripts,
 * and — on Windows — the installer exe that outlived us by design. Everything we
 * create is TMP_PREFIX-prefixed precisely so this one function can collect it
 * all. *.part is always orphaned at startup (no download can be in flight yet);
 * everything else must be >24h old so we never delete a sibling instance's
 * in-progress update.
 */
function sweepStaleTemp(): void {
  const now = Date.now()
  let entries: string[] = []
  try {
    entries = readdirSync(os.tmpdir())
  } catch {
    return
  }
  for (const name of entries) {
    if (!name.startsWith(`${TMP_PREFIX}-`)) continue
    const full = path.join(os.tmpdir(), name)
    if (pendingUpdatePath && pendingUpdatePath.startsWith(full)) continue // never sweep a live stage
    try {
      const isOrphanPart = name.endsWith('.part')
      const stale = now - statSync(full).mtimeMs > 24 * 60 * 60 * 1000
      if (isOrphanPart || stale) {
        rmSync(full, { recursive: true, force: true })
      }
    } catch {
      /* ignore — best-effort hygiene */
    }
  }
}

// ── Registration: IPC + cadence ──────────────────────────────────────────────
/** Register updater IPC + return control handles for launch/periodic checks. */
export function registerUpdater(getWindow: () => BrowserWindow | null): {
  checkAndNotify: () => Promise<void>
  stop: () => void
} {
  sweepStaleTemp()

  // Never send to a destroyed webContents: a window closed mid-download while
  // the host's getter still returns the stale reference would make send() throw
  // synchronously — inside the download's 'data' listener, poisoning the stream.
  const send = (channel: string, payload: unknown): void => {
    const w = getWindow()
    if (w && !w.isDestroyed()) w.webContents.send(channel, payload)
  }

  const notify = (r: UpdateCheck): void => {
    if (r.status === 'available') {
      lastAvailable = r
      send('update:available', r)
    }
  }

  // update:check must be able to RESOLVE only — a rejection travels through IPC
  // and strands the renderer on 'checking' forever. Same wrapper serves the
  // timer, the resume hook, and the returned checkAndNotify.
  const doCheck = async (): Promise<UpdateCheck> => {
    try {
      const r = await getUpdateStatus()
      notify(r)
      return r
    } catch (e) {
      return { status: 'error', message: (e as Error).message || 'Update check failed' }
    }
  }

  ipcMain.handle('app:version', () => app.getVersion())
  ipcMain.handle('update:can-self-install', () => canSelfInstall())
  ipcMain.handle('update:check', () => doCheck())
  // Renderer calls this right after init() has attached its listeners. Main
  // replays the last "available" result so a launch-time discovery is never lost
  // to a listener-attachment race (the old 2.5s setTimeout hack).
  ipcMain.handle('update:get-pending', () => lastAvailable)
  ipcMain.handle('shell:open-external', (_e, url: unknown) => {
    if (typeof url === 'string' && /^https?:\/\//.test(url)) {
      shell.openExternal(url).catch(() => {}) // no OS handler → rejection; don't leak it
    }
  })

  // NO url argument, deliberately. Main downloads the {downloadUrl, sha256Url}
  // pair from its OWN last check state, read atomically from one object. The old
  // renderer-supplied URL was both a security hole (any GitHub-hosted URL was
  // accepted) and a race (a periodic re-check could bump main's sha256 state
  // while the renderer held a stale URL — guaranteed spurious checksum mismatch).
  ipcMain.handle('update:download', async () => {
    if (!canSelfInstall()) return { ok: false, error: 'Self-install not supported here' }
    if (downloadInFlight) return { ok: false, error: 'busy' }
    const av = lastAvailable
    if (!av || !av.downloadUrl) {
      return { ok: false, error: 'No downloadable update — check again' }
    }
    if (process.platform === 'darwin') {
      // Fail BEFORE a 100MB+ download if the swap can't possibly succeed.
      const { targetAppBundle } = resolveDarwinTarget()
      try {
        accessSync(path.dirname(targetAppBundle), fsConstants.W_OK)
      } catch {
        return {
          ok: false,
          error: `Install location not writable (${path.dirname(targetAppBundle)}) — move the app to a folder you own and try again`
        }
      }
    }
    downloadInFlight = true
    const ts = Date.now()
    const destPath =
      process.platform === 'win32'
        ? path.join(os.tmpdir(), `${TMP_PREFIX}-update-${ts}${WIN_ASSET_SUFFIX}`)
        : path.join(os.tmpdir(), `${TMP_PREFIX}-update-${ts}.dmg`)

    // Throttle progress: per-chunk events flood the renderer (hundreds of sends
    // per second on a fast pipe) for zero benefit. Emit only when the integer
    // percent changes or ≥150ms elapsed — the time rule also paces the
    // indeterminate (total === 0) case.
    let lastPct = -1
    let lastEmit = 0
    const onProgress = (downloaded: number, total: number): void => {
      const now = Date.now()
      const pct = total > 0 ? Math.floor((downloaded / total) * 100) : -1
      if (pct !== lastPct || now - lastEmit >= 150) {
        lastPct = pct
        lastEmit = now
        send('update:download-progress', { downloaded, total })
      }
    }

    try {
      await downloadToFile(av.downloadUrl, destPath, onProgress)

      // SHA-256 gate BEFORE staging. If CI published a .sha256, a mismatch
      // aborts before we ever mount the dmg / touch the installer. If the
      // sidecar is absent (old release), log a warning and proceed — TLS to
      // GitHub is still the integrity floor. Be honest about what the sidecar
      // buys: it travels the same channel as the artifact, so it detects
      // corruption/truncation, not a compromised release (see SKILL.md §5 for
      // the minisign upgrade).
      if (av.sha256Url) {
        const expected = await fetchExpectedSha(av.sha256Url)
        if (expected) {
          const actual = await sha256File(destPath)
          if (actual !== expected) {
            await fs.unlink(destPath).catch(() => {})
            return { ok: false, error: 'Checksum mismatch — download corrupted or tampered' }
          }
        } else {
          console.warn('[updater] .sha256 asset present but unreadable; skipping verify')
        }
      } else {
        console.warn('[updater] no .sha256 asset for this release; skipping checksum verify')
      }

      if (process.platform === 'win32') {
        // The verified installer IS the stage — nothing to extract.
        pendingUpdatePath = destPath
      } else {
        pendingUpdatePath = await mountAndExtractMacDmg(destPath)
        await fs.unlink(destPath).catch(() => {})
      }
      return { ok: true }
    } catch (e) {
      await fs.unlink(destPath).catch(() => {})
      return { ok: false, error: (e as Error).message }
    } finally {
      downloadInFlight = false
    }
  })
  ipcMain.handle('update:apply', () => applyUpdate())

  // Cadence: the renderer runs the launch check; this timer covers a
  // long-running app learning about a release cut hours later; the resume hook
  // covers the laptop that sleeps every night and therefore never meets the
  // timer. Never poll by the minute — the quota is shared machine-wide.
  const timer = setInterval(() => {
    void doCheck()
  }, RECHECK_INTERVAL_MS)
  // Node keeps the event loop alive for an unref'd timer's sake; don't hold the
  // app open just for the re-check.
  timer.unref?.()

  const onResume = (): void => {
    if (Date.now() - lastCheckedAtMs > RECHECK_INTERVAL_MS) void doCheck()
  }
  powerMonitor.on('resume', onResume)

  return {
    checkAndNotify: async () => {
      await doCheck()
    },
    stop: () => {
      clearInterval(timer)
      powerMonitor.removeListener('resume', onResume)
      // Without this, a second registerUpdater() (harness setup/teardown, dev
      // hot-reload, window-recreation patterns) throws "Attempted to register a
      // second handler". Drop 'app:version' / 'shell:open-external' from the
      // list if your host app registers those two elsewhere.
      for (const ch of [
        'app:version',
        'update:can-self-install',
        'update:check',
        'update:get-pending',
        'shell:open-external',
        'update:download',
        'update:apply'
      ]) {
        ipcMain.removeHandler(ch)
      }
    }
  }
}
