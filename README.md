# robogears-skills

Agent Skills for [Claude Code](https://claude.com/claude-code) — one repo, one skill per folder.

This replaces the old one-repo-per-skill setup. The previous repos
(`overnight-protocol`, `audit`, `github-ship`, `github-updater`) are archived and
read-only; everything new lands here.

## Install a skill

Skills live in `~/.claude/skills/`. To install one from this repo:

```bash
git clone https://github.com/robogears/robogears-skills.git
cp -R robogears-skills/skills/<skill-name> ~/.claude/skills/
```

Or grab the zip from a release and unpack it into `~/.claude/skills/`.

Restart Claude Code (or start a new session) and the skill is available as
`/<skill-name>`.

## Repo layout

```
skills/
└── <skill-name>/
    ├── SKILL.md          # frontmatter: name, version, description — the whole contract
    ├── references/       # deeper docs the skill reads on demand
    ├── scripts/          # anything it runs
    └── assets/           # templates, files it copies out
```

`SKILL.md` is the only required file. Its frontmatter carries the version —
there is no separate manifest.

## Conventions

- **Versioning** — semver in the `SKILL.md` frontmatter (`version: 0.1.1`), mirrored
  into the description as `<name> (vX.Y.Z) — …` so the running agent can see it.
- **Releases** — tagged `<skill-name>-vX.Y.Z`. The asset is a **payload-only** zip
  (`SKILL.md` plus its subdirectories — no LICENSE, README, or repo plumbing) named
  `<skill-name>-<version>.zip`, so it unpacks straight into `~/.claude/skills/`.
- **Line endings** — shell scripts are LF-locked via `.gitattributes`; a CRLF
  shebang is a silent, confusing failure.
- **Source-only** — no CI builds these. Releases are cut locally.

## License

MIT — see [LICENSE](LICENSE).
