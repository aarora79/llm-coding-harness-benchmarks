Install and configure the self-hosted LLM setup for Claude Code.

Detect whether you are running on the **GPU server** or the **local machine** and perform the appropriate setup steps.

## Step 1 — Detect environment

Run `nvidia-smi` to check if a GPU is present.

- If a GPU is detected: this is the **GPU server** — proceed with GPU Server Setup below.
- If no GPU is detected: this is the **local machine** — proceed with Local Machine Setup below.

## GPU Server Setup

### 1. Check prerequisites

Run the following and report what is found:

```bash
nvidia-smi
```

### 2. Install Ollama

Check if Ollama is already installed:

```bash
ollama --version
```

If not installed, install it:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### 3. Start Ollama service

Ensure Ollama is running:

```bash
sudo systemctl enable ollama
sudo systemctl start ollama
```

### 4. Pull the model

Check if the model is already present:

```bash
ollama list
```

If `qwen3.5:35b` is not listed, pull it (~22GB on first run):

```bash
ollama pull qwen3.5:35b
```

Report progress. This may take several minutes depending on internet speed.

### 5. Verify GPU inference

Send a test prompt and verify the model responds:

```bash
curl -sf --max-time 120 http://localhost:11434/api/generate \
  -d '{"model":"qwen3.5:35b","prompt":"Reply with: ready","stream":false}'
```

Report the response and confirm the model is working on GPU.

### 6. Print connection instructions

Print the public IP of this server:

```bash
curl -s http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || curl -s ifconfig.me
```

Tell the user:

"GPU server setup complete. Now run `/install` on your **local machine** and provide this IP when prompted."

---

## Local Machine Setup

### 1. Check Claude Code is installed

```bash
claude --version
```

If not installed, instruct the user to install it:

```bash
npm install -g @anthropic-ai/claude-code
```

### 2. Check SSH key

Ask the user: "What is the path to your SSH key for the GPU server? (e.g. ~/.ssh/my-key.pem)"

Verify the key file exists at the path provided.

### 3. Ask for GPU server IP

Ask the user: "What is the public IP address of your GPU server?"

Store it as G6E_IP.

### 4. Test SSH connectivity

```bash
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -i <key> ubuntu@<G6E_IP> echo "ok"
```

If this fails, report the error and suggest checking:
- Security group allows port 22 from the current IP
- The correct username (ubuntu for Deep Learning AMI)
- The key file has correct permissions (`chmod 600 <key>`)

### 5. Open SSH tunnel

```bash
export G6E_IP=<ip>
export G6E_KEY=<key>
./scripts/tunnel.sh start
```

Verify the tunnel is working:

```bash
curl -s http://localhost:11434/v1/models
```

Report the model name from the response.

### 6. Configure Claude Code settings

Back up any existing Claude Code settings:

```bash
cp ~/.claude/settings.json ~/.claude/settings.json.backup 2>/dev/null || true
```

Copy the template settings:

```bash
cp config/settings.template.json ~/.claude/settings.json
```

### 7. Verify end-to-end

Run a quick test:

```bash
claude -p "Reply with only the words: setup complete"
```

If the response contains "setup complete", the full chain is working.

### 8. Print summary

Print a summary of what was configured:

- GPU server IP
- SSH tunnel status (port 11434)
- Model responding at localhost:11434
- Claude Code settings updated

Tell the user:
"Setup complete. Run `./scripts/claude-local.sh` to start a Claude Code session backed by your self-hosted model. Your original Claude Code settings have been backed up to ~/.claude/settings.json.backup."
