"""Tests for the SWE benchmark dataset loader."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# The benchmark scripts are not a package; add the scripts dir to the path so
# dataset_loader (underscore name, importable) can be imported by module name.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from dataset_loader import DatasetError, load_dataset  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SHIPPED_DATASET = _REPO_ROOT / "benchmarks" / "dataset" / "mcp-gateway-registry.yaml"

_MINIMAL = """\
schema_version: "1.0"
name: tiny
title: Tiny dataset
description: A minimal valid dataset.
default_ref: main
metrics: [input_tokens, output_tokens, num_turns]
complexity_levels: [low, medium, high]
tasks:
  - id: only-task
    repo: https://github.com/example/repo
    complexity: low
    tags: [demo]
    problem_statement: |
      Do the thing.
"""


def _write(text: str) -> Path:
    """Write dataset text to a temp file and return its path."""
    temp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    temp.write(text)
    temp.close()
    return Path(temp.name)


class LoadDatasetTest(unittest.TestCase):
    def test_shipped_dataset_loads(self) -> None:
        dataset = load_dataset(_SHIPPED_DATASET)
        self.assertEqual(dataset.name, "mcp-gateway-registry-swe")
        self.assertEqual(len(dataset.tasks), 5)
        self.assertIn("num_turns", dataset.metrics)

    def test_ref_defaults_to_dataset_default(self) -> None:
        dataset = load_dataset(_write(_MINIMAL))
        self.assertEqual(dataset.task_by_id("only-task").ref, "main")

    def test_task_ref_overrides_default(self) -> None:
        text = _MINIMAL.replace(
            "    complexity: low", '    ref: "1.2.3"\n    complexity: low'
        )
        dataset = load_dataset(_write(text))
        self.assertEqual(dataset.task_by_id("only-task").ref, "1.2.3")

    def test_missing_file_raises(self) -> None:
        with self.assertRaisesRegex(DatasetError, "not found"):
            load_dataset("/nonexistent/dataset.yaml")

    def test_unsupported_schema_version_raises(self) -> None:
        text = _MINIMAL.replace('schema_version: "1.0"', 'schema_version: "9.9"')
        with self.assertRaisesRegex(DatasetError, "unsupported schema_version"):
            load_dataset(_write(text))

    def test_bad_complexity_raises(self) -> None:
        text = _MINIMAL.replace("    complexity: low", "    complexity: extreme")
        with self.assertRaisesRegex(DatasetError, "complexity 'extreme'"):
            load_dataset(_write(text))

    def test_missing_problem_source_raises(self) -> None:
        text = _MINIMAL.replace("    problem_statement: |\n      Do the thing.\n", "")
        with self.assertRaisesRegex(DatasetError, "at least one of"):
            load_dataset(_write(text))

    def test_issue_url_alone_is_valid(self) -> None:
        text = _MINIMAL.replace(
            "    problem_statement: |\n      Do the thing.\n",
            "    problem_issue_url: https://github.com/example/repo/issues/1\n",
        )
        dataset = load_dataset(_write(text))
        task = dataset.task_by_id("only-task")
        self.assertIsNone(task.problem_statement)
        self.assertTrue(task.problem_issue_url)

    def test_duplicate_task_id_raises(self) -> None:
        text = _MINIMAL + """\
  - id: only-task
    repo: https://github.com/example/repo
    complexity: high
    tags: [dupe]
    problem_statement: duplicate id
"""
        with self.assertRaisesRegex(DatasetError, "duplicate task id"):
            load_dataset(_write(text))

    def test_ground_truth_is_optional_and_parsed(self) -> None:
        dataset = load_dataset(_SHIPPED_DATASET)
        faiss = dataset.task_by_id("remove-faiss")
        self.assertIsNotNone(faiss.ground_truth)
        self.assertTrue(faiss.ground_truth.expectations)
        # Minimal dataset omits ground_truth entirely.
        minimal = load_dataset(_write(_MINIMAL))
        self.assertIsNone(minimal.task_by_id("only-task").ground_truth)


if __name__ == "__main__":
    unittest.main()
