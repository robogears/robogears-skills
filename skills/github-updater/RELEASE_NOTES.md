# github-updater v0.1.1

First public release. A [Claude Code](https://claude.com/claude-code) skill with
battle-tested recipes for adding a self-updater to any app distributed through GitHub
Releases.

## Highlights

- **One platform-agnostic core spec:** ETag-cached release discovery → semver compare →
  strict asset-name match → download → SHA-256 gate → download/verify/stage/swap/relaunch
  with rollback.
- **Per-stack implementations:** Electron (macOS `.dmg` self-install + Windows/NSIS),
  Swift/SwiftUI macOS, C#/.NET Windows (Inno/Velopack), Linux AppImage, self-hosted
  Python/server apps, and single-binary CLIs.
- **Built for the hard case** — ad-hoc-signed, un-notarized apps that self-install without
  the App Store or a paid Developer ID.
- Reference implementations included: `updater-main.ts`, renderer/IPC wiring, Windows NSIS
  notes, and a packaging + tag-triggered CI guide.

## Scope

For building, wiring, or debugging an in-app updater. Not for iOS/Android (stores own
mobile updates), not for cutting the release (see `github-ship`), and not for end-to-end
updater testing (see `updater-e2e-harness`).

## Install

```bash
unzip github-updater-0.1.1.zip -d ~/.claude/skills/
```

Then describe the updater you're wiring in Claude Code. Requires Claude Code.
