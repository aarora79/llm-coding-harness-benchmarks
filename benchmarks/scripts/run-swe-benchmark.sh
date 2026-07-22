#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# run-swe-benchmark.sh - Run /swe benchmark for all 5 tasks against a model.
#
# Uses Claude Code headless mode (claude -p) with the non-interactive /swe skill.
# All answers are pre-populated so no human input is needed.
#
# Prerequisites:
#   - SSH tunnel to vLLM endpoint on port 8000
#   - Model served and responding at http://127.0.0.1:8000/v1/messages
#
# Usage:
#   ./run-swe-benchmark.sh <model-name>
#
# Examples:
#   ./run-swe-benchmark.sh kimi-k2.7-code
#   ./run-swe-benchmark.sh glm-5.2
#   MODEL_HOST=remote-server ./run-swe-benchmark.sh qwen3.6-35b
#
# Environment variables:
#   HOST              vLLM host (default: 127.0.0.1)
#   PORT              vLLM port (default: 8000)
#   MAX_OUTPUT_TOKENS max output tokens (default: 16000)
# ---------------------------------------------------------------------------

MODEL="${1:?Usage: $0 <model-name>}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
MAX_OUTPUT_TOKENS="${MAX_OUTPUT_TOKENS:-16000}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

REPO_PATH="benchmarks/swe-benchmark-data/mcp-gateway-registry/repo"
TAG="1.24.4"

# Pre-written answers for each problem (from docs-local/SWE_BENCHMARK_PROMPTS.md)
declare -A ANSWERS
ANSWERS[ssrf-hardening-outbound-url-validation]="1. Security audit finding — the registry fetches user-supplied URLs (agent card, health checks) with no SSRF guard. An existing guard exists for skill fetches but isn't reused elsewhere. 2. Both — operators running the gateway and downstream teams registering MCP servers. 3. Python/FastAPI, runs on ECS, no deadline. Must be backwards-compatible. 4. Medium — promote existing _is_safe_url() into a shared utility, apply to agent-card fetch and server health-check paths, add config for allowlist."

ANSWERS[migrate-ecs-env-vars-to-secrets-manager]="1. Plaintext secrets are stored as ECS environment variables in Terraform — security risk. Moving to Secrets Manager adds encryption, rotation, and audit trail. 2. Operators deploying the registry on AWS ECS + Terraform. 3. Terraform/ECS setup in this repo. No Helm/EKS needed. No deadline. 4. All sensitive env vars across all ECS services. 5. AWS Secrets Manager only — rotation support and cross-account access. 6. Keep plaintext env-var as fallback during migration. 7. Medium — Terraform across all ECS services + app config loader changes."

ANSWERS[replace-keycloak-db-password-with-rds-iam]="1. Switch Keycloak's RDS connection from static username/password to RDS IAM authentication. Remove static password from config entirely. 2. Operators deploying on AWS ECS + RDS (Terraform). No Helm/EKS needed. 3. Must remain backwards-compatible with password auth as fallback (feature flag). No Keycloak version change. No deadline. 4. Medium."

ANSWERS[remove-faiss]="1. FAISS replaced by DocumentDB native hybrid search. FAISS is unnecessary dependency complicating deployment. 2. Operators (no more FAISS native lib headaches) and developers (simpler codebase). End-users unaffected. 3. Python/FastAPI. Must not break existing search. No deadline. 4. Medium — remove FAISS code paths, dependencies, Docker build steps, tests."

ANSWERS[remove-efs-from-terraform-aws-ecs]="1. EFS no longer needed — application uses S3/DocumentDB for all persistent storage. EFS adds cost and complexity. 2. Operators deploying via Terraform. 3. Terraform/AWS ECS. Must ensure no service depends on EFS mount. No deadline. 4. Medium — remove EFS resources from Terraform, remove volume/mount config from ECS task definitions."

PROBLEMS=(
  ssrf-hardening-outbound-url-validation
  migrate-ecs-env-vars-to-secrets-manager
  replace-keycloak-db-password-with-rds-iam
  remove-faiss
  remove-efs-from-terraform-aws-ecs
)

echo "============================================"
echo "SWE Benchmark: ${MODEL}"
echo "Endpoint: http://${HOST}:${PORT}"
echo "Tag: ${TAG}"
echo "Problems: ${#PROBLEMS[@]}"
echo "============================================"
echo ""

# Verify endpoint is reachable
if ! curl -sf --max-time 5 "http://${HOST}:${PORT}/v1/models" >/dev/null 2>&1; then
  echo "[error] vLLM not reachable at http://${HOST}:${PORT}. Is the tunnel up?"
  exit 1
fi

PASSED=0
FAILED=0

for problem in "${PROBLEMS[@]}"; do
  echo "--------------------------------------------"
  echo "Running: ${problem}"
  echo "--------------------------------------------"

  OUTPUT_DIR="${REPO_ROOT}/benchmarks/swe-benchmark-data/mcp-gateway-registry/${problem}/${MODEL}"

  # Skip if all 4 artifacts already exist
  if [[ -f "${OUTPUT_DIR}/github-issue.md" && -f "${OUTPUT_DIR}/lld.md" && -f "${OUTPUT_DIR}/review.md" && -f "${OUTPUT_DIR}/testing.md" ]]; then
    echo "  [skip] All artifacts already exist. Delete folder to re-run."
    PASSED=$((PASSED + 1))
    continue
  fi

  PROMPT="/swe repo: ${REPO_PATH} problem: ${problem} model: ${MODEL} tag: ${TAG} answers: \"${ANSWERS[$problem]}\""

  START_TIME=$(date +%s)

  ANTHROPIC_BASE_URL="http://${HOST}:${PORT}" \
  ANTHROPIC_API_KEY="local" \
  CLAUDE_CODE_USE_BEDROCK="0" \
  CLAUDE_CODE_MAX_OUTPUT_TOKENS="${MAX_OUTPUT_TOKENS}" \
  CLAUDE_CODE_SUBAGENT_MODEL="${MODEL}" \
  DISABLE_NON_ESSENTIAL_MODEL_CALLS="1" \
  claude -p "${PROMPT}" \
    --model "${MODEL}" \
    --settings "${REPO_ROOT}/self-hosted/vllm/config/claude-code.json" \
    --setting-sources local,project \
    --permission-mode bypassPermissions \
    --output-format json \
    > "/tmp/swe-${MODEL}-${problem}.json" 2>&1 || true

  END_TIME=$(date +%s)
  ELAPSED=$((END_TIME - START_TIME))

  # Check if artifacts were created
  if [[ -f "${OUTPUT_DIR}/github-issue.md" && -f "${OUTPUT_DIR}/lld.md" ]]; then
    echo "  [done] ${ELAPSED}s - artifacts at ${OUTPUT_DIR}"
    PASSED=$((PASSED + 1))
  else
    echo "  [FAIL] ${ELAPSED}s - artifacts missing. Check /tmp/swe-${MODEL}-${problem}.json"
    FAILED=$((FAILED + 1))
  fi
  echo ""
done

echo "============================================"
echo "Results: ${PASSED} passed, ${FAILED} failed"
echo "============================================"
