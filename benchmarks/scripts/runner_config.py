#!/usr/bin/env python3
"""Load and validate the SWE benchmark runner configuration.

The runner config is a small YAML file that supplies the run-time parameters
for the headless harness: which endpoint and model to drive, which dataset to
run, where to put outputs, and how to invoke `claude -p` (permission mode,
allowed tools, turn cap). Every field can be overridden on the command line so
a committed config stays the reusable default while one-off runs stay flexible.

Run it from the ``benchmarks/`` directory with its own venv:

    uv run scripts/runner_config.py config/runner.example.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)

# Tools the /swe skill needs to read a repo and write the four artifacts. The
# skill only reads code and writes markdown, so this stays deliberately narrow.
DEFAULT_ALLOWED_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "Write",
    "Edit",
    "Bash(git clone*)",
    "Bash(git -C*)",
    "Bash(mktemp*)",
    "Task",
]
# acceptEdits lets the skill write artifacts without a prompt while still
# refusing anything not covered by the allowlist. We never default to
# bypassPermissions.
DEFAULT_PERMISSION_MODE = "acceptEdits"
VALID_PERMISSION_MODES = {"default", "acceptEdits", "plan"}
DEFAULT_MAX_TURNS = 60
DEFAULT_MAX_OUTPUT_TOKENS = 16000
DEFAULT_TIMEOUT_SECONDS = 1800


class RunnerConfigError(Exception):
    """Raised when the runner config is missing, unparseable, or invalid."""


class RunnerConfig(BaseModel):
    """Run-time parameters for the headless SWE benchmark harness."""

    model_config = ConfigDict(extra="forbid")

    # Routing: where claude -p sends requests and which model it names.
    endpoint: str = Field(
        description="Base URL of the OpenAI/Anthropic-compatible endpoint "
        "(e.g. http://127.0.0.1:8000)."
    )
    model: str = Field(description="Model name/id to pass to claude --model.")
    api_key: str = Field(default="local", description="API key sent to the endpoint.")

    # What to run and where outputs go.
    dataset: str = Field(description="Path to the benchmark dataset YAML file.")
    output_dir: str = Field(
        default="swe-benchmark-data",
        description="Directory (relative to repo root) where artifacts land.",
    )
    clone_dir: str = Field(
        default="/tmp",  # nosec B108 - clone parent; each repo lands in a mkdtemp subdir
        description="Parent directory for per-task temporary repo clones.",
    )
    tasks: list[str] = Field(
        default_factory=list,
        description="Task ids to run. Empty means every task in the dataset.",
    )
    concurrency: int = Field(
        default=1,
        ge=1,
        description="How many tasks to run at once. 1 (default) runs serially. "
        "Values above 1 overlap runs on the endpoint, which invalidates the "
        "single-tenant vllm_prometheus window-delta metrics for those runs.",
    )

    # How claude -p is invoked.
    permission_mode: str = Field(default=DEFAULT_PERMISSION_MODE)
    allowed_tools: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_TOOLS))
    max_turns: int = Field(default=DEFAULT_MAX_TURNS, ge=1)
    max_output_tokens: int = Field(default=DEFAULT_MAX_OUTPUT_TOKENS, ge=1)
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT_SECONDS, ge=1)
    settings_file: str | None = Field(
        default=None,
        description="Optional claude --settings JSON file (e.g. the vLLM config).",
    )

    def validate_semantics(self) -> None:
        """Check fields the type system cannot.

        Raises:
            RunnerConfigError: If a value is present but invalid.
        """
        if self.permission_mode not in VALID_PERMISSION_MODES:
            raise RunnerConfigError(
                f"permission_mode '{self.permission_mode}' not in "
                f"{sorted(VALID_PERMISSION_MODES)}. bypassPermissions and "
                "dangerously-skip-permissions are intentionally not allowed."
            )
        if not self.endpoint.startswith(("http://", "https://")):
            raise RunnerConfigError(
                f"endpoint '{self.endpoint}' must start with http:// or https://"
            )


def _apply_overrides(data: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Merge CLI overrides onto raw config data (CLI wins).

    Args:
        data: The parsed YAML config mapping.
        overrides: CLI-supplied values; None entries are ignored.

    Returns:
        A new mapping with non-None overrides applied.
    """
    merged = dict(data)
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value
    return merged


def load_runner_config(
    path: str | Path | None,
    overrides: dict[str, Any] | None = None,
) -> RunnerConfig:
    """Load the runner config from YAML and apply CLI overrides.

    Args:
        path: Path to the config YAML file, or None to build purely from
            overrides (useful for CLI-only runs).
        overrides: CLI-supplied values that take precedence over the file.

    Returns:
        The validated RunnerConfig.

    Raises:
        RunnerConfigError: If the file is missing, unparseable, or invalid.
    """
    overrides = overrides or {}

    if path is None:
        raw: dict[str, Any] = {}
    else:
        file_path = Path(path)
        if not file_path.exists():
            raise RunnerConfigError(f"Runner config not found: {file_path}")
        try:
            loaded = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise RunnerConfigError(f"Failed to parse {file_path}: {exc}") from exc
        if loaded is None:
            raw = {}
        elif isinstance(loaded, dict):
            raw = loaded
        else:
            raise RunnerConfigError(f"{file_path}: top level must be a mapping")

    merged = _apply_overrides(raw, overrides)

    try:
        config = RunnerConfig.model_validate(merged)
    except ValidationError as exc:
        raise RunnerConfigError(f"Invalid runner config:\n{exc}") from exc

    config.validate_semantics()
    return config


def _summarize(config: RunnerConfig) -> None:
    """Log a short human-readable summary of the runner config."""
    logger.info("Runner config:")
    logger.info("  endpoint: %s", config.endpoint)
    logger.info("  model: %s", config.model)
    logger.info("  dataset: %s", config.dataset)
    logger.info("  output_dir: %s", config.output_dir)
    logger.info("  clone_dir: %s", config.clone_dir)
    logger.info("  tasks: %s", config.tasks or "(all)")
    logger.info("  concurrency: %s", config.concurrency)
    logger.info("  permission_mode: %s", config.permission_mode)
    logger.info("  max_turns: %s", config.max_turns)
    logger.info("  allowed_tools: %s", ", ".join(config.allowed_tools))


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Validate and summarize a SWE benchmark runner config.",
        epilog="Example:\n  uv run scripts/runner_config.py config/runner.example.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("config", help="Path to the runner config YAML file")
    return parser.parse_args()


def main() -> None:
    """Validate the given runner config file and print a summary."""
    args = _parse_args()
    try:
        config = load_runner_config(args.config)
    except RunnerConfigError as exc:
        logger.error("Invalid runner config: %s", exc)
        sys.exit(1)
    _summarize(config)
    logger.info("Runner config is valid.")


if __name__ == "__main__":
    main()
