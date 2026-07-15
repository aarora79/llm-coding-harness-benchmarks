---
name: implement
description: "Implement code from an LLD spec using the current model. Reads the best LLD for a given problem, implements the code changes against the target repo, produces a unified diff, and captures detailed metrics (tokens, cache hits, wall-clock time, tool calls). Results go under benchmarks/swe-benchmark-data/{repo-name}/{problem-name}/implementations/{model-name}/. Used to compare how well different self-hosted models translate a design into working code."
license: Apache-2.0
metadata:
  author: Prateek Shekhar
  version: "1.0"
---

# Implement Skill

Use this skill when the user wants to benchmark how well the current model can **implement code** from an existing Low-Level Design (LLD) document. The LLD is the spec — the model's job is to produce the actual code changes.

**This skill writes code.** It reads the LLD, explores the target repo, writes the implementation files, and produces a unified diff. It does NOT run tests, deploy, or open PRs.

## Workflow

1. **Gather Inputs** — Detect model, confirm problem, locate LLD and target repo
2. **Read the LLD** — Parse the spec completely before writing any code
3. **Explore Target Repo** — Understand current code structure relevant to the LLD
4. **Implement** — Write all code changes described in the LLD
5. **Generate Diff** — Produce a unified diff of all changes against the base
6. **Capture Metrics** — Record timing, tokens, cache stats
7. **Save Artifacts** — Write everything to the output directory

---

## Step 1: Gather Inputs

### 1.0 Detect and confirm the model

Look at the system context for the active model id. Derive a kebab-case folder name:
- `qwen3.6-35b` → `qwen3.6-35b`
- `qwen3-coder-30b` → `qwen3-coder-30b`
- `us.anthropic.claude-opus-4-6-v1` → `claude-opus-4-6`

Announce:
> Implementing as **`{model-name}`**. Confirm or override.

### 1.1 Confirm the problem

Ask:
> Which problem? (e.g. `ssrf-hardening-outbound-url-validation`)

Or accept if passed as parameter.

### 1.2 Locate the LLD

The LLD source is the **best available** `lld.md` for this problem. Look in:
```
benchmarks/swe-benchmark-data/{repo-name}/{problem-name}/implementations/spec.md
```

If `spec.md` doesn't exist yet, ask the user which model's LLD to use as the spec, then copy it:
```
benchmarks/swe-benchmark-data/{repo-name}/{problem-name}/{source-model}/lld.md
→ benchmarks/swe-benchmark-data/{repo-name}/{problem-name}/implementations/spec.md
```

### 1.3 Locate the target repo

The target repo lives at:
```
benchmarks/swe-benchmark-data/{repo-name}/repo/
```

Confirm it exists and check the git ref (should be clean, on main/tag).

### 1.4 Record start time

Note the wall-clock start time for metrics.

---

## Step 2: Read the LLD

Read `spec.md` in full. Pay attention to:
- **File Changes** section — which files to create/modify
- **Implementation Details** — the actual logic described
- **Data Models** — new types, schemas, interfaces
- **Configuration Parameters** — new config/env vars
- **Dependencies** — new packages needed

Do NOT skip any section. The LLD is your complete specification.

---

## Step 3: Explore Target Repo

Based on the File Changes section of the LLD, read the current state of every file that will be modified. Understand:
- Existing code patterns and style
- Import conventions
- Error handling patterns
- Test file locations and patterns

---

## Step 4: Implement

Work through the LLD's File Changes section systematically:

1. Create new files as specified
2. Modify existing files per the LLD's instructions
3. Follow the repo's existing code style exactly
4. Include all imports, type annotations, error handling described in the LLD
5. Do NOT add anything not in the LLD
6. Do NOT skip anything in the LLD

Write all changes directly into the target repo working tree (we'll diff later).

**Important:** If the LLD references a dependency to install, note it but do NOT run package managers. Just write the code.

---

## Step 5: Generate Diff

After all code is written, generate a unified diff:

```bash
cd benchmarks/swe-benchmark-data/{repo-name}/repo/
git diff > /tmp/implementation.patch
```

Save the patch content. Then **revert** the working tree so the repo stays clean for the next model:

```bash
git checkout -- .
git clean -fd
```

---

## Step 6: Capture Metrics

Record in `metrics.json`:

```json
{
  "model": "{model-name}",
  "problem": "{problem-name}",
  "repo": "{repo-name}",
  "instance_type": "g6e.12xlarge",
  "instance_cost_per_hr": 10.49,
  "wall_clock_seconds": null,
  "task_cost_usd": null,
  "input_tokens": null,
  "output_tokens": null,
  "thinking_tokens": null,
  "cache_read_tokens": null,
  "cache_write_tokens": null,
  "tool_calls": null,
  "files_created": [],
  "files_modified": [],
  "diff_lines_added": null,
  "diff_lines_removed": null,
  "errors": 0,
  "timestamp": "{ISO 8601}"
}
```

**Note:** Token counts and cache stats will be filled in post-run by the metrics extraction script from the session JSONL. Fill in what you can observe directly: wall_clock_seconds, files_created, files_modified, diff stats, errors, timestamp.

---

## Step 7: Save Artifacts

Create the output directory and save all artifacts:

```
benchmarks/swe-benchmark-data/{repo-name}/{problem-name}/implementations/{model-name}/
  diff.patch          ← the unified diff
  metrics.json        ← timing and stats
  implementation.md   ← brief notes on decisions made, deviations from LLD (if any)
```

The `implementation.md` should be SHORT (under 20 lines):
- Files created/modified (list)
- Any places where the LLD was ambiguous and you made a judgment call
- Any parts of the LLD you could not implement and why

---

## Summary Output

After saving artifacts, present:

```
## Implementation Complete

Model: {model-name}
Problem: {problem-name}
Files changed: {N} ({added} added, {modified} modified)
Diff: +{lines_added} -{lines_removed}
Wall clock: {seconds}s
Output: benchmarks/swe-benchmark-data/{repo-name}/{problem-name}/implementations/{model-name}/
```

---

## Rules

1. **Spec is law** — implement exactly what the LLD says, no more, no less
2. **Match repo style** — follow existing patterns for imports, naming, error handling
3. **Clean working tree** — always revert the repo after generating the diff
4. **No test execution** — write test files if the LLD specifies them, but don't run them
5. **No package installs** — note dependencies but don't install them
6. **One model, one run** — each implementation is independent, no knowledge of other models' output
