# robogears-skills

Agent Skills for [Claude Code](https://claude.com/claude-code) — one repo, one skill per folder.

Eight skills, all in daily use. They lean opinionated on purpose: each one encodes a
specific way of working rather than trying to be a general-purpose tool. Several are
strictly read-only by design — they report and change nothing until you say so.

This repo replaces the old one-repo-per-skill setup; the previous repos
(`overnight-protocol`, `audit`, `github-ship`, `github-updater`) are archived and
read-only.

## The skills

### 🔍 [audit](skills/audit) · v0.1.3

A deep, strictly read-only audit of an entire project — bugs, security holes,
crash-resilience, data safety, resource leaks, dependency health, and code health.

What makes it different from a normal review: it **reads every first-party file and
proves it** (the report states "read N of M files" with counted skips), fans out
parallel finders, then hands every serious finding to a **fresh skeptic whose only job
is to disprove it** — anything that survives is real, anything refuted is dropped. It
loops until a full sweep turns up nothing new, rather than stopping after one pass.

Carries stack-specific probes for Electron, web servers, Docker, Swift/macOS, browser
extensions, C#/.NET, C++, CLIs, and Windows. Finding IDs are deterministic, so repeat
audits diff cleanly into NEW / STILL-OPEN / FIXED / ACCEPTED / REGRESSION. Delivers a
beginner-readable report plus a visual HTML breakdown, and **changes nothing** —
the fix pass is a separate, opt-in step.

### 🌙 [overnightprotocol](skills/overnightprotocol) · v0.3.1

An autonomous build loop that doesn't stop. Hand it a task list, go to sleep.

It runs on Claude Code's built-in `/loop` — no hooks, nothing added to your global
settings. It works the official list first; when that's exhausted it **generates its own
work** — QA-hardening, running `/audit` and fixing what it finds, then researching the
project for quality-of-life improvements. Every item is committed and pushed on a branch
(it asks whether to stay on yours or use a new one, and never commits to `main`). New
secret-shaped files are never committed, and deny rules block force-pushes. Usage
limits are left to Claude Code's auto-resume, which in the Desktop app covers the
5-hour limit. It ends only when you explicitly say so.

### 📁 [teedaa](skills/teedaa) · v0.1.0

A read-only auditor for any folder — built for the case that breaks other tools: huge
Dropbox/CloudStorage trees full of cloud-only placeholder files, which it inspects
**without downloading a single byte**.

Maps exactly where the storage went, finds genuine dead weight (old installers, caches,
partial downloads, sync-conflict copies, empty-folder forests), and knows what *not* to
touch — git trees, app bundles, libraries, and backups are structurally protected from
ever being suggested. It tells personal camera photos apart from app assets, and flags
sensitive files discreetly. Output is a beginner-readable report plus an interactive
dashboard with a click-to-zoom storage treemap. Cleanup is **move-only quarantine** with
a manifest and undo script, gated on explicit approval. It cannot delete anything.

### 🧹 [check-for-dupes](skills/check-for-dupes) · v0.1.5

Finds duplicate files by hashing their **actual contents** (SHA-256) — never by
filename, because same-name files are routinely different photos and different-name
files are routinely identical.

Handles Dropbox cloud-only placeholders without downloading them. Duplicates are
**moved** to a quarantine folder with a manifest and an undo script — never deleted —
and the run finishes with a verification pass that proves nothing was lost.

### 🚢 [github-ship](skills/github-ship) · v0.1.1

The exact workflow for committing and releasing any project to GitHub, with or without
CI. Three commands: **push** (commit, no release), **ship** (cut a versioned, tagged
release, left as a draft), **ship and publish** (flip it to public Latest).

It handles the whole sequence — stack detection, version bump, release-notes rewrite,
tagging, watching CI, verifying the release body actually rendered — and publishes only
when explicitly authorized. Also covers the failure modes: empty release bodies, tags
that didn't trigger CI, first releases, prereleases.

### 🔄 [github-updater](skills/github-updater) · v0.1.1

Battle-tested recipes for adding a self-updater to an app distributed through GitHub
Releases — Electron (macOS `.dmg` self-install + Windows/NSIS), Swift/SwiftUI, C#/.NET,
Linux AppImage, and single-binary CLIs.

One platform-agnostic core spec — ETag-cached release discovery, version comparison,
an asset-name contract, a SHA-256 gate, then download → verify → stage → swap →
relaunch with rollback — plus a concrete implementation per stack. Written for the
awkward case in particular: ad-hoc-signed, un-notarized apps updating themselves
outside the App Store.

### 📇 [ankideckcreator](skills/ankideckcreator) · v0.1.4

Builds a complete, genuinely studiable Anki deck on any topic.

Card content is written by agents and then **adversarially verified** by a separate
pass — because in spaced repetition, an error you miss gets rehearsed hundreds of times.
Every card gets an example sentence and neural TTS audio. Decks are delivered live into
Anki over AnkiConnect with an `.apkg` backup written first.

Its one hard rule: it **never touches your deck options**. It creates no presets, reassigns
nothing, and writes no scheduling limits — new decks simply inherit Anki's Default, and
existing decks keep whatever they're on, so extending a deck you're already studying
can't disturb its schedule or FSRS parameters.

### ✍️ [prompt-architect](skills/prompt-architect) · v0.1.1

Engineers prompts for *other* AI systems — the specialty is coding agents and full app
builds. It produces the prompt, not the thing the prompt asks for.

Every coding prompt gets the same eight sections (context, objective, testable
requirements, constraints and non-goals, process directives, executable acceptance
criteria, output contract, a tightly-scoped escape hatch). Bring an existing prompt and
it scores it 1–10 across clarity, specificity, context, completeness and structure
before rewriting, so the improvement is visible rather than asserted. It refuses to
invent facts about your project — anything it wasn't told becomes a visible
`[you fill this in]` rather than a plausible guess.

## Install

Skills live in `~/.claude/skills/`.

```bash
git clone https://github.com/robogears/robogears-skills.git
cp -R robogears-skills/skills/<skill-name> ~/.claude/skills/
```

Or download a release zip and unpack it straight into `~/.claude/skills/`.

Start a new Claude Code session and the skill is available as `/<skill-name>`.

## Repo layout

```
skills/
└── <skill-name>/
    ├── SKILL.md          # frontmatter: name, version, description — the whole contract
    ├── references/       # deeper docs the skill reads on demand
    ├── scripts/          # anything it runs
    └── assets/           # templates, files it copies out
```

`SKILL.md` is the only required file. Its frontmatter carries the version — there is no
separate manifest.

## Conventions

- **Versioning** — semver in the `SKILL.md` frontmatter (`version: 0.1.1`), mirrored into
  the description as `<name> (vX.Y.Z) — …` so the running agent can see it.
- **Releases** — tagged `<skill-name>-vX.Y.Z`. The asset is a **payload-only** zip
  (`SKILL.md` plus its subdirectories, no repo plumbing) named `<skill-name>-<version>.zip`,
  so it unpacks straight into `~/.claude/skills/`.
- **Line endings** — shell scripts are LF-locked via `.gitattributes`; a CRLF shebang is
  a silent, confusing failure.
- **Source-only** — no CI builds these. Releases are cut locally.

## License

MIT — see [LICENSE](LICENSE).
