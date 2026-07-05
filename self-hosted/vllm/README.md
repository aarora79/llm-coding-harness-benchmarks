# Self-Hosted Coding Models on EC2 with vLLM

[![License: MIT-0](https://img.shields.io/badge/License-MIT--0-yellow.svg)](../../LICENSE) [![vLLM](https://img.shields.io/badge/vLLM-0.24.0-blue)](https://docs.vllm.ai) [![Node: g6e.12xlarge](https://img.shields.io/badge/EC2-g6e.12xlarge%20(4%C3%97L40S)-orange)](https://aws.amazon.com/ec2/instance-types/g6e/)

> **This is sample code intended for demonstration and learning purposes only.** It is not meant for production use. Review and harden all scripts, configurations, and IAM permissions before using in any production or sensitive environment.

Serve open-weight coding models (Qwen3-Coder-30B, Qwen3-32B, and larger) on a single multi-GPU EC2 node with [vLLM](https://docs.vllm.ai), sharded across all GPUs with tensor parallelism. This is the serving layer the [hosting-strategy experiment](../../README.md) is built on: vLLM sustains high throughput under concurrent load, which is the regime where a fixed-cost GPU node beats per-token API pricing.

The sibling [../ollama/](../ollama/) path is the *convenience* path — one model, single-stream, minimal setup. **This vLLM path is the *throughput* path** — many concurrent requests, tensor parallelism, the batched tokens/sec the cost model needs.

> **TL;DR — don't copy-paste, run the skill.** The install is heavy (driver-level checks, apt packages, a multi-GB vLLM wheel, a ~57 GB model download, and two environment fixes that are specific to the Deep Learning AMI). Rather than paste the steps below by hand, run the repo skill and it drives the whole thing:
> 
> ```
> /vllm-setup
> ```
>
> The rest of this README documents exactly *what* that skill installs and *why*, so you can audit it or reproduce it manually.

---

## The reference node

Everything here is **verified on this exact machine** (July 2026):

| | |
|---|---|
| **Instance** | `g6e.12xlarge` |
| **GPU** | 4 × NVIDIA L40S, 46 GB each (**184 GB total VRAM**) |
| **CPU** | 48 vCPU (AMD EPYC 7R13) |
| **RAM** | 372 GB |
| **Disk** | 193 GB gp3 root (≈ 178 GB free — a 30B model is ~57 GB on disk) |
| **AMI** | Deep Learning OSS Nvidia Driver AMI GPU PyTorch (Ubuntu 24.04) |
| **OS** | Ubuntu 24.04.4 LTS |
| **NVIDIA driver** | 595.71.05 (pre-installed on the DLAMI) |
| **CUDA (driver)** | 13.2 |
| **Region** | `us-west-2` (any region with G6e capacity works) |
| **On-demand price** | ~$10.49/hr (see the strategy doc's cost model) |

The 4 × L40S = 184 GB is the point: it holds Tier 1/2 models (30B–80B) that will not fit on one 46 GB card, and tensor parallelism keeps all four GPUs busy on every token.

### Why this instance (and what else fits)

Weight memory = `params × bytes/param` (BF16 = 2 bytes). On 184 GB of VRAM, after leaving room for the KV cache and CUDA graphs:

| Model | Type | Params (active) | BF16 weights | Fits 4×L40S? |
|-------|------|-----------------|--------------|--------------|
| **Qwen3-Coder-30B-A3B-Instruct** ⭐ | MoE | 30.5B (3B) | ~61 GB | ✅ comfortably — **the default** |
| Qwen3-32B | dense | 32.8B (all) | ~66 GB | ✅ (every param active → ~10× the per-token compute of a 3B-active MoE) |
| Qwen3.6-35B-A3B | MoE | 35.9B (3B) | ~72 GB | ✅ |
| Qwen3-Coder-Next | MoE (hybrid Mamba) | 79.6B (3B) | ~160 GB | ✅ tight — reduce `--max-model-len` **and** cap `--max-num-seqs` (Mamba cache), see [model file](models/qwen3-coder-next.md) |

⭐ The default is the **30B-A3B coder MoE**: only 3B parameters activate per token, so it is fast and leaves ~120 GB of VRAM for a large KV cache and high concurrency — exactly what a throughput benchmark wants.

**Per-model serving guidelines** live in [`models/`](models/) — one file each with the exact serve command, the right tool-call parser, and model-specific tuning notes:

- [Qwen3-Coder-30B-A3B](models/qwen3-coder-30b.md) ⭐ — the default MoE coder
- [Qwen3-32B](models/qwen3-32b.md) — dense chat model (`hermes` parser)
- [Qwen3.6-35B-A3B](models/qwen3.6-35b-a3b.md) — 3.6-generation MoE
- [Qwen3-Coder-Next (80B)](models/qwen3-coder-next.md) — largest fit, reduced context

---

## What gets installed (the full dependency stack)

From the OS up. The [`vllm-install.sh`](scripts/vllm-install.sh) script performs every step; this table is what it lays down and why.

| Layer | Component | Version (verified) | Source | Why it's needed |
|-------|-----------|--------------------|--------|-----------------|
| **Driver** | NVIDIA driver + `nvidia-smi` | 595.71.05 | pre-installed on DLAMI | GPU access; `nvidia-smi` for metrics |
| **OS build tools** | `build-essential` (gcc 13.3) | Ubuntu 24.04 | `apt` | vLLM's Triton backend JIT-compiles a CUDA helper at startup |
| **OS Python headers** | `python3.12-dev` (`Python.h`) | 3.12 | `apt` | that JIT compile `#include <Python.h>` — **missing on the DLAMI by default** |
| **Pkg manager** | `uv` | 0.11+ | astral.sh | fast, reproducible venv + wheel installs |
| **Runtime** | Python venv at `~/vllm-env` | 3.12 | `uv venv` | isolates vLLM from the AMI's `/opt/pytorch` env |
| **Inference engine** | `vllm` | 0.24.0 | PyPI (via `uv pip`) | the model server |
| **Tensor lib** | `torch` | 2.11.0+cu130 | pulled by vLLM | GPU compute |
| **Kernels** | `flashinfer`, `triton`, FlashAttention 2 | flashinfer 0.6.12 | pulled by vLLM | attention + MoE + sampling kernels |
| **Monitoring** | `nvtop` | 3.0.2 | `apt` | htop-style live GPU TUI |
| **Monitoring** | `gpustat` | latest | `uv pip` (into venv) | one-line-per-GPU scriptable snapshot |

`nvidia-smi` is already on the DLAMI; `nvtop` and `gpustat` are added because they are far nicer for *watching* VRAM and utilization while a benchmark runs.

### The two DLAMI-specific fixes (why a naive `pip install vllm` fails here)

Both are handled automatically by the scripts, but they are the two things that will bite you on a fresh Deep Learning AMI, so they are worth stating plainly:

1. **`Python.h: No such file or directory`** — vLLM's Triton/inductor path compiles `cuda_utils.c` with `gcc` on the first `profile_run`, and that needs the CPython dev headers. The DLAMI ships CPython but **not** `python3.12-dev`. → `vllm-install.sh` installs `python3.12-dev build-essential`.

2. **`Could not find nvcc and default cuda_home='/usr/local/cuda' doesn't exist`** — FlashInfer's sampler JIT-compiles CUDA kernels at boot and hardcodes `/usr/local/cuda`, but the DLAMI's toolkit lives at `/opt/pytorch/cuda`. → [`vllm-serve.sh`](scripts/vllm-serve.sh) sets `VLLM_USE_FLASHINFER_SAMPLER=0` (use vLLM's native Torch sampler — no runtime nvcc, boots faster) and points `CUDA_HOME` at the toolkit the DLAMI actually ships, as a belt-and-suspenders fallback for anything else that JIT-compiles.

---

## Install

### Option A — run the skill (recommended)

From a Claude Code session **on the GPU instance**:

```
/vllm-setup
```

It runs the steps below, reports each one, and stops for you at the "inference works" checkpoint.

### Option B — run the scripts by hand

```bash
git clone https://github.com/aws-samples/sample-claude-code-multi-model ~/repo
cd ~/repo/self-hosted/vllm/scripts

./vllm-install.sh      # driver check → apt deps → uv → venv → vLLM → monitoring → GPU check
```

`vllm-install.sh` is idempotent — re-running it skips anything already present.

---

## Serve a model

```bash
./vllm-serve.sh                          # default: Qwen3-Coder-30B-A3B, TP=4, port 8000
```

That single command:

- shards the model across all 4 GPUs (`--tensor-parallel-size 4`),
- binds the OpenAI-compatible API to `127.0.0.1:8000` (no public ingress),
- **streams the full server log to the console AND tee's it to a file**: `self-hosted/vllm/logs/vllm-serve.log`, and
- polls until the server answers, then prints the next steps.

> **The log file is never committed.** `self-hosted/vllm/logs/` is in [`.gitignore`](../../.gitignore) (and the repo-wide `*.log` rule covers it too). Tail it live in another terminal with `tail -f self-hosted/vllm/logs/vllm-serve.log`.

### Command parameters

Every knob is an environment variable with a sensible default for this node. Each one maps to an underlying `vllm serve` flag, shown in the last column so you can see exactly what the script passes:

| Variable | Default | What it does | vLLM flag it sets |
|----------|---------|--------------|-------------------|
| `MODEL` | `Qwen/Qwen3-Coder-30B-A3B-Instruct` | HF repo id to serve | positional `serve <MODEL>` |
| `SERVED_NAME` | `qwen3-coder-30b` | name clients pass as `--model` / in the API | `--served-model-name` |
| `TP` | `4` | tensor-parallel size = number of GPUs to shard across | `--tensor-parallel-size` |
| `PORT` | `8000` | OpenAI-compatible API port | `--port` |
| `MAX_MODEL_LEN` | `32768` | context window to serve | `--max-model-len` |
| `ROPE_SCALING` | *(unset)* | extend context past the model's 32K native window with YaRN — a bare factor (e.g. `4` → 128K) or full JSON | `--rope-scaling` |
| `MAX_NUM_SEQS` | *(unset)* | cap on concurrent sequences (vLLM default 256); **required for hybrid Mamba models on a VRAM-tight node** — e.g. Qwen3-Coder-Next, see its [model file](models/qwen3-coder-next.md) | `--max-num-seqs` |
| `GPU_MEM_UTIL` | `0.90` | fraction of each GPU's VRAM vLLM may use | `--gpu-memory-utilization` |
| `TOOL_PARSER` | `qwen3_coder` | tool-call parser for agentic clients; `none` disables tools | `--tool-call-parser` (+ `--enable-auto-tool-choice`) |
| `VLLM_ENV` | `~/vllm-env` | path to the vLLM virtualenv | *(picks the `vllm` binary)* |
| `HF_TOKEN` | *(unset)* | HuggingFace token for gated/faster downloads | *(env, not a flag)* |

The server also always binds `--host 127.0.0.1` (loopback only — reach it via the SSH tunnel), applies `VLLM_USE_FLASHINFER_SAMPLER=0` + a `CUDA_HOME` fallback (the two DLAMI fixes), and **always enables prefix caching** (`--enable-prefix-caching`) — this is a no-cost optimization that reduces prompt token processing when consecutive requests share common prefixes (e.g. conversation history, system prompts).

**Spell out every parameter explicitly (recommended for a benchmark).** So a run is fully reproducible from its command line — no reliance on defaults — pass all of them even when they equal the default:

```bash
MODEL="Qwen/Qwen3-Coder-30B-A3B-Instruct" \
SERVED_NAME="qwen3-coder-30b" \
TP=4 \
PORT=8000 \
MAX_MODEL_LEN=32768 \
GPU_MEM_UTIL=0.90 \
TOOL_PARSER="qwen3_coder" \
VLLM_ENV="$HOME/vllm-env" \
  ./vllm-serve.sh
```

That is exactly equivalent to the bare `./vllm-serve.sh`, which under the hood runs:

```bash
vllm serve Qwen/Qwen3-Coder-30B-A3B-Instruct \
  --tensor-parallel-size 4 \
  --host 127.0.0.1 \
  --port 8000 \
  --served-model-name qwen3-coder-30b \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --enable-prefix-caching
```

More examples:

```bash
# A dense 32B alternative — the Qwen3 chat models use the `hermes` parser
MODEL=Qwen/Qwen3-32B SERVED_NAME=qwen3-32b TOOL_PARSER=hermes ./vllm-serve.sh

# The 80B MoE — trim context so the KV cache fits, AND cap concurrent sequences:
# it is a hybrid Mamba model that needs one Mamba cache block per sequence, so the
# default max_num_seqs=256 exceeds the ~134 blocks that fit and boot aborts. See its model file.
MODEL=Qwen/Qwen3-Coder-Next SERVED_NAME=qwen3-coder-next \
  MAX_MODEL_LEN=16384 MAX_NUM_SEQS=128 ./vllm-serve.sh

# Extend context to 128K with YaRN — the 32K-native models need rope scaling to go past 32768.
# ROPE_SCALING=4 = 4x the 32768 native window; expect ~1/4 the concurrency (4x KV cache per request).
MODEL=Qwen/Qwen3-32B SERVED_NAME=qwen3-32b TOOL_PARSER=hermes \
  MAX_MODEL_LEN=131072 ROPE_SCALING=4 ./vllm-serve.sh

# Plain completion server, no tool calling
TOOL_PARSER=none ./vllm-serve.sh

# Watch the whole boot in the foreground (Ctrl-C to stop)
./vllm-serve.sh --foreground

# Stop the background server and free the GPUs
./vllm-serve.sh --stop
```

> **Every script takes `--help`.** Run `./vllm-install.sh --help`, `./vllm-serve.sh --help`, `./vllm-verify.sh --help`, or `./opencode-setup.sh --help` for the full env-var and option listing.

### Verify inference

```bash
./vllm-verify.sh        # GET /v1/models + a real chat completion round-trip
```

Confirms the server is up, names the served model, sends a prompt, and prints prompt/completion token counts. (This is a single-request smoke test — for sustained batched tokens/sec, use the throughput harness.)

### Run inference yourself

The server speaks the OpenAI API, so any OpenAI-compatible client works. Pass the `served-model-name` (default `qwen3-coder-30b`) as the model. A raw `curl`:

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-coder-30b",
    "messages": [
      {"role": "user", "content": "Write a Python function that returns the nth Fibonacci number."}
    ],
    "max_tokens": 256,
    "temperature": 0.2
  }' | python3 -m json.tool
```

The same endpoint from the OpenAI Python SDK (`pip install openai`):

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="not-needed")
resp = client.chat.completions.create(
    model="qwen3-coder-30b",
    messages=[{"role": "user", "content": "Reverse a string in Python."}],
    max_tokens=128,
)
print(resp.choices[0].message.content)
```

Streaming (token-by-token) is the same call with `--data` field `"stream": true` on the `curl`, or `stream=True` in the SDK. From your laptop, open the SSH tunnel first (see [below](#connect-a-client-ssh-tunnel)) and swap `127.0.0.1` for the tunneled `localhost:8000`.

---

## Client test programs (`pyproject.toml` + `uv`)

The `curl`/SDK snippets above are one-offs. For writing real programs that exercise the endpoint — inference smoke tests, streaming, concurrency probes — this directory ships a [`pyproject.toml`](pyproject.toml) and a [`clients/`](clients/) folder with runnable scripts.

These clients talk to the server over HTTP and **do not import `vllm`** — they are completely independent of the server's `~/vllm-env`. Give them their own `uv`-managed venv:

```bash
cd self-hosted/vllm

# uv is already installed by vllm-install.sh; if you're on a fresh laptop:
#   curl -LsSf https://astral.sh/uv/install.sh | sh

uv sync                       # creates .venv/ and installs openai, httpx, tiktoken
```

`uv sync` reads [`pyproject.toml`](pyproject.toml), resolves a locked set into `.venv/`, and writes `uv.lock` for reproducibility. Then run any client with `uv run` (no need to activate the venv):

```bash
# one chat completion + token usage (pairs with vllm-verify.sh)
uv run clients/hello_inference.py

# your own prompt, streamed token-by-token
uv run clients/hello_inference.py --stream \
  --prompt "Write a merge sort in Go."

# point at a tunneled endpoint from your laptop
BASE_URL=http://localhost:8000/v1 MODEL=qwen3-coder-30b \
  uv run clients/hello_inference.py
```

| Dependency | Why it's here |
|------------|---------------|
| `openai` | the OpenAI-compatible client for vLLM's `/v1` endpoint |
| `httpx` | raw HTTP + async — for streaming and concurrency probes |
| `tiktoken` | token accounting when comparing cost against API pricing |

`.venv/` is gitignored; `uv.lock` is committed so the client environment is reproducible.

---

## The serving configuration explained

The defaults in `vllm-serve.sh` are chosen for a throughput benchmark on 4×L40S. Here is what each knob does and what vLLM actually reports at boot on this node.

### Which kernels are we using?

vLLM picks a specific compute kernel for each stage. These are the exact ones this node selects at boot (from the server log) — named here so there's no ambiguity about what is running:

| Stage | Kernel selected on this node | How it's chosen |
|-------|------------------------------|-----------------|
| **Attention** | **FlashAttention 2** (`FLASH_ATTN`) | vLLM auto-selects on L40S, over `FLASHINFER` / `TRITON_ATTN` / `FLEX_ATTENTION` |
| **MoE experts** | **Triton** unquantized MoE (`TRITON`) | auto, over `FlashInfer TRTLLM` / `FlashInfer CUTLASS` / `BATCHED_TRITON` |
| **KV cache / paging** | **PagedAttention** | always on |
| **Sampling (top-k/top-p)** | **native Torch sampler** | **forced** via `VLLM_USE_FLASHINFER_SAMPLER=0` — FlashInfer's sampler would JIT-compile CUDA at boot and fail on the DLAMI (no `/usr/local/cuda`) |
| **All-reduce (4-GPU)** | **PYNCCL** | auto; custom all-reduce disabled (L40S is PCIe-only, no NVLink) |
| **Graph execution** | **CUDA graphs** | captured at boot (~1 GiB) |

Each is explained in detail below.

### Tensor parallelism (`--tensor-parallel-size 4`)

vLLM splits every weight matrix *across* the 4 GPUs, so each card holds ¼ of the model and every GPU participates in every token. This is what lets a 61 GB (or 160 GB) model serve at all on 46 GB cards, and it keeps all four GPUs busy rather than idle-in-a-pipeline.

- **vs. Ollama's pipeline split:** Ollama assigns whole *layers* to different GPUs (pipeline parallelism), so at low concurrency only one GPU is active at a time. Tensor parallelism activates all GPUs per token → higher throughput under load. This is the core reason the vLLM path exists.
- **Data parallelism** (running N independent model replicas) is *not* used here: one 30B replica already fits with room to spare, and a single replica with a big KV cache maximizes concurrency per model copy. Data parallelism becomes relevant only for very small models where several replicas fit.

On this node vLLM auto-selects the `PYNCCL` all-reduce backend for the 4-GPU group (custom all-reduce is disabled because the L40S GPUs are PCIe-only, not NVLink — expected on G6e).

### Continuous batching (automatic)

vLLM's scheduler interleaves many in-flight requests token-by-token ("continuous" / in-flight batching) instead of waiting for fixed batches. You don't configure it — it's on. It's why the tokens/sec under 20 concurrent requests is many times the single-request number from `vllm-verify.sh`.

### PagedAttention + KV cache

vLLM stores attention keys/values in paged GPU blocks (PagedAttention), which packs concurrent requests efficiently. With the default `--gpu-memory-utilization 0.90` on the 30B model, vLLM reports on this node:

```
Available KV cache memory: 23.74 GiB
GPU KV cache size:         1,037,152 tokens
Maximum concurrency for 32,768 tokens per request: 31.65x
```

Reading that last line: at a full 32K-token context, this node can hold ~32 concurrent requests' worth of KV cache. Shorter prompts → proportionally more concurrency. Raising `GPU_MEM_UTIL` gives more KV cache (more concurrency) at the cost of headroom; lowering `MAX_MODEL_LEN` does the same.

### Long context and `ROPE_SCALING` (YaRN)

**Native context windows differ sharply by model — check the [per-model file](models/) before assuming.** Of the models in this folder, only the dense **Qwen3-32B** is 32K-native; the three MoE models (Qwen3-Coder-30B, Qwen3.6-35B-A3B, Qwen3-Coder-Next) are all **256K-native**. This matters because `--max-model-len` is a hard ceiling you set (it never auto-expands to native), and YaRN rope scaling is only needed when you ask for *more than native*:

- **Serving ≤ native window → no `ROPE_SCALING`.** Just set `MAX_MODEL_LEN` to what you want. For the 256K-native MoE models, that covers everything up to 256K (e.g. 128K needs no scaling); for Qwen3-32B, everything up to 32768.
- **Serving > native window → enable YaRN.** vLLM will *reject* an oversized `--max-model-len` on a model without rope scaling. `ROPE_SCALING` wires it up: pass a bare factor and the script builds the `--rope-scaling` JSON assuming a **32768** native window (factor `4` → 131072 tokens), or — for the 256K-native models — pass a **full JSON object** with the correct `original_max_position_embeddings` (the bare-number shorthand's 32768 assumption would be wrong for them).

```bash
# Qwen3-32B (32K native) → 128K with YaRN, bare factor is correct here
MODEL=Qwen/Qwen3-32B SERVED_NAME=qwen3-32b TOOL_PARSER=hermes \
  MAX_MODEL_LEN=131072 ROPE_SCALING=4 ./vllm-serve.sh

# Qwen3.6-35B-A3B (256K native) → 128K needs NO scaling, just raise the window
MODEL=Qwen/Qwen3.6-35B-A3B SERVED_NAME=qwen3.6-35b \
  MAX_MODEL_LEN=131072 ./vllm-serve.sh
```

Two caveats whenever you *do* extend past native. **(1) Throughput cost:** context length scales KV-cache VRAM linearly, so 4× the window ≈ ¼ the concurrency — a poor trade for a pure throughput benchmark, correct only when you genuinely need the long window. On 4×L40S, KV-cache VRAM (not the model's native window) is usually the real ceiling. **(2) YaRN is static:** once enabled it applies to every request and can slightly degrade quality on short prompts, so leave `ROPE_SCALING` unset unless you actually need the extra length.

### Attention backend (`FLASH_ATTN`)

vLLM auto-selects **FlashAttention 2** on the L40S (out of `FLASH_ATTN`, `FLASHINFER`, `TRITON_ATTN`, `FLEX_ATTENTION`). Fused, memory-efficient attention — nothing to configure.

### MoE backend (`TRITON`)

For the Qwen MoE models, vLLM routes the expert layers through its **Triton** unquantized MoE kernels. The strategy doc's key insight lives here: only the 3B active parameters run per token even though 30B are resident, so per-token compute (and cost) tracks the *active* count, not the total.

### Sampling kernel (native Torch, not FlashInfer)

The top-k/top-p sampler is forced to vLLM's **native Torch implementation** via `VLLM_USE_FLASHINFER_SAMPLER=0` in `vllm-serve.sh`. FlashInfer's sampler JIT-compiles CUDA kernels at boot against `/usr/local/cuda`, which does not exist on the DLAMI — so leaving it on crashes the engine (one of the two DLAMI fixes above). The native sampler needs no runtime `nvcc`, so the server boots reliably. Sampling is a tiny fraction of per-token cost, so there is no throughput penalty worth measuring here.

### Tool calling (`--enable-auto-tool-choice --tool-call-parser qwen3_coder`)

Agentic clients — opencode, Claude Code — drive the model through *tool calls* (read file, run bash, edit) and send `tool_choice: "auto"` on every request. vLLM rejects that unless the server was started with `--enable-auto-tool-choice` and a `--tool-call-parser` matching the model's tool-call format. `vllm-serve.sh` turns this on by default with the **`qwen3_coder`** parser (correct for the Qwen3-Coder models). Without it you get: `"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set`. Use `hermes` for the Qwen3 chat models, or `TOOL_PARSER=none` for a plain completion server.

### Precision (BF16, the default)

Weights are served at native BF16 (2 bytes/param) — no quantization. This keeps the benchmark an apples-to-apples quality comparison against full-precision APIs (the strategy doc's "quantization confound" risk). To trade quality for capacity you could add `--quantization fp8`, but the default here is deliberately unquantized.

### CUDA graphs

vLLM captures CUDA graphs at boot (`~1 GiB`, ~14s on this node) to cut per-step launch overhead. Automatic; it's part of why first-boot takes ~1–2 minutes after the weights are cached.

### Telemetry: off

`vllm-serve.sh` exports `VLLM_NO_USAGE_STATS=1` and `DO_NOT_TRACK=1` so vLLM's usage stats never leave the box — matching the strategy doc's data-egress concern for self-hosted deployments.

---

## Monitor the GPUs

While a model is serving or a benchmark is running:

```bash
nvtop                              # live htop-style TUI across all 4 GPUs
~/vllm-env/bin/gpustat -i 1        # refresh one line per GPU every second
nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv --loop=1
```

Under load you should see all four L40S climb in utilization together (tensor parallelism), each holding ~42 GB with the 30B model + KV cache.

---

## Connect a client (SSH tunnel)

The server binds to `127.0.0.1` only. Reach it from your laptop exactly like the Ollama path — an SSH tunnel, no public ingress:

```bash
# on your laptop
export G6E_IP=<instance-public-ip>
export G6E_KEY=~/.ssh/<your-key>.pem
LOCAL_MODEL_PORT=8000 ../ollama/scripts/tunnel.sh start   # forwards localhost:8000 → EC2:8000
```

Then point any OpenAI-compatible client (including Claude Code, opencode, or `curl`) at `http://localhost:8000/v1`.

---

## Drive a coding agent: opencode

The raw clients above send single prompts. To run a real *agentic* coding session against the self-hosted model — file reads, edits, bash, multi-step reasoning — wire up [opencode](https://opencode.ai), a terminal coding agent. [`opencode-setup.sh`](scripts/opencode-setup.sh) does it in one step and is **idempotent — it checks whether opencode is already installed and skips the install if so**:

```bash
cd self-hosted/vllm/scripts

./opencode-setup.sh            # install opencode if missing, then write the vLLM provider config
./opencode-setup.sh --launch   # ... and drop straight into an interactive session
./opencode-setup.sh --check    # just report install + config state
```

What it does:

- **Installs opencode only if absent** (`curl -fsSL https://opencode.ai/install | bash` → `~/.opencode/bin/opencode`). If it's already there, it says so and skips.
- **Writes `~/.config/opencode/opencode.json`** registering a custom OpenAI-compatible provider `vllm` pointed at `http://localhost:8000/v1`, with the served model as the default. Any existing config is backed up first.
- **Checks the vLLM server is reachable** and reminds you if it isn't.

A copy of the generated config lives at [`config/opencode.json`](config/opencode.json) for reference.

> **Tool calling must be on.** opencode is agentic and sends `tool_choice: "auto"`. `vllm-serve.sh` enables tool calling by default (`--enable-auto-tool-choice --tool-call-parser qwen3_coder`); if you started the server with `TOOL_PARSER=none`, opencode will error until you restart it with a parser.

Then, with `~/.opencode/bin` on your PATH:

```bash
opencode                       # interactive TUI, backed by vllm/qwen3-coder-30b
opencode run "explain this repo"   # one-shot
opencode models vllm               # confirm the provider is wired
```

Verified working end-to-end on the reference node: opencode's `build` agent driving the self-hosted `qwen3-coder-30b` through vLLM.

---

## What's inside

| File | What it does |
|------|--------------|
| [models/](models/) | Per-model serving guidelines — one `.md` per model (serve command, parser, tuning) |
| [scripts/vllm-install.sh](scripts/vllm-install.sh) | Full install: driver check → apt deps → uv → venv → vLLM → monitoring → GPU verify |
| [scripts/vllm-serve.sh](scripts/vllm-serve.sh) | Serve a model tensor-parallel across all GPUs; tee's logs; `--foreground` / `--stop` |
| [scripts/vllm-verify.sh](scripts/vllm-verify.sh) | Smoke-test the endpoint with a real chat completion |
| [scripts/opencode-setup.sh](scripts/opencode-setup.sh) | Install opencode (if missing) + point it at the vLLM endpoint |
| [clients/hello_inference.py](clients/hello_inference.py) | Minimal Python inference client (openai SDK) |
| [pyproject.toml](pyproject.toml) | `uv`-managed deps for the Python clients |
| [config/opencode.json](config/opencode.json) | Reference opencode provider config for vLLM |
| `logs/` | vLLM server logs (gitignored — never committed) |

> Every script supports `--help` — env vars, defaults, and options.

## Tear down

```bash
./vllm-serve.sh --stop                                    # stop serving, free GPUs
aws ec2 stop-instances --instance-ids <id>                # pause (weights persist on EBS)
aws ec2 terminate-instances --instance-ids <id>           # destroy
```

## See also

- [../ollama/README.md](../ollama/README.md) — the single-GPU convenience path
- [../../README.md](../../README.md) — the multi-model project and the cost/quality experiment
