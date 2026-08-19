---
name: github-ship
version: 0.1.1
description: >-
  github-ship (v0.1.1) — The exact workflow for committing and releasing ANY project to GitHub — Electron,
  Swift/macOS, C#/.NET, C++, Python, Rust, Go, Docker, browser extensions, CLIs,
  source-only — with or without CI. Three commands: push = commit to the current branch,
  no release; ship = cut a versioned, tagged GitHub Release left as a draft/unlisted (CI
  builds artifacts, or build locally with no CI); ship and publish = ship, then flip the
  draft to a public Latest release. Use whenever the user says "push", "ship", "ship
  it", "ship and publish", "publish", "cut a release", "tag a version", or close
  paraphrase — it disambiguates and runs the right sequence (detection, version bump,
  RELEASE_NOTES rewrite, tag, watch CI, verify body, publish only when authorized). Also
  for failed release builds, empty release bodies, tags that didn't trigger CI, first
  releases, prereleases/RCs. NOT for wiring an in-app updater (github-updater), testing
  one (updater-e2e-harness), or scaffolding a new app (robogears-app-init).
---

# Shipping & pushing to GitHub

This skill encodes a specific, deliberate vocabulary and the exact command sequences
behind each word. Getting the vocabulary right is the whole point — the three commands do
very different things, and doing the wrong one wastes a CI build or exposes a release
before it's reviewed.

## The vocabulary — memorize this first

The user speaks in three commands. **Never guess which one they mean; the words are
precise.**

| Command | Means | Result |
|---|---|---|
| **push** | Commit to the current branch and push. | New commit on the branch. **No** version bump, **no** tag, **no** release, **no** release build. |
| **ship** | Cut a full versioned release, but leave the GitHub Release as a **draft (unlisted)**. | Version bumped, tag pushed, artifacts built (CI or local), a **draft** Release exists with notes + artifacts. The public sees **nothing** yet. |
| **ship and publish** | Everything `ship` does, then make it **public**. | The draft is flipped to a **published, Latest** Release. Users (and any in-app updater) now see it. |

Corollaries the user relies on:
- **"publish"** on its own, when a draft already exists for the current version, means
  just the final flip-to-public step. **Re-verify the draft first** (see that workflow) —
  a draft may have sat for days, been rebuilt, or lost its body since it was cut.
- **"ship"** alone always stops at draft. Do **not** publish unless the user said
  "publish". A draft is your safety net: they review artifacts + notes, then decide.
- **"push"** is not a release. Don't bump the version or tag on a plain push. Batching
  several pushes and then one `ship` is the normal rhythm — releases are deliberate,
  not per-commit.
- A **prerelease** ("ship an rc/beta") is a `ship` variant — see "Shipping a
  prerelease" below. It publishes with `--prerelease` and is never Latest.

> **Why "unlisted" = draft:** GitHub has no true "unlisted" release. A **draft** release
> is visible only to repo collaborators, is excluded from `releases/latest` and the
> public Releases list, and its assets aren't downloadable by the public — exactly the
> "shipped but not announced" state the user means by *ship*. Publishing removes the
> draft flag and (with `--latest`) marks it Latest. Bonus: draft-first is also GitHub's
> officially recommended flow for **immutable releases** (drafts stay editable; only
> publication locks anything — see the immutability notes below).

## Never ship or publish unprompted

Only run `ship` / `ship and publish` when the user explicitly says so. If they asked for a
**code change** (not a ship), make the edit, run a build/typecheck if applicable, and
**stop** — do not bump the version, touch RELEASE_NOTES, tag, or publish. Each release
costs a build and creates a new draft; the user wants to batch related work into one
cohesive release. A plain **push** is fine when they say "push" or "commit" — but that
too is on request, not reflexive.

---

## Workflow: `push`

The simplest command. Commit the relevant work to the current branch and push.

```bash
git status                    # see what's changed; know the current branch
# Stage EXPLICITLY by path — never a blind `git add -A`. If you do use -A, the junk
# gate below is BLOCKING:
git add path/to/changed/file ...
```

**The junk gate** — run before *every* commit this skill makes (push commits AND the
ship release commit). It knows every ecosystem's build output, not just npm's:

```bash
JUNK='\.env(\.|$)|\.pem$|\.key$|\.p12$|\.keystore$|\.netrc$|(^|/)credentials?\.(json|ya?ml|txt)$|(^|/)node_modules/|(^|/)dist(/|$)|(^|/)out(/|$)|(^|/)build/|(^|/)release(/|$)|(^|/)target/|(^|/)bin/|(^|/)obj/|__pycache__|\.pyc$|\.egg-info|(^|/)\.?venv/|(^|/)Pods/|(^|/)DerivedData/|(^|/)\.build/|(^|/)cmake-build-|\.log$|\.DS_Store'

# 1) Anything staged that .gitignore already bans is junk, no exceptions:
git diff --cached --name-only | git check-ignore --stdin && echo "!! gitignored files staged"

# 2) The pattern gate — this block FAILS (non-zero) when junk is staged; the commit
#    must not happen until a re-run prints nothing:
if git diff --cached --name-only | grep -iqE "$JUNK"; then
  echo "!! junk staged — unstaging:"
  git diff --cached --name-only | grep -iE "$JUNK" | while IFS= read -r f; do
    git restore --staged -- "$f"
  done
  false   # BLOCKING: stop, re-check, only commit when the grep matches nothing
fi
```

A few repos track paths the pattern flags as **source** (an electron-builder
`build/after-pack.js` signing hook, a `bin/` of scripts). The gate is a prompt to look,
not an absolute ban — re-stage a flagged path only when you've confirmed it's tracked
source. But `dist/`, `node_modules/`, `target/`, `__pycache__/`, logs, and anything
matching `git check-ignore` are never source.

```bash
git commit -m "$(cat <<'EOF'
<concise summary line>

<1-3 short paragraphs: what changed and why. Root cause if it's a fix.>

Co-Authored-By: <your agent/model name> <noreply@anthropic.com>
EOF
)"
git push origin <current-branch>
```

That's it — no tag, no version bump, no release. Report the commit hash and that it's
pushed. If the push is rejected because someone pushed in between, `git pull --rebase
origin <branch>` (usually clean) and push again.

---

## Workflow: `ship` (→ draft / unlisted)

The full release sequence, stopping at a **draft**. Follow it in order.

### 0. Detect the project & discover the current state

**First, know what you're shipping.** Read
`references/project-type-detection.md` — it maps signal files → ecosystem →
version file(s) → post-bump hook → release path (tag-triggered CI / **no-CI local
build** / Docker image / source-only). Two verified traps: a `package.json` with no
`"version"` field (dependency-only — not an npm project) and a `pyproject.toml` that's
only tool config (not packaging). Detection decides everything that follows.

Then the preflight — look before you leap:

```bash
gh auth status                                  # authenticated, and as the RIGHT account
gh repo view --json nameWithOwner --jq .nameWithOwner
git remote get-url origin                       # ^ must be the SAME repo — in forks/multi-remote
                                                #   clones gh can resolve to the wrong one
                                                #   (fix: gh repo set-default).
gh api user --jq .login                         # WHO you are, vs the owner segment above —
                                                #   login ≠ owner is fine ONLY when the login is
                                                #   an expected collaborator for THIS repo.
gh repo view --json viewerPermission --jq .viewerPermission
                                                # must be WRITE or ADMIN. Multi-account setups:
                                                #   gh auth switch to the owning account BEFORE
                                                #   any mutating command.
git fetch --tags --prune origin                 # local-only tag lists collide on a 2nd machine
git tag --list 'v*' --sort=-v:refname | head -5 # candidate base for the next number, BUT:
gh release list --limit 10                      # cross-check: suffixed tags (v1.2.3-rc.1) sort
                                                # ABOVE v1.2.3 in versionsort — pick the next
                                                # number from the latest PUBLISHED non-prerelease
                                                # release, and spot stale unpublished drafts

# PREFLIGHT GATE — all must hold before you bump:
git status --porcelain --untracked-files=no     # tracked changes: must print NOTHING (blocking —
                                                # uncommitted work would leak into the release)
git status --porcelain                          # untracked-only leftovers: warn + confirm, don't block
git rev-parse --abbrev-ref HEAD                 # the intended release branch. Don't assume main:
                                                # confirm against `gh repo view --json defaultBranchRef`
                                                # and ASK if they differ. Abort on detached HEAD.
git status -sb                                  # not diverged from origin/<branch>
```

**Zero tags so far?** This is a **first release** — see "First release ever" below
before continuing.

**Stale-draft hygiene:** if `gh release list` shows an *older* unpublished draft (a prior
`ship` the user never published), don't stack a new one beside it. Supersede or delete
it: `gh release delete v<old> --yes --cleanup-tag` (one command removes the draft *and*
its orphaned tag; also `git tag -d v<old>` locally). Deleting a **draft** is always safe
tag-wise — drafts don't create the tag until publish, so the name stays reusable even
under immutability. Confirm with the user first if they might still want it.

### 0.5 Present the ship plan — then execute

Before touching any file, show the user a compact plan and get a nod (skip the pause
only if they said something like "just ship it, don't ask"):

```
Ship plan: <repo> (<detected type>)
  version:   v0.1.5 → v0.1.6 (patch)
  bump:      package.json, package-lock.json (+ npm run sync-ext → chrome-extension/manifest.json)
  notes:     RELEASE_NOTES.md rewritten from 7 commits since v0.1.5
  release:   tag v0.1.6 → CI workflow release.yml → draft with MyApp-0.1.6-arm64.dmg (+.sha256)
             [or: no CI — local build via <cmd>, then gh release create --draft]
  after:     draft only. Publishing is a separate "publish" command.
```

This is where a wrong version pick, a missed lockstep file, or "wait, that repo
releases from `dev`" gets caught — before anything is mutated.

### 1. Bump the version (patch by default)

Bump to the smallest unused `X.Y.Z` in **every** place the project tracks version, kept
in lockstep in the **same commit**. Patch by default; minor for a sizeable batch of
features; major only for breaking changes.

**Deliberately not adopted:** conventional-commit prefixes and bump-inference/changelog
tooling (release-please, changesets, semantic-release, git-cliff). This portfolio
commits straight to main with no PR flow, notes are hand-curated from the commit range
(`generate-notes` is a drafting aid only), and the bump level is a **human judgment**.
Revisit only if a PR-based flow ever appears.

**Dynamic-versioning check first:** if the project derives its version from git
(setuptools-scm / hatch-vcs, GitVersion, goreleaser, git-describe CMake) — or is Go,
where the tag *is* the version — there is **no file to bump**; the annotated tag does
it. Editing a fallback file breaks the build. Detection notes in
`references/project-type-detection.md`.

Otherwise, find every version-bearing file **mechanically** — grep for the OLD version
with boundaries so `0.1.5` doesn't match `10.1.5` or `0.1.50`:

```bash
git grep -nE '(^|[^0-9.])0\.1\.5([^0-9]|$)'   # the OLD version
```

Bump each real hit (skip changelog entries and coincidentally-matching pinned deps;
lockfiles are regenerated, not hand-edited). `references/version-bump-table.md` has the
exact file(s) and command per ecosystem — npm/pnpm/yarn, Python, Rust, Go, C#, Swift
(XcodeGen and raw pbxproj), CMake and version-in-code C++, Gradle/Maven, extensions,
subdirectory manifests, and **repo-specific post-bump hooks**. General rule: after
bumping, check the repo's task runner (`package.json` scripts, Makefile, justfile,
xtask) for a `sync`/`version` helper and run it before committing — e.g.
robogearsDownloader's `npm run sync-ext` propagates the version into the extension
manifest; miss it and you recreate the mismatch bug it exists to prevent. A version
that disagrees between tag, app, and artifact filename is the #1 cause of a broken
updater.

**Never force-move an already-published tag.** Bump to a new number instead. The only
exception: a tag whose release is still a **draft** AND the user confirmed a redo —
then you may delete the tag (local + origin) and re-tag the same version. (Under
**immutable releases** this stays safe for drafts — but a *published* immutable
release's tag name is burned forever, even after deleting the release. Recovery from a
botched published release is always bump-forward.)

### 2. Rewrite `RELEASE_NOTES.md` entirely

Replace the whole file with the new version's body (don't append — old versions stay on
the Releases page). CI feeds this file verbatim as the release body via `body_path`, so
the structure is a contract.

First confirm what the repo's release step actually consumes:
`grep -rn 'body_path\|notes-file\|generate_release_notes' .github/workflows/ 2>/dev/null`
(no workflows dir → Path B; notes travel via `--notes-file`; nothing to confirm) — this
skill's contract is `body_path: RELEASE_NOTES.md`; adapt if a repo differs, and if the
repo also maintains a `CHANGELOG.md` history file, prepend the new section there too
(RELEASE_NOTES stays current-version-only).

Draft the bullets from the actual commit range, not from memory:

```bash
prev=$(git describe --tags --abbrev=0 2>/dev/null)
git log "${prev:?no previous tag — first release, see below}"..HEAD --oneline
# optional extra raw material — GitHub's server-side draft (PR-less repos get plain
# commit lines; still useful for the auto compare link). target_commitish pins the
# range to YOUR branch — omit it and GitHub generates from the default branch:
gh api "repos/{owner}/{repo}/releases/generate-notes" -f tag_name="vX.Y.Z" \
  -f target_commitish="$(git rev-parse --abbrev-ref HEAD)" --jq .body
```

Don't confuse `--generate-notes` with `--notes-from-tag`, which merely reuses the
annotated tag's message as the body.

Turn those commits into user-facing bullets (group by feature, drop noise). See
`references/release-notes-template.md` for the template, per-project-type Install
sections, and worked examples.

**First release ever:** `git describe` fails with no tags — and unguarded, the
composed `git log ..HEAD` silently prints *nothing* (exit 0), leaving you drafting
notes from empty raw material. Use `git log --oneline` (full history) instead, take
the initial version from the manifest (or ask: v0.1.0 vs v1.0.0), and use
`https://github.com/<owner>/<repo>/commits/vX.Y.Z` in place of the compare link.

### 3. Commit, tag — with the consistency assert — then push

```bash
git add RELEASE_NOTES.md <every file the version bump touched>
# run the junk gate from `push` — the release commit gets the same protection
git commit -m "$(cat <<'EOF'
vX.Y.Z: <one-line summary>

<2-4 short paragraphs or tight bullets: what shipped and why.>

Co-Authored-By: <your agent/model name> <noreply@anthropic.com>
EOF
)"
git push origin <branch>

# CONSISTENCY ASSERT — the tag must equal what you just bumped. A typo'd tag
# (v0.1.61 for a 0.1.16 bump) passes every later check until the updater breaks:
V=X.Y.Z
git grep -nE "(^|[^0-9.])${V//./\\.}([^0-9]|$)" -- <bumped files>  # every bumped file moved
grep -q "v$V" RELEASE_NOTES.md                                     # notes heading matches

git tag -a "v$V" -m "v$V: <one-line summary>"
git push origin "v$V"           # <- THIS is what triggers CI. Don't skip it.
```

Use an **annotated** tag (`-a`). House policy is annotated but **unsigned** — `-a`,
not `-s`; no GPG key custody in this portfolio. If a repo ever requires signed tags,
`git tag -s` slots into the same sequence. Pushing the branch before the tag matters: if the
branch push is rejected (someone pushed in between), rebase, push the branch, *then*
tag and push the tag.

### 4. Build the artifacts — three paths

**Path A — tag-triggered CI (the default when `.github/workflows` has a release
workflow).** Don't grab the run immediately — it can take more than a few seconds to
register, and a one-shot lookup misdiagnoses that race as "the tag didn't trigger":

```bash
run_id=""
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
  run_id=$(gh run list --branch "vX.Y.Z" --event push --limit 1 \
    --json databaseId --jq '.[0].databaseId')      # on tag pushes, headBranch IS the tag
  [ -n "$run_id" ] && break
  sleep 5
done
[ -n "$run_id" ] || { echo "no run after 60s — see troubleshooting: tag trigger mismatch"; false; }
gh run watch "$run_id" --exit-status --interval 10
```

Block until every job finishes green. For long native builds that could outlive a
single watch call, poll instead: `gh run view "$run_id" --json status,conclusion`
in a bounded loop. If no run ever registers, see `references/troubleshooting.md`
("Tag pushed but no workflow runs").

**Path B — no CI (build locally, release with gh).** Several repos here (VibeLight,
VibeDolphin) release this way; do NOT invent a workflow mid-ship. Run the repo's
documented release build (per `project-type-detection.md`), emit checksum sidecars,
and create the draft directly:

```bash
<repo build command(s)>                                   # e.g. ./scripts/make-dmg.sh
for f in <artifacts>; do shasum -a 256 "$f" > "$f.sha256"; done
gh release create "vX.Y.Z" --draft --verify-tag --title "vX.Y.Z" \
  --notes-file RELEASE_NOTES.md <artifact files and .sha256 sidecars>
```

`--verify-tag` aborts if the tag wasn't pushed — exactly the guard you want. Steps 5–6
apply unchanged. (Offering to add a CI workflow is a separate, later conversation —
mention it, don't do it unprompted.)

**Path C — Docker/server image.** "Ship" = push the *versioned* image + a draft
release carrying the notes; "publish" = also move the `latest` tag (that's the
public-visibility flip for images):

```bash
docker buildx build --push -t "ghcr.io/<owner>/<repo>:vX.Y.Z" .   # or CI does this on the tag
gh release create "vX.Y.Z" --draft --verify-tag --notes-file RELEASE_NOTES.md
# publish step adds:  docker buildx imagetools create -t ghcr.io/<owner>/<repo>:latest \
#                        ghcr.io/<owner>/<repo>:vX.Y.Z
```

(ghcr needs `packages: write` in CI, or a `gh auth token | docker login ghcr.io` locally.)

### 5. Verify the draft — body AND expected assets. Do not skip this.

```bash
gh release view vX.Y.Z --json isDraft,body,assets \
  --jq '{draft:.isDraft, bodyLen:(.body|length), assets:[.assets[].name]}'
```

- `bodyLen` should be a few hundred+ chars. **If it's empty/tiny, find the root cause
  first.** The overwhelmingly common cause in this portfolio is a release *job* with no
  `actions/checkout` step — `body_path` reads from the workspace, so the body ships
  empty **every** release until the one-line fix lands. Immediate recovery so you can
  finish now, then commit the workflow fix:
  ```bash
  gh release edit vX.Y.Z --notes-file RELEASE_NOTES.md   # then re-verify
  ```
  Details + the rarer re-run case: `references/troubleshooting.md`.
- `assets` must match what the project type **expects** (decide from detection, not
  reflex): built artifacts **with the exact expected names** (an updater matches by
  filename) *plus their `.sha256` sidecars* for desktop apps; **legitimately empty**
  for source-only releases (GitHub's auto source tarballs don't appear in `.assets`);
  for Docker, verify the image instead:
  `docker manifest inspect ghcr.io/<owner>/<repo>:vX.Y.Z`.
- `draft` must be `true` (this is `ship`, not `ship and publish`).

### 6. Report and stop

Give the user: the CI run link (if any), the draft release link — `gh release view
vX.Y.Z --web` opens it for review — and state plainly that it's a **draft**. **Do not
publish.**

---

## Workflow: `ship and publish` (→ public / Latest)

Run the entire **`ship`** sequence above (steps 0–5, including body verification —
never publish an empty body). Then, and only then, flip it public:

```bash
gh release edit vX.Y.Z --draft=false --latest
gh release view vX.Y.Z --json isDraft,isPrerelease,url \
  --jq '{draft:.isDraft, prerelease:.isPrerelease, url:.url}'
```

`draft` must come back `false` and `prerelease` `false` (for a normal release).

**Post-publish confirmation — this is literally what the updaters see:**

```bash
gh api repos/<owner>/<repo>/releases/latest --jq .tag_name    # must print vX.Y.Z
```

If it returns an *older* tag: a stray prerelease flag or missing `--latest` — see
troubleshooting. Until it returns your tag, users' in-app updaters silently never get
the update.

**Then the distribution step, if the project has one** (from detection): npm
`npm publish` (package.json without `"private": true`), PyPI `uv publish`/twine,
crates.io `cargo publish`, a Homebrew tap formula bump (new url + sha256), Docker
`:latest` retag (Path C above), or a store upload for extensions. For libraries,
skipping this leaves users unable to install the version you just "published" —
confirm the registry actually serves it before reporting success.

Report the published URL and lead with what shipped.

> If the user says **"publish"** and a draft already exists for the current version,
> skip the build — but **re-run step 5's verification first**. Drafts go stale.
> Verify, flip, then run the `releases/latest` confirmation.

> **Immutability note:** if the repo has immutable releases enabled, publishing locks
> assets + tag permanently and auto-generates a signed attestation (`gh release verify
> vX.Y.Z`). Nothing about this flow changes — draft-first is exactly the recommended
> pattern — but post-publish fixes are limited to title/notes edits, and a bad publish
> can only be superseded by a new version, never re-cut.

## Shipping a prerelease (rc / beta)

Same `ship` sequence with three deltas:

1. Tag `vX.Y.Z-rc.N` (notes heading matches the full string).
2. Publish (when asked) with `gh release edit vX.Y.Z-rc.N --draft=false --prerelease`
   — **no `--latest`**, and skip the `releases/latest` confirmation: prereleases never
   appear there, **by design**. Stable-channel updaters won't offer it; that's the
   point.
3. Don't "fix" `isPrerelease: true` on an intentional RC — the troubleshooting
   remedy for a wrong Latest only applies to releases *meant* to be stable.

When the real release ships later, its `vX.Y.Z` tag is distinct — never reuse or
force-move the rc tag.

---

## Hard rules — never violate

- **Never auto-ship or auto-publish.** Only on an explicit ship/publish instruction.
- **`ship` stops at draft.** Publishing requires "publish" / "ship and publish". When
  unsure which command they meant, ask — the words are cheap.
- **Never force-move a published tag; never reuse a published tag name.** Bump to a
  new version. (Under immutable releases the platform enforces this — a published
  tag name is unrecoverable even after deletion.)
- **Never skip step 5, and never publish an empty body.** Fix the root cause
  (usually a missing `actions/checkout` in the release job), don't paste notes by
  hand every ship.
- **The junk gate is blocking, on every commit this skill makes.** No `.env`,
  credentials, build output, or anything `git check-ignore` flags. Stage by explicit
  path.
- **Never bypass hooks** (`--no-verify`) or signing unless the user explicitly
  authorizes it. If a hook fails, fix the cause.
- **Confirm the account and repo before the first mutating command** (step 0's
  `gh auth status` / owner match) — multi-account setups ship to the wrong repo
  otherwise.
- **Use the right Co-Authored-By line** for whatever agent/model is running.

## Setup: the CI workflow this assumes (one-time, per repo that wants CI)

`ship`'s Path A assumes a tag-triggered workflow that builds artifacts and creates a
**draft** release. If a repo has none, Path B (local build) works today; create the
workflow only when the user asks. Essentials:

- Trigger on `push:` tags `v*` (plus `workflow_dispatch` for manual test builds);
  `permissions: contents: write`.
- Build, emit a `.sha256` sidecar **for every artifact** (`for f in <artifacts>; do
  shasum -a 256 "$f" > "$f.sha256"; done` — on Windows runners use `certutil
  -hashfile` or ASCII-encoded output, never PowerShell `>` which writes UTF-16),
  upload artifacts, then **only on a `v*` tag** create a `draft: true` release with
  `body_path: RELEASE_NOTES.md`.
- **The release job MUST `actions/checkout` the repo** — `body_path` reads from the
  workspace; without a checkout every body ships empty.
- **Keep `draft: true` in the workflow forever.** Publishing is a human/`gh` decision.
- **Pin actions to full commit SHAs** (a mutable tag like `@v4` can be re-pointed;
  orgs can even enforce SHA pinning now). Current pins (verified 2026-07):
  ```yaml
  - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0        # v7.0.0
  - uses: actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e      # v6.4.0
  - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
  - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
  - uses: softprops/action-gh-release@718ea10b132b3b2eba29c1007bb80653f286566b # v3.0.1
  ```
  (softprops v3.x = Node 24 runtime; v2.6.2 is the last Node-20 line; inputs incl.
  `draft`/`body_path` are unchanged v2→v3.)
- Optional provenance for public repos: `actions/attest-build-provenance@0f67c3f4856b2e3261c31976d6725780e5e4c373 # v4.1.1`
  on the built artifacts; users verify with `gh attestation verify <file> -R owner/repo`.
  (Immutable releases add a free signed release attestation on publish with zero
  workflow changes.)

A full annotated Electron workflow lives in the `github-updater` skill's
`references/packaging-and-ci.md` — reuse its shape for any tag-triggered release.

## Monorepos (brief)

Per-package releases need a per-package tag scheme (`<pkg>-vX.Y.Z`): scope discovery
with `git tag --list '<pkg>-v*'` and `git describe --match '<pkg>-v*'`, keep
per-package notes files, and give each package's workflow its own tag filter. Don't
apply the single-`v*` recipes to a monorepo unchanged.

## Troubleshooting

When CI fails, a body is empty, artifacts are missing, a tag didn't trigger a build —
or you need the hotfix-from-an-old-tag, yank-a-published-release, or
fix-published-notes recipes — read `references/troubleshooting.md`. Many failures are
transient CDN flakes that need a job rerun, **not** a re-tag.

## Tone when reporting

Lead with what shipped. A one-line or small-table summary, then the links (CI run,
release URL), then whether it's draft or public. No preamble; the user can read the
diff. Keep the closing summary to a sentence or two.
