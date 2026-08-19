# audit

A deep, strictly **read-only**, adversarially-verified whole-project audit — delivered as
a report a non-programmer can follow, before a single line is changed.

`audit` is a [Claude Code Agent Skill](https://code.claude.com/docs/en/skills). You point
it at a project; it reads *everything*, hunts for what's genuinely wrong or improvable,
**proves each claim with evidence and then tries to disprove it**, and writes up the
survivors — changing nothing.

## What it does

- **Reads the whole tree, and proves it.** A coverage ledger tracks every first-party
  file as READ or skipped-for-a-named-reason, and the report states the N-of-M numbers.
  An audit that can't show its coverage isn't done.
- **Eight universal passes.** Bugs · security · crash-resilience · data safety ·
  performance & resource leaks · dependency health · quality-of-life · code health — each
  a deliberate pass, not one skim wearing eight hats.
- **Stack-specific probes.** Loads the highest-yield checks for whatever it detects:
  Electron, web server (Flask/FastAPI/Django/Express), Docker, Swift/macOS, browser
  extension, C#/.NET, C++/Qt, CLI (Rust/Go/Node), and a Windows overlay.
- **Adversarial verification.** Every Critical/High finding is handed to a fresh skeptic
  whose only job is to *disprove* it; refuted findings are dropped, unsettled ones move to
  "Needs investigation." No plausible-but-wrong claims survive.
- **Loops until dry.** Fans out parallel finders, then keeps sweeping for new surface
  until a full pass turns up nothing new — the completion gate is coverage + a dry sweep,
  never elapsed time.
- **Stable finding IDs.** Each finding gets a deterministic `<CAT>-<slug>-<hash8>` id, so
  a repeat audit diffs cleanly into **NEW / STILL-OPEN / FIXED / ACCEPTED / REGRESSION**
  against an `AUDIT_BASELINE.md` you maintain.
- **A report for the owner.** Beginner-first: every finding has a plain-English "what's
  wrong / where / why it matters / how to fix," jargon glossed in place, evidence snippets
  captioned. Plus a shareable, theme-aware HTML visual breakdown of the same findings.

It **reports and changes nothing.** After the report, it offers a fix pass — it never
starts one.

## Modes

| Mode | Scope |
|---|---|
| `full` (default) | Every pass and every matching stack probe. |
| `quick` | Bugs + security + crash-resilience + the stack's highest-risk probes. |
| `security` | The full security pass + reachable advisories + every stack security probe. |
| `qol` | Quality-of-life + code health only; Suggestions-only report. |
| `--since <ref>` | Delta audit of code changed since `<ref>` (plus the unchanged code it now reaches). |

## Install & use

```bash
unzip audit-*.zip -d ~/.claude/skills/
```

Then, in Claude Code:

```
/audit                    # audit the current project (full)
/audit ~/Projects/foo quick
/audit --since v1.2.0     # only what changed since a release
```

## The guarantees

Strictly read-only: it never edits, renames, deletes, or reformats anything in the project
tree, never runs a state-changing git or build command, and writes its working files and
report only *outside* the audited tree. The only network use is read-only queries to
package registries and vulnerability databases. Secrets found in the tree or git history
are mechanically truncated to 10 characters in the report so it never becomes a second
copy of the leak.

## Relationship to robogears-audit

`audit` is the leaner, self-contained sibling of `robogears-audit` and supersedes it for
generic whole-project audit asks. They share the finding-ID scheme and `AUDIT_BASELINE.md`
format, so a baseline written by one is read by the other.

## License

[MIT](LICENSE) © 2026 robogears
