#!/usr/bin/env python3
"""Pre-flight helper for the end-to-end benchmark orchestrator.

Enumerates the artifact directories a SWE benchmark run would write to, for a
given dataset and model, and either reports which already exist (so a headless
run does not stall on the /swe skill's overwrite prompt) or clears them.

The directory layout mirrors the harness exactly -- it reuses the dataset
loader, ``model_to_slug`` (the folder-name normalization), and ``_repo_name``
(the repo-basename derivation) rather than re-deriving any of them here, so this
helper and the harness can never disagree about where artifacts land.

Run from the ``benchmarks/`` directory:

    uv run scripts/preflight_check.py --dataset dataset/mcp-gateway-registry.yaml \
        --model qwen3.6-35b --check
    uv run scripts/preflight_check.py --dataset dataset/mcp-gateway-registry.yaml \
        --model qwen3.6-35b --clear

Exit codes (``--check``): 0 = no existing folders (safe to run), 2 = one or
more exist (need clearing), 1 = an error (bad dataset, bad args).
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import shutil
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from dataset_loader import DatasetError, load_dataset  # noqa: E402
from runner_config import model_to_slug  # noqa: E402

# The four artifacts the /swe skill writes; their presence is what makes the
# skill stop and ask before overwriting.
_ARTIFACT_FILENAMES = ("github-issue.md", "lld.md", "review.md", "testing.md")

# The output root, relative to benchmarks/, matching the harness default.
_OUTPUT_DIR = "swe-benchmark-data"


def _repo_name_from_harness() -> "callable":
    """Load the harness module and return its ``_repo_name`` function.

    The harness file name (``run-swe-headless.py``) is not a valid module
    identifier, so import it by path rather than with a plain ``import``.

    Returns:
        The harness ``_repo_name`` callable.

    Raises:
        RuntimeError: If the harness module cannot be loaded.
    """
    path = _SCRIPTS_DIR / "run-swe-headless.py"
    spec = importlib.util.spec_from_file_location("swe_harness", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load harness module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._repo_name


def _target_dirs(dataset_path: str, model: str) -> list[Path]:
    """Return the artifact directory for every task in the dataset.

    Args:
        dataset_path: Path to the dataset YAML (relative to benchmarks/).
        model: The model id passed to the harness (full id, not the slug).

    Returns:
        Absolute artifact directories, one per task, in dataset order.

    Raises:
        DatasetError: If the dataset is missing or invalid.
    """
    benchmarks_dir = _SCRIPTS_DIR.parent
    resolved = Path(dataset_path)
    if not resolved.is_absolute():
        resolved = benchmarks_dir / dataset_path
    dataset = load_dataset(resolved)
    repo_name = _repo_name_from_harness()
    slug = model_to_slug(model)
    root = benchmarks_dir / _OUTPUT_DIR
    return [root / slug / repo_name(task.repo) / task.id for task in dataset.tasks]


def _existing(dirs: list[Path]) -> list[Path]:
    """Return the subset of dirs that exist and contain at least one artifact."""
    found: list[Path] = []
    for d in dirs:
        if d.is_dir() and any((d / name).exists() for name in _ARTIFACT_FILENAMES):
            found.append(d)
    return found


def _run_check(dirs: list[Path]) -> int:
    """Report existing artifact folders. Returns the process exit code."""
    existing = _existing(dirs)
    if not existing:
        logger.info("OK: no existing artifact folders for this model; safe to run.")
        logger.info("Would write %d task folder(s):", len(dirs))
        for d in dirs:
            logger.info("  %s", d)
        return 0
    logger.warning(
        "%d of %d target folder(s) already contain artifacts and would make the "
        "headless /swe run stall on its overwrite prompt:",
        len(existing),
        len(dirs),
    )
    for d in existing:
        logger.warning("  EXISTS: %s", d)
    logger.warning("Clear them with --clear (or rename them to keep the prior run).")
    return 2


def _run_clear(dirs: list[Path]) -> int:
    """Remove existing artifact folders. Returns the process exit code."""
    existing = _existing(dirs)
    if not existing:
        logger.info("Nothing to clear: no existing artifact folders for this model.")
        return 0
    for d in existing:
        shutil.rmtree(d)
        logger.info("cleared %s", d)
    logger.info("Cleared %d folder(s).", len(existing))
    return 0


def main() -> None:
    """Parse arguments and run the requested pre-flight action."""
    parser = argparse.ArgumentParser(
        description="Check or clear the artifact folders a benchmark run would write to.",
    )
    parser.add_argument(
        "--dataset", required=True, help="Dataset YAML path (relative to benchmarks/)."
    )
    parser.add_argument(
        "--model", required=True, help="Model id (the full id passed to the harness)."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check", action="store_true", help="Report existing folders (exit 2 if any)."
    )
    group.add_argument(
        "--clear", action="store_true", help="Remove existing artifact folders."
    )
    args = parser.parse_args()

    try:
        dirs = _target_dirs(args.dataset, args.model)
    except DatasetError as exc:
        logger.error("Dataset error: %s", exc)
        sys.exit(1)

    sys.exit(_run_check(dirs) if args.check else _run_clear(dirs))


if __name__ == "__main__":
    main()
