---
name: summarize
description: "Summarize a /swe benchmark run for a given repo/problem/model triple. Reports artifact completion status (github-issue.md, lld.md, review.md, testing.md), error signals captured during the run, and a token-and-themes summary derived from the matching session JSONL files under ~/.claude/projects/. Output is a single markdown report saved next to the artifacts."
license: Apache-2.0
metadata:
  author: Amit Arora
  version: "1.1"
---

# Summarize Skill

Use this skill **after** a `/swe` benchmark run to evaluate how that run went. It performs two passes:

1. **Artifact pass** - check the `benchmarks/swe-benchmark-data/{repo-name}/{problem-name}/{model-name}/` folder and report which of the four expected files exist, are non-empty, and are well-formed; surface any error/diagnostic content found in those files.
2. **Session pass** - read the JSONL files from `~/.claude/projects/<encoded-cwd>/` for the session(s) that produced the artifacts, and summarize token usage (per model, broken down by input / output / cache-create / cache-read), tool-call mix, error events, and recurring themes from user prompts and assistant turns.

The result is written as a single markdown report next to the artifacts and printed back to the user.

**This skill stops at reporting. It does not modify the artifacts, re-run `/swe`, or open issues.**

## Workflow

1. **Gather Inputs** - Capture `--repo`, `--problem`, and `--model` (never guess them); optionally `--session-id` to narrow the JSONL pass
2. **Locate Artifacts Folder** - Resolve `benchmarks/swe-benchmark-data/{repo}/{problem}/{model}/`
3. **Artifact Pass** - Check existence, size, and quality of `github-issue.md`, `lld.md`, `review.md`, `testing.md`
4. **Locate Session JSONL Files** - Resolve `~/.claude/projects/<encoded-cwd>/*.jsonl` and filter by session/time
5. **Session Pass** - Aggregate token usage, tool calls, errors, and themes
6. **Write Report** - Save `summary.md` next to the artifacts and print it
7. **Present Summary & Seek Guidance**

---

## Step 1: Gather Inputs

**NEVER guess `--repo`, `--problem`, or `--model`.** Either the user passes them as command-line parameters or you ask explicitly. Do not infer them from the current working directory, recent files, or memory.

### 1.1 Required Inputs

1. **`--repo`** (kebab-case, e.g. `mcp-gateway-registry`) - matches the `{repo-name}` folder under `benchmarks/swe-benchmark-data/`.
2. **`--problem`** (kebab-case, e.g. `remove-faiss`) - matches the `{problem-name}` folder.
3. **`--model`** (kebab-case, e.g. `claude-opus-4-7`) - matches the `{model-name}` folder.

If any are missing, ask explicitly:

> The `/summarize` skill requires three inputs that I will not guess. Please provide:
> 1. `--repo <repo-name>`
> 2. `--problem <problem-name>`
> 3. `--model <model-name>`

### 1.2 Optional Inputs

- **`--session-id <uuid>`** - limit the session pass to a specific JSONL file. If not given, the skill picks every JSONL whose `cwd` matches this repository and whose `gitBranch` / time window overlaps with the artifact mtimes.
- **`--since <ISO timestamp>`** / **`--until <ISO timestamp>`** - further narrow the session pass.
- **`--out <path>`** - override the default report path (`summary.md` inside the artifacts folder).

## Step 2: Locate the Artifacts Folder

Resolve the artifact folder using the repo root - never hardcode `/home/ubuntu/...`:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
ART_DIR="$REPO_ROOT/benchmarks/swe-benchmark-data/{repo}/{problem}/{model}"
```

If `$ART_DIR` does not exist, stop and report it as a hard failure: there is nothing to summarize.

## Step 3: Artifact Pass

For each of the four expected artifacts, record:

| Field | How to Compute |
|-------|----------------|
| Exists | `test -f "$ART_DIR/<file>"` |
| Size (bytes) | `stat -c %s "$ART_DIR/<file>"` |
| Lines | `wc -l < "$ART_DIR/<file>"` |
| Last modified | `stat -c %y "$ART_DIR/<file>"` |
| Looks well-formed | Heuristic checks below |

### Expected Files and Heuristics

| File | Heuristic for "looks well-formed" |
|------|-----------------------------------|
| `github-issue.md` | Contains `## Title`, `## Description`, and at least one `### Acceptance Criteria` checkbox |
| `lld.md` | Contains `# Low-Level Design`, a `Table of Contents`, and at least the `Architecture`, `Data Models`, and `File changes` headings |
| `review.md` | Contains all five reviewers (`Pixel`, `Byte`, `Circuit`, `Cipher`, `Sage`) and a `Verdict:` line for each |
| `testing.md` | Contains `# Testing Plan`, `## 1. Functional Tests`, and a `## 6. Test Execution Checklist` |

### Error Signals to Capture

While scanning each artifact, also surface lines that suggest the run was incomplete:

- Lines containing `TODO`, `TBD`, `FIXME`, `{...}` placeholder fences left in by the model
- Lines starting with `Error:`, `Traceback`, or `# Aborted`
- Headings present in the template but with empty bodies (e.g. a `### Strengths` immediately followed by another heading)

Record up to 10 such hits per file with line numbers.

### Output of the Artifact Pass

Build an in-memory dict shaped like:

```json
{
  "artifacts": {
    "github-issue.md": {"exists": true, "bytes": 1840, "lines": 56, "well_formed": true, "issues": []},
    "lld.md":         {"exists": true, "bytes": 12930, "lines": 410, "well_formed": true, "issues": [{"line": 312, "snippet": "TODO: pick algorithm"}]},
    "review.md":      {"exists": true, "bytes": 4220, "lines": 130, "well_formed": false, "issues": [{"line": 88, "snippet": "Verdict: NEEDS REVISION"}]},
    "testing.md":     {"exists": false}
  }
}
```

## Step 4: Locate Session JSONL Files

Claude Code stores per-project session transcripts under `~/.claude/projects/<encoded-cwd>/*.jsonl`. The encoded directory is the absolute project path with `/` replaced by `-` and a leading `-` (e.g. `/home/ubuntu/repos/sample-claude-code-multi-model` becomes `-home-ubuntu-repos-sample-claude-code-multi-model`).

The encoded path is derived from `git rev-parse --show-toplevel`, not necessarily from the current working directory. This means if you run from a subdirectory (e.g. `repo/bedrock`), the encoded path will still be for the repo root.

### Session File Selection Algorithm

1. **Get the repo root and encoded path:**
   ```bash
   REPO_ROOT="$(git rev-parse --show-toplevel)"
   ENCODED="$(echo "$REPO_ROOT" | sed 's|/|-|g')"
   SESSIONS_DIR="$HOME/.claude/projects/$ENCODED"
   ```

2. **Check for subdirectory sessions:** Also check for sessions in directories that start with `$ENCODED-` (these represent runs from subdirectories):
   ```bash
   # For REPO_ROOT=/home/ubuntu/repos/sample-claude-code-multi-model
   # ENCODED=-home-ubuntu-repos-sample-claude-code-multi-model
   # Also check: -home-ubuntu-repos-sample-claude-code-multi-model-bedrock
   SHADOW_DIRS=$(ls -d ${SESSIONS_DIR}-* 2>/dev/null || true)
   ```

3. **Build list of session directories:**
   - Primary: `$SESSIONS_DIR`
   - Shadow (subdirectory runs): Any `${SESSIONS_DIR}-*` directories

4. **Find matching sessions:**
   - If `--session-id <uuid>` is provided, use only that session
   - Otherwise, list all `*.jsonl` files in all session directories
   - Filter to sessions whose timestamps overlap with artifact mtimes (1-hour grace window)

5. **Verify session by cwd (if needed):**
   - Check the `cwd` field in user events to ensure it matches the repo path
   - If multiple sessions overlap in time, use the one with matching `cwd`

If no JSONL file matches, mark the session pass as `Not Available` in the report and continue - the artifact pass alone is still useful.

## Step 5: Session Pass

Each line in a session JSONL is a JSON object. The relevant types are:

| `type` | What it carries | What we extract |
|--------|-----------------|-----------------|
| `assistant` | `message.usage`, `message.model`, `message.content` (text + tool_use blocks) | Tokens (input / output / cache-create / cache-read), model id, tool calls |
| `user` | `message.content` (text or tool_result) | User prompt text; tool_result error strings |
| `system` | System reminders, hook output | Error / warning lines |
| `attachment` | File attachments | Filenames referenced |

Pseudocode for the aggregation:

```python
import json
from collections import Counter, defaultdict
import re

totals = defaultdict(lambda: {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "messages": 0,
})
tool_calls = Counter()
errors = []
user_prompts = []

for path in selected_jsonl_files:
    with open(path) as f:
        for line in f:
            obj = json.loads(line)
            t = obj.get("type")
            msg = obj.get("message") or {}
            if t == "assistant" and isinstance(msg, dict):
                model = msg.get("model", "unknown")
                u = msg.get("usage") or {}
                bucket = totals[model]
                bucket["input_tokens"] += u.get("input_tokens", 0) or 0
                bucket["output_tokens"] += u.get("output_tokens", 0) or 0
                bucket["cache_creation_input_tokens"] += (
                    u.get("cache_creation_input_tokens", 0) or 0
                )
                bucket["cache_read_input_tokens"] += (
                    u.get("cache_read_input_tokens", 0) or 0
                )
                bucket["messages"] += 1
                for block in msg.get("content", []) or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_calls[block.get("name", "?")] += 1
            elif t == "user" and isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str):
                    user_prompts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                user_prompts.append(block.get("text", ""))
                            elif block.get("type") == "tool_result" and block.get("is_error"):
                                errors.append(str(block.get("content", ""))[:500])
            elif t == "system":
                text = obj.get("content") or obj.get("text") or ""
                if any(s in text for s in ("Error", "error", "failed", "Traceback")):
                    errors.append(text[:500])
```

### Themes

Extract recurring topics from `user_prompts`:

1. Lowercase, strip punctuation, drop stopwords.
2. Take the top 15 most frequent 1-3 word phrases. (Use a simple counter; no NLP libs required.)
3. Cluster verbs ("update", "remove", "add", "review", "ask") to label intent.

Themes go in the report as a short bullet list, e.g.:

```
- folder structure / repo layout (12 mentions)
- gitignore / submodule discussion (5 mentions)
- "do not guess" inputs (4 mentions)
- skill rewrite (testing, lld, review) (4 mentions)
```

## Step 6: Write the Report

Save `summary.md` next to the artifacts (or to `--out` if provided):

```markdown
# Summary: {repo} / {problem} / {model}

*Created: {iso-timestamp}*
*Artifacts: `benchmarks/swe-benchmark-data/{repo}/{problem}/{model}/`*

## Run Status

| Artifact | Exists | Bytes | Lines | Well-formed | Issues |
|----------|--------|-------|-------|-------------|--------|
| github-issue.md | yes | 1840 | 56 | yes | 0 |
| lld.md          | yes | 12930 | 410 | yes | 1 |
| review.md       | yes | 4220 | 130 | no  | 3 |
| testing.md      | no  | -    | -   | -   | -   |

**Overall:** {GREEN / YELLOW / RED} - {one-line headline}

### Issues Captured in Artifacts

- `lld.md:312` `TODO: pick algorithm`
- `review.md:88` `Verdict: NEEDS REVISION`
- ...

## Session Token Usage

| Model | Messages | Input | Output | Cache Create | Cache Read | Total |
|-------|----------|-------|--------|--------------|------------|-------|
| claude-opus-4-7 | 97 | 12,403 | 28,192 | 145,200 | 312,800 | 498,595 |
| claude-haiku-4-5 | 8 | 432 | 980 | 0 | 0 | 1,412 |
| **All models** | 105 | 12,835 | 29,172 | 145,200 | 312,800 | 500,007 |

*Cache hit ratio:* `cache_read / (cache_read + input)` = 96.2%

## Tool Call Mix

| Tool | Count |
|------|-------|
| Bash | 41 |
| Read | 33 |
| Edit | 14 |
| Agent | 5 |
| Write | 4 |
| Grep | 3 |

## Errors and Warnings

- `tool_result is_error: ENOENT: no such file ...` (truncated)
- `system: Error: pre-commit hook failed - ruff check exited 1`
- ...

## Themes from User Prompts

- folder structure / repo layout (12 mentions)
- "do not guess" inputs (4 mentions)
- skill rewrite (4 mentions)
- ...

## Sessions Included

| Session | First event | Last event | Lines |
|---------|-------------|------------|-------|
| 9afad8af-a121-4047-b07b-fb8b8556eb55 | 2026-06-05T15:43Z | 2026-06-05T17:10Z | 257 |
| 6e8e145a-b043-4722-af15-68841a6853cb | 2026-06-05T17:11Z | 2026-06-05T17:55Z | 88 |

## Next Steps

- {Auto-generated: e.g. "Re-run /swe to regenerate testing.md (missing)."}
- {e.g. "Resolve the NEEDS REVISION verdict in review.md before implementing."}
```

### Overall Status Rule

Pick `GREEN`, `YELLOW`, or `RED` using these thresholds:

| Status | Rule |
|--------|------|
| GREEN | All four artifacts exist, all four well-formed, no errors in session pass |
| YELLOW | All four artifacts exist but at least one has issues, OR session pass shows non-fatal errors |
| RED | One or more artifacts missing, OR a fatal error stopped the run |

## Step 7: Present Summary & Seek Guidance

After writing `summary.md`, print the report (or a tightened version) back to the user and ask:

1. Should I open the relevant artifact(s) so you can inspect the issues yourself?
2. Should I diff this run against another `{model-name}` run for the same problem? (e.g. `claude-opus-4-7` vs `claude-sonnet-4-6`)
3. Anything else you want me to dig into from the session JSONL files (e.g. specific tool errors, specific time window)?

Do not modify any artifact, re-run `/swe`, open issues, or commit anything unless the user explicitly authorizes it as a separate request.

---

## Important Guidelines

### Read-Only Posture
- This skill is **strictly read-only** with respect to the artifact folder and the session JSONL files. Never edit them.
- The only file the skill writes is `summary.md` in the artifacts folder (or the `--out` override).

### Hard Stops
1. **Do not guess inputs.** If `--repo`, `--problem`, or `--model` are missing, ask.
2. **Do not hardcode `/home/ubuntu/...`.** Use `git rev-parse --show-toplevel` and `$HOME`.
3. **Do not modify the artifact files.** Reading them is the whole point.
4. **Do not implement code, run tests, or open PRs.** Out of scope.

### Session JSONL Caveats
- Token counts come from `message.usage`. Only `assistant` events carry it. User events do not.
- A single session may span multiple models if `/model` was used mid-run; group totals by `message.model`.
- `cache_creation_input_tokens` and `cache_read_input_tokens` are real billable tokens; include them in the totals.
- The `web_search_requests` / `web_fetch_requests` under `usage.server_tool_use` are billable separately - call them out if non-zero.
- **Important:** When the session was run from a subdirectory, the session directory may be encoded differently (e.g. `-home-ubuntu-repos-sample-claude-code-multi-model-bedrock` instead of `-home-ubuntu-repos-sample-claude-code-multi-model`). Always check shadow directories.

### Heuristics, Not Verdicts
The "well-formed" checks are heuristics. If a heuristic fails, report the failure transparently rather than declaring the artifact bad. The user is the final judge.

## Example Usage

User: "/summarize --repo mcp-gateway-registry --problem remove-faiss --model claude-opus-4-7"

1. Resolve `$ART_DIR=benchmarks/swe-benchmark-data/mcp-gateway-registry/remove-faiss/claude-opus-4-7/`
2. Confirm `$ART_DIR` exists
3. Artifact pass: stat each of the four expected files; record sizes, line counts, well-formed checks, and any TODO/error snippets
4. Resolve `$SESSIONS_DIR=$HOME/.claude/projects/-home-ubuntu-repos-sample-claude-code-multi-model/`
5. If no matching sessions found, also check `$HOME/.claude/projects/-home-ubuntu-repos-sample-claude-code-multi-model-*/`
6. Session pass: walk every `*.jsonl` whose timestamps overlap the artifact mtimes; aggregate tokens by model, count tool calls, capture errors, extract themes
7. Write `$ART_DIR/summary.md`
8. Print the report and ask whether to diff against another model

---

## Constraints

- **Read-only over artifacts and JSONL** - the only output write is `summary.md`.
- **No execution** of the cloned `repo/` tree.
- **No emojis, clever code, or em-dashes** in any output.
- **Naming**: always "Amazon Bedrock" (never "AWS Bedrock").
