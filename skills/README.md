# skills/

One folder per skill. Drop a skill directory in here and commit it.

Each folder needs a `SKILL.md` at its root — that file *is* the skill. Everything
else is optional and loaded on demand:

```
skills/
└── my-skill/
    ├── SKILL.md          # required — frontmatter + instructions
    ├── references/       # docs the skill reads when it needs them
    ├── scripts/          # anything it executes
    └── assets/           # templates and files it copies out
```

Minimum viable `SKILL.md`:

```markdown
---
name: my-skill
version: 0.1.0
description: my-skill (v0.1.0) — What it does, and the phrases that should trigger it.
---

# My Skill

Instructions the agent follows.
```

The `description` is what the agent matches against to decide whether to load the
skill, so write it for triggering: say what it does, name the phrases a user would
actually type, and say what it is *not* for.
