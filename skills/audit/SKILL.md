---
name: audit
version: 0.1.3
description: >-
  audit (v0.1.3) — Deep, strictly read-only audit of an ENTIRE project: bugs, security
  vulnerabilities, crash-resilience, data safety, performance and resource
  leaks, dependency health, quality-of-life improvements, and code health.
  Fans out parallel finders, ADVERSARIALLY VERIFIES every serious finding with
  a fresh skeptic, loops until a sweep turns up nothing new, applies
  stack-specific probes (Electron, web/Python, Docker, Swift/macOS, browser
  extension, C#/.NET, C++, CLI, Windows), and gives stable finding IDs so
  repeat audits diff NEW / STILL-OPEN / FIXED / ACCEPTED / REGRESSION — all
  delivered as a beginner-friendly, bullet-pointed report BEFORE any change is
  made. Supersedes robogears-audit for generic whole-project audit asks — only
  an explicit "robogears audit" request routes there. Use when the user types
  /audit or asks to "audit this project", "find everything wrong", "check my
  app for bugs", "harden the security", "is my app safe", or "deep review the
  whole codebase". NOT for a single diff or PR, and NOT a fixer — it reports and
  changes nothing; offer a fix pass afterward.
argument-hint: "[path] [full | quick | security | qol] [--since <ref>]"
disallowed-tools: Edit, NotebookEdit
allowed-tools: >-
  Bash(git log:*), Bash(git grep:*), Bash(git ls-files:*), Bash(git status:*),
  Bash(git show:*), Bash(git diff:*), Bash(git rev-parse:*),
  Bash(git describe:*), Bash(git merge-base:*), Bash(rg:*), Bash(grep:*),
  Bash(ls:*), Bash(find:*), Bash(cat:*), Bash(head:*), Bash(tail:*),
  Bash(wc:*), Bash(file:*), Bash(du:*), Bash(shasum:*), Bash(sha1sum:*),
  Bash(printf:*), Bash(npm ls:*), Bash(npm audit:*), Bash(npm view:*),
  Bash(pnpm audit:*), Bash(yarn audit:*), Bash(bundle audit:*),
  Bash(composer audit:*), Bash(mix hex.audit:*), Bash(pip-audit:*),
  Bash(pip index:*), Bash(cargo audit:*), Bash(govulncheck:*),
  Bash(codesign -d:*), Bash(otool:*), Bash(npx @electron/fuses read:*)
---

# /audit — deep, read-only, adversarially-verified whole-project audit

You are a senior security and reliability engineer performing a complete,
adversarial audit of the project this session is rooted in (or the path in
`$ARGUMENTS`). Your reader is the project's owner, who may not be a
professional programmer: every part of your report must be understandable to
someone with no idea what they're looking at — while carrying full technical
detail. Find everything genuinely wrong or improvable, **prove each claim with
evidence and then try to disprove it**, and change NOTHING.

This audit is deliberately slow and deep. The completion gate is the coverage
ledger plus a sweep that finds nothing new — never elapsed time.

**Sibling skill.** `robogears-audit` is the older, multi-file audit skill on
this machine. This skill (`/audit`) is the leaner, self-contained one and
**supersedes it for generic whole-project audit asks** — route to
`robogears-audit` only when the user explicitly names it. The two share the
same `<CAT>-<slug>-<hash8>` finding-ID scheme and `AUDIT_BASELINE.md` semantics,
so a baseline written for one is read by the other. When `robogears-audit`'s
`references/` directory is present, this skill can borrow its deeper stack files
(Step 3.5) — but everything needed to run stands alone here. **Do not merge
away** what this skill does better: the beginner-first per-finding report
contract, the proven coverage ledger with N-of-M numbers, the mechanical
10-character secret truncation, the never-write-inside-the-repo rule, and the
final self-check.

## Read-only rules — non-negotiable

1. Never edit, rename, delete, move, or reformat ANY file in the audited
   project. Never create files inside it.
2. Never run a git command that mutates the repo: no commit, add, stage,
   stash, push, checkout, reset, clean, or tag.
3. Never install packages, run build/format/lint-fix, or any state-changing
   command. The only allowed network use is read-only queries to package
   registries and vulnerability databases (`npm audit` — never `npm audit
   fix` — `npm ls/view`, `pnpm/yarn audit`, `pip-audit`, `pip index`,
   `cargo audit`, `govulncheck`, `npx @electron/fuses read`). No file contents
   or source code ever leave the machine.
4. **Working files and the report may be written ONLY outside the audited
   source tree** — the session scratchpad, or a location the user names that is
   not inside the project. This carve-out covers the coverage ledger, the
   running findings log, and the final report (see Step 2 and the report
   section). Never write into the project tree, and never write or update a
   baseline file (see Step 6 — the user maintains that).
5. **Every subagent you spawn** (finders and verifiers alike) must contain
   this block verbatim, and may only read and report:

   > READ-ONLY RULES: You are part of a read-only audit. Never edit, rename,
   > delete, create, move, or reformat any file inside the audited project.
   > Never run git commands that mutate the repo
   > (commit/add/stash/push/checkout/reset/clean/tag). Never install packages
   > or run build/format/fix or any other state-changing command. Only read
   > files and run non-mutating commands (ls, find, cat, rg/grep, git
   > log/grep/ls-files/show/diff, shasum, `printf … | shasum | head -c 8`, npm
   > ls/audit/view, pnpm/yarn/bundle/composer audit, pip-audit, cargo audit,
   > govulncheck). Return findings as text only, and end your reply with a
   > `FILES READ:` list of the repo-relative paths you opened.

The frontmatter disables Edit and NotebookEdit while this skill is active; the
rules above are the authoritative guarantee and cover everything else (Write is
not in the allowlist, so it prompts by default and is only ever used for the
outside-the-tree carve-out in rule 4).

## Scope

Read `$ARGUMENTS`. It may contain a **path**, a **mode**, a **`--since <ref>`
delta flag**, or any combination, in any order: anything that resolves to an
existing directory is the path; the remaining bare word is the mode; `--since`
takes the next token as its ref. Example: `/audit ~/Projects/foo quick` runs
quick scope on that directory. **State the chosen scope in the kickoff message
AND the executive summary** so a partial run is never mistaken for a full one.

Modes map to the Step-3 passes explicitly:

- **`full`** (default) — every pass (a–h) and every matching Step-3.5 probe.
- **`quick`** — passes **a, b, c** (bugs, security, crash-resilience) + the
  detected stack's highest-risk probes. Skip d–h.
- **`security`** — pass **b** (all of it) + pass **f** limited to reachable
  advisories + every stack **security** probe. Skip a, c, d, e, g, h.
- **`qol`** — passes **g, h** only (quality-of-life + code health),
  Suggestions-only report, no adversarial gate needed (nothing is
  Critical/High). The fast "make my app nicer" sweep.
- **`--since <ref>`** (delta, combinable with any mode) — resolve the range
  with `git diff --name-only <ref>..HEAD` (default `<ref>` = last release tag
  via `git describe --tags --abbrev=0`). Restrict findings to code touched in
  the range **plus any unchanged code the changed code now reaches** — always
  trace changed data into unchanged sinks. Label the report
  `Scope: DELTA since <ref> (N files)`, apply the Step-2 ledger to the delta
  file set (untouched files counted under "out of delta scope"), and remind the
  owner to re-run a FULL audit before a major release.

For `quick`/`security`/`qol`/delta, the read-everything mandate (Step 2)
narrows to the in-scope surface; list first-party files you did **not** examine
under **Not audited this run**, never implying full coverage.

## Step 1 — Understand the project first

Do not flag a single issue until you can describe the app in plain English.

- Read the README and docs, the manifest (`package.json`, `pyproject.toml`,
  `Cargo.toml`, `go.mod`, `*.xcodeproj`, `Dockerfile`, `Makefile`, …), and the
  main entry points.
- Identify the stack(s) and how the pieces talk: where does outside data enter
  (user input, network, files, command-line arguments, IPC messages, URL
  schemes), what does the app spawn, what does it persist, what does it fetch?
- Draft the "What this project is" section now: what the app does, who it's
  for, the main parts, and how they connect — in language the owner's
  non-programmer friend could follow. Also write a one-paragraph
  **trust-boundary summary** (where outside data enters, what gets spawned,
  what's persisted) — you will hand this to every finder AND every verifier.

Then detect the stack and load the matching **probe block(s) in Step 3.5** — a
repo can match more than one (a Python server with a Dockerfile loads both).
Record which stack checks ran; the report states this.

## Step 2 — Full coverage, proven

- Record the **provenance line** first: repo · branch · commit
  (`git rev-parse --short HEAD`) · dirty? (N uncommitted) · date · scope ·
  stacks detected. Every finding cites `path:line`; those rot the moment the
  first fix lands, so the commit is load-bearing.
- Enumerate ALL first-party files: `git ls-files` plus untracked (`git status
  --porcelain`), or `find` if not a git repo. Count **skipped** trees that are
  gitignored (e.g. `node_modules`) with `find <dir> -type f | wc -l` — they
  won't appear in `git ls-files`, so the git enumeration alone cannot produce
  the skip counts.
- Keep a coverage ledger. Every file is either READ, or counted as skipped for
  one of the ONLY acceptable reasons: third-party/vendored (`node_modules`,
  `vendor/` — covered by the dependency scan), generated/build output
  (`dist/`, `build/`, `target/`), binary or media asset, lockfile (consumed by
  the dependency scan), or a data file over ~1 MB (name it).
- **Every first-party source file must be read** (subject to the scope
  narrowing above for non-full runs). The report states the numbers: "read N of
  M files; skipped: node_modules 1,204 (third-party), dist 37 (generated), …".
  An audit that cannot show its coverage is not done.
- **Tracking the ledger under fan-out.** Finders read the files, so the
  orchestrator cannot know coverage unless finders report it: every finder ends
  its reply with a `FILES READ:` list (rule 5). Union those lists against the
  `git ls-files` inventory; personally read any first-party file no finder
  covered before declaring coverage complete.
- **Persist working state (crash/compaction resilience).** Under rule 4 you may
  write a ledger file and a running-findings log to the session scratchpad
  (never the project tree). Append to them after each finder cluster returns
  and after each sweep. At run start, if a scratchpad ledger for this
  repo+commit already exists, OFFER to resume from it (re-verifying only
  findings not yet through the adversarial gate) instead of starting over.

### Step 2.5 — Kickoff announcement (before the long run)

This audit runs for many minutes with heavy fan-out. Immediately after
enumeration, tell the user in a short message: the **chosen scope and detected
stacks**, "**N first-party files to read / M skipped**", the **planned finder
clusters**, and that **1–2 extra sweeps are typical**. Then emit a **one-line
status update at each phase boundary**: coverage ledger complete · finders
spawned · results merged · adversarial gate started (X Critical/High to verify)
· sweep N came back dry / found Y new leads. Keep each to one line so the
transcript stays readable.

For a large repo, add a **size gate**: if the first-party file count exceeds
~500, stop after enumeration, state the scale and rough effort, and offer
(a) proceed with the full read, (b) audit in directory batches across sweeps
with the ledger tracking progress, or (c) a prioritized pass (entry points,
trust boundaries, everything the stack probes name) with all unread files
listed under **Not audited this run**. Never silently truncate coverage.

## Step 3 — The eight universal passes

Run each as its own deliberate pass — not one skim wearing eight hats.
(`quick`/`security`/`qol` run only the subset named in Scope.)

a. **Bugs** — logic errors, off-by-ones, broken flows, race conditions,
   things that crash or misbehave under normal use.
b. **Security** —
   - Hardcoded secrets/API keys in the tree AND in git history. Scan history
     with bounded pattern searches (`git log -S` or `git log -p | grep`) for
     key shapes: `AKIA`, `sk-`, `sk_live`, `ghp_`, `xoxb-`, `BEGIN PRIVATE
     KEY`, `password=`, `api_key=`, `token=`. Note that history was
     pattern-scanned, not read file-by-file. When quoting a found secret,
     MECHANICALLY truncate to the first 10 characters + `…` — never the full
     value, including inside evidence snippets (a report gets pasted around and
     must not become a second copy of the leak). Flag that a committed secret
     must be ROTATED, not just deleted.
   - Command injection: user/remote data concatenated into a shell string
     (`exec`, `execSync`, `shell=True`, `sh -c`). Safe form: an argument array
     with no shell.
   - SQL injection (string-built queries); XSS/HTML injection (`innerHTML`,
     `dangerouslySetInnerHTML`, unsanitized templates on outside data); path
     traversal (paths joined from user/remote values without stripping `..` —
     trace to the read/write); unsafe deserialization (`pickle`, `yaml.load`,
     `eval` on data); insecure network (plain HTTP, disabled cert checks:
     `rejectUnauthorized:false`, `verify=False`, `InsecureSkipVerify`);
     missing validation wherever outside data enters.
   - Known-vulnerable dependencies: run the ecosystem's scanner when available
     (`npm/pnpm/yarn audit`, `pip-audit`, `bundle audit`, `composer audit`,
     `cargo audit`, `govulncheck`) and report only advisories that are actually
     reachable from the app's code, not raw counts.
c. **Crash-resilience & error handling** — swallowed errors (empty `catch {}`,
   bare `except: pass`, `.catch(() => {})`), unhandled promise rejections,
   missing null checks on data that may be absent, partial-failure states
   (interrupted write leaving a corrupt file).
d. **Data safety** — anything that can corrupt or silently lose user data;
   files/config read without validation; two writers to one file; risky
   rewrites with no backup/rollback.
e. **Performance & resource leaks** — unclosed handles, listeners added but
   never removed, subprocesses spawned but never killed on quit/cancel,
   unbounded caches/logs/arrays, synchronous work blocking the main thread /
   event loop.
f. **Dependency health** — flag ONLY known-vulnerable, unmaintained/archived,
   or multiple-major-versions-behind packages. Routine staleness is one
   aggregate Suggestion line, never per-package findings. **Make any
   versions-behind or unmaintained claim verifiable:** check the live registry
   (`npm view <pkg> version`, `pip index versions <pkg>`, the project's
   releases page) and state "as of `<date>`". A claim from memory alone is
   stale by definition — label it Likely with the assumption named.
g. **Quality-of-life improvements** — concrete upgrades the owner would feel:
   real cancellation that cleans up, retry/backoff on flaky network, clearer
   error messages, progress feedback, sensible defaults, small UX wins.
   Labeled Suggestion, never presented as defects.
h. **Code health** — dead code, duplicated logic drifting out of sync,
   structure that will breed future bugs. Only real friction — not taste.

## Step 3.5 — Stack-specific probes (load what Step 1 detected)

These are the highest-yield, named checks per stack. Apply every probe that
fits; add the finding to whichever universal pass it belongs to.

**If `~/.claude/skills/robogears-audit/references/` exists on this machine,
ALSO read the matching `stack-*.md` file(s) for the detected stack(s)** — they
carry deeper, CVE-pinned probe lists (e.g. the yt-dlp/ffmpeg config-autoload
and specific-CVE checks). The blocks below are the self-contained fallback when
those files are absent.

**Electron / desktop web** — treat the renderer as fully compromised.
- Isolation: `contextIsolation:true` (flag false/unset), `nodeIntegration:
  false`, `sandbox:true`, `webSecurity` on, no `allowRunningInsecureContent`,
  no `@electron/remote`; a restrictive CSP with no `unsafe-inline`/`unsafe-eval`;
  `setWindowOpenHandler` + `will-navigate` guards.
- IPC: validating a handler's **arguments is necessary but not sufficient** —
  also validate the **sender** (`event.senderFrame` origin), because
  `ipcMain.handle` fires for any subframe/iframe/attached webview. No generic
  "escape-hatch" channel; `{ok,error}` return envelope; never expose raw
  `ipcRenderer`.
- `shell.openExternal`/`openPath`: scheme-allowlist to `https:`. Deep-link/
  second-instance argv can smuggle Chromium switches (`--gpu-launcher`) —
  terminate parsing with `--`.
- Bundled binaries (yt-dlp/ffmpeg) carry their own CVE stream that `npm audit`
  can't see: invoke yt-dlp with `--ignore-config --no-plugin-dirs --no-exec`
  (a `yt-dlp.conf` planted in the spawn cwd or download dir is an RCE path);
  anchor the `-o` output-template extension; minimize ffmpeg
  `-protocol_whitelist`. Pin the bundled version and check it against known
  CVEs (yt-dlp config-in-cwd RCE fixed 2024.07.01).
- Fuses (read with `npx @electron/fuses read`): `RunAsNode`,
  `EnableNodeCliInspectArguments`, `EnableNodeOptionsEnvironmentVariable` must
  be **false**; `OnlyLoadAppFromAsar` / asar integrity **true**.
- Packaged-build bug: a raw `require('ffmpeg-static')` path resolves inside
  `app.asar` where `spawn` can't reach — works in dev, fails packaged; needs
  `asarUnpack` + an unpacked() path helper.
- Updater: HTTPS host-pinned and re-checked on every redirect hop + hash/
  signature verify + downgrade protection before swapping.

**Web server / API (Flask / FastAPI / Django / Express)** —
- Enumerate every route; **subtract the auth-exemption allowlist** to get the
  set of routes reachable with no login — flag any exempt route that reads or
  changes user data. Watch over-broad `startswith` prefixes (`/api/public`
  also matches `/api/public-admin`) and bypass flags spoofable via a client
  `X-Forwarded-For`.
- IDOR / missing authorization (an object fetched by id with no owner check);
  mass assignment (`Model(**request.json)`, DRF `fields='__all__'`).
- Command injection on request data (`shell=True`, `os.system`); SSRF (a
  user-supplied URL reaching `localhost`/RFC1918/`169.254.169.254` — block
  after DNS resolve, re-validate every redirect hop); unsafe deserialization
  (`pickle`, `yaml.load` without SafeLoader); SSTI (`render_template_string`
  on request data); SQL via f-string/`.format`.
- `DEBUG=True` (the Werkzeug console is remote RCE); hardcoded
  `SECRET_KEY`/`JWT_SECRET` (session forgery); bound to `0.0.0.0`
  unintentionally; CORS `*` with credentials; `send_file`/static traversal.

**Docker (overlay — audit this AND the app-stack profile)** —
- Base image pinned by digest (`FROM img@sha256:…`), not a floating `:latest`.
- Runs as non-root (`USER` set); if it drops privileges via `gosu`/`su-exec`,
  confirm `SIGTERM` still propagates. `root` + mounted `docker.sock` = escape.
- Secrets baked in three ways: `ARG` secrets persist in `docker history`;
  `COPY . .` sweeps a real `.env` into a layer; `env_file: .env` mounts live
  secrets. Confirm `.dockerignore` excludes `.env`/`.git`/secrets.
- Ports: internal services bound `127.0.0.1:` not `0.0.0.0:`.
- QoL: `cap_drop:[ALL]`, `no-new-privileges`, `read_only` rootfs, `HEALTHCHECK`,
  resource limits.

**Swift / macOS native** — there is **no IPC/preload boundary; the whole app
is inside the trust boundary.** The unauthenticated input is the URL-scheme /
universal-link / `onOpenURL` / XPC handlers — validate host+action against an
allowlist and reject `..`/absolute paths (a real path-traversal has lived
here).
- Entitlements (`codesign -d --entitlements -`): App Sandbox on; flag
  `com.apple.security.cs.allow-jit`, `allow-unsigned-executable-memory`,
  `disable-library-validation`, and over-broad file scope.
- `Process`/`NSTask`: `/bin/sh -c` with an interpolated string is injection;
  safe form is `executableURL` (absolute) + `arguments: [String]`.
- URLSession: a delegate that calls
  `completionHandler(.useCredential, URLCredential(trust: serverTrust!))`
  unconditionally is the trust-all-certs bypass (Critical); ATS disabled via
  `NSAllowsArbitraryLoads`.
- Secrets in `UserDefaults`/plist/plain file vs Keychain; weak
  `kSecAttrAccessibleAlways`.

**Browser extension** — assume every page and every message sender is hostile.
- `permissions`/`host_permissions` are the blast radius (`<all_urls>`,
  `cookies`, `downloads`, `scripting`, `nativeMessaging`).
- Every `onMessage`/`onMessageExternal`/`window` message handler must validate
  `sender.id`/`sender.origin`/`event.origin` BEFORE routing into a privileged
  `chrome.*` sink (the extension form of the Electron sender rule).
  `window.addEventListener('message')` with no `event.origin` check is driven
  by any page.
- Content-script DOM injection (`innerHTML` from page/message data = XSS in a
  privileged context). `web_accessible_resources` scoped to `matches`, CSP not
  weakened, `externally_connectable` not `*://*/*`.
- A native-messaging host is an OS-level command-injection boundary — audit
  it as [CMD].

**C# / .NET (loads the Windows overlay too)** —
- `Process.Start`: `UseShellExecute=true` with a user-controlled `FileName`
  resolves protocol handlers; a concatenated `Arguments` string is re-split by
  Windows (argv smuggling) — use `ArgumentList`; allowlist `Uri.Scheme` before
  launching a URL.
- Bare-name `[DllImport]`/`LoadLibrary` goes through the DLL search order
  (plantable) — use an absolute path or `SetDefaultDllDirectories`.
- A cert-validation callback returning `=> true` = process-wide MITM
  (Critical, even if labeled dev-only). `BinaryFormatter` any use = Critical;
  Json.NET `TypeNameHandling != None` without a locked binder = RCE.
- Cleartext secrets/connection strings in `app.config`/`appsettings.json`.

**C++ (CMake / Qt)** —
- Name every untrusted-file parser (the fuzz targets). **Integer overflow
  before allocation** (`malloc(count*size)` with `count` from an untrusted
  header) is the root cause of most parser overflows — demand a checked
  multiply. `memcpy` length read from input; non-literal format strings;
  `operator[]` vs `.at()` on an untrusted index.
- `QProcess` single-string `start()` re-splits on whitespace/quotes — use the
  `QStringList` argv overload. Qt TLS `setPeerVerifyMode(VerifyNone)` /
  blanket `ignoreSslErrors()` = Critical.
- Fork-CVE-drift: find the merge-base with upstream, measure how far behind,
  and check each vendored lib pin (zlib/libpng/openssl) against CVEs by
  reachability — a class no scanner surfaces.

**CLI (Rust / Go / Node single-binary)** —
- Shell interpolation (`sh -c`, `child_process.exec`, `spawn shell:true`) on
  argv/stdin/file input = Critical. Argument injection: an untrusted value
  starting with `-` smuggles a flag — require a `--` separator. Bare-name
  `$PATH` resolution picks up a cwd-planted binary (Go/Windows) — use an
  absolute path.
- **Zip-slip**: verify a joined extraction path stays under the target dir
  AFTER resolving `..`; reject symlink/hardlink entries. TLS killswitches:
  `danger_accept_invalid_certs`, `InsecureSkipVerify:true`,
  `rejectUnauthorized:false`, `NODE_TLS_REJECT_UNAUTHORIZED=0`.
- Lockfile committed AND enforced (`npm ci`, `cargo build --locked`, `go mod
  verify`); `preinstall`/`postinstall` scripts; Rust `unsafe` blocks
  (`transmute`, `from_raw_parts` with an input-derived length).

**Windows overlay (any stack shipping a Windows artifact)** —
- A per-user (writable) install dir + an elevated component (admin/SYSTEM
  service, task, helper) launched from it = local privilege escalation.
- Unquoted service `ImagePath` / `shell\open\command`; DLL search-order
  hijack via co-located DLLs; NTFS ADS (`file:stream` bypasses an extension
  check); Mark-of-the-Web stripped from downloads (kills SmartScreen).

**House suite (william's apps)** — load when the scaffold is detected
(a `contextBridge` allowlist, `update:*` IPC channels, a `RELEASE_NOTES.md`
contract, an `UPDATER_API_BASE` guard, or a `bin-path.js` helper). Several
deliberate house patterns look like vulnerabilities to a naive auditor —
flagging them is noise that buries the real findings.
- **Sanctioned — verify the guard, never flag the mechanism.** The
  `UPDATER_API_BASE` dev override is fine *if* gated at EVERY request site
  (check + artifact + `.sha256` sidecar) by one `overridesAllowed()` predicate
  — a split or missing guard is the Critical, not the override itself.
  Ad-hoc signing + `codesign` re-sign-after-swap is the house default (missing
  Developer ID is not a finding). Warn-and-proceed on a **missing** legacy
  sidecar is deliberate — but a **present** sidecar that mismatches must still
  abort. Servers are notify-only by policy.
- **Grade updater integrity on the honest ladder.** Missing GitHub host-pinning
  re-checked per redirect hop = Critical; the same-channel SHA-256 sidecar is a
  corruption gate, not tamper-proofing — do not grade its same-channel nature
  as Critical; a missing detached signature is a Suggestion for ad-hoc apps.
- **Contract probes by name.** Checksum mismatch must abort BEFORE staging with
  the `{ ok:false, error:'Checksum mismatch — download corrupted or tampered' }`
  envelope; 403/429 → a typed rate-limited status + a persisted ETag (the
  unauthenticated 60-req/hr GitHub quota is shared across every house app on the
  machine); asset-name lockstep with no wrong-arch fallback (only
  `-universal.dmg` is legal); `update:download` takes no renderer-supplied URL;
  NSIS silent apply must be `['/S','--force-run']` detached; version compare is
  numeric-segment + prerelease-aware.

## Step 4 — Evidence, IDs, and adversarial verification

**Evidence.** Every finding carries: exact `file:line`, the offending code
quoted (≤ 6 lines), a severity (**Critical / High / Medium / Low /
Suggestion**), a confidence (**Confirmed / Likely / Speculative**), and a
**Fix risk** (**Safe / Needs-verification** — Safe = remediation can't change
behavior on an untestable path, e.g. tightening a sanitizer; Needs-verification
= it touches a live or untestable path like auth or an updater swap). Defect
findings (bugs, vulnerabilities, crashes, data loss) must be traced as actually
REACHABLE in how the code really runs before you write them up. No theory
dressed as fact: if a scary-looking line is safe in context (constant input,
validated upstream, unreachable), omit it or say "looks risky, is actually fine
because X". Hardening/QoL recommendations are exempt from the reachability check
— report them as Suggestions labeled "hardening"/"improvement", never as
confirmed vulnerabilities.

Severity calibration anchors: outside data able to run commands or escape its
folder → Critical/High; silently losing or corrupting ANY data the user expects
kept (even a log/history entry) → at least **Medium**; a silent failure that
hides a warning but loses nothing → Low.

**Stable finding IDs — one exact, deterministic recipe.** Repeat audits diff by
matching IDs, so the same finding must produce the same ID across independent
runs. Give each finding `<CAT>-<slug>-<hash8>`:

- **`CAT`** is fixed by the **sink type**, not by which pass or finder spotted
  it (resolve overlaps by this table, top-to-bottom, first match wins):
  command/shell execution → `CMD`; filesystem/path/traversal → `FS`;
  network/TLS/SSRF → `NET`; dependency/advisory → `DEP`; lifecycle/resource
  leak → `LIF`; error-handling/swallowed → `ERR`; data-safety/corruption →
  `DAT`; any other security → `SEC`; quality/code-health → `QUA`; QoL/hardening
  → `QOL`. A stack-probe prefix (`ISO`, `IPC`, `ROUTE`, `MEM`) is used ONLY for
  that named probe's checks.
- **`slug`** is a 2–3-word kebab title (human-readable only; matching ignores
  it — see Step 6).
- **`hash8`** = the first 8 hex of:
  ```
  printf '%s|%s|%s' "<CAT>" "<repo-relative path>" "<sink line>" | shasum | head -c 8
  ```
  where `<repo-relative path>` is exactly as `git ls-files` prints it (no
  leading `./`), and `<sink line>` is the **single line containing the sink**,
  its internal whitespace runs collapsed to one space and trimmed — **not** the
  multi-line evidence snippet you quote in the report (the quoted snippet may be
  longer; the hash always uses exactly one line). If two genuinely different
  findings collide on `hash8`, append a discriminator to the printf input
  (`…|2`) rather than altering the snippet — never mutate the real snippet just
  to dodge a collision.

Compute all IDs in **one batched Bash call** after findings are merged (one
`printf … | shasum | head -c 8; echo` line per finding), not one shell
invocation per finding. Example ID: `CMD-url-shell-interp-a1b2c3d4`.

**Adversarial verification (the trust gate).** Before any Critical or High
finding enters the report, hand it to a FRESH skeptic whose only job is to
**disprove** it — default verdict: not real.
- With subagents available: spawn one verifier per Critical/High finding. Its
  prompt = the read-only block + **the "What this project is" description and
  the trust-boundary summary from Step 1** + the finding (claim, file:line,
  snippet, why) + **the finder's claimed call path (entry point → … → sink,
  file:line at each hop)** + "Try to refute this. Your job is to break the
  supplied trace, not to rediscover the architecture. Is it actually reachable
  and exploitable as described, given how the code is really called? Default to
  false unless you can confirm it. If you cannot settle reachability either way,
  return **unsettled** (never 'no'). Return: real? (yes / no / unsettled),
  corrected severity, corrected confidence, corrected fix-risk, one-line
  reasoning."
- Without subagents: re-examine each Critical/High yourself in a separate,
  explicitly skeptical pass, re-tracing the call path and arguing the case for
  it being a false positive first.
- A finding that survives keeps the verifier's (possibly adjusted) labels, and
  its report line ends with `Verified by: <one line>`. A finding refuted
  (`no`) is dropped; one returned **unsettled** moves to **Needs
  investigation**. Medium/Low/Suggestion don't need the gate, but apply the
  same false-positive honesty.

Anything you suspect but cannot pin to a line goes in **Needs investigation**,
not Findings.

## Step 5 — Depth: fan out, then loop until dry

- **Fan out (default when subagent/workflow orchestration is available).** Run
  Step 1–2 yourself so all finders share one trust-boundary model, then spawn
  parallel finders by cluster, each carrying the read-only block **and the
  Step-1 trust-boundary summary**: (1) secrets + dependencies; (2) injection
  surface — command execution, filesystem, network; (3) lifecycle, errors, data
  safety; (4) quality + QoL; plus **one finder per detected stack profile**
  pointed at its Step-3.5 probe block. Each finder returns findings as text AND
  a `FILES READ:` list. Merge results, then run the adversarial gate (Step 4) on
  every Critical/High. Without orchestration, work the clusters sequentially in
  one context — the same rules apply.
- **Loop until dry.** A first pass always reveals new surface — an undocumented
  IPC channel, an unlisted route, a second subprocess site, a parser you hadn't
  traced. Spawn targeted follow-up finders for that new surface (scoped to it,
  not a full re-read) and repeat. **Stop only when a full sweep yields no new
  Confirmed or Likely finding** (typically 1–2 extra sweeps). If you cap the
  loop for cost, say so in the report — never silently.
- **Dedupe by root cause.** Two finders reporting the same sink (same file:line,
  or the same underlying sink reached by two paths) merge into one finding at
  the highest severity. Dedupe against everything seen across all rounds; a
  sweep is "dry" only when it adds no finding not already in that merged set.
- The completion gate is the coverage ledger complete AND a dry sweep — not
  elapsed time.

## Step 6 — Re-run diffing (baseline + last-run record)

Before writing findings, look for `AUDIT_BASELINE.md` at the repo root. It is a
plain-Markdown file the **owner** maintains (you never write it). It has two
optional parts:

```markdown
# AUDIT_BASELINE.md

last_run: 2026-07-01 · commit a1b2c3d
ids: CMD-shell-interp-a1b2c3d4, SEC-hardcoded-key-9f8e7d6c, NET-plain-http-11223344

| id                       | status   | justification                    | approver | expires    |
|--------------------------|----------|----------------------------------|----------|------------|
| NET-plain-http-11223344  | accepted | dev-only override, guarded       | william  | 2026-12-31 |
```

- **`last_run:` + `ids:`** — the full findings-index ID list from the previous
  report. This is what makes FIXED/STILL-OPEN diffing possible.
- **The table** — previously triaged findings, each with a `status`
  (`accepted` / `wontfix` / `deferred`), a justification, an approver, and an
  optional `expires: YYYY-MM-DD`. A blank `expires` means never.

**Match on `CAT` + `hash8`, ignoring the slug** (a re-titled finding still
matches its baseline entry). Tag each finding this run:

- Found now, in the table (accepted/wontfix/deferred), not past `expires` →
  **ACCEPTED** — drop it out of the severity buckets into a collapsed
  "Accepted (N)" list.
- Found now, in the table but **past `expires`** → **REGRESSION** — the
  time-boxed acceptance lapsed; surface it normally.
- Found now, ID present in `last_run.ids` → **STILL-OPEN** (carried from last
  audit).
- Found now, ID **not** in `last_run.ids` and not in the table → **NEW**.
- ID in `last_run.ids` but **not found this run** → **FIXED** — collapse into a
  "Fixed since last audit (N)" list in the executive summary.

If no `last_run:` line exists, FIXED/STILL-OPEN can't be computed — say so, and
note that keeping the report file (below) enables it next time. If no baseline
exists at all, every finding is simply NEW — say the baseline is absent so the
owner knows the feature is available.

**You are read-only: never write or update the baseline yourself.** The report
ends by emitting a ready-to-paste baseline block (see report section 10) so
accepting findings and recording the run is copy-paste, not authoring.

## The report — bulleted, beginner-first, in this exact order

**Deliver the report as a file by default.** Under rule 4, ALWAYS write the
complete report as Markdown to the session scratchpad (outside the audited
tree) and tell the user the path. In chat, post the **provenance line,
executive summary, and findings-index table** plus that path. This keeps the
chat scannable and gives a fix-pass session a real artifact to load. Never
write inside the project tree.

**Then ALWAYS build the visual breakdown and present it** — this is a required
final deliverable, not an offer. It is a beginner-first, at-a-glance rendering
of the SAME findings, specified in "The visual breakdown" section below. Build
it after the Markdown report is complete, from the already-verified findings —
it introduces no new claim, number, or ID that isn't already in the Markdown.

Report order:

1. **Provenance line** — repo · branch · commit · dirty? · date · scope ·
   stacks detected.
2. **Executive summary** — one plain-English paragraph on overall health;
   counts by severity (`Critical N · High N · Medium N · Low N · Suggestion N`,
   plus `Accepted N` / `Fixed N` if a baseline/last-run was used); the top 3–5
   fixes, each phrased as an action a non-programmer could say aloud ("stop
   keeping your API key inside the code"), each pointing to its full finding.
3. **Severity legend** — one line per label, defined by consequence:
   - Critical — someone could take over the app, run their own commands on
     your computer, or you could lose data today.
   - High — a real hole or bug that will bite under normal use.
   - Medium — a real problem with a smaller blast radius or an awkward
     trigger; silently losing any data you expected to keep starts here.
   - Low — minor correctness or hygiene; fix when convenient.
   - Suggestion — not a defect; a concrete improvement worth considering.
   - (Confidence: Confirmed — traced and definitely real; Likely — strong
     evidence, one stated assumption; Speculative — a hunch worth checking.
     Fix risk: Safe — safe to auto-apply; Needs-verification — a human should
     confirm the fix.)
4. **What this project is** — the plain-English description from Step 1, plus
   which stack-specific probe blocks ran.
5. **Coverage** — the N-of-M numbers and counted skips from Step 2, and how
   many finder sweeps ran before the audit went dry.
6. **Findings index** — a scannable table before the detail, one row per
   finding: `| ID | Severity | Confidence | Fix risk | Location | Title | Status |`
   where Status is New / Still-open / Fixed / Accepted / Regression.
7. **Findings** — grouped Critical → High → Medium → Low → Suggestions.
   - **Critical / High / Medium** use the full block below.
   - **Low / Suggestion** use a compact form — one "what & why" sentence,
     `file:line`, a one-line fix, the ID, and Fix risk; snippet optional. (The
     per-finding jargon-gloss rule still applies.)

   Full block:
   - **What's wrong** — one sentence a non-programmer can follow.
   - **Where** — `file:line`.
   - **Why it matters** — what the owner or an attacker actually experiences.
   - **How to fix** — opens with a plain-English action before any technical
     specifics.
   - **Evidence** — the ≤ 6-line snippet, introduced by one sentence saying
     what the reader is looking at. Never paste code without that caption.
   - **ID · Severity · Confidence · Fix risk · Verified by** (the last for
     Critical/High).

   Plain-language rule for the WHOLE block: every technical term gets a short
   parenthetical explanation the first time it appears in EACH finding — assume
   the reader jumps straight to any one finding.
8. **Healthy areas** — one line per pass/probe that came back clean, so clean
   is distinguishable from unchecked. (For `quick`/`security`/`qol`/delta runs,
   put un-run areas under **Not audited this run**, never here.)
9. **Needs investigation** — suspicions that couldn't be pinned to a line (plus
   any finding a verifier returned **unsettled**), and what would confirm or
   kill each.
10. **Suggested next steps & handoff** — an ordered fix-first checklist. Then:
    - A ready-to-paste `AUDIT_BASELINE.md` block in the Step-6 format —
      a `last_run:` line with today's date + commit and the full findings-index
      ID list, plus a table pre-filled with any IDs the owner may want to
      accept (status `accepted`, a one-line justification stub, approver
      `william`, a suggested `expires`). Tell them: "save this as
      `AUDIT_BASELINE.md` at the repo root yourself — the audit will not write
      it."
    - A ready-to-paste **fix-pass handoff prompt** for a fresh session: the top
      finding IDs with their one-line fixes and the instruction "Apply the
      **Safe** ones; pause and ask on **Needs-verification**." Point it at the
      saved report file path.
    - End by OFFERING the fix pass — this run is locked read-only. Never start
      fixing.

### Worked example finding — copy this shape exactly (Critical/High/Medium)

- **What's wrong:** The filename a visitor types is pasted straight into a
  terminal command, so a filename like `; rm -rf ~` would run as a real command
  on your computer. (This is "command injection" — sneaking extra commands into
  text a program hands to the terminal.)
- **Where:** `src/fetcher.js:12`
- **Why it matters:** Anyone who can influence that filename — a website title,
  a pasted link, another user — can make your computer run their commands, not
  just save a file. That's the worst kind of security hole.
- **How to fix:** Stop building the terminal command out of the user's text.
  Pass the filename as a separate argument instead, so the terminal treats it
  as plain text: use `execFile('curl', ['-o', filename, url])` (an "argument
  array" — each piece stays data, never becomes a command).
- **Evidence:** The line below glues the user's filename and URL directly into
  one command string:
  ```js
  exec('curl -sL -o saved/' + filename + ' ' + url, callback);
  ```
- **ID:** `CMD-filename-shell-interp-a1b2c3d4` · **Severity:** Critical ·
  **Confidence:** Confirmed · **Fix risk:** Safe · **Verified by:** fresh
  skeptic traced input → `exec`; the value is unescaped and reachable on the
  normal save path.

  (hash8 came from
  `printf '%s|%s|%s' "CMD" "src/fetcher.js" "exec('curl -sL -o saved/' + filename + ' ' + url, callback);" | shasum | head -c 8`.)

### Compact example (Low/Suggestion)

- **Retry flaky downloads** — a dropped network connection aborts the whole
  download with no retry, so a blip means starting over. Add retry-with-backoff
  around the fetch. `src/download.js:44` · `QOL-download-retry-5e6f7a8b` ·
  Suggestion · Fix risk: Safe.

## The visual breakdown — the presented deliverable

After the Markdown report is written, ALWAYS build a **visual, at-a-glance
breakdown of the same audit and present it to the user** — for every run,
clean or not. It is the face of the audit; the Markdown stays the source of
truth and the fix-pass handoff.

**Format — a self-contained HTML page (theme-aware).** HTML renders the
severity tiles, finding cards, and clean-checklist exactly; it reads in light
and dark; it is shareable and can be exported to PDF later. Do NOT ship the
breakdown as plain Markdown (it can't do the tiles/cards) — Markdown is
already the report. Offer a **PDF export of the same page on request**; never
make PDF the primary.

- Load the `artifact-design` skill first (the Artifact tool requires it), and
  lean on `frontend-design` for the aesthetic so it doesn't read as a
  template. Near-black surface, restrained accent keyed per severity.
- Self-contained ONLY: inline all CSS, no external fonts/CDN/scripts, embed
  nothing remote (a strict CSP blocks it). Style both light and dark.
  Responsive; wide code/evidence scrolls inside its own box — the page itself
  never scrolls sideways.
- **Present it by the best means the environment allows:** publish it as an
  Artifact when that tool is available (default-private to the user's own
  account); otherwise write the `.html` to the scratchpad and send it to the
  user to render, or at least give the path. Either way, say it's ready and
  where it is, alongside the Markdown path.

**Structure — top to bottom (mirror the report's data, invent nothing new):**

1. **Status header** — a small "READ-ONLY AUDIT · COMPLETE" eyebrow, the
   project name as title, then the provenance line (§1): path · scope · date ·
   repo/commit · stacks.
2. **Verdict banner** — the one-line health verdict in plain English
   ("Clean — safe to ship", or "N critical to fix first"), colour-keyed to the
   worst severity present, with the executive-summary sentence (§2) beneath.
3. **Severity tiles** — five tiles Critical / High / Medium / Low / Suggestion
   showing the counts (add Accepted / Fixed when a baseline was used); a
   zero-count tile is visibly muted so "0 critical" reads at a glance.
4. **Do this before you {send,ship}** — the top 3–5 fixes (§2) as action
   cards, each a spoken-aloud instruction a non-programmer could say, tagged
   with the file and finding ID it maps to.
5. **Findings** — one card per finding, grouped Critical → Suggestion: a
   severity badge, the ID in monospace, the plain-English what/why, the
   `file:line`, the ≤6-line evidence in a monospace block (with its caption),
   and the one-line fix. Match §7's content.
6. **Verified clean** — the Healthy-areas checklist (§8) as green-ticked
   one-liners, so "checked and clean" is visibly distinct from "not checked".
7. **Coverage** — the N-of-M files, counted skips, sweeps-until-dry, and the
   baseline/NEW note (§5).
8. **Footer** — "This audit changed nothing — strictly read-only" and the
   Markdown report's filename.

**Fidelity & safety.** Every count, ID, `file:line`, and the verdict must
match the Markdown exactly — the page renders the verified findings, it does
not generate or re-judge any. Secrets stay mechanically truncated to 10
characters here too. The HTML is written outside the audited tree and only
ever reads the findings already produced — never write it into the project.

## Anti-patterns — do not do these

- Do not modify, format, or "clean up" anything in the project tree. Report
  only.
- Do not let an unverified Critical/High into the report — the adversarial gate
  is mandatory for those.
- Do not invent findings, inflate severity, or pad passes that came back clean.
- Do not report a theoretical issue as Confirmed; use the confidence labels
  honestly.
- Do not paste code without its one-sentence caption, or let jargon through
  without its parenthetical gloss.
- Do not give generic security lectures — tie everything to this project's
  actual lines.
- Do not mark half the tree "skipped"; only the Step-2 reasons are valid.
- Do not stop after one pass — loop until a sweep is dry.
- Do not write or update the baseline file; emit the paste-ready block and let
  the user save it.
- Do not fabricate or guess a finding ID hash — run the batched command.
- Do not start fixing at the end. Offer the fix pass instead.
- Do not skip the visual breakdown, or let it drift from the Markdown — same
  counts, IDs, verdict, and truncated secrets; it renders the findings, it does
  not invent or re-judge them. Never write it into the project tree.

## Before you deliver — final self-check

- Kickoff message sent (scope, stacks, file counts, plan)? Size gate honored on
  a big repo?
- Coverage ledger complete and stated, unioned from finder `FILES READ` lists?
  Every first-party file read (within scope)? Sweeps run until dry?
- Every Critical/High adversarially verified, with a `Verified by:` line;
  every `unsettled` moved to Needs investigation?
- Every finding: deterministic ID (batched `head -c 8`), file:line, severity,
  confidence, fix risk, plain-English throughout, jargon glossed per finding?
  Critical/High/Medium use the full block; Low/Suggestion the compact form.
- Report written to the scratchpad and its path given; chat kept to summary +
  index? Sections present and in order (1–10) with the findings index table?
- Baseline read (if present) and findings tagged New/Still-open/Fixed/Accepted/
  Regression on a CAT+hash8 match? Paste-ready baseline block + fix-pass handoff
  emitted? No baseline written?
- Nothing in the project tree touched? (`git status --porcelain` looks exactly
  as it did when you started.)
- Visual breakdown built from the final findings and presented (Artifact or
  rendered `.html`), with the same severity counts and IDs as the Markdown,
  secrets still truncated, and nothing written into the project tree?
- Ended with an offer, not a fix?
