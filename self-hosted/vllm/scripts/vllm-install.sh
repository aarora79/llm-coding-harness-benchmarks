#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# vllm-install.sh — Install vLLM into an isolated virtualenv on a GPU node.
#
# Verified on the reference node:
#   - Instance:  g6e.12xlarge (4x NVIDIA L40S, 46 GB each; 48 vCPU; 372 GB RAM)
#   - AMI:       Deep Learning OSS Nvidia Driver AMI GPU PyTorch (Ubuntu 24.04)
#   - Driver:    595.71.05   CUDA 13.2   (pre-installed on the DLAMI)
#   - vLLM:      0.24.0       Python 3.12
#
# We build a dedicated venv (default ~/vllm-env) with `uv` rather than touching
# the AMI's /opt/pytorch env, so a bad install is one `rm -rf` away from clean.
#
# Usage:
#   ./vllm-install.sh                 # install into ~/vllm-env
#   VLLM_ENV=/mnt/vllm ./vllm-install.sh
# ---------------------------------------------------------------------------

VLLM_ENV="${VLLM_ENV:-$HOME/vllm-env}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'
info()   { echo -e "${BLUE}[info]${RESET}  $1"; }
ok()     { echo -e "${GREEN}[ok]${RESET}    $1"; }
fail()   { echo -e "${RED}[fail]${RESET}  $1"; exit 1; }
header() { echo -e "\n${BOLD}=== $1 ===${RESET}"; }

header "Step 1 — Check GPU + driver"
command -v nvidia-smi >/dev/null 2>&1 || fail "nvidia-smi not found. Use a GPU instance with NVIDIA drivers (e.g. the Deep Learning AMI)."
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv
GPU_COUNT=$(nvidia-smi --query-gpu=name --format=csv,noheader | wc -l | xargs)
ok "$GPU_COUNT GPU(s) detected"

header "Step 2 — Install build prerequisites (Python headers + gcc)"
# vLLM's Triton/inductor backend JIT-compiles a small CUDA helper (cuda_utils.c)
# at server startup with gcc, and that compile needs the Python dev headers
# (<Python.h>). The Deep Learning AMI ships CPython but NOT python3.12-dev, so
# without this the server crashes on the first profile_run with:
#   fatal error: Python.h: No such file or directory
# Install the headers + a compiler toolchain up front. (Verified fix on the
# Ubuntu 24.04 DLAMI, 2026-07.)
PY_MM="${PYTHON_VERSION}"
if ! ls "/usr/include/python${PY_MM}/Python.h" >/dev/null 2>&1; then
    info "Installing python${PY_MM}-dev + build-essential (needs sudo)..."
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "python${PY_MM}-dev" build-essential
fi
if ls "/usr/include/python${PY_MM}/Python.h" >/dev/null 2>&1; then
    ok "Python headers present (/usr/include/python${PY_MM}/Python.h)"
else
    fail "Python.h for ${PY_MM} still missing. Install python${PY_MM}-dev manually."
fi

header "Step 3 — Install uv (fast Python package manager)"
if ! command -v uv >/dev/null 2>&1; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
ok "uv $(uv --version | awk '{print $2}')"

header "Step 4 — Create the vLLM virtualenv ($VLLM_ENV)"
if [[ -x "$VLLM_ENV/bin/python" ]]; then
    ok "venv already exists at $VLLM_ENV"
else
    uv venv "$VLLM_ENV" --python "$PYTHON_VERSION"
    ok "created $VLLM_ENV (Python $PYTHON_VERSION)"
fi

header "Step 5 — Install vLLM"
if "$VLLM_ENV/bin/python" -c "import vllm" 2>/dev/null; then
    ok "vLLM already installed: $("$VLLM_ENV/bin/python" -c 'import vllm; print(vllm.__version__)')"
else
    info "Installing vLLM (pulls torch + CUDA wheels — several minutes, multi-GB)..."
    VIRTUAL_ENV="$VLLM_ENV" uv pip install --python "$VLLM_ENV/bin/python" vllm
    ok "vLLM installed: $("$VLLM_ENV/bin/python" -c 'import vllm; print(vllm.__version__)')"
fi

header "Step 6 — Install GPU monitoring tooling"
# nvidia-smi ships with the driver (already present on the DLAMI). Add two nicer
# live monitors so you can watch VRAM + utilization while the benchmark runs:
#   nvtop   — htop-style TUI for GPUs (apt)
#   gpustat — one-line-per-GPU snapshot, scriptable (pip, into the vLLM venv)
if command -v nvidia-smi >/dev/null 2>&1; then
    ok "nvidia-smi present: $(nvidia-smi --version | head -1 2>/dev/null || echo installed)"
fi
if ! command -v nvtop >/dev/null 2>&1; then
    info "Installing nvtop (needs sudo)..."
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nvtop >/dev/null 2>&1 || info "(nvtop unavailable via apt — skipping)"
fi
command -v nvtop >/dev/null 2>&1 && ok "nvtop $(nvtop --version 2>/dev/null | awk '{print $NF}')"
if ! "$VLLM_ENV/bin/python" -c "import gpustat" 2>/dev/null; then
    info "Installing gpustat into the vLLM venv..."
    VIRTUAL_ENV="$VLLM_ENV" uv pip install --python "$VLLM_ENV/bin/python" gpustat >/dev/null 2>&1 || true
fi
"$VLLM_ENV/bin/python" -c "import gpustat" 2>/dev/null && ok "gpustat ready ($VLLM_ENV/bin/gpustat)"

header "Step 7 — Verify vLLM sees the GPUs"
"$VLLM_ENV/bin/python" - <<'PY'
import torch
n = torch.cuda.device_count()
print(f"  torch {torch.__version__}  |  CUDA {torch.version.cuda}  |  {n} GPU(s) visible")
for i in range(n):
    p = torch.cuda.get_device_properties(i)
    print(f"    GPU {i}: {p.name}  {p.total_memory // (1024**3)} GB")
assert n >= 1, "no CUDA devices visible to torch"
PY
ok "vLLM environment ready"

echo ""
echo "Next: serve a model —"
echo "  ./vllm-serve.sh                       # Qwen3-Coder-30B-A3B on all GPUs"
echo "  MODEL=Qwen/Qwen3-32B ./vllm-serve.sh  # a dense 32B alternative"
