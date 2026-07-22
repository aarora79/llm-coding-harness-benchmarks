#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# run-swe-claude-p.sh — Run /swe benchmark headless via claude -p
#
# Uses Claude Code's headless mode with the non-interactive /swe skill.
# Works with Opus on Bedrock (default) or any model via LiteLLM proxy.
#
# Usage:
#   ./run-swe-claude-p.sh <problem> [model]
#   ./run-swe-claude-p.sh ssrf-hardening-outbound-url-validation
#   ./run-swe-claude-p.sh ssrf-hardening-outbound-url-validation deepseek-v3.2
#
# For 3rd-party models via LiteLLM proxy:
#   ANTHROPIC_BASE_URL=http://127.0.0.1:4000 \
#   ANTHROPIC_API_KEY=local \
#   ./run-swe-claude-p.sh ssrf-hardening-outbound-url-validation deepseek-v3.2
# ---------------------------------------------------------------------------

PROBLEM="${1:?Usage: $0 <problem-slug> [model-name]}"
MODEL="${2:-claude-opus-4-8}"
MAX_TURNS="${MAX_TURNS:-40}"

# Pre-written answers for each problem
get_answers() {
  case "$1" in
    ssrf-hardening-outbound-url-validation)
      echo "1. Security audit finding — the registry fetches user-supplied URLs with no SSRF guard. 2. Both — operators and downstream teams. 3. Python/FastAPI, ECS, no deadline, backwards-compatible. 4. Medium — promote _is_safe_url() to shared utility."
      ;;
    migrate-ecs-env-vars-to-secrets-manager)
      echo "1. Plaintext secrets are stored as ECS environment variables in Terraform — security risk. Moving to Secrets Manager adds encryption, rotation, and audit trail. 2. Operators deploying the registry on AWS ECS + Terraform. 3. Terraform/ECS setup in this repo. No Helm/EKS needed. No deadline. 4. All sensitive env vars across all ECS services. 5. AWS Secrets Manager only. 6. Keep plaintext env-var as fallback during migration. 7. Medium — Terraform across all ECS services + app config loader changes."
      ;;
    replace-keycloak-db-password-with-rds-iam)
      echo "1. Switch Keycloak RDS connection from static password to RDS IAM auth. Remove static password from config entirely. 2. Operators deploying on AWS ECS + RDS (Terraform). No Helm/EKS needed. 3. Must remain backwards-compatible with password auth as fallback (feature flag). No Keycloak version change. No deadline. 4. Medium."
      ;;
    remove-faiss)
      echo "1. FAISS replaced by DocumentDB native hybrid search. Unnecessary dependency complicating deployment. 2. Operators and developers. End-users unaffected. 3. Python/FastAPI. Must not break existing search. No deadline. 4. Medium — remove FAISS code paths, dependencies, Docker build steps, tests."
      ;;
    remove-efs-from-terraform-aws-ecs)
      echo "1. EFS no longer needed — application uses S3/DocumentDB for all persistent storage. EFS adds cost and complexity. 2. Operators deploying via Terraform. 3. Terraform/AWS ECS. Must ensure no service depends on EFS mount. No deadline. 4. Medium — remove EFS resources from Terraform, remove volume/mount config from ECS task definitions."
      ;;
    *)
      echo ""
      ;;
  esac
}

ANSWERS=$(get_answers "$PROBLEM")
if [[ -z "$ANSWERS" ]]; then
  echo "[error] Unknown problem: $PROBLEM" >&2
  echo "Available: ssrf-hardening-outbound-url-validation migrate-ecs-env-vars-to-secrets-manager replace-keycloak-db-password-with-rds-iam remove-faiss remove-efs-from-terraform-aws-ecs" >&2
  exit 1
fi

PROMPT="/swe repo: benchmarks/swe-benchmark-data/mcp-gateway-registry/repo problem: ${PROBLEM} model: ${MODEL} tag: 1.24.4 answers: \"${ANSWERS}\""

echo "Problem: ${PROBLEM}"
echo "Model: ${MODEL}"
echo "Max turns: ${MAX_TURNS}"
echo ""

# Build claude args
CLAUDE_ARGS=(
  -p "$PROMPT"
  --dangerously-skip-permissions
  --output-format json
  --max-turns "$MAX_TURNS"
)

# If using a non-default model via proxy, add extra settings
if [[ -n "${ANTHROPIC_BASE_URL:-}" ]]; then
  echo "Endpoint: ${ANTHROPIC_BASE_URL}"
  CLAUDE_ARGS+=(
    --model "$MODEL"
    --settings '{"apiKeyHelper":"echo sk-local-vllm"}'
    --setting-sources local,project
  )
  export CLAUDE_CODE_USE_BEDROCK=0
  export CLAUDE_CODE_MAX_OUTPUT_TOKENS=16000
  export CLAUDE_CODE_SUBAGENT_MODEL="$MODEL"
  export DISABLE_NON_ESSENTIAL_MODEL_CALLS=1
fi

echo "Starting..."
echo ""

exec claude "${CLAUDE_ARGS[@]}"
