"""Tests for the SWE benchmark runner config loader."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from runner_config import RunnerConfigError, load_runner_config  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SHIPPED_CONFIG = _REPO_ROOT / "benchmarks" / "config" / "runner.example.yaml"

_MINIMAL = """\
endpoint: http://127.0.0.1:8000
model: test-model
dataset: dataset/example.yaml
"""


def _write(text: str) -> Path:
    """Write config text to a temp file and return its path."""
    temp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    temp.write(text)
    temp.close()
    return Path(temp.name)


class LoadRunnerConfigTest(unittest.TestCase):
    def test_shipped_config_loads(self) -> None:
        config = load_runner_config(_SHIPPED_CONFIG)
        self.assertEqual(config.model, "qwen3.6-35b")
        self.assertEqual(config.permission_mode, "acceptEdits")
        self.assertIn("Read", config.allowed_tools)

    def test_defaults_applied(self) -> None:
        config = load_runner_config(_write(_MINIMAL))
        self.assertEqual(config.api_key, "local")
        self.assertEqual(config.permission_mode, "acceptEdits")
        self.assertEqual(config.max_turns, 60)
        self.assertEqual(config.tasks, [])
        self.assertEqual(config.concurrency, 1)

    def test_concurrency_override_and_floor(self) -> None:
        config = load_runner_config(_write(_MINIMAL), {"concurrency": 4})
        self.assertEqual(config.concurrency, 4)
        with self.assertRaises(RunnerConfigError):
            load_runner_config(_write(_MINIMAL), {"concurrency": 0})

    def test_cli_overrides_win(self) -> None:
        config = load_runner_config(
            _write(_MINIMAL),
            {"model": "override-model", "max_turns": 10, "tasks": ["a", "b"]},
        )
        self.assertEqual(config.model, "override-model")
        self.assertEqual(config.max_turns, 10)
        self.assertEqual(config.tasks, ["a", "b"])

    def test_none_overrides_are_ignored(self) -> None:
        config = load_runner_config(
            _write(_MINIMAL), {"model": None, "endpoint": None}
        )
        self.assertEqual(config.model, "test-model")

    def test_missing_file_raises(self) -> None:
        with self.assertRaisesRegex(RunnerConfigError, "not found"):
            load_runner_config("/nonexistent/runner.yaml")

    def test_bypass_permissions_rejected(self) -> None:
        text = _MINIMAL + "permission_mode: bypassPermissions\n"
        with self.assertRaisesRegex(RunnerConfigError, "permission_mode"):
            load_runner_config(_write(text))

    def test_bad_endpoint_scheme_rejected(self) -> None:
        text = "endpoint: 127.0.0.1:8000\nmodel: m\ndataset: d.yaml\n"
        with self.assertRaisesRegex(RunnerConfigError, "http"):
            load_runner_config(_write(text))

    def test_unknown_field_rejected(self) -> None:
        text = _MINIMAL + "bogus_field: 1\n"
        with self.assertRaises(RunnerConfigError):
            load_runner_config(_write(text))

    def test_config_from_overrides_only(self) -> None:
        config = load_runner_config(
            None,
            {"endpoint": "http://localhost:9000", "model": "m", "dataset": "d.yaml"},
        )
        self.assertEqual(config.endpoint, "http://localhost:9000")


if __name__ == "__main__":
    unittest.main()
