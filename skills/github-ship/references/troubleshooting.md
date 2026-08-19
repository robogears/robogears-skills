# Release troubleshooting

Common failure modes and their exact fixes. Many are transient CDN flakes that just need a
job rerun — **not** a re-tag. Re-tagging burns a version number and re-runs everything, so
try a rerun first.

## Empty release body after CI completes

This is step 5 of `ship`. Never publish until the body verifies. There are **two** causes
— diagnose which one you have, because the durable fix differs:

```bash
gh release view vX.Y.Z --json body --jq '(.body | length)'   # 0 or tiny == broken
```

**Cause A (by far the most common — a real bug, fix it once): the release job never
checks out the repo.** If the job that runs `softprops/action-gh-release` has no
`actions/checkout` step, then `RELEASE_NOTES.md` doesn't exist in the workspace and
`body_path` reads nothing — so the body ships empty **every single release**. This is not
a softprops quirk; it's a missing step. Repos here have shipped with exactly this bug for
months while their CLAUDE.md blamed softprops and prescribed a permanent manual
`gh release edit` workaround — if you find that workaround documented, it's this missing
step. The correct fix is one line in the workflow:

```yaml
release:
  needs: build
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0            # v7.0.0
      # ^ THE FIX. Without a checkout, RELEASE_NOTES.md is absent from the workspace.
    - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c   # v8.0.1
      with: { path: dist }
    - uses: softprops/action-gh-release@718ea10b132b3b2eba29c1007bb80653f286566b # v3.0.1
      with:
        draft: true
        body_path: RELEASE_NOTES.md
        files: dist/**
```

(SHA pins verified 2026-07 — SKILL.md's Setup section is the pin list of record.
softprops v3.x runs on Node 24; v2.6.2 is the last Node-20 line; the inputs, including
`draft` and `body_path`, are unchanged v2→v3.) Commit that and the empty-body problem is
gone forever for that repo — no more per-ship manual fix. Then fix the doc that blamed
softprops.

**Cause B (genuine, rarer): softprops re-running on a release that already exists** for
the tag can leave the body blank on the *update* path even when the file is present. This
is the case where `gh release edit` is the right tool, not a workaround.

**Immediate recovery (either cause) — so you can finish shipping now:**
```bash
gh release edit vX.Y.Z --notes-file RELEASE_NOTES.md
gh release view vX.Y.Z --json body --jq '(.body | length)'   # re-verify > 0
```
If it was Cause A, still commit the `actions/checkout` fix afterward so the next ship is
clean.

## CI fails with "GH_TOKEN is not set" (electron-builder project)

electron-builder tries to auto-publish on a tag push. Stop it — let CI's release action do
the upload instead:
```yaml
# electron-builder.yml (or package.json#build)
publish: null
```
and run the packager with `--publish never`.

## `actions/upload-artifact` fails: "No files were found"

The build produced a file whose name/path doesn't match what the workflow's `path:`
expects. Almost always a **target mismatch**: an `npm run build:*` script passes a
`--mac <target>` / `--win <target>` CLI flag that **overrides** the targets declared in the
build config (the CLI flag wins), so the artifact lands with a different name/extension.
Fix: align the build script, the build config, and the workflow's `path:`/`artifactName`.
Verify against the build log line `building target=... file=...`.

## Missing/extra artifact on the release (e.g. a stray `.blockmap`)

- **Missing artifact:** same target/name mismatch as above.
- **Unwanted `.dmg.blockmap`:** electron-builder emits it for `electron-updater`'s
  differential downloads. A **whole-file / custom** updater never reads it — remove
  `release/*.dmg.blockmap` from the workflow's upload `path:` and (optionally) delete any
  already-attached ones: `gh release delete-asset vX.Y.Z <name>.dmg.blockmap -y`.

## `npm ci` fails downloading a binary (e.g. `Failed to download ffmpeg` / HTTP 502)

Transient GitHub release-asset CDN flake (a `postinstall` download choked). Just rerun —
builds cache, so it's seconds. **Don't re-tag.**
```bash
gh run rerun <run-id> --failed
```

## `softprops/action-gh-release` fails with "Bad credentials" on first try

Same transient flake. Rerun the failed release job.

## CI failed and **no** release was created (release job skipped after a build job failed)

No release means no force-move concern — you may re-tag the **same** version once you've
fixed the cause. No permission needed, but tell the user:
```bash
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
```

**The immutability boundary (verified 2026-07):** re-using a tag name is safe **only**
while nothing was ever *published* under it —
- **No release exists** (this entry): re-tag freely.
- **Only a draft exists:** delete the draft first (`gh release delete vX.Y.Z --yes
  --cleanup-tag`), then re-tag. Drafts never create or lock the tag — the name stays
  reusable even with immutable releases enabled.
- **A PUBLISHED immutable release ever existed on that tag:** the name is **burned
  forever** — it survives deleting the release, disabling the setting, even deleting and
  recreating the repo (API error: "tag_name was used by an immutable release"). Recovery
  from a botched published release is always **bump forward** to the next patch, never
  delete-and-retry.

## Tag pushed but no workflow runs

**First rule out the registration race** — a tag-triggered run can take up to ~60 s to
appear, and a one-shot lookup misdiagnoses that lag as misconfiguration. Poll before you
diagnose (SKILL.md step 4's loop does exactly this):
```bash
for _ in $(seq 12); do
  gh run list --branch "vX.Y.Z" --event push --limit 1 \
    --json databaseId --jq '.[0].databaseId' | grep -q . && break
  sleep 5
done
```
Only after a full minute of nothing: the workflow's tag trigger doesn't match your tag
name. Confirm:
```yaml
on:
  push:
    tags:
      - 'v*'      # must match e.g. v1.2.3 — a bare 1.2.3 tag, or a monorepo
                  # pkg-v1.2.3 tag, sails past this filter and triggers nothing
```

## `git push origin main` rejected after the commit succeeds

Someone (often the user editing on GitHub.com) pushed in between. Rebase and retry:
```bash
git pull --rebase origin main
git push origin main
git tag -a vX.Y.Z -m "vX.Y.Z"   # tag wasn't created yet — the sequence stopped at the push
git push origin vX.Y.Z
```

## Hotfix from an old tag (patch a release without shipping main)

When main has moved on with unreleased work but a shipped version needs a fix:

```bash
git fetch --tags origin
git checkout -b hotfix/vX.Y.(Z+1) vX.Y.Z      # branch FROM the released tag
git cherry-pick <fix-sha>                      # or make the fix directly
# bump PATCH in every version file (same lockstep rules as ship step 1)
# rewrite RELEASE_NOTES.md as usual, then stage by explicit path (junk gate applies):
git add RELEASE_NOTES.md <bumped files> && git commit -m "vX.Y.(Z+1): hotfix ..."
git push origin hotfix/vX.Y.(Z+1)
git tag -a "vX.Y.(Z+1)" -m "vX.Y.(Z+1): hotfix" && git push origin "vX.Y.(Z+1)"
```

**Tag triggers are branch-agnostic** — CI fires on the tag push no matter which branch
the tagged commit sits on, so the rest of `ship` (watch CI, verify draft, publish on
request) is unchanged. Two notes:
- The release notes' compare link base is the tag you **branched from**
  (`compare/vX.Y.Z...vX.Y.(Z+1)`) — not main's head, which contains work this hotfix
  doesn't ship.
- If main also needs the fix, cherry-pick it back to main afterward; don't merge the
  hotfix branch wholesale.

## Yank a published release (pull it back from the public)

```bash
gh release edit vX.Y.Z --draft=true
gh api repos/<owner>/<repo>/releases/latest --jq .tag_name   # must print the PREVIOUS tag
```

Re-drafting removes it from the public Releases list and `releases/latest` falls back to
the previous published release — in-app updaters stop offering it immediately (that
`gh api` check is literally what they poll). Fix or supersede, then re-publish
deliberately.

**Immutable releases CANNOT be re-drafted** — once published, post-publish edits are
limited to **title and notes only** (verified 2026-07). Recovery there: ship a fixed
version and publish it so `releases/latest` re-points forward, and edit the bad
release's notes to say "superseded by vX.Y.(Z+1) — do not install".

## Fix a typo in already-published notes

Edit `RELEASE_NOTES.md` first (it's the single source of truth — never let the file and
the release body diverge), then re-apply:
```bash
gh release edit vX.Y.Z --notes-file RELEASE_NOTES.md
```
This works **even on immutable releases** — notes stay editable after publish. If the fix
is for an *older* release (RELEASE_NOTES.md holds only the current version), pass the
corrected text with `--notes` instead of the file.

## `gh` not on PATH after install

First ask the shell: `command -v gh` — it prints the absolute path if any install is
reachable. Typical locations when it isn't: macOS/Homebrew `/opt/homebrew/bin/gh`; Linux
`/usr/bin/gh` (distro package) or `/usr/local/bin/gh`; Windows/winget
`C:\Users\<user>\AppData\Local\Microsoft\WinGet\Links\gh.exe`. Use the absolute path.

## `gh release view --json isLatest` errors: unknown JSON field

The mistake is in the **verify query**, not in `gh release edit`. `gh release view` exposes
no `isLatest` field, so `--json isLatest` fails. To confirm a release is Latest, don't ask
the release object — ask the `releases/latest` endpoint, which is also exactly what the
in-app updaters poll:
```bash
gh api repos/<owner>/<repo>/releases/latest --jq .tag_name   # must equal vX.Y.Z
```
For the release's own flags, use `isDraft` / `isPrerelease` (both should be `false` once
published):
```bash
gh release view vX.Y.Z --json isDraft,isPrerelease,url --jq '{draft:.isDraft, prerelease:.isPrerelease, url:.url}'
```

## `releases/latest` returns an OLDER tag after publishing

The flip published, but not as Latest. Two usual causes:
- The release is flagged **prerelease** (`isPrerelease: true`) — prereleases are never
  eligible to be Latest. Clear it: `gh release edit vX.Y.Z --prerelease=false --latest`.
  **Only for a release that was *meant* to be stable** — never "fix" an intentional
  rc/beta this way; prereleases are excluded from Latest by design.
- `--latest` was omitted on the `--draft=false` flip. Re-run with it:
  `gh release edit vX.Y.Z --draft=false --latest`, then re-confirm with the `gh api` line
  above. Until it returns your tag, users' updaters won't see the release.

## Deleting a stale/superseded draft (and its orphaned tag)

When a prior `ship` left a draft the user never published and you're superseding it, one
command removes the draft **and** its tag on origin:
```bash
gh release delete v<old> --yes --cleanup-tag
git tag -d v<old>                 # --cleanup-tag only touches origin; delete locally too
```
Always safe tag-wise, even with immutable releases enabled — drafts never create or lock
the tag, so the name stays reusable. Confirm with the user first if there's any chance
they still want that draft.

## Runtime-deprecation annotations in the run (e.g. "Node.js N is deprecated")

Runner-side annotations about an action's runtime ("Node.js N is deprecated",
`set-output`, etc.) are **warnings, not failures** — the run's conclusion is unaffected.
Ignore them mid-ship; bump the pinned action versions when convenient (SKILL.md's Setup
section carries the current SHA pins).
