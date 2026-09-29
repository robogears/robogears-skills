#!/usr/bin/env bash
# overnightprotocol: add / remove the loop's hard limits as Claude Code deny rules.
#
#   deny-rules.sh add    <project_dir> <base_branch> <work_branch> [--adopt-legacy]
#   deny-rules.sh remove <project_dir>
#   deny-rules.sh status <project_dir>
#   deny-rules.sh list   <base_branch> <work_branch>      (print the rules, change nothing)
#
# Rules go into <project>/.claude/settings.local.json (Claude Code enforces deny rules even
# with permission prompts bypassed). The rules this script ADDED are recorded inside the
# repository's .git folder (<git-dir>/overnight-loop/deny-added.json) — never committed,
# pushed or merged — so `remove` takes out exactly those. A rule the user already had is
# never recorded and never removed. --adopt-legacy (used only when migrating a run started
# by v0.2/v0.3.0) also records the three colon-form rules those versions wrote
# (git push --force:*, git push -f:*, git clean:*) so wrap-up removes them.
# Paths are made absolute (~ expanded), so the shell's current folder can't send the rules
# astray. If git TRACKS .claude/settings.local.json, `add` refuses (exit 4): the rules would
# otherwise be committed and pushed. If the loop created the file, `remove` deletes it again
# when nothing else is left in it.
#
# Wildcards use Claude Code's matcher: `*` matches anything (spaces included) and the whole
# command must match. The rules cover the COMMON forms, for `git push`, `git -C <dir> push`
# and `git -c <key=value> push`: force pushes (--force, --force-with-lease, -f,
# " +refspec"), --mirror, --all, --prune, remote branch deletion, and pushes to main /
# master / the base branch (never the work branch) as a plain name, :refspec, refs/heads/…
# or quoted. Also gh pr merge, git reset --hard, git branch -D / --delete --force and
# git clean -…, plain and with -C. Not covered: /usr/bin/git, env git, aliases.
#
# Exit: 0 ok · 2 usage / not a git repository · 3 settings file unreadable/invalid ·
#       4 settings.local.json is tracked by git (nothing changed in any error case).

set -u
MODE="${1:-}"
case "$MODE" in
  add)    { [ $# -eq 4 ] || { [ $# -eq 5 ] && [ "$5" = "--adopt-legacy" ]; }; } || { echo "usage: deny-rules.sh add <project_dir> <base_branch> <work_branch> [--adopt-legacy]" >&2; exit 2; } ;;
  remove|status) [ $# -eq 2 ] || { echo "usage: deny-rules.sh $MODE <project_dir>" >&2; exit 2; } ;;
  list)   [ $# -eq 3 ] || { echo "usage: deny-rules.sh list <base_branch> <work_branch>" >&2; exit 2; } ;;
  *) echo "usage: deny-rules.sh add|remove|status|list …" >&2; exit 2 ;;
esac

exec python3 - "$@" <<'PY'
import json, os, shutil, subprocess, sys, tempfile

mode = sys.argv[1]
LEGACY = ["Bash(git push --force:*)", "Bash(git push -f:*)", "Bash(git clean:*)"]

def rules(base, work):
    # plain "git push", plus the two option forms agents actually use (-C dir, -c key=value).
    # A generic "git * push" would also match commit MESSAGES that mention "push --force".
    PREFIXES = ("git push", "git -C * push", "git -c * push")
    r = []
    for P in PREFIXES:
        r += [f"Bash({P} *--force*)", f"Bash({P} -f*)", f"Bash({P} * -f)", f"Bash({P} * -f *)",
              f"Bash({P} * +*)", f"Bash({P} *--mirror*)", f"Bash({P} *--all)", f"Bash({P} *--all *)",
              f"Bash({P} *--prune*)", f"Bash({P} *--delete*)", f"Bash({P} * -d *)", f"Bash({P} * :*)"]
    r += ["Bash(git reset --hard*)", "Bash(git -C * reset --hard*)",
          "Bash(git branch -D*)", "Bash(git branch * -D*)", "Bash(git -C * branch -D*)",
          "Bash(git branch *--delete --force*)", "Bash(git branch *--force --delete*)",
          "Bash(git clean *)", "Bash(git -C * clean -*)",
          "Bash(gh pr merge*)"]
    protected = []
    for b in ("main", "master", base):
        if b and b != work and b not in protected:
            protected.append(b)
    for P in PREFIXES:
        for b in protected:
            for form in (f" {b}", f":{b}", f":refs/heads/{b}", f" refs/heads/{b}",
                         f" '{b}'", f' "{b}"', f":{b}'", f':{b}"'):
                r += [f"Bash({P} *{form})", f"Bash({P} *{form} *)"]
    return list(dict.fromkeys(r))

if mode == "list":
    print("\n".join(rules(sys.argv[2], sys.argv[3])))
    sys.exit(0)

proj = os.path.realpath(os.path.expanduser(sys.argv[2]))
if not os.path.isdir(proj):
    print(f"not a directory: {proj}", file=sys.stderr); sys.exit(2)
g = subprocess.run(["git", "-C", proj, "rev-parse", "--absolute-git-dir"], capture_output=True, text=True)
if g.returncode != 0:
    print(f"not a git repository: {proj}", file=sys.stderr); sys.exit(2)
settings = os.path.join(proj, ".claude", "settings.local.json")
record = os.path.join(g.stdout.strip(), "overnight-loop", "deny-added.json")

def load_settings():
    if not os.path.exists(settings) or os.path.getsize(settings) == 0:
        return {}
    try:
        with open(os.path.realpath(settings), encoding="utf-8") as f:
            cfg = json.load(f)
    except (ValueError, OSError) as e:
        print(f"REFUSED: {settings} is not valid JSON ({e}); nothing changed.", file=sys.stderr); sys.exit(3)
    if not isinstance(cfg, dict):
        print(f"REFUSED: {settings} is not a JSON object; nothing changed.", file=sys.stderr); sys.exit(3)
    perms = cfg.get("permissions", {})
    if not isinstance(perms, dict) or not isinstance(perms.get("deny", []), list):
        print(f"REFUSED: {settings} has an unexpected permissions/deny shape; nothing changed.", file=sys.stderr); sys.exit(3)
    return cfg

def write_json(path, data):
    real = os.path.realpath(path)            # keep a symlinked file a symlink
    os.makedirs(os.path.dirname(real), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(real), prefix=".tmp-overnight-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, allow_nan=False)
            f.write("\n"); f.flush(); os.fsync(f.fileno())
        with open(tmp, encoding="utf-8") as f:
            if json.load(f) != data:
                raise ValueError("re-read mismatch")
        if os.path.exists(real):
            shutil.copymode(real, tmp)
        os.replace(tmp, real)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def load_record():
    try:
        with open(record, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("added", []) if isinstance(d, dict) else []
    except (OSError, ValueError):
        return []

def load_full_record():
    try:
        with open(record, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}

if mode == "add":
    base, work = sys.argv[3], sys.argv[4]
    adopt = len(sys.argv) > 5 and sys.argv[5] == "--adopt-legacy"
    t = subprocess.run(["git", "-C", proj, "ls-files", "--error-unmatch", ".claude/settings.local.json"], capture_output=True)
    if t.returncode == 0:
        print(f"REFUSED: git tracks {settings}, so the deny rules would be committed and pushed. "
              "Untrack it (git rm --cached .claude/settings.local.json + add it to .gitignore) or run without deny rules.", file=sys.stderr)
        sys.exit(4)
    existed = os.path.exists(settings)
    cfg = load_settings()
    deny = cfg.setdefault("permissions", {}).setdefault("deny", [])
    rec = load_full_record()
    added = rec.get("added", []) if isinstance(rec.get("added"), list) else []
    created = bool(rec.get("created")) or not existed
    adopted = [r for r in LEGACY if adopt and r in deny and r not in added]
    new = [r for r in rules(base, work) if r not in deny]
    deny.extend(new)
    added += [r for r in adopted + new if r not in added]
    if new:
        write_json(settings, cfg)
    write_json(record, {"settings": settings, "added": added, "created": created})
    msg = f"deny rules: {len(new)} added, {len(rules(base, work)) - len(new)} already present"
    if adopted:
        msg += f", {len(adopted)} from an older version of this skill adopted as loop-owned"
    print(msg)
elif mode == "remove":
    added = load_record()
    if not added:
        print("deny rules: nothing recorded as added by the loop — nothing removed"); sys.exit(0)
    cfg = load_settings()
    deny = cfg.get("permissions", {}).get("deny", [])
    keep = [r for r in deny if r not in added]
    removed = len(deny) - len(keep)
    if removed:
        cfg["permissions"]["deny"] = keep
        if not keep:
            del cfg["permissions"]["deny"]
            if not cfg["permissions"]:
                del cfg["permissions"]
        write_json(settings, cfg)
    if load_full_record().get("created") and cfg == {} and os.path.exists(settings):
        os.unlink(os.path.realpath(settings))   # the loop created it and nothing else is in it
    os.unlink(record)
    print(f"deny rules: {removed} removed (only the ones the loop added)")
else:  # status
    added = load_record()
    cfg = load_settings()
    deny = cfg.get("permissions", {}).get("deny", [])
    live = [r for r in added if r in deny]
    print(f"deny rules: {len(live)} of {len(added)} loop-added rules present in {settings}")
PY
