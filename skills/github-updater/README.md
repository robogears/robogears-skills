# github-updater

Battle-tested recipes for adding a **self-updater** to any app distributed through GitHub
Releases.

`github-updater` is a [Claude Code Agent Skill](https://code.claude.com/docs/en/skills). It
gives you one platform-agnostic core spec plus concrete per-stack implementations for the
"Check for updates → Download → Verify → Restart to apply" feature — including the awkward
case of ad-hoc-signed, un-notarized apps that must self-install without the App Store or a
paid Developer ID.

## The core spec (every platform)

1. **Discover** the latest release (ETag-cached against the GitHub API).
2. **Compare** versions (semver, prerelease-aware).
3. **Match** the right asset by a strict filename contract.
4. **Download** it, then **verify** against a same-channel SHA-256 gate.
5. **Stage → swap → relaunch**, with rollback if the swap fails.

The filename contract and the SHA-256 gate are the load-bearing parts: a version that
disagrees between tag, app, and asset filename is the number-one cause of a broken updater.

## Per-stack implementations

| Stack | Reference |
|---|---|
| Electron (macOS `.dmg` self-install + Windows/NSIS) | `references/updater-main.ts`, `references/renderer-and-ipc.md`, `references/updater-windows.md` |
| Swift / SwiftUI macOS | `references/impl-swift-macos.md` |
| C#/.NET Windows (Inno / Velopack) | `references/impl-windows-native.md` |
| Linux AppImage | `references/impl-linux-appimage.md` |
| Self-hosted Python / server apps | `references/impl-server-python.md` |
| Packaging & tag-triggered CI | `references/packaging-and-ci.md` |

Single-binary CLIs (Rust/Go/Node) reuse the core spec directly.

## Install & use

```bash
unzip github-updater-*.zip -d ~/.claude/skills/
```

Then, in Claude Code, describe what you're wiring:

> Add a "Check for updates / Restart to apply" feature to my Electron app that pulls from
> GitHub Releases.

The skill walks the core spec, then drops in the implementation for your detected stack.

## Scope

**For:** building, wiring, or debugging an in-app updater for a desktop or server app on
GitHub Releases — App Translocation, `.dmg` self-install, NSIS silent reinstall, AppImage
self-update, and the stage/swap/relaunch dance.

**Not for:** iOS/Android (the stores own mobile updates); cutting the release itself (see
`github-ship`); or testing an updater end-to-end without a throwaway public release (see
`updater-e2e-harness`).

## License

[MIT](LICENSE) © 2026 robogears
