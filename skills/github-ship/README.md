# github-ship

One deliberate vocabulary for committing and releasing **any** project to GitHub — with or
without CI.

`github-ship` is a [Claude Code Agent Skill](https://code.claude.com/docs/en/skills). It
encodes three precise commands and the exact, ordered command sequences behind each, so a
release never skips a step, exposes a draft early, or ships with an empty body.

## The three commands

| Command | Means | Result |
|---|---|---|
| **push** | Commit to the current branch and push. | New commit. No version bump, no tag, no release. |
| **ship** | Cut a full versioned release, left as a **draft**. | Version bumped in lockstep, tag pushed, artifacts built (CI or local), a draft release with notes + assets. The public sees nothing yet. |
| **ship and publish** | Everything `ship` does, then flip it **public/Latest**. | Users — and any in-app updater — now see it. |

The words are precise on purpose: `ship` always stops at a draft (your review gate);
publishing is a separate, explicit step.

## What it handles

- **Every ecosystem.** Detects the project type and its version file(s): npm/pnpm/yarn,
  Python, Rust, Go, C#, Swift (XcodeGen and raw pbxproj), CMake/C++, Gradle/Maven, browser
  extensions, Docker, and source-only — including dynamic-versioning projects where the
  tag *is* the version and there's no file to bump.
- **Three release paths.** Tag-triggered CI, no-CI local build, or a Docker/server image —
  it picks the right one from what the repo actually has, and never invents a workflow
  mid-ship.
- **The guardrails that matter.** A blocking junk gate on every commit (no `.env`,
  credentials, or build output), a consistency assert that the tag equals what you bumped,
  a mandatory draft verification (body length + expected assets) before anyone publishes,
  and a post-publish check that `releases/latest` actually resolves to your tag — because
  that's literally what in-app updaters read.
- **The failure recipes.** Empty release body (usually a missing `actions/checkout`), a tag
  that didn't trigger CI, stale drafts, prereleases, first releases, monorepo tag schemes,
  and immutable-release semantics — all covered in `references/`.

## Install & use

```bash
unzip github-ship-*.zip -d ~/.claude/skills/
```

Then, in Claude Code, from the repo you want to release:

```
push                  # commit + push the current branch
ship                  # cut a versioned release, leave it as a draft
ship and publish      # ship, then flip to public/Latest
publish               # flip an existing draft public (re-verifies first)
```

## What's inside

| Path | What it is |
|---|---|
| `SKILL.md` | The vocabulary and the ordered command sequences. |
| `references/project-type-detection.md` | Signal files → ecosystem → version files → release path. |
| `references/version-bump-table.md` | The exact file(s) and command to bump, per ecosystem. |
| `references/release-notes-template.md` | The `RELEASE_NOTES.md` contract and per-type Install sections. |
| `references/troubleshooting.md` | Empty bodies, tags that didn't trigger, yanks, hotfixes. |

## Design stance

Deliberately **not** adopting conventional-commit inference or changelog tooling
(release-please, changesets, semantic-release): notes are hand-curated from the commit
range and the bump level is a human judgment. It never ships or publishes unprompted.

## License

[MIT](LICENSE) © 2026 robogears
