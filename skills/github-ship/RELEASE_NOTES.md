# github-ship v0.1.1

First public release. A [Claude Code](https://claude.com/claude-code) skill that encodes
one deliberate vocabulary for committing and releasing any project to GitHub — with or
without CI.

## Highlights

- **Three precise commands:** `push` (commit only), `ship` (versioned release, left as a
  draft), and `ship and publish` (flip public/Latest). The words are unambiguous, so a
  release never publishes early by accident.
- **Every ecosystem** — npm/pnpm/yarn, Python, Rust, Go, C#, Swift, CMake/C++,
  Gradle/Maven, browser extensions, Docker, and source-only — with correct handling of
  dynamic-versioning projects where the tag is the version.
- **Three release paths** — tag-triggered CI, no-CI local build, or a Docker/server image —
  chosen from what the repo actually has.
- **Guardrails:** a blocking junk gate on every commit, a tag/version consistency assert,
  mandatory draft verification (body + assets) before publishing, and a post-publish
  `releases/latest` check — what in-app updaters actually read.
- **Failure recipes** in `references/`: empty release bodies (missing `actions/checkout`),
  tags that didn't trigger CI, stale drafts, prereleases, first releases, and monorepo tag
  schemes.

## Install

```bash
unzip github-ship-0.1.1.zip -d ~/.claude/skills/
```

Then say `push`, `ship`, or `ship and publish` in Claude Code. Requires Claude Code and the
GitHub CLI (`gh`).
