#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# select-best-lld.sh — Copy the best LLD as spec.md for implementation benchmarks
#
# Usage:
#   ./select-best-lld.sh <repo-name> <problem-name> <source-model>
#
# Example:
#   ./select-best-lld.sh mcp-gateway-registry ssrf-hardening-outbound-url-validation claude-opus-4-8
#
# This copies the LLD from the source model's /swe run into implementations/spec.md
# ---------------------------------------------------------------------------

REPO="${1:?Usage: $0 <repo-name> <problem-name> <source-model>}"
PROBLEM="${2:?Usage: $0 <repo-name> <problem-name> <source-model>}"
SOURCE_MODEL="${3:?Usage: $0 <repo-name> <problem-name> <source-model>}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BENCH_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/swe-benchmark-data"

SOURCE_LLD="${BENCH_DIR}/${REPO}/${PROBLEM}/${SOURCE_MODEL}/lld.md"
IMPL_DIR="${BENCH_DIR}/${REPO}/${PROBLEM}/implementations"
SPEC_FILE="${IMPL_DIR}/spec.md"

if [[ ! -f "$SOURCE_LLD" ]]; then
  echo "[error] Source LLD not found: $SOURCE_LLD" >&2
  echo "Available models for this problem:" >&2
  ls -d "${BENCH_DIR}/${REPO}/${PROBLEM}"/*/ 2>/dev/null | xargs -I{} basename {} | grep -v implementations >&2
  exit 1
fi

mkdir -p "$IMPL_DIR"

if [[ -f "$SPEC_FILE" ]]; then
  echo "[warn] spec.md already exists, overwriting"
fi

cp "$SOURCE_LLD" "$SPEC_FILE"
echo "[done] Copied LLD from ${SOURCE_MODEL} → ${SPEC_FILE}"
echo "       $(wc -l < "$SPEC_FILE") lines"
