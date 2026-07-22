#!/usr/bin/env python3
"""Run the SWE benchmark headless against any Anthropic-compatible endpoint.

Sends a single large prompt containing all codebase context + task description,
and asks the model to produce all 4 artifacts in one response. No tool calls,
no interactivity, no skill detection needed.

Usage:
    python3 run-swe-headless.py --endpoint http://127.0.0.1:4000 --model deepseek-v3.2 --problem ssrf-hardening-outbound-url-validation
    python3 run-swe-headless.py --endpoint http://127.0.0.1:8000 --model kimi-k2.7-code --problem remove-faiss

Requires: requests
"""

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
BENCH_DIR = REPO_ROOT / "benchmarks" / "swe-benchmark-data" / "mcp-gateway-registry"
REPO_DIR = BENCH_DIR / "repo"

DEFAULT_MAX_TOKENS = 16000
REQUEST_TIMEOUT_SECONDS = 600
EXPECTED_ARTIFACT_COUNT = 4

PROBLEMS = {
    "ssrf-hardening-outbound-url-validation": {
        "description": "SSRF hardening: validate outbound URLs on agent card fetch and health check endpoints. The registry fetches user-supplied URLs with no SSRF guard on the agent-card and health-check paths, even though a guard (_is_safe_url) already exists for SKILL.md fetches.",
        "answers": "1. Security audit finding — the registry fetches user-supplied URLs (agent card, health checks) with no SSRF guard. An existing guard exists for skill fetches but isn't reused elsewhere. 2. Both — operators running the gateway and downstream teams registering MCP servers. 3. Python/FastAPI, runs on ECS, no deadline. Must be backwards-compatible. 4. Medium — promote existing _is_safe_url() into a shared utility, apply to agent-card fetch and server health-check paths, add config for allowlist.",
        "key_files": [
            "registry/services/skill_service.py:69-190",
            "registry/utils/agent_validator.py:196-230",
            "registry/health/service.py:620-710",
            "registry/core/config.py:280-300",
        ],
    },
    "migrate-ecs-env-vars-to-secrets-manager": {
        "description": "Migrate sensitive ECS environment variables to AWS Secrets Manager. Identify which env vars in ECS task definitions contain secrets, create Secrets Manager resources in Terraform, update ECS task definitions to pull from Secrets Manager via the secrets block.",
        "answers": "1. Plaintext secrets are stored as ECS environment variables in Terraform — security risk. Moving to Secrets Manager adds encryption, rotation, and audit trail. 2. Operators deploying the registry on AWS ECS + Terraform. 3. Terraform/ECS setup in this repo. No Helm/EKS needed. No deadline. 4. All sensitive env vars across all ECS services. 5. AWS Secrets Manager only. 6. Keep plaintext env-var as fallback during migration. 7. Medium — Terraform across all ECS services + app config loader changes.",
        "key_files": [
            "terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf:1-100",
            "terraform/aws-ecs/modules/mcp-gateway/iam.tf:1-50",
            "terraform/aws-ecs/modules/mcp-gateway/secrets.tf:1-50",
            "registry/core/config.py:1-60",
        ],
    },
    "replace-keycloak-db-password-with-rds-iam": {
        "description": "Replace the Keycloak database password with RDS IAM authentication. Switch Keycloak's RDS connection from static username/password to RDS IAM auth tokens.",
        "answers": "1. Switch Keycloak's RDS connection from static username/password to RDS IAM authentication. Remove static password from config entirely. 2. Operators deploying on AWS ECS + RDS (Terraform). No Helm/EKS needed. 3. Must remain backwards-compatible with password auth as fallback (feature flag). No Keycloak version change. No deadline. 4. Medium.",
        "key_files": [
            "terraform/aws-ecs/keycloak-database.tf:1-50",
            "terraform/aws-ecs/keycloak-ecs.tf:1-120",
            "terraform/aws-ecs/variables.tf:90-110",
            "docker/keycloak/Dockerfile:1-30",
        ],
    },
    "remove-faiss": {
        "description": "Remove FAISS from the codebase. FAISS is being replaced by DocumentDB's native hybrid search. Remove all FAISS imports, dependencies, configuration, code paths, Docker build steps, and tests.",
        "answers": "1. FAISS replaced by DocumentDB native hybrid search. FAISS is unnecessary dependency complicating deployment. 2. Operators (no more FAISS native lib headaches) and developers (simpler codebase). End-users unaffected. 3. Python/FastAPI. Must not break existing search. No deadline. 4. Medium — remove FAISS code paths, dependencies, Docker build steps, tests.",
        "key_files": [
            "registry/search/service.py:1-50",
            "registry/core/config.py:990-1010",
            "registry/core/schemas.py:500-520",
            "pyproject.toml:20-30",
        ],
    },
    "remove-efs-from-terraform-aws-ecs": {
        "description": "Remove EFS from terraform/aws-ecs/. EFS is obsolete — the application uses S3/DocumentDB for persistent storage. Delete EFS file system, mount targets, security groups, volume mounts from ECS task definitions.",
        "answers": "1. EFS no longer needed — application uses S3/DocumentDB for all persistent storage. EFS adds cost and complexity. 2. Operators deploying via Terraform. 3. Terraform/AWS ECS. Must ensure no service depends on EFS mount. No deadline. 4. Medium — remove EFS resources from Terraform, remove volume/mount config from ECS task definitions.",
        "key_files": [
            "terraform/aws-ecs/modules/mcp-gateway/storage.tf:1-70",
            "terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf:215-230",
            "terraform/aws-ecs/modules/mcp-gateway/variables.tf:255-280",
            "terraform/aws-ecs/modules/mcp-gateway/outputs.tf:45-80",
        ],
    },
}


def _read_file_range(
    relative_path: str, line_range: tuple[int, int] | None = None
) -> str:
    """Read a file or a range of lines from the repo.

    Args:
        relative_path: Path relative to the benchmark repo directory.
        line_range: Optional (start, end) 1-indexed inclusive line range.

    Returns:
        The requested file content with a header, or a not-found marker.
    """
    full_path = REPO_DIR / relative_path
    if not full_path.exists():
        return f"[FILE NOT FOUND: {relative_path}]"

    lines = full_path.read_text(encoding="utf-8").splitlines()

    if line_range:
        start, end = line_range
        lines = lines[max(0, start - 1) : end]
        header = f"--- {relative_path} (lines {start}-{end}) ---"
    else:
        header = f"--- {relative_path} ---"

    return f"{header}\n" + "\n".join(lines)


def _build_context(problem_key: str) -> str:
    """Build the codebase context for a problem.

    Args:
        problem_key: Slug identifying the problem in PROBLEMS.

    Returns:
        Concatenated source snippets for the problem's key files.
    """
    problem = PROBLEMS[problem_key]
    context_parts = []

    for file_spec in problem["key_files"]:
        if ":" in file_spec:
            path, range_str = file_spec.rsplit(":", 1)
            start, end = map(int, range_str.split("-"))
            context_parts.append(_read_file_range(path, (start, end)))
        else:
            context_parts.append(_read_file_range(file_spec))

    return "\n\n".join(context_parts)


def _build_structure_text() -> str:
    """Build a top-2-levels directory listing of the repo.

    Returns:
        Newline-joined directory paths (up to the first 50 entries).
    """
    structure = []
    for p in sorted(REPO_DIR.rglob("*")):
        if ".git" in p.parts or "__pycache__" in p.parts or "node_modules" in p.parts:
            continue
        if p.is_dir() and len(p.relative_to(REPO_DIR).parts) <= 2:
            structure.append(str(p.relative_to(REPO_DIR)) + "/")
    return "\n".join(structure[:50])


def _build_prompt(problem_key: str) -> str:
    """Build the full prompt for a problem.

    Args:
        problem_key: Slug identifying the problem in PROBLEMS.

    Returns:
        The complete prompt string to send to the model.
    """
    problem = PROBLEMS[problem_key]
    context = _build_context(problem_key)
    structure_text = _build_structure_text()

    return f"""You are a senior software engineer. You will produce 4 markdown design documents for the task below.

DO NOT ask any questions. DO NOT request clarification. All information you need is provided. If anything is ambiguous, make your best judgment and note assumptions.

## Repository: mcp-gateway-registry
Tag: 1.24.4
Tech stack: Python (FastAPI), Terraform (AWS ECS/RDS), TypeScript (React frontend)

## Project Structure (top 2 levels)
{structure_text}

## Task: {problem_key}
{problem["description"]}

## Clarifying Answers (from the user)
{problem["answers"]}

## Key Source Files
{context}

---

## Instructions

Produce exactly 4 artifacts below. Each artifact must be a complete, standalone markdown document. Separate them with the exact headers shown.

=== github-issue.md ===
Write a formal GitHub issue specification with:
- Problem statement
- Acceptance criteria (checkboxes)
- Out-of-scope items
- Implementation notes

=== lld.md ===
Write a low-level design document with:
- Overview and goals
- Codebase analysis (reference actual files and line numbers from the context above)
- Architecture and data models
- File-by-file implementation plan with code snippets
- Configuration parameters
- Alternatives considered
- Rollout plan

=== review.md ===
Write a multi-persona expert review with 5 reviewers (Frontend, Backend, SRE, Security, SMTS). Each reviewer gives:
- Verdict (APPROVED / APPROVED WITH CHANGES / NEEDS REVISION)
- Key findings (2-4 bullet points)
- Specific recommendations

=== testing.md ===
Write a testing plan covering:
- Unit tests (with concrete test cases)
- Integration tests
- Functional/API tests (curl examples)
- Backwards compatibility tests
- Deployment surface tests (Terraform, Docker, Helm)
- E2E tests

Begin producing the artifacts now. Do not include any preamble or explanation outside the artifacts."""


def _parse_artifacts(response_text: str) -> dict[str, str]:
    """Parse the 4 artifacts from the model's response.

    Args:
        response_text: Raw text returned by the model.

    Returns:
        Mapping of artifact filename to its content.
    """
    artifacts: dict[str, str] = {}
    pattern = r"=== (github-issue\.md|lld\.md|review\.md|testing\.md) ==="
    parts = re.split(pattern, response_text)

    # parts = [preamble, filename1, content1, filename2, content2, ...]
    for i in range(1, len(parts) - 1, 2):
        filename = parts[i]
        content = parts[i + 1].strip()
        artifacts[filename] = content

    return artifacts


def _send_request(
    endpoint: str,
    model: str,
    prompt: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Send the prompt to the API endpoint.

    Args:
        endpoint: Base URL of the Anthropic-compatible API.
        model: Model name to request.
        prompt: The prompt text to send.
        max_tokens: Maximum output tokens to generate.

    Returns:
        Dictionary with response text, token counts, latency, and model name.
    """
    url = f"{endpoint}/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": "local",
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }

    start = time.time()
    try:
        response = requests.post(
            url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.RequestException:
        logger.exception(
            "Request to %s failed. Check that the server is running and reachable.", url
        )
        sys.exit(1)
    elapsed = time.time() - start

    if response.status_code != 200:
        logger.error(
            "Error %s from %s: %s", response.status_code, url, response.text[:500]
        )
        sys.exit(1)

    data = response.json()
    usage = data.get("usage", {})

    return {
        "text": data["content"][0]["text"],
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "latency_seconds": round(elapsed, 1),
        "model": data.get("model", model),
    }


def _log_result(result: dict[str, Any]) -> None:
    """Log token counts, latency, and generation rate for a response.

    Args:
        result: The response dictionary returned by _send_request.
    """
    logger.info("Response received")
    logger.info("  Input tokens: %s", f"{result['input_tokens']:,}")
    logger.info("  Output tokens: %s", f"{result['output_tokens']:,}")
    logger.info("  Latency: %ss", result["latency_seconds"])
    logger.info(
        "  Generation: %.1f tok/s", result["output_tokens"] / result["latency_seconds"]
    )


def _save_outputs(
    result: dict[str, Any],
    artifacts: dict[str, str],
    args: argparse.Namespace,
) -> None:
    """Write parsed artifacts and run metrics to the output directory.

    Args:
        result: The response dictionary returned by _send_request.
        artifacts: Mapping of artifact filename to content.
        args: Parsed command-line arguments.
    """
    output_dir = BENCH_DIR / args.problem / args.model
    output_dir.mkdir(parents=True, exist_ok=True)

    for filename, content in artifacts.items():
        (output_dir / filename).write_text(content + "\n", encoding="utf-8")
        logger.info("  Saved: %s", output_dir / filename)

    metrics = {
        "model": args.model,
        "problem": args.problem,
        "endpoint": args.endpoint,
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "latency_seconds": result["latency_seconds"],
        "generation_tokens_per_sec": round(
            result["output_tokens"] / result["latency_seconds"], 1
        ),
        "artifacts_produced": len(artifacts),
        "max_tokens": args.max_tokens,
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    logger.info("  Saved: %s", output_dir / "metrics.json")


def _run(args: argparse.Namespace) -> None:
    """Build the prompt, run the request, and persist the outputs.

    Args:
        args: Parsed command-line arguments.
    """
    logger.info("Problem: %s", args.problem)
    logger.info("Model: %s", args.model)
    logger.info("Endpoint: %s", args.endpoint)
    logger.info("Max tokens: %s", args.max_tokens)

    prompt = _build_prompt(args.problem)
    logger.info("Prompt length: %s chars (~%s tokens)", len(prompt), len(prompt) // 4)

    if args.dry_run:
        print("\n--- PROMPT ---")
        print(prompt)
        return

    logger.info("Sending request...")
    result = _send_request(args.endpoint, args.model, prompt, args.max_tokens)
    _log_result(result)

    artifacts = _parse_artifacts(result["text"])
    logger.info("  Artifacts parsed: %s", list(artifacts.keys()))

    if len(artifacts) < EXPECTED_ARTIFACT_COUNT:
        logger.warning(
            "Only %s/%s artifacts found. Response may be truncated.",
            len(artifacts),
            EXPECTED_ARTIFACT_COUNT,
        )
        logger.warning("Try increasing --max-tokens (currently %s)", args.max_tokens)

    _save_outputs(result, artifacts, args)

    if len(artifacts) == EXPECTED_ARTIFACT_COUNT:
        logger.info("Done - all %s artifacts produced.", EXPECTED_ARTIFACT_COUNT)
    else:
        logger.warning(
            "Partial - %s/%s artifacts. Increase --max-tokens or check the response.",
            len(artifacts),
            EXPECTED_ARTIFACT_COUNT,
        )


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        The parsed argument namespace.
    """
    parser = argparse.ArgumentParser(description="Run SWE benchmark headless")
    parser.add_argument(
        "--endpoint", required=True, help="API endpoint (e.g. http://127.0.0.1:4000)"
    )
    parser.add_argument("--model", required=True, help="Model name")
    parser.add_argument(
        "--problem", required=True, choices=list(PROBLEMS.keys()), help="Problem slug"
    )
    parser.add_argument(
        "--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="Max output tokens"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print prompt without sending"
    )
    return parser.parse_args()


def main() -> None:
    """Parse arguments and delegate to the run orchestrator."""
    args = _parse_args()
    _run(args)


if __name__ == "__main__":
    main()
