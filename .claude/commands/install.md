Set up Claude Code to run against models on Amazon Bedrock or a self-hosted
EC2 model. Pick the path with the user, then perform that path's setup.

## Step 1 — Confirm the path

Ask the user:

> Which path do you want to set up?
>
> 1. **Bedrock** — run Claude Code against any of 43 models on Amazon Bedrock
>    (5 native Anthropic + 38 third-party). Per-token pricing, no GPU. **Recommended for most users.**
> 2. **Self-hosted** — run Claude Code against an open-source model on an
>    Amazon EC2 GPU instance. Fixed hourly cost, model stays in your account.

Wait for the user's choice. If they answer "1", "bedrock", "amazon bedrock",
or similar, do **Bedrock Setup** below. If they answer "2", "self-hosted",
"ec2", or similar, do **Self-Hosted Setup** below.

If the answer is ambiguous, ask once more before proceeding.

---

## Bedrock Setup

### 1. Verify prerequisites

Run the following and report what is found. Do not proceed until each check passes.

```bash
# Claude Code is installed
claude --version

# Python 3.9+ for the LiteLLM proxy
python3 --version

# AWS credentials are configured (any of: aws configure, IAM role, AWS SSO)
aws sts get-caller-identity
```

If any of these fails, report which one and tell the user how to fix it
(install Claude Code with `npm install -g @anthropic-ai/claude-code`,
install Python 3.9+, run `aws configure`).

### 2. Confirm Bedrock model access

Ask the user:

> Have you enabled Bedrock model access for the models you want to use?
> (Bedrock console → Model access → Manage model access)

If they say no, point them at https://console.aws.amazon.com/bedrock/home#/modelaccess
and pause until they confirm.

### 3. Start the LiteLLM proxy

The proxy auto-installs `litellm` and `aws-bedrock-token-generator`, generates
a 12-hour Bedrock bearer token, and starts a local HTTP server on port 4000.

```bash
cd bedrock
./scripts/setup-proxy.sh
```

Verify the proxy is healthy:

```bash
curl -sf http://localhost:4000/health | head -c 200
```

If `/health` does not respond, show the last 30 lines of the proxy log:

```bash
tail -30 .litellm.log
```

The most common failure is missing Bedrock model access — re-check Step 2.

### 4. Run a smoke test

Pick one **native** Anthropic model (no proxy needed) and one **third-party**
model (via the proxy) to confirm both routing paths work:

```bash
# Native Bedrock (Anthropic)
./scripts/claude-model.sh --model claude-sonnet -p "Reply with: native ok"

# Third-party via proxy
./scripts/claude-model.sh --model qwen-coder-30b -p "Reply with: proxy ok"
```

Report both responses. If either fails, surface the error and suggest the
fix (model access, region, or proxy state).

### 5. Print summary and next steps

Tell the user:

> Bedrock setup complete. To start an interactive Claude Code session with a
> chosen model:
>
> ```
> ./scripts/claude-model.sh --model <alias>
> ```
>
> See `bedrock/README.md` for the full list of 43 model aliases and the
> HumanEval benchmark comparing them. Run `./scripts/setup-proxy.sh --refresh`
> to renew the 12-hour Bedrock token when it expires.

---

## Self-Hosted Setup

The self-hosted path has its own setup command. Tell the user:

> Self-hosted setup is multi-step (GPU server + local machine). Run this
> instead, from the `self-hosted/` directory, where the dedicated install
> command knows how to detect whether you are on the GPU server or the
> local machine and walk you through both:
>
> ```
> cd self-hosted
> /install
> ```

Do not duplicate those steps here — defer to `self-hosted/.claude/commands/install.md`.
