#!/usr/bin/env python3
"""Shared core for the SWE artifact judges.

Both judge backends -- the direct Bedrock Mantle call (``llm_as_judge.py``) and
the agentic ``codex exec`` run (``codex_judge.py``) -- score the same four
artifacts against the same rubric and must produce identically-shaped,
identically-validated ``eval.json`` output. That common ground lives here:

  * the strict score schema (``EvaluationResult`` and friends),
  * prompt rendering from ``judge_prompt.txt`` (``render_judge_prompt``),
  * parsing and validating a model's reply (``parse_and_validate_result``),
  * the atomic ``eval.json`` writer (``atomic_write_json``),
  * small file helpers (``read_text``, ``optional_file``).

Each backend imports these and adds only its own transport (an HTTP request vs.
a codex subprocess) plus its judge-metadata block.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from string import Template
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ARTIFACT_FILES = {
    "github_issue": "github-issue.md",
    "lld": "lld.md",
    "review": "review.md",
    "testing": "testing.md",
}
DEFAULT_TEMPLATE_PATH = Path(__file__).with_name("judge_prompt.txt")
Score = Annotated[int, Field(strict=True, ge=0, le=25)]


class JudgeError(Exception):
    """Raised when judge inputs, model output, or score data are invalid."""


class ArtifactScore(BaseModel):
    """Validated scores for one artifact."""

    model_config = ConfigDict(extra="forbid")

    completeness: Score
    correctness: Score
    specificity: Score
    risk_awareness: Score
    total: Annotated[int, Field(strict=True, ge=0, le=100)]
    notes: str

    @model_validator(mode="after")
    def total_is_correct(self) -> "ArtifactScore":
        expected = (
            self.completeness
            + self.correctness
            + self.specificity
            + self.risk_awareness
        )
        if self.total != expected:
            raise ValueError(f"total is {self.total}; expected {expected}")
        return self


class ScoreSet(BaseModel):
    """The fixed four-artifact score set."""

    model_config = ConfigDict(extra="forbid")

    github_issue: ArtifactScore
    lld: ArtifactScore
    review: ArtifactScore
    testing: ArtifactScore


class EvaluationResult(BaseModel):
    """Strict model-produced evaluation before judge metadata is attached."""

    model_config = ConfigDict(extra="forbid")

    task: str
    model: str
    scores: ScoreSet
    task_score: float
    verdict: str

    @model_validator(mode="after")
    def task_score_is_correct(self) -> "EvaluationResult":
        totals = [
            self.scores.github_issue.total,
            self.scores.lld.total,
            self.scores.review.total,
            self.scores.testing.total,
        ]
        expected = round(sum(totals) / len(totals), 2)
        if abs(self.task_score - expected) > 0.001:
            raise ValueError(f"task_score is {self.task_score}; expected {expected}")
        return self


def read_text(path: Path, label: str) -> str:
    """Read a non-empty UTF-8 text file, raising JudgeError on any problem.

    Args:
        path: File to read.
        label: Human-readable name used in error messages.

    Returns:
        The file's text.

    Raises:
        JudgeError: If the file is missing, unreadable, or empty.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise JudgeError(f"missing {label}: {path}") from exc
    except OSError as exc:
        raise JudgeError(f"could not read {label} {path}: {exc}") from exc
    if not content.strip():
        raise JudgeError(f"{label} is empty: {path}")
    return content


def optional_file(path: str | None, label: str) -> str | None:
    """Read a file when a path is given, else return None.

    Args:
        path: Optional file path.
        label: Human-readable name used in error messages.

    Returns:
        The file's text, or None when no path was supplied.
    """
    return read_text(Path(path).expanduser().resolve(), label) if path else None


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise JudgeError(f"missing {label}: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise JudgeError(f"could not parse {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise JudgeError(f"{label} must contain a top-level JSON object: {path}")
    return value


def _default_task_context(metadata: dict[str, Any]) -> str:
    for key in ("task_context", "problem_statement", "task_description"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return (
        "No independent task statement was supplied. Evaluate requirement coverage "
        "only where established by the task identifier, repository context, or "
        "internally consistent artifacts, and report this evidence gap."
    )


def _default_repository_context(metadata: dict[str, Any]) -> str:
    context = {
        key: metadata[key]
        for key in ("repo", "ref", "complexity", "tags")
        if key in metadata
    }
    return (
        json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True)
        if context
        else "No independent repository context was supplied."
    )


def render_judge_prompt(
    folder: str | Path,
    *,
    template_path: str | Path = DEFAULT_TEMPLATE_PATH,
    task_context: str | None = None,
    repository_context: str | None = None,
) -> tuple[str, str, str, dict[str, Any] | None]:
    """Load an artifact folder and render ``judge_prompt.txt``.

    Args:
        folder: Directory containing the four required Markdown artifacts.
        template_path: Judge prompt template path.
        task_context: Optional independent task requirements. Defaults from
            ``metrics.json`` when present, else a documented evidence-gap notice.
        repository_context: Optional independent repository evidence. Defaults
            from ``metrics.json`` fields when present.

    Returns:
        A tuple of (rendered prompt, task id, candidate id, metrics-or-None).

    Raises:
        JudgeError: If the folder, artifacts, or template are invalid.
    """
    artifact_dir = Path(folder).expanduser().resolve()
    if not artifact_dir.is_dir():
        raise JudgeError(f"artifact folder is not a directory: {artifact_dir}")

    metrics_path = artifact_dir / "metrics.json"
    metrics = (
        _load_json_object(metrics_path, "metrics.json")
        if metrics_path.exists()
        else None
    )
    metadata = metrics or {}
    # Prefer identifiers recorded in metrics.json. Fall back to the folder
    # layout, which is <model>/<repo>/<task>/: the leaf is the task and the
    # grandparent is the model.
    task_id = metadata.get("task") or artifact_dir.name
    candidate_id = metadata.get("model") or artifact_dir.parent.parent.name
    if not isinstance(task_id, str) or not task_id.strip():
        raise JudgeError("task identifier must be a non-empty string")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise JudgeError("candidate identifier must be a non-empty string")

    artifacts = {
        name: read_text(artifact_dir / filename, filename)
        for name, filename in ARTIFACT_FILES.items()
    }
    template = Template(
        read_text(Path(template_path).expanduser().resolve(), "prompt template")
    )
    values = {
        "TASK_ID_JSON": json.dumps(task_id, ensure_ascii=False),
        "CANDIDATE_ID_JSON": json.dumps(candidate_id, ensure_ascii=False),
        "TASK_CONTEXT_JSON": json.dumps(
            task_context
            if task_context is not None
            else _default_task_context(metadata),
            ensure_ascii=False,
        ),
        "REPOSITORY_CONTEXT_JSON": json.dumps(
            repository_context
            if repository_context is not None
            else _default_repository_context(metadata),
            ensure_ascii=False,
        ),
        "GITHUB_ISSUE_JSON": json.dumps(artifacts["github_issue"], ensure_ascii=False),
        "LLD_JSON": json.dumps(artifacts["lld"], ensure_ascii=False),
        "REVIEW_JSON": json.dumps(artifacts["review"], ensure_ascii=False),
        "TESTING_JSON": json.dumps(artifacts["testing"], ensure_ascii=False),
    }
    try:
        prompt = template.substitute(values)
    except (KeyError, ValueError) as exc:
        raise JudgeError(f"invalid prompt template {template_path}: {exc}") from exc
    return prompt, task_id, candidate_id, metrics


def parse_and_validate_result(
    text: str, *, task_id: str, candidate_id: str
) -> dict[str, Any]:
    """Parse a model reply into a validated evaluation dict.

    Tolerates a single fenced code block wrapping the JSON. Enforces the strict
    schema (criteria 0-25, totals = sums, task_score = mean) and that the
    returned identifiers match the submission exactly.

    Args:
        text: The model's reply text.
        task_id: The task id the reply must echo.
        candidate_id: The candidate id the reply must echo.

    Returns:
        The validated evaluation as a JSON-ready dict.

    Raises:
        JudgeError: If the reply is not valid JSON, fails the schema, or the
            identifiers do not match.
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        raw = json.loads(candidate)
        result = EvaluationResult.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise JudgeError(f"judge returned an invalid evaluation: {exc}") from exc
    if result.task != task_id:
        raise JudgeError(f"judge returned task {result.task!r}; expected {task_id!r}")
    if result.model != candidate_id:
        raise JudgeError(
            f"judge returned model {result.model!r}; expected candidate {candidate_id!r}"
        )
    return result.model_dump(mode="json")


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Write JSON to ``path`` atomically (temp file + fsync + os.replace).

    Args:
        path: Destination file.
        value: JSON-serializable mapping to write.

    Raises:
        JudgeError: If the file cannot be written.
    """
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    except OSError as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise JudgeError(f"could not write {path}: {exc}") from exc
