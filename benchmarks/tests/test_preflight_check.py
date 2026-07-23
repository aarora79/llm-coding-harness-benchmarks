"""Tests for the end-to-end benchmark pre-flight helper."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))


def _load_preflight():
    """Import preflight_check.py by path (module name has no dashes, but be explicit)."""
    path = _SCRIPTS_DIR / "preflight_check.py"
    spec = importlib.util.spec_from_file_location("preflight_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pf = _load_preflight()

_DATASET = """\
schema_version: "1.0"
name: t
title: T
description: d
default_ref: main
metrics: [input_tokens]
complexity_levels: [low]
tasks:
  - id: task-one
    repo: https://github.com/example/my-repo
    complexity: low
    tags: [x]
    problem_statement: do the thing
  - id: task-two
    repo: https://github.com/example/my-repo
    complexity: low
    tags: [x]
    problem_statement: do the other thing
"""


class TargetDirsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ds = _SCRIPTS_DIR.parent / "dataset" / "_preflight_test.yaml"
        self.ds.write_text(_DATASET, encoding="utf-8")

    def tearDown(self) -> None:
        self.ds.unlink(missing_ok=True)

    def test_one_dir_per_task_with_slug(self) -> None:
        dirs = pf._target_dirs(str(self.ds), "us.anthropic.claude-opus-4-8")
        self.assertEqual(len(dirs), 2)
        # Bedrock prefix stripped for the folder slug; repo basename used.
        self.assertTrue(str(dirs[0]).endswith("my-repo/task-one/claude-opus-4-8"))
        self.assertTrue(str(dirs[1]).endswith("my-repo/task-two/claude-opus-4-8"))

    def test_plain_model_slug_unchanged(self) -> None:
        dirs = pf._target_dirs(str(self.ds), "qwen3-coder-30b")
        self.assertTrue(str(dirs[0]).endswith("my-repo/task-one/qwen3-coder-30b"))


class ExistingTest(unittest.TestCase):
    def test_only_folders_with_artifacts_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            has = root / "with-artifact"
            has.mkdir()
            (has / "lld.md").write_text("x", encoding="utf-8")
            empty = root / "empty"
            empty.mkdir()
            missing = root / "does-not-exist"
            found = pf._existing([has, empty, missing])
            self.assertEqual(found, [has])


if __name__ == "__main__":
    unittest.main()
