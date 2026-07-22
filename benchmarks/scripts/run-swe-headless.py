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
import os
import re
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).parent.parent.parent
BENCH_DIR = REPO_ROOT / "benchmarks" / "swe-benchmark-data" / "mcp-gateway-registry"
REPO_DIR = BENCH_DIR / "repo"

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


def read_file_range(relative_path, line_range=None):
    """Read a file or a range of lines from the repo."""
    full_path = REPO_DIR / relative_path
    if not full_path.exists():
        return f"[FILE NOT FOUND: {relative_path}]"

    lines = full_path.read_text().splitlines()

    if line_range:
        start, end = line_range
        lines = lines[max(0, start - 1):end]
        header = f"--- {relative_path} (lines {start}-{end}) ---"
    else:
        header = f"--- {relative_path} ---"

    return f"{header}\n" + "\n".join(lines)


def build_context(problem_key):
    """Build the codebase context for a problem."""
    problem = PROBLEMS[problem_key]
    context_parts = []

    for file_spec in problem["key_files"]:
        if ":" in file_spec:
            path, range_str = file_spec.rsplit(":", 1)
            start, end = map(int, range_str.split("-"))
            context_parts.append(read_file_range(path, (start, end)))
        else:
            context_parts.append(read_file_range(file_spec))

    return "\n\n".join(context_parts)


def build_prompt(problem_key):
    """Build the full prompt for a problem."""
    problem = PROBLEMS[problem_key]
    context = build_context(problem_key)

    # Get project structure
    structure = []
    for p in sorted(REPO_DIR.rglob("*")):
        if ".git" in p.parts or "__pycache__" in p.parts or "node_modules" in p.parts:
            continue
        if p.is_dir() and len(p.relative_to(REPO_DIR).parts) <= 2:
            structure.append(str(p.relative_to(REPO_DIR)) + "/")
    structure_text = "\n".join(structure[:50])

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


def parse_artifacts(response_text):
    """Parse the 4 artifacts from the model's response."""
    artifacts = {}
    pattern = r"=== (github-issue\.md|lld\.md|review\.md|testing\.md) ==="
    parts = re.split(pattern, response_text)

    # parts = [preamble, filename1, content1, filename2, content2, ...]
    for i in range(1, len(parts) - 1, 2):
        filename = parts[i]
        content = parts[i + 1].strip()
        artifacts[filename] = content

    return artifacts


def send_request(endpoint, model, prompt, max_tokens=16000):
    """Send the prompt to the API endpoint."""
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
    response = requests.post(url, headers=headers, json=payload, timeout=600)
    elapsed = time.time() - start

    if response.status_code != 200:
        print(f"Error {response.status_code}: {response.text[:500]}", file=sys.stderr)
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


def main():
    parser = argparse.ArgumentParser(description="Run SWE benchmark headless")
    parser.add_argument("--endpoint", required=True, help="API endpoint (e.g. http://127.0.0.1:4000)")
    parser.add_argument("--model", required=True, help="Model name")
    parser.add_argument("--problem", required=True, choices=list(PROBLEMS.keys()), help="Problem slug")
    parser.add_argument("--max-tokens", type=int, default=16000, help="Max output tokens")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt without sending")
    args = parser.parse_args()

    print(f"Problem: {args.problem}")
    print(f"Model: {args.model}")
    print(f"Endpoint: {args.endpoint}")
    print(f"Max tokens: {args.max_tokens}")

    prompt = build_prompt(args.problem)
    print(f"Prompt length: {len(prompt)} chars (~{len(prompt)//4} tokens)")

    if args.dry_run:
        print("\n--- PROMPT ---")
        print(prompt)
        return

    print("\nSending request...")
    result = send_request(args.endpoint, args.model, prompt, args.max_tokens)

    print(f"\nResponse received:")
    print(f"  Input tokens: {result['input_tokens']:,}")
    print(f"  Output tokens: {result['output_tokens']:,}")
    print(f"  Latency: {result['latency_seconds']}s")
    print(f"  Generation: {result['output_tokens'] / result['latency_seconds']:.1f} tok/s")

    # Parse artifacts
    artifacts = parse_artifacts(result["text"])
    print(f"  Artifacts parsed: {list(artifacts.keys())}")

    if len(artifacts) < 4:
        print(f"\n  WARNING: Only {len(artifacts)}/4 artifacts found. Response may be truncated.")
        print(f"  Try increasing --max-tokens (currently {args.max_tokens})")

    # Save artifacts
    output_dir = BENCH_DIR / args.problem / args.model
    output_dir.mkdir(parents=True, exist_ok=True)

    for filename, content in artifacts.items():
        (output_dir / filename).write_text(content + "\n")
        print(f"  Saved: {output_dir / filename}")

    # Save metrics
    metrics = {
        "model": args.model,
        "problem": args.problem,
        "endpoint": args.endpoint,
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "latency_seconds": result["latency_seconds"],
        "generation_tokens_per_sec": round(result["output_tokens"] / result["latency_seconds"], 1),
        "artifacts_produced": len(artifacts),
        "max_tokens": args.max_tokens,
    }
    (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    print(f"  Saved: {output_dir / 'metrics.json'}")

    if len(artifacts) == 4:
        print("\nDone — all 4 artifacts produced.")
    else:
        print(f"\nPartial — {len(artifacts)}/4 artifacts. Increase --max-tokens or check the response.")


if __name__ == "__main__":
    main()
