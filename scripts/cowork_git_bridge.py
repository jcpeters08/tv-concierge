#!/usr/bin/env python3
"""Run TV Concierge sync from a disposable Git clone.

Cowork sandbox mounts can allow Git lock creation while denying lock
unlink under the mounted repo's .git directory. This bridge keeps all Git
operations in a clone on normal local temporary storage, while the sync
script still reads/writes the vault through the usual mounted paths.
"""

from __future__ import annotations

import argparse
import configparser
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_SOURCE_REPO = Path.home() / "Git" / "tv-concierge"
DEFAULT_BRANCH = "main"


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check, text=True)


def read_origin_url(source_repo: Path) -> str:
    """Read origin.url directly from .git/config.

    Avoiding `git config` here is intentional: this script may be launched
    specifically because Git writes are unhealthy in the mounted source repo.
    """
    config_path = source_repo / ".git" / "config"
    parser = configparser.ConfigParser()
    if not parser.read(config_path):
        raise FileNotFoundError(f"missing Git config: {config_path}")

    section = 'remote "origin"'
    try:
        return parser[section]["url"].strip()
    except KeyError as exc:
        raise ValueError(f"missing remote.origin.url in {config_path}") from exc


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def validate_bridge_workdir(source_repo: Path, bridge_workdir: Path) -> None:
    if is_relative_to(bridge_workdir, source_repo):
        raise ValueError(
            f"bridge workdir must not be inside the mounted repo: {bridge_workdir}"
        )


def default_bridge_workdir() -> Path:
    base = Path(os.environ.get("TV_CONCIERGE_COWORK_BASE", tempfile.gettempdir()))
    return base / "tv-concierge-cowork-git"


def prepare_clone(source_repo: Path, bridge_workdir: Path, branch: str) -> Path:
    source_repo = source_repo.expanduser().resolve()
    bridge_workdir = bridge_workdir.expanduser().resolve()
    validate_bridge_workdir(source_repo, bridge_workdir)

    remote_url = read_origin_url(source_repo)
    if (bridge_workdir / ".git").exists():
        run(["git", "fetch", "--prune", "origin"], cwd=bridge_workdir)
        run(["git", "checkout", branch], cwd=bridge_workdir)
        run(["git", "reset", "--hard", f"origin/{branch}"], cwd=bridge_workdir)
        run(["git", "clean", "-fd", "--", "data", "scripts"], cwd=bridge_workdir)
    else:
        if bridge_workdir.exists():
            shutil.rmtree(bridge_workdir)
        bridge_workdir.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--branch", branch, "--single-branch", remote_url, str(bridge_workdir)])

    return bridge_workdir


def run_sync(bridge_workdir: Path) -> int:
    env = os.environ.copy()
    env["TV_CONCIERGE_REPO_ROOT"] = str(bridge_workdir)
    result = subprocess.run(
        [sys.executable, "scripts/sync.py"],
        cwd=str(bridge_workdir),
        env=env,
        text=True,
    )
    return result.returncode


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-repo",
        type=Path,
        default=Path(os.environ.get("TV_CONCIERGE_SOURCE_REPO", str(DEFAULT_SOURCE_REPO))),
        help="Mounted repo used only for reading remote.origin.url.",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path(os.environ.get("TV_CONCIERGE_COWORK_WORKDIR", str(default_bridge_workdir()))),
        help="Disposable clone path on non-mounted storage.",
    )
    parser.add_argument(
        "--branch",
        default=os.environ.get("TV_CONCIERGE_BRANCH", DEFAULT_BRANCH),
        help="Branch to clone/reset before running sync.",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Prepare the clone and print its path without running sync.py.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        bridge_workdir = prepare_clone(args.source_repo, args.workdir, args.branch)
    except Exception as exc:
        print(f"ERROR: failed to prepare Cowork Git bridge: {exc}", file=sys.stderr)
        return 2

    print(f"COWORK_GIT_WORKDIR={bridge_workdir}")
    if args.prepare_only:
        return 0
    return run_sync(bridge_workdir)


if __name__ == "__main__":
    sys.exit(main())
