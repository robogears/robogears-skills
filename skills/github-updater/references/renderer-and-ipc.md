# Renderer UI + IPC bridge (Electron)

Everything the user-facing side needs: the shared types (the `UpdateCheck` single
source of truth), the preload `contextBridge` surface, the zustand store (the UI
state machine), the `UpdateButton`, and the main-entry wiring. This is step 4 of the
SKILL.md build order — `references/updater-main.ts` must already be in place; the
channel names below must match its `ipcMain.handle` / `webContents.send` strings
byte-for-byte.

Two contracts govern this whole file:

- **The renderer owns the first check.** `init()` runs a silent check plus a
  `getPendingUpdate()` replay. Main only pushes later (periodic/wake) discoveries.
- **Every `window.api` invoke is wrapped in `try/catch` → a typed error state.**
  An IPC handler rejection must degrade to "error", never strand the UI —
  `checkState` can never stick on `'checking'`.

## Shared types (`src/shared/models.ts`)

**This file is the single source of truth for the `UpdateCheck` union.** SKILL.md
quotes it; `updater-main.ts` implements it. If you change it here, change main in
the same commit — a variant that main returns but the renderer doesn't handle is an
unhandled status the first time GitHub throttles or the machine goes offline.

```ts
export type UpdateCheck =
  | {
      status: 'available'
      version: string
      notes?: string // release body markdown — the "What's new" content, already fetched
      publishedAt?: string // ISO 8601 from the release JSON
      downloadUrl?: string // ABSENT when no arch-matched asset exists — route to releaseUrl
      sha256Url?: string //   companion sidecar; verified in main, opaque to the UI
      releaseUrl: string
      reason?: 'no-asset-for-arch'
    }
  | { status: 'up-to-date'; version: string }
  | { status: 'no-releases' } // 404 from releases/latest: brand-new repo, not an error
  | { status: 'rate-limited'; retryAfterSeconds?: number }
  | { status: 'offline' } // distinct from error: retry on connectivity, don't blame GitHub
  | { status: 'error'; message: string }

// The `update:available` push payload is exactly the available variant:
export type UpdateAvailable = Extract<UpdateCheck, { status: 'available' }>

export interface UpdateProgress {
  downloaded: number
  total: number // 0 => length unknown; render an INDETERMINATE bar, not a frozen "0%"
}
```

`available` with **no** `downloadUrl` (`reason: 'no-asset-for-arch'`) means the UI
must route to `releaseUrl` — never self-install a wrong-arch build (SKILL.md §4).
`notes` costs zero extra requests: the release `body` is already in the JSON main
parsed; dropping it just forces users to update blind.

## Preload bridge (`src/preload/index.ts`)

Expose a **fixed, typed** `window.api` — never raw `ipcRenderer`. These methods plus
two subscriptions are the entire update surface. The channels are raw strings,
intentionally not part of any typed IPC map — reproduce exactly:

| Direction | Channel | Payload → Result |
|---|---|---|
| invoke | `app:version` | → `string` |
| invoke | `update:check` | → `UpdateCheck` (also pushes `update:available` if newer) |
| invoke | `update:get-pending` | → `UpdateCheck \| null` (replays last available) |
| invoke | `update:can-self-install` | → `boolean` ((darwin \|\| win32) && `app.isPackaged`) |
| invoke | `update:download` | **no args** → `{ ok: boolean; error?: string }` |
| invoke | `update:apply` | → `{ ok: boolean; code?: 'stage-missing' \| 'busy'; error?: string }` (`error` carries typed failures like the writability preflight; treat `!ok` with no `code` as a generic failure) |
| invoke | `shell:open-external` | `url: string` → opens http(s) URLs only |
| push | `update:available` | `UpdateCheck` (the `available` variant) |
| push | `update:download-progress` | `{ downloaded: number; total: number }` |

**`downloadUpdate()` takes NO url.** Main downloads the asset + sidecar pair from
its own last check state. A renderer-supplied URL is both a security hole (any
GitHub-hosted URL would be accepted — someone else's repo included) and a race: a
periodic re-check can bump main's sha256 state while the renderer holds a stale
URL, guaranteeing a spurious checksum-mismatch failure. Don't reintroduce the
parameter.

```ts
import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import type { UpdateAvailable, UpdateCheck, UpdateProgress } from '../shared/models'

type Unsubscribe = () => void
function subscribe<T>(channel: string, cb: (payload: T) => void): Unsubscribe {
  const listener = (_e: IpcRendererEvent, payload: T): void => cb(payload)
  ipcRenderer.on(channel, listener)
  return () => ipcRenderer.removeListener(channel, listener)
}

const updaterApi = {
  getAppVersion: (): Promise<string> => ipcRenderer.invoke('app:version'),
  checkForUpdates: (): Promise<UpdateCheck> => ipcRenderer.invoke('update:check'),
  getPendingUpdate: (): Promise<UpdateCheck | null> => ipcRenderer.invoke('update:get-pending'),
  canSelfInstall: (): Promise<boolean> => ipcRenderer.invoke('update:can-self-install'),
  downloadUpdate: (): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('update:download'), // NO url — main owns the asset+sidecar pair
  applyUpdate: (): Promise<{ ok: boolean; code?: 'stage-missing' | 'busy'; error?: string }> =>
    ipcRenderer.invoke('update:apply'),
  openExternal: (url: string): Promise<void> => ipcRenderer.invoke('shell:open-external', url),
  onUpdateAvailable: (cb: (u: UpdateAvailable) => void): Unsubscribe =>
    subscribe('update:available', cb),
  onUpdateProgress: (cb: (p: UpdateProgress) => void): Unsubscribe =>
    subscribe('update:download-progress', cb)
}

// Merge into your existing window.api object; never expose raw ipcRenderer.
contextBridge.exposeInMainWorld('api', { ...updaterApi /* , ...yourOtherApi */ })
```

The preload must stay **CommonJS** (sandboxed preloads can't be ESM), and the
`BrowserWindow` webPreferences should have `contextIsolation: true, sandbox: true,
nodeIntegration: false` — the standard hardened Electron posture.

**No CSP/`connect-src` change is needed for GitHub** — all update networking lives
in the main process (Electron's `net` module, so system proxies and the OS cert
store work), never in the renderer. Do NOT add GitHub hosts to the renderer CSP.

## Renderer store (`src/renderer/src/state/updates-store.ts`)

A small zustand store is the UI state machine. `init()` must run once at app start.
**Order inside `init()` is load-bearing:**

1. **Subscriptions first** — attach `onUpdateAvailable` / `onUpdateProgress` before
   any invoke that could cause main to push.
2. Fetch the environment facts (`appVersion`, `canSelfInstall`).
3. **Run the first check, silently** — `check({ silent: true })` never flashes
   "Checking…" and surfaces nothing unless the result is `available`. If `init()`
   omits this call, the app's first check is the 12h timer — a fresh launch would
   never learn about an update for half a day.
4. **Replay** — `getPendingUpdate()` pulls anything main discovered before the
   listeners existed (e.g. a main-side wake check in a recreated window).

```ts
import { create } from 'zustand'
import type { UpdateAvailable, UpdateCheck } from '@shared/models'

export type DownloadState = 'idle' | 'downloading' | 'ready' | 'restarting' | 'failed'
export type CheckState =
  | 'idle'
  | 'checking'
  | 'up-to-date'
  | 'no-releases'
  | 'available'
  | 'rate-limited'
  | 'offline'
  | 'error'

interface UpdatesState {
  appVersion: string
  canSelfInstall: boolean
  available: UpdateAvailable | null
  downloadState: DownloadState
  progressPct: number
  indeterminate: boolean // true while downloading with no known content-length
  checkState: CheckState
  retryAfterSeconds: number // live countdown while rate-limited
  init: () => void
  check: (opts?: { silent?: boolean }) => Promise<void>
  startDownload: () => Promise<void>
  apply: () => Promise<void>
  openRelease: () => void
}

let initialized = false
let onlineArmed = false

export const useUpdates = create<UpdatesState>((set, get) => {
  let rateLimitTimer: ReturnType<typeof setInterval> | null = null

  // Manual-check feedback flashes briefly, then returns to idle. Never override
  // an 'available' that landed in the meantime.
  const flashThenIdle = (state: CheckState, ms = 4000): void => {
    set({ checkState: state })
    setTimeout(() => {
      if (get().checkState === state) set({ checkState: 'idle' })
    }, ms)
  }

  // Rate-limited: live countdown; the button stays disabled until it hits zero.
  const startRetryCountdown = (seconds: number): void => {
    if (rateLimitTimer) clearInterval(rateLimitTimer)
    set({ checkState: 'rate-limited', retryAfterSeconds: seconds })
    rateLimitTimer = setInterval(() => {
      const s = get().retryAfterSeconds - 1
      if (s <= 0) {
        if (rateLimitTimer) clearInterval(rateLimitTimer)
        rateLimitTimer = null
        set({ checkState: 'idle', retryAfterSeconds: 0 })
      } else {
        set({ retryAfterSeconds: s })
      }
    }, 1000)
  }

  // Offline: arm ONE 'online' listener that silently re-checks when connectivity
  // returns. Once, not per-failure — repeated offline checks must not stack listeners.
  const armOnlineRecheck = (): void => {
    if (onlineArmed) return
    onlineArmed = true
    window.addEventListener(
      'online',
      () => {
        onlineArmed = false
        if (get().checkState === 'offline') set({ checkState: 'idle' })
        void get().check({ silent: true })
      },
      { once: true }
    )
  }

  const applyResult = (r: UpdateCheck, silent: boolean): void => {
    if (r.status === 'available') {
      // Silent or not: an available update always surfaces the pill.
      set({ available: r, checkState: 'available' })
      return
    }
    if (r.status === 'offline') armOnlineRecheck() // arm even for silent checks
    if (silent) return // auto-checks surface nothing except 'available'
    switch (r.status) {
      case 'up-to-date':
        flashThenIdle('up-to-date')
        break
      case 'no-releases':
        flashThenIdle('no-releases')
        break
      case 'offline':
        set({ checkState: 'offline' }) // cleared by the online listener above
        break
      case 'rate-limited':
        startRetryCountdown(r.retryAfterSeconds ?? 60)
        break
      case 'error':
        flashThenIdle('error')
        break
    }
  }

  return {
    appVersion: '',
    canSelfInstall: false,
    available: null,
    downloadState: 'idle',
    progressPct: 0,
    indeterminate: false,
    checkState: 'idle',
    retryAfterSeconds: 0,

    init: () => {
      if (initialized) return
      initialized = true
      // 1. Subscriptions FIRST.
      window.api.onUpdateAvailable((u) => {
        if (get().available?.version === u.version) return // de-dupe repeat notices
        set({ available: u, checkState: 'available' })
      })
      window.api.onUpdateProgress(({ downloaded, total }) => {
        if (get().downloadState !== 'downloading') return
        // Events arrive already throttled from main (integer-percent change or
        // ≥150 ms) — set state per event, no renderer-side throttle needed.
        if (total > 0) {
          set({ indeterminate: false, progressPct: Math.floor((downloaded / total) * 100) })
        } else {
          set({ indeterminate: true }) // no content-length: indeterminate bar
        }
      })
      // 2. Environment facts (failures are non-fatal; defaults already safe).
      window.api.getAppVersion().then((v) => set({ appVersion: v })).catch(() => {})
      window.api.canSelfInstall().then((c) => set({ canSelfInstall: c })).catch(() => {})
      // 3. THE first check — silent: no 'checking' flash, nothing surfaced
      //    unless an update is actually available. The renderer owns this.
      void get().check({ silent: true })
      // 4. Replay anything main discovered before our listeners existed.
      window.api
        .getPendingUpdate()
        .then((r) => {
          if (r && r.status === 'available' && !get().available) {
            set({ available: r, checkState: 'available' })
          }
        })
        .catch(() => {})
    },

    check: async (opts = {}) => {
      const silent = opts.silent === true
      if (!silent) set({ checkState: 'checking' })
      try {
        applyResult(await window.api.checkForUpdates(), silent)
      } catch {
        // Handler rejected (or preload surface missing). Degrade to a typed
        // error — 'checking' must NEVER be a terminal state.
        if (silent) return
        flashThenIdle('error')
      }
    },

    startDownload: async () => {
      const a = get().available
      if (!a) return
      // Open the release page instead of self-installing when EITHER this build
      // can't self-install (dev/unpackaged/unsupported platform) OR there's no
      // arch-matched asset (no downloadUrl). Never install the wrong arch.
      if (!get().canSelfInstall || !a.downloadUrl) {
        window.api.openExternal(a.releaseUrl).catch(() => {})
        return
      }
      set({ downloadState: 'downloading', progressPct: 0, indeterminate: false })
      try {
        // No URL — main downloads ITS OWN asset+sidecar pair from its check state.
        const r = await window.api.downloadUpdate()
        set({ downloadState: r.ok ? 'ready' : 'failed', indeterminate: false })
      } catch {
        set({ downloadState: 'failed', indeterminate: false })
      }
    },

    apply: async () => {
      set({ downloadState: 'restarting' })
      try {
        const r = await window.api.applyUpdate()
        if (r.ok) return // the app is quitting; nothing more to do
        if (r.code === 'stage-missing') {
          // Staged artifact vanished (tmpdir purge after a long "Restart to
          // apply" wait). Drop back so the button offers a fresh download
          // instead of hanging on "Updating…".
          set({ downloadState: 'idle' })
        } else if (r.code !== 'busy') {
          set({ downloadState: 'ready' }) // unknown failure: let the user retry
        }
        // 'busy': another apply is already in flight and will quit the app.
      } catch {
        set({ downloadState: 'ready' })
      }
    },

    openRelease: () => {
      const a = get().available
      if (a) window.api.openExternal(a.releaseUrl).catch(() => {})
    }
  }
})
```

Call `useUpdates.getState().init()` (or `useEffect(() => init(), [])`) once at the
top of your app tree. Its silent check + `getPendingUpdate()` replay is what
surfaces a launch-time update — do not reintroduce a main-side
`setTimeout(2500)` launch hack (see "Why no setTimeout" below).

## The button (`src/renderer/src/components/UpdateButton.tsx`)

One component renders the whole state machine. It renders `null` when there's
nothing to say, so you can drop it anywhere (a top-bar pill, a Settings row)
without conditionals. The states, in the order the code checks them:

| State | Label | Click | Disabled |
|---|---|---|---|
| offline (manual check) | `You're offline` | — (auto-rechecks once on `online`) | yes |
| rate-limited | `Rate-limited — retry in Ns` (live countdown) | — | yes, until countdown ends |
| no self-install path | `Get vX` | opens `releaseUrl` | no |
| available | `Update to vX` + collapsible **What's new** | download | no |
| downloading | `Downloading N%` (or `Downloading…` if indeterminate) | — | yes |
| ready | `Restart to apply` | apply | no |
| restarting | `Updating…` | — | yes |
| failed | `Download failed — retry` | download again | no |

```tsx
import type { JSX } from 'react'
import { useUpdates } from '../state/updates-store'

// Minimal, SAFE notes renderer: headings, bullets, plain paragraphs. React
// escapes all text nodes. NEVER dangerouslySetInnerHTML the raw release body —
// it is arbitrary markdown/HTML from RELEASE_NOTES.md, an XSS vector in a
// window that holds IPC privileges. If you want full markdown, parse+sanitize
// (e.g. a strict allowlist); plain text is fine for release bullets.
function renderNotes(notes: string): JSX.Element {
  return (
    <div className="update-notes">
      {notes.split(/\r?\n/).map((line, i) => {
        const t = line.trim()
        if (!t) return null
        if (t.startsWith('#'))
          return <p key={i} className="notes-heading">{t.replace(/^#+\s*/, '')}</p>
        if (/^[-*]\s+/.test(t))
          return <p key={i} className="notes-bullet">• {t.replace(/^[-*]\s+/, '')}</p>
        return <p key={i}>{t}</p>
      })}
    </div>
  )
}

export function UpdateButton({ className = '' }: { className?: string }): JSX.Element | null {
  const available = useUpdates((s) => s.available)
  const canSelfInstall = useUpdates((s) => s.canSelfInstall)
  const state = useUpdates((s) => s.downloadState)
  const checkState = useUpdates((s) => s.checkState)
  const retryAfterSeconds = useUpdates((s) => s.retryAfterSeconds)
  const pct = useUpdates((s) => s.progressPct)
  const indeterminate = useUpdates((s) => s.indeterminate)
  const startDownload = useUpdates((s) => s.startDownload)
  const apply = useUpdates((s) => s.apply)

  if (!available) {
    // Manual-check outcomes that deserve a visible state even with no update:
    if (checkState === 'offline') {
      return <button className={`update-btn ${className}`} disabled>You&apos;re offline</button>
    }
    if (checkState === 'rate-limited') {
      return (
        <button className={`update-btn ${className}`} disabled>
          Rate-limited — retry in {retryAfterSeconds}s
        </button>
      )
    }
    return null
  }

  // No self-install path: this build can't self-install, or the release has no
  // arch-matched asset (downloadUrl undefined). Both resolve to "open the
  // release page" — startDownload() already routes there.
  const cannotSelfInstall = !canSelfInstall || !available.downloadUrl

  let label: string
  let onClick: () => void
  let disabled = false
  let ready = false

  if (cannotSelfInstall) {
    label = `Get v${available.version}` // opens release page in browser
    onClick = () => void startDownload()
  } else if (state === 'downloading') {
    label = indeterminate ? 'Downloading…' : `Downloading ${pct}%`
    onClick = () => {}
    disabled = true
  } else if (state === 'ready') {
    label = 'Restart to apply'
    onClick = () => void apply()
    ready = true
  } else if (state === 'restarting') {
    label = 'Updating…'
    onClick = () => {}
    disabled = true
  } else if (state === 'failed') {
    label = 'Download failed — retry'
    onClick = () => void startDownload()
  } else {
    label = `Update to v${available.version}`
    onClick = () => void startDownload()
  }

  return (
    <div className="update-pill">
      <button
        className={`update-btn ${ready ? 'is-ready' : ''} ${className}`}
        onClick={onClick}
        disabled={disabled}
      >
        {label}
      </button>
      {available.notes && state === 'idle' && (
        <details className="update-whats-new">
          <summary>What&apos;s new</summary>
          {renderNotes(available.notes)}
        </details>
      )}
    </div>
  )
}
```

The "What's new" content is free — `notes` is the release `body` already in the
check response; no extra request, no blind updates.

### Optional: skip-this-version affordance

**OPTIONAL** — add only if the app has a persisted-settings channel. A small `×`
next to the pill stops the nagging for a release the user has declined:

```tsx
<button
  className="update-skip"
  title={`Skip v${available.version}`}
  onClick={() => {
    void window.api.setSetting('updates.skippedVersion', available.version) // your settings channel
    useUpdates.setState({ available: null, checkState: 'idle' })
  }}
>
  ×
</button>
```

Main's side of the deal (SKILL.md §9): `notify()` suppresses results where
`version === skippedVersion`, and clears `skippedVersion` when a *newer* version
appears — skip means "skip this one," not "never update."

### Manual "Check for updates" (Settings)

A Settings row that calls `useUpdates().check()` (no `silent` flag — manual checks
show feedback) and reflects `checkState`:

- `checking` → "Checking…"
- `up-to-date` → "Up to date"
- `no-releases` → "No releases yet" (fresh repo; not an error)
- `offline` → "You're offline" (a one-shot `online` listener re-checks automatically)
- `rate-limited` → "GitHub rate limit — retry in `{retryAfterSeconds}`s" (the manual
  path is where a user hammering the button hits the shared 60-req/hr
  unauthenticated cap; saying *why* and *for how long* beats a generic failure)
- `error` → "Couldn't reach GitHub"

Silent checks (launch, periodic, wake, online-restore) never touch these labels —
only a manual check shows "you're current" confirmation.

### Indeterminate progress bar (CSS)

When `indeterminate` is true the label reads "Downloading…" and any progress *bar*
must not sit at 0% width forever — animate it:

```css
.update-bar { position: relative; overflow: hidden; height: 3px; background: #1a1a1a; }
.update-bar > .fill { height: 100%; background: #fff; }
/* determinate: width driven by progressPct inline style */
.update-bar.is-indeterminate > .fill {
  width: 40%;
  animation: update-indeterminate 1.1s ease-in-out infinite;
}
@keyframes update-indeterminate {
  0% { transform: translateX(-120%); }
  100% { transform: translateX(320%); }
}
```

Drive it from the store: `class={indeterminate ? 'update-bar is-indeterminate' :
'update-bar'}` and, when determinate, set the fill's `style={{ width: pct + '%' }}`.

## Main-process wiring

In your main entry, after the window(s) exist. `registerUpdater` takes a **getter**
(so it always talks to the current window, even after recreation) and returns
control handles. It arms the periodic re-check (12h) and the `powerMonitor` wake
re-check on its own.

```ts
import { registerUpdater } from './updater'

// inside app.whenReady(), after createWindow():
const updater = registerUpdater(() => mainWindow)   // getter, not the window
app.on('before-quit', () => updater.stop())
```

**Who runs the first check: the renderer**, at the end of its `init()` — the silent
`check()` plus the `getPendingUpdate()` replay. A main-driven
`updater.checkAndNotify()` after registration is optional belt-and-suspenders; the
renderer path is the one that must exist. Do not make main's optional check the
only one — a store whose `init()` merely subscribes and replays (no `check()`)
leaves the 12h timer as the first check.

### Why no `setTimeout(2500)` anymore

An older shape of this recipe had main check ~2.5s after launch and *hope* the
renderer had attached its `onUpdateAvailable` listener by then — a race that
silently swallowed the notice on a slow machine. The fix is **pull, not push at a
guessed time**: the renderer checks when *it* is ready, and `getPendingUpdate()`
replays anything main found earlier. Main still pushes `update:available` for
later (periodic/wake) discoveries; the store's version de-dupe keeps a pull and a
push from double-firing.

### Progress events are throttled in main

Main sends `update:download-progress` only on integer-percent changes or ≥150 ms
elapsed — never per network chunk (per-chunk `webContents.send` floods hundreds of
events/second on a fast pipe and re-renders the button for sub-percent changes).
The store therefore sets state on every event it receives, with no renderer-side
throttle. If you see render flooding, fix main's throttle — don't paper over it in
the store.
