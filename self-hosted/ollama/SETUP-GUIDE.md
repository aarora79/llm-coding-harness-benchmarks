# Running Claude Code with a Self-Hosted LLM on Amazon EC2 (Advanced: llama.cpp)

A practical guide to setting up Claude Code backed by Qwen 3.5-35B using llama.cpp on an
Amazon EC2 GPU instance. This gives you full control over model flags and context settings.

> **Looking for the simpler setup?** Use `scripts/ec2-setup.sh` instead — it installs Ollama
> automatically and is ready in minutes. This guide covers llama.cpp for users who want
> lower-level tuning (custom quantization, SWA caching, context length, etc.).

---

## What This Achieves

Claude Code (the CLI) normally calls Anthropic's API or Amazon Bedrock. This setup replaces
that backend with a self-hosted open-weight model (Qwen 3.5-35B) running on an AWS GPU instance.
The end result: full Claude Code agentic workflows — file edits, bash commands, multi-step
reasoning — powered by a model you control, at a fixed hourly cost.

```
Your Machine (Mac/Linux)
  │ SSH tunnel (localhost:8131 → g6e:8131)
  ▼
EC2 GPU Server (g6e.xlarge)
  llama-server + Qwen 3.5-35B
  NVIDIA L40S, 45GB VRAM
```

Claude Code runs locally on your machine. The SSH tunnel forwards requests transparently
to the model server on the GPU instance. No API keys. No cloud routing.

---

## Hardware Requirements

| Component | Minimum | Used in this guide |
|---|---|---|
| Instance | Any NVIDIA GPU instance | g6e.xlarge |
| GPU | NVIDIA GPU with 24GB+ VRAM | L40S (45GB VRAM) |
| Disk | 50 GB | 100 GB gp3 |
| AMI | Deep Learning Base OSS Nvidia (Ubuntu 22.04) | ami-014135eb43056a305 |

> The Deep Learning AMI comes with NVIDIA drivers and CUDA pre-installed — no manual driver setup needed.

**Your local machine** just needs Claude Code and SSH — no GPU required.

---

## Step 1 — Launch the GPU Server

```bash
aws ec2 run-instances \
  --region us-east-1 \
  --image-id ami-014135eb43056a305 \
  --instance-type g6e.xlarge \
  --key-name <your-key> \
  --security-group-ids <your-sg> \
  --subnet-id <your-subnet> \
  --associate-public-ip-address \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":100,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=llm-gpu-server}]'
```

**Security group rule needed:**

- Port 22 inbound from your IP only (SSH access — the tunnel rides this)

---

## Step 2 — Build llama.cpp with CUDA

SSH into the GPU server and run:

```bash
sudo apt-get update -qq && sudo apt-get install -y cmake ninja-build git

git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build -G Ninja -DGGML_CUDA=ON
cmake --build build --config Release -j $(nproc)
```

> This takes ~15 minutes on a g6e.xlarge (4 vCPUs). The CUDA kernel compilation is the bottleneck.

Verify:
```bash
~/llama.cpp/build/bin/llama-server --version
# Should show: CUDA device detected — NVIDIA L40S
```

---

## Step 3 — Start the Model Server

This downloads Qwen 3.5-35B (~22GB) on first run from HuggingFace, then starts serving:

```bash
nohup ~/llama.cpp/build/bin/llama-server \
  -hf unsloth/Qwen3.5-35B-A3B-GGUF:Q4_K_M \
  --host 127.0.0.1 \
  --port 8131 \
  -ngl 999 \
  -t 2 \
  -c 131072 \
  -b 512 \
  -ub 1024 \
  --parallel 1 \
  -fa on \
  --jinja \
  --keep 1024 \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --swa-full \
  --no-context-shift \
  --reasoning off \
  --mlock \
  --no-mmap \
  > /tmp/llama-server.log 2>&1 &
```

Verify it's running:
```bash
curl http://localhost:8131/v1/models
```

**Key flags explained:**

| Flag | Why |
|---|---|
| `-ngl 999` | Offload all layers to GPU. Without this, inference runs on CPU — ~10x slower. |
| `--swa-full` | Enables prompt caching for Qwen's sliding window attention. ~10x faster on follow-up turns. |
| `--no-context-shift` | Required when using `--swa-full`. |
| `--reasoning off` | Disables Qwen's internal chain-of-thought. Claude Code manages its own reasoning — no need to waste tokens here. |
| `--mlock` | Locks model weights in RAM, prevents OS from swapping them out. |
| `--host 127.0.0.1` | Binds to localhost only. Access via SSH tunnel — never expose this port directly. |

**Performance observed on g6e.xlarge (L40S):**
- Prompt processing: ~58 tokens/sec
- Generation: ~117 tokens/sec
- VRAM used: 24GB of 45GB available

---

## Step 4 — Open SSH Tunnel from Your Machine

Run this on your local machine:

```bash
export G6E_IP=<your-gpu-server-public-ip>
export G6E_KEY=~/.ssh/<your-key>.pem

./scripts/tunnel.sh start
```

Or manually:

```bash
ssh -o StrictHostKeyChecking=no \
    -o ServerAliveInterval=60 \
    -N -f \
    -L 8131:localhost:8131 \
    -i ~/.ssh/<your-key>.pem \
    ubuntu@<gpu-server-public-ip>
```

Verify from your machine:

```bash
curl http://localhost:8131/v1/models
```

---

## Step 5 — Run Claude Code

Use the provided script — it temporarily swaps your Claude Code settings to point at the
local model, then restores your original config when you exit:

```bash
./scripts/claude-local.sh
```

Or manually set the env and run:
```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8131 \
ANTHROPIC_API_KEY=local \
claude
```

Confirm it's using Qwen:
```bash
claude -p "What model are you?"
# Hello! I'm Qwen3.5-35B-A3B (unsloth-optimized variant)...
```

---

## Monitoring GPU Utilization

Run this on the GPU server while sending prompts to see the GPU spike:

```bash
nvidia-smi --query-gpu=utilization.gpu,utilization.memory,memory.used,temperature.gpu,clocks.current.graphics \
  --format=csv --loop=1
```

Expected: 0% at idle, spikes to ~80-100% during active inference.

---

## Lessons Learned

### 1. Cloud provider settings in settings.json override shell environment variables
If your machine uses Amazon Bedrock (`CLAUDE_CODE_USE_BEDROCK=1` in `settings.json`),
passing env var overrides on the command line will not work. The `env` block in
`settings.json` is applied after shell env vars and wins.

**Fix:** Use `claude-local.sh` — it swaps `settings.json` for the session and restores it on exit.

### 2. llama.cpp CUDA build is slow on small instances
Expect ~15 minutes on a 4-core instance. The CUDA compiler (`nvcc`) is the bottleneck.
Consider using `-j $(nproc)` and pre-built binaries for repeated deployments.

### 3. Model downloads to HuggingFace cache, not llama.cpp cache
The `-hf` flag uses HuggingFace Hub. The 22GB GGUF lands in `~/.cache/huggingface/hub/`.
Plan disk accordingly. First startup is slow; subsequent starts are instant from cache.

### 4. `--reasoning off` replaces the old thinking flag
The previously documented `--chat-template-kwargs '{"enable_thinking": false}'` is deprecated.
Use `--reasoning off` for Qwen 3.5.

### 5. SSH tunnel is the right network pattern
Binding the model server to `0.0.0.0` would work but widens the attack surface unnecessarily.
An SSH tunnel keeps the model port on localhost and leverages your existing key-based auth.

---

## Cost Reference

| Resource | Rate | Notes |
|---|---|---|
| g6e.xlarge on-demand | ~$1.86/hr | 1x NVIDIA L40S, 45GB VRAM |
| Model download | one-time | ~22GB, free from HuggingFace |

Stop the instance when not in use — model weights are preserved on the EBS volume.

---

## Tear Down

```bash
aws ec2 terminate-instances --region us-east-1 --instance-ids <gpu-instance-id>
aws ec2 delete-security-group --region us-east-1 --group-id <sg-id>
```
