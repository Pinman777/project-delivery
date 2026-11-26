#!/usr/bin/env python3
"""Local, fail-closed operations for the project-delivery skill.

No network and no deployment commands are executed by this script. GitHub push,
repository creation, server deployment and cron installation are external gates.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

# Import watchdog functionality
try:
    from .autonomous_watchdog import AutonomousWatchdog
except ImportError:
    AutonomousWatchdog = None

DOC_FILES = [
    "PROJECT.md", "ARCHITECTURE.md", "DECISIONS.md", "DEVLOG.md",
    "RUNBOOK.md", "VERIFICATION.md", "ROLLBACK.md", "SECURITY.md",
]
STAGES = [
    "intake", "scaffolded", "baseline-verified", "in-progress", "verifying",
    "review", "ready-for-release", "released", "archived",
]
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{16,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
]
IGNORED_DIRS = {".git", "node_modules", ".venv", "dist", "build", "__pycache__", ".backups"}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(argv, cwd: Path | None = None, check: bool = True):
    proc = subprocess.run(argv, cwd=cwd, text=True, encoding="utf-8", errors="replace",
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(map(str, argv))}\n{proc.stderr[-1500:]}")
    return proc


def git(root: Path, *args, check: bool = True):
    return run(["git", "-C", str(root), *args], check=check)


def head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").stdout.strip()


def load_project(root: Path):
    return load_json(root / "project.json")


def save_project(root: Path, project) -> None:
    write_json(root / "project.json", project)
    write_status(root, project)


def write_status(root: Path, project) -> None:
    backup = project.get("last_backup") or {}
    lines = [
        f"# Status — {project['slug']}", "",
        f"- Stage: **{project['stage']}**",
        f"- Mode: `{project['mode']}`",
        f"- Environment: `{project['environment']}`",
        f"- Root: `{root}`",
        f"- Repository: {project.get('repo_url') or 'local only (external GitHub gate pending)'}",
        f"- Last good commit: `{project.get('last_good_commit') or '—'}`",
        f"- Deployed commit: `{project.get('deployed_commit') or 'not deployed'}`",
        f"- Last backup: `{backup.get('created_at') or 'not created'}`",
        f"- Backup restore-check: `{backup.get('restore_check') or 'not verified'}`",
        f"- Obsidian: `{project.get('obsidian_file')}`",
        f"- Verification: `{project.get('verify_cmd') or 'not configured'}`",
        "",
        "External actions (repo creation, push, deploy, cron) require explicit go zeon.",
        "",
    ]
    (root / "STATUS.md").write_text("\n".join(lines), encoding="utf-8")


def create_doc(root: Path, name: str, content: str) -> None:
    path = root / "docs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def scaffold(args) -> None:
    root = Path(args.root).resolve()
    if root.exists() and any(root.iterdir()) and not args.adopt:
        raise RuntimeError(f"refusing to scaffold into non-empty directory without --adopt: {root}")
    root.mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "scripts").mkdir(exist_ok=True)
    (root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    obsidian_root = Path(args.obsidian_root).resolve()
    obsidian_root.mkdir(parents=True, exist_ok=True)
    obsidian_file = obsidian_root / "PROJECT.md"
    if not obsidian_file.exists():
        obsidian_file.write_text(
            f"---\ntitle: \"{args.slug}\"\ntype: project\nstatus: intake\ncreated: {now()[:10]}\nmodified: {now()[:10]}\ntags:\n  - project\nauthor: qwenpaw\n---\n\n"
            f"# {args.slug}\n\n## Goal\n{args.goal}\n\n## Environment\n{args.environment}\n\n"
            "## Scope\n\n## Non-scope\n\n## Verification\n\n## Decisions\n\n## Log\n",
            encoding="utf-8",
        )
    existing_path = root / "project.json"
    existing = load_json(existing_path) if existing_path.exists() else None
    if existing and existing.get("environment") != args.environment:
        raise RuntimeError(
            f"environment lock: existing={existing.get('environment')} requested={args.environment}"
        )
    project = existing or {
        "slug": args.slug,
        "mode": args.mode,
        "environment": args.environment,
        "root": str(root),
        "goal": args.goal,
        "repo_url": args.repo_url or "",
        "default_branch": "main",
        "obsidian_file": str(obsidian_file),
        "stage": "intake",
        "active_users": bool(args.active_users),
        "data_class": args.data_class,
        "verify_cmd": args.verify_cmd,
        "created_at": now(),
        "last_good_commit": None,
        "deployed_commit": None,
        "last_backup": None,
    }
    if existing:
        project.update({
            "mode": args.mode,
            "goal": args.goal or existing.get("goal", ""),
            "obsidian_file": str(obsidian_file),
            "active_users": bool(args.active_users),
            "data_class": args.data_class,
        })
        if args.verify_cmd:
            project["verify_cmd"] = args.verify_cmd
        if args.repo_url:
            project["repo_url"] = args.repo_url
    write_json(root / "project.json", project)
    write_status(root, project)
    for name in DOC_FILES:
        if name == "STATUS.md":
            continue
        create_doc(root, name, f"# {name.removesuffix('.md').title()} — {args.slug}\n\n"
                 f"Owner: project-delivery · Environment: {args.environment}\n\n")
    create_doc(root, "PROJECT.md", f"# {args.slug}\n\n## Goal\n{args.goal}\n\n"
               f"## Mode\n{args.mode}\n\n## Environment\n{args.environment}\n\n"
               "## Scope\n\n## Non-scope\n\n## Success criterion\n\n"
               f"{args.verify_cmd}\n\n## Constraints\n\n- active_users: {bool(args.active_users)}\n- data_class: {args.data_class}\n")
    create_doc(root, "DEVLOG.md", f"# DEVLOG — {args.slug}\n\n")
    (root / ".gitignore").write_text(
        ".env\n.env.*\n*.log\n*.db\n*.sqlite\n*.dump\n*.sql\nnode_modules/\n.venv/\ndist/\nbuild/\n__pycache__/\n",
        encoding="utf-8",
    )
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "name: ci\non: [push, pull_request]\njobs:\n  verify:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n      - uses: actions/setup-python@v5\n        with:\n          python-version: '3.12'\n"
        "      - run: python3 -m unittest discover -s tests\n      - run: python3 scripts/project_ops.py doctor --root .\n",
        encoding="utf-8",
    )
    if not (root / ".git").exists():
        git(root, "init", "-b", "main")
    git(root, "config", "user.name", "QwenPaw Project Delivery")
    git(root, "config", "user.email", "project-delivery@local")
    if not existing:
        git(root, "add", "-A")
        git(root, "commit", "-m", "chore: initialize project", check=False)
    log_action(root, "scaffold", "project_ops.py scaffold", 0, ["project.json", "docs/", ".github/workflows/ci.yml"], args.goal, "baseline verification")
    print(json.dumps({"ok": True, "root": str(root), "obsidian_file": str(obsidian_file), "commit": head(root)}, ensure_ascii=False))


def log_action(root: Path, action: str, command: str, exit_code: int, files, reason: str, next_step: str) -> None:
    entry = (
        f"\n## {now()}\n"
        f"- action: {action}\n- command: `{command}`\n- exit_code: {exit_code}\n"
        f"- files: {', '.join(files) if files else '—'}\n- reason: {reason}\n- next: {next_step}\n"
    )
    with (root / "docs" / "DEVLOG.md").open("a", encoding="utf-8") as handle:
        handle.write(entry)


def require_project(root: Path):
    project = load_project(root)
    if Path(project["root"]).resolve() != root.resolve():
        raise RuntimeError(f"environment lock: root mismatch {root} != {project['root']}")
    if project["environment"] not in {"local", "server"}:
        raise RuntimeError(f"environment lock: unsupported value {project['environment']}")
    return project


def doctor(root: Path) -> dict:
    root = root.resolve()
    project = require_project(root)
    problems = []
    for name in DOC_FILES:
        if not (root / "docs" / name).is_file():
            problems.append(f"missing docs/{name}")
    if not (root / "docs" / "DEVLOG.md").is_file():
        problems.append("missing append-only DEVLOG")
    if project["stage"] not in STAGES:
        problems.append(f"invalid stage {project['stage']}")
    if not (root / ".git").exists():
        problems.append("not a git repository")
    forbidden = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for filename in files:
            path = Path(base) / filename
            if filename.endswith((".key", ".pem", ".p12", ".pfx")):
                forbidden.append(str(path.relative_to(root)))
            if path.stat().st_size > 10 * 1024 * 1024:
                forbidden.append(f"large file {path.relative_to(root)}")
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if any(pattern.search(text) for pattern in SECRET_PATTERNS):
                forbidden.append(f"possible secret {path.relative_to(root)}")
    problems.extend(forbidden)
    deployed = project.get("deployed_commit")
    if deployed and deployed != project.get("last_good_commit"):
        problems.append(f"deployed drift: deployed={deployed} last_good={project.get('last_good_commit')}")
    backup = project.get("last_backup") or {}
    if backup and backup.get("restore_check") != "passed":
        problems.append("backup restore-check missing/failed")
    report = {
        "ok": not problems,
        "slug": project["slug"],
        "stage": project["stage"],
        "environment": project["environment"],
        "head": head(root) if (root / ".git").exists() else None,
        "last_good_commit": project.get("last_good_commit"),
        "deployed_commit": deployed,
        "backup_restore_check": backup.get("restore_check"),
        "problems": problems,
    }
    (root / "doctor.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return report


def run_verify(root: Path) -> None:
    project = require_project(root)
    command = project.get("verify_cmd")
    if not command:
        return
    proc = run(shlex.split(command), cwd=root, check=False)
    if proc.returncode:
        raise RuntimeError(f"verification command failed ({proc.returncode}): {proc.stderr[-1000:]}")


def verify(root: Path, stage: str, deployed_commit: str | None = None) -> None:
    root = root.resolve()
    project = require_project(root)
    if stage not in STAGES:
        raise RuntimeError(f"invalid stage {stage}")
    if stage == "baseline-verified":
        doctor(root)
        project["stage"] = "scaffolded"
    elif stage == "verifying":
        run_verify(root)
        project["stage"] = "verifying"
    elif stage == "review":
        run_verify(root)
        result = doctor(root)
        if result["problems"]:
            raise RuntimeError(f"doctor failed: {result['problems']}")
        git(root, "diff", "--check")
        project["stage"] = "review"
    elif stage == "ready-for-release":
        run_verify(root)
        backup = project.get("last_backup") or {}
        if backup.get("restore_check") != "passed":
            raise RuntimeError("release gate: no restore-checked backup")
        result = doctor(root)
        if result["problems"]:
            raise RuntimeError(f"doctor failed: {result['problems']}")
        git(root, "diff", "--check")
        project["stage"] = "ready-for-release"
        project["last_good_commit"] = head(root)
    elif stage == "released":
        last_good = project.get("last_good_commit")
        if not last_good:
            raise RuntimeError("release gate: no verified last_good_commit")
        if not deployed_commit or deployed_commit != head(root):
            raise RuntimeError("release gate: deployed commit differs from current HEAD")
        ancestor = git(root, "merge-base", "--is-ancestor", last_good, deployed_commit, check=False)
        if ancestor.returncode:
            raise RuntimeError("release gate: deployed commit is not a descendant of last_good_commit")
        run_verify(root)
        project["stage"] = "released"
    else:
        raise RuntimeError(f"unsupported verification stage {stage}")
    save_project(root, project)
    log_action(root, f"verify:{stage}", f"project_ops.py verify --stage {stage}", 0, ["project.json", "STATUS.md"], "gate passed", "next gate")
    print(json.dumps({"ok": True, "stage": project["stage"], "head": head(root)}, ensure_ascii=False))


def backup(root: Path) -> None:
    root = root.resolve()
    project = require_project(root)
    commit = head(root)
    stamp = now().replace(":", "").replace("-", "")
    backup_dir = root.parent / f"{root.name}.backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    archive = backup_dir / "source.tar.gz"
    with archive.open("wb") as handle:
        proc = subprocess.run(["git", "-C", str(root), "archive", "--format=tar.gz", "-o", "/dev/stdout", commit], stdout=handle, stderr=subprocess.PIPE)
        if proc.returncode:
            raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-1000:])
    obsidian_source = Path(project["obsidian_file"])
    obsidian_copy = backup_dir / "obsidian-PROJECT.md"
    if obsidian_source.exists():
        shutil.copy2(obsidian_source, obsidian_copy)
    manifest = {
        "commit": commit,
        "created_at": now(),
        "archive": archive.name,
        "archive_sha256": sha256(archive),
        "obsidian_sha256": sha256(obsidian_copy) if obsidian_copy.exists() else None,
        "restore_check": "pending",
    }
    restore_dir = backup_dir / "restore"
    restore_dir.mkdir()
    run(["tar", "-xzf", str(archive), "-C", str(restore_dir)])
    manifest_files = {}
    for base, _, files in os.walk(restore_dir):
        for filename in files:
            path = Path(base) / filename
            manifest_files[str(path.relative_to(restore_dir))] = sha256(path)
    manifest["files"] = manifest_files
    manifest["restore_check"] = "passed"
    write_json(backup_dir / "manifest.json", manifest)
    project["last_backup"] = {
        "path": str(backup_dir),
        "created_at": manifest["created_at"],
        "restore_check": "passed",
        "manifest": str(backup_dir / "manifest.json"),
    }
    save_project(root, project)
    log_action(root, "backup+restore-check", "git archive + isolated restore", 0, [str(backup_dir)], "rollback point verified", "continue work")
    print(json.dumps({"ok": True, "backup": str(backup_dir), "files": len(manifest_files)}, ensure_ascii=False))


def commit(root: Path, message: str) -> None:
    root = root.resolve()
    require_project(root)
    result = doctor(root)
    if result["problems"]:
        raise RuntimeError(f"commit gate: {result['problems']}")
    git(root, "diff", "--check")
    git(root, "add", "-A")
    git(root, "commit", "-m", message)
    save_project(root, load_project(root))
    print(json.dumps({"ok": True, "commit": head(root)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--root", required=True)
    init.add_argument("--slug", required=True)
    init.add_argument("--mode", choices=["new", "existing", "production"], required=True)
    init.add_argument("--environment", choices=["local", "server"], required=True)
    init.add_argument("--goal", required=True)
    init.add_argument("--verify-cmd", default="")
    init.add_argument("--obsidian-root", required=True)
    init.add_argument("--repo-url", default="")
    init.add_argument("--active-users", nargs="?", const=True, default=False,
                      type=lambda value: str(value).lower() in {"1", "true", "yes", "on"})
    init.add_argument("--data-class", choices=["public", "internal", "sensitive"], default="public")
    init.add_argument("--adopt", action="store_true")
    for name in ["doctor", "backup"]:
        p = sub.add_parser(name)
        p.add_argument("--root", required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--root", required=True)
    verify_parser.add_argument("--stage", required=True)
    verify_parser.add_argument("--deployed-commit")
    commit_parser = sub.add_parser("commit")
    commit_parser.add_argument("--root", required=True)
    commit_parser.add_argument("--message", default="chore: checkpoint")
    watchdog_parser = sub.add_parser("watchdog")
    watchdog_parser.add_argument("--root", required=True)
    watchdog_parser.add_argument("--check-interval", type=int, default=30)
    watchdog_parser.add_argument("--max-iterations", type=int, default=100)
    watchdog_parser.add_argument("--heartbeat-file")
    args = parser.parse_args()
    root = Path(args.root)
    if args.command == "init":
        scaffold(args)
    elif args.command == "doctor":
        doctor(root)
    elif args.command == "backup":
        backup(root)
    elif args.command == "verify":
        verify(root, args.stage, args.deployed_commit)
    elif args.command == "commit":
        commit(root, args.message)
    elif args.command == "watchdog":
        if AutonomousWatchdog is None:
            raise RuntimeError("autonomous watchdog not available")
        watchdog = AutonomousWatchdog(
            root=root,
            check_interval=args.check_interval,
            max_iterations=args.max_iterations,
            heartbeat_file=args.heartbeat_file,
        )
        watchdog.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
