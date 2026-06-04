#!/usr/bin/env python3
"""Tests for the Cowork Git bridge.

Run with:
  python3 scripts/test_cowork_git_bridge.py
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cowork_git_bridge as bridge


class CoworkGitBridgeTests(unittest.TestCase):
    def test_reads_origin_url_from_git_config_without_git_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            git_dir = repo / ".git"
            git_dir.mkdir()
            (git_dir / "config").write_text(
                "[core]\n"
                "\trepositoryformatversion = 0\n"
                "[remote \"origin\"]\n"
                "\turl = git@github.com:jcpeters08/tv-concierge.git\n"
                "\tfetch = +refs/heads/*:refs/remotes/origin/*\n"
            )

            self.assertEqual(
                bridge.read_origin_url(repo),
                "git@github.com:jcpeters08/tv-concierge.git",
            )

    def test_rejects_bridge_workdir_inside_mounted_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            workdir = repo / ".cowork-git"

            with self.assertRaisesRegex(ValueError, "must not be inside"):
                bridge.validate_bridge_workdir(repo, workdir)


if __name__ == "__main__":
    unittest.main()
