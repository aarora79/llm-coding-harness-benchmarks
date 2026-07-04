---
name: vllm-setup
description: "Stand up a vLLM inference server for an open-weight coding model on a multi-GPU EC2 node (reference: g6e.12xlarge, 4xL40S). Drives the full flow end to end — verify the GPU node, install vLLM and its OS/Python dependencies (including the two Deep Learning AMI-specific fixes), serve a model with tensor parallelism, and confirm inference works — then hands off to opencode. Use when the user wants to self-host a model with vLLM, get vLLM inference running on EC2, or reproduce the hosting-strategy throughput benchmark. Wraps the scripts in self-hosted/vllm/scripts/."
license: Apache-2.0
metadata:
  author: Amit Arora
  version: "1.0"
---

# vLLM Setup Skill

Use this skill to bring up a vLLM inference server for an open-weight coding
model on a multi-GPU EC2 GPU node, and confirm it actually serves tokens.
The install is heavy (apt packages, a multi-GB wheel, a ~57 GB model
download, and two environment fixes specific to the Deep Learning AMI), so
this skill drives the vetted scripts rather than having the user paste
commands by hand.

**This skill runs ON the GPU instance**, not the user's laptop. The very
first thing it must do is confirm it is on a GPU node (Step 1). If there is
no GPU, stop and tell the user to run this on the EC2 instance instead.

All the underlying logic lives in
[`self-hosted/vllm/scripts/`](../../../self-hosted/vllm/scripts/):
`vllm-install.sh`, `vllm-serve.sh`, `vllm-verify.sh`. This skill
orchestrates them, reports each step, and stops at the "inference works"
checkpoint. The full architecture and the *why* behind every dependency is
documented in
[`self-hosted/vllm/README.md`](../../../self-hosted/vllm/README.md) — read
it if the user asks what is being installed or why a step exists.

## Workflow

1. **Confirm the node** — verify a GPU + capture specs; abort if not on a
   GPU box
2. **Confirm the model** — announce the default, let the user override
3. **Install** — run `vllm-install.sh`, report each layer
4. **Serve** — run `vllm-serve.sh` with the chosen model, wait for ready
5. **Verify** — run `vllm-verify.sh`, show the real inference round-trip
6. **Report + hand off** — summarize, show how to monitor, point to opencode

Keep the SSH-tunnel / client-connection steps for after inference is
confirmed; the goal of this skill is a working local endpoint on the
instance.

---

## Step 1 — Confirm this is the GPU node

Run:

```bash
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv
nproc && free -h | head -2 && df -h / | tail -1
```

- **No `nvidia-smi` / no GPU:** stop. Tell the user this skill must run on
  the EC2 GPU instance (e.g. `g6e.12xlarge`), not their laptop, and offer to
  help launch one.
- **GPU present:** report what was found — number and type of GPUs, total
  VRAM, vCPU, RAM, free disk. Confirm there is enough free disk for the
  model (a 30B model is ~57 GB on disk; warn if free space is under ~80 GB).

Also detect the instance type and AMI if metadata is reachable
(best-effort):

```bash
TOKEN=$(curl -sf -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
curl -sf -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-type
```

## Step 2 — Confirm the model

Announce the default and let the user override before installing:

> I'll serve **`Qwen/Qwen3-Coder-30B-A3B-Instruct`** (a 3B-active MoE coder
> model, ~61 GB in BF16) across all detected GPUs with tensor parallelism. >
> Alternatives that fit a 4×L40S (184 GB) node: `Qwen/Qwen3-32B` (dense),
> `Qwen/Qwen3.6-35B-A3B`, `Qwen/Qwen3-Coder-Next` (80B MoE — use a smaller
> `MAX_MODEL_LEN`). Want the default, or a different model?

Lock in `MODEL` and a short `SERVED_NAME` (e.g. `qwen3-coder-30b`) from the
answer.

## Step 3 — Install vLLM and dependencies

From the repo's script directory:

```bash
cd self-hosted/vllm/scripts
./vllm-install.sh
```

`vllm-install.sh` is idempotent — it skips anything already present. As it
runs, tell the user what each layer is for (the script prints headers;
summarize them):

- **build-essential + python3.12-dev** — vLLM's Triton backend JIT-compiles
  a CUDA helper at startup that needs `gcc` and `<Python.h>`; the DLAMI
  lacks the Python headers by default. This is the #1 thing that breaks a
  naive install.
- **uv → `~/vllm-env` → vLLM** — an isolated venv so the install is
  disposable and does not touch the AMI's `/opt/pytorch` env.
- **nvtop + gpustat** — live GPU monitoring for watching the benchmark.

If the install fails, read the error, cross-reference the "two
DLAMI-specific fixes" section of the README, and fix before proceeding. Do
NOT continue to serving on a failed install.

## Step 4 — Serve the model

```bash
cd self-hosted/vllm/scripts
MODEL="<chosen>" SERVED_NAME="<chosen>" ./vllm-serve.sh
```

- Note the reference-node fixes the serve script applies automatically:
  `VLLM_USE_FLASHINFER_SAMPLER=0` (native sampler — avoids FlashInfer's
  runtime nvcc requirement against a `/usr/local/cuda` that does not exist
  on the DLAMI) and a `CUDA_HOME` fallback pointing at `/opt/pytorch/cuda`.
- The script tees the full server log to
  `self-hosted/vllm/logs/vllm-serve.log` (gitignored) and polls until ready.
  First serve of a model downloads the weights (~57 GB for 30B) — this can
  take several minutes. Reassure the user; tail the log if they want to
  watch: `tail -f self-hosted/vllm/logs/vllm-serve.log`.
- If the process exits early, read the tail of that log for the root cause.

Once ready, surface the useful runtime numbers vLLM printed — especially the
KV cache size and **"Maximum concurrency for N tokens per request"** line,
since that concurrency figure is what the throughput/cost benchmark builds
on.

## Step 5 — Verify inference

```bash
cd self-hosted/vllm/scripts
./vllm-verify.sh
```

Show the user the model's actual reply and the prompt/completion token
counts. This proves the endpoint serves real tokens. Be explicit that the
single-request tokens/sec here is **not** the throughput number — batched
concurrency is much higher (cite the concurrency figure from Step 4).

## Step 6 — Report and hand off to opencode

Summarize:

- node (instance type, GPUs, VRAM), model served, `served-model-name`, port
- that the OpenAI-compatible API is live at `http://127.0.0.1:8000/v1`
- how to monitor: `nvtop`, `~/vllm-env/bin/gpustat -i 1`
- how to stop: `./vllm-serve.sh --stop`
- how to reach it from a laptop: SSH tunnel (point at
  `../ollama/scripts/tunnel.sh` with `LOCAL_MODEL_PORT=8000`)

Then hand off:

> vLLM inference is confirmed working. Next: install **opencode** and point
> it
> at this local endpoint so you can drive a real coding agent against the
> self-hosted model. Want me to set that up?

Do NOT install opencode inside this skill — that is the next step, offered
to the user, not performed automatically.

---

## Notes for the operator

- **Idempotent / resumable:** every script skips work already done.
  Re-running the skill after a fix is safe.
- **One model at a time by default:** the default 30B fits with room to
  spare; a single replica with a large KV cache maximizes concurrency. Only
  serve a second model on a second port if VRAM allows.
- **Logs are never committed:** `self-hosted/vllm/logs/` is gitignored.
- **Precision is BF16 (unquantized)** by default — deliberate, to keep the
  benchmark an apples-to-apples quality comparison with full-precision APIs.
