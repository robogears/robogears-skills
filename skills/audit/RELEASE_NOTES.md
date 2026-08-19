# audit v0.1.3

First public release. A deep, strictly read-only, adversarially-verified whole-project
audit for [Claude Code](https://claude.com/claude-code) — delivered as a beginner-readable
report before anything is changed.

## Highlights

- **Eight universal passes** — bugs, security, crash-resilience, data safety, performance
  & resource leaks, dependency health, quality-of-life, and code health — each run as its
  own deliberate pass.
- **Stack-specific probes** for Electron, web servers (Flask/FastAPI/Django/Express),
  Docker, Swift/macOS, browser extensions, C#/.NET, C++/Qt, CLIs, and a Windows overlay.
- **Adversarial verification** — every Critical/High finding is handed to a fresh skeptic
  to disprove before it enters the report; refuted findings are dropped.
- **Proven coverage** — a ledger tracks every first-party file as read or skipped for a
  named reason, with the N-of-M numbers stated in the report.
- **Loop-until-dry** fan-out — parallel finders keep sweeping until a full pass finds
  nothing new; the completion gate is coverage plus a dry sweep, not elapsed time.
- **Stable finding IDs** for re-run diffing (NEW / STILL-OPEN / FIXED / ACCEPTED /
  REGRESSION) against an `AUDIT_BASELINE.md` you maintain.
- **Beginner-first report** plus a shareable, theme-aware HTML visual breakdown; secrets
  are mechanically truncated to 10 characters.
- Strictly read-only — reports and changes nothing; offers a fix pass afterward.

## Modes

`full` (default) · `quick` · `security` · `qol` · `--since <ref>` (delta).

## Install

```bash
unzip audit-0.1.3.zip -d ~/.claude/skills/
```

Then run `/audit` in Claude Code. Requires Claude Code.
