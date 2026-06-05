---
name: swe
description: "End-to-end Software Engineering skill that benchmarks how well a given LLM can take a problem from idea to a complete design package. Creates structured documentation under benchmarks/swe-benchmark-data/{repo-name}/{problem-name}/{model-name}/ with a GitHub issue spec, low-level design (LLD), expert review, and testing plan, so multiple models can be compared on the same problem within the same repository. The skill stops at design and review - it does NOT implement the change."
license: Apache-2.0
metadata:
  author: Amit Arora
  version: "2.1"
---

# Software Engineering (SWE) Skill

Use this skill when the user wants to evaluate how a particular LLM performs on a software-engineering problem. Every run is treated as a benchmark: the problem is the input, the model is the contestant, and the artifacts (`github-issue.md`, `lld.md`, `review.md`, `testing.md`) are the output that can be compared across models.

**This skill stops at design.** It does not write or modify production code, run tests, open PRs, or commit anything. The deliverable is the four artifact files described below.

## Workflow

1. **Gather Requirements** - Detect the active model and confirm it; ask for the GitHub URL; ask for tag-vs-main; confirm the task; locate or clone the target repo with user approval
2. **Quick Codebase Review** - Explore the codebase to understand structure
3. **Create Benchmark Folder** - Create `benchmarks/swe-benchmark-data/{repo-name}/{problem-name}/{model-name}/` directory
4. **Write GitHub Issue** - Create `github-issue.md` with the issue specification
5. **Deep Codebase Analysis** - Thoroughly explore relevant code
6. **Write Low-Level Design** - Create `lld.md` with technical details
7. **Expert Review** - Create `review.md` with multi-persona feedback
8. **Write Testing Plan** - Create `testing.md` with functional, backwards-compat, UX, deployment, and E2E tests
9. **Present Summary & Seek Guidance** - Present the four artifacts and ask for direction

---

## Step 1: Gather Requirements

**NEVER guess the repo URL, the tag, or the task description.** All of them must come from the user. Do not infer them from session context, the current working directory, recent files, or memory.

The skill MUST do four things in this exact order at the very start of every run, even when some values were passed in as parameters. Confirm what was passed; ask for what is missing. Do not move past Step 1 until all four have an explicit user-confirmed answer.

### 1.0 Announce and confirm the model first

Before anything else, the skill must figure out which model is currently driving this session and tell the user, then ask the user to confirm or override.

How to figure it out:

- Look at the system context for the active model id (e.g. a line like "You are powered by the model named Opus 4.7 (1M context). The exact model ID is us.anthropic.claude-opus-4-7[1m].").
- Pick the canonical model name from the ID. Strip vendor/region prefixes (`us.anthropic.`), drop bracketed context-window suffixes (`[1m]`), and use kebab-case. Examples: `us.anthropic.claude-opus-4-7[1m]` -> `claude-opus-4-7`; `claude-sonnet-4-6` -> `claude-sonnet-4-6`; `claude-haiku-4-5-20251001` -> `claude-haiku-4-5`.
- If you cannot determine the model from system context, do not invent one - tell the user you could not detect it and ask.

Then announce and confirm:

> I am using **`{detected-model-name}`** for this run. This will be used as the `{model-name}` folder under the benchmark directory.
>
> Is that correct, or would you like to use a different name? (Reply with the name in kebab-case, e.g. `claude-opus-4-7`, `claude-sonnet-4-6`, `gpt-5`.)

Wait for confirmation. Only after the user confirms (or supplies an override) lock in `{model-name}` and continue. Do **not** ask for the model again later in Step 1.5; remove that question from the remaining clarifications since it has already been settled here.

### 1.1 Question 1 - GitHub repo URL

**Always ask first.** This is the canonical identifier of the target repository; everything else (folder names, clone commands, README rows) is derived from it.

> **Q1.** What is the GitHub URL of the repository you want to benchmark?
> Example: `https://github.com/agentic-community/mcp-gateway-registry`

If the user provided `--repo <url>` (or any equivalent param), echo the URL back and ask for confirmation rather than skipping the question.

From the URL, derive:
- `{repo-name}` = the basename of the URL with `.git` stripped (kebab-case as-is, e.g. `mcp-gateway-registry`).
- `{owner}` = the path segment before the repo name (used only in messages, never inferred for anything else).

State the derived `{repo-name}` back to the user before continuing.

### 1.2 Question 2 - Git tag or main

**Ask second, only after Q1 is answered.**

> **Q2.** Which version should I check out?
> 1. A specific git tag (e.g. `1.24.4`) - recommended for reproducible benchmarks.
> 2. `main` - latest commit on the default branch.

Record the answer as `{ref}`. If the user picked a tag, `{ref}` is the tag name. If the user picked main, `{ref}` is `main`.

### 1.3 Question 3 - Confirm the task

**Ask third, only after Q1 and Q2 are answered.**

> **Q3.** Is this your task?
> "{the task description the user originally provided, or a placeholder if none was provided}"

If the user passed `--task <description>`, repeat it verbatim and ask for yes/no confirmation. If they did not provide a task at all, ask them to describe it now and then confirm:

> What task should the model attempt against this repo? Example: "remove FAISS from the codebase and documentation".

Once the user confirms the task wording, derive a kebab-case `{problem-name}` from it (e.g. "remove FAISS from the codebase" -> `remove-faiss`) and **confirm the derived name with the user one more time** before creating any folders.

### 1.4 Locate or Clone the Target Repository

Now that `{repo-name}`, `{ref}`, and `{problem-name}` are settled, resolve paths. All paths are expressed relative to the repository root via `git rev-parse --show-toplevel` - never hardcode absolute paths. Let:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
BENCH_DIR="$REPO_ROOT/benchmarks/swe-benchmark-data"
```

1. **Check locally first.** Look for the cloned source at `$BENCH_DIR/{repo-name}/repo/`. If it exists and is a valid git checkout, confirm the ref:

   ```bash
   git -C "$BENCH_DIR/{repo-name}/repo" describe --tags --exact-match  # for tags
   # or
   git -C "$BENCH_DIR/{repo-name}/repo" rev-parse --abbrev-ref HEAD     # for main
   ```

   If the local checkout is at the wrong ref, tell the user and ask whether to re-clone (Option A: delete `repo/` and re-clone at `{ref}`) or keep the existing checkout.

2. **If the path does not exist, announce the clone command and ask for approval before running it.** Use `$BENCH_DIR` (not an absolute path):

   > I will run the following clone command. Approve to proceed?
   > ```
   > REPO_ROOT="$(git rev-parse --show-toplevel)"
   > BENCH_DIR="$REPO_ROOT/benchmarks/swe-benchmark-data"
   > mkdir -p "$BENCH_DIR/{repo-name}"
   > git -C "$BENCH_DIR/{repo-name}" clone --branch {ref} --depth 1 {url} repo
   > ```

   Only run the clone after the user approves. After cloning, append a row to the benchmark README's "Benchmark Repositories" section if one is not already there (URL, ref, local path, artifact path).

### 1.5 Remaining Clarifying Questions

Once the model, URL, ref, task, and the local checkout are settled, gather the rest. Do **not** re-ask which model is being benchmarked - that was already settled in Step 1.0.

1. What problem does this solve?
2. Who are the users/consumers?
3. Are there any constraints (language, framework, environment, deadlines)?
4. What is the expected scope (small/medium/large)?

## Step 2: Quick Codebase Review

Before creating any design documents, perform a quick exploration of the codebase to understand:

1. **Project Structure** - top-level layout, source roots, config files
2. **Related Components** - existing features similar to the one being designed
3. **Entry Points** - main scripts, CLIs, or app entrypoints

This quick review takes 5-10 minutes and helps you ask better clarifying questions and avoid proposing designs that conflict with existing architecture.

## Step 3: Create Benchmark Folder

All artifacts live under a top-level `benchmarks/` directory. Within it, every run gets its own `{repo-name}/{problem-name}/{model-name}/` subfolder so multiple models can be benchmarked on the same problem within a given repository and compared side-by-side.

The target repository's source code is **not** stored here. It is cloned locally at a specific tag by each contributor following the instructions in `benchmarks/swe-benchmark-data/README.md`, into a `repo/` subdirectory under `{repo-name}/`. The `repo/` checkout is gitignored so it is never committed.

### Folder Structure

```
benchmarks/
└── swe-benchmark-data/
    ├── README.md                   # Lists target repos, tags, and tasks to benchmark
    └── {repo-name}/
        ├── repo/                   # Cloned source (gitignored - cloned by contributor)
        ├── {problem-name}/
        │   └── {model-name}/
        │       ├── github-issue.md      # GitHub issue specification
        │       ├── lld.md               # Low-level design document
        │       ├── review.md            # Expert review document
        │       └── testing.md           # Testing plan (functional, backwards-compat, UX, deployment, E2E)
        └── {next-problem-name}/
            └── ...
```

Conventions:

- Use kebab-case for `{repo-name}` and match the upstream repository name (e.g. `mcp-gateway-registry`). The list of supported `{repo-name}` values, their upstream URLs, and the tag to clone are all defined in `benchmarks/swe-benchmark-data/README.md`.
- Source code for the target lives at `benchmarks/swe-benchmark-data/{repo-name}/repo/`. If that path does not exist, stop and direct the user to the setup instructions in the benchmark README - do not run `/swe` against a repo that has not been cloned.
- Use kebab-case for `{problem-name}` (e.g. `remove-faiss`, `remove-efs-from-terraform-aws-ecs`). Prefer the exact name listed in the benchmark README's task table.
- Use kebab-case for `{model-name}` and prefer the canonical model id (e.g. `claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5`, `gpt-5`).
- The same `{repo-name}/{problem-name}` folder will accumulate one subfolder per model that has attempted it - do not delete sibling model folders.

### Pre-existing Artifacts: Confirm Before Overwriting

Before writing any artifact, check whether the target `{model-name}/` folder already contains any of `github-issue.md`, `lld.md`, `review.md`, or `testing.md`. If **one or more** of them exist, **stop and ask the user** what to do. Never silently overwrite.

Concretely:

1. List which of the four files already exist (with size and last-modified time, so the user can see they're real prior work).
2. Present the choices clearly and wait for the user's answer:

   > The following artifacts already exist at `benchmarks/swe-benchmark-data/{repo}/{problem}/{model}/`:
   > - `lld.md` (12.6 KB, modified 2026-06-04)
   > - `review.md` (4.1 KB, modified 2026-06-04)
   >
   > How would you like to proceed?
   > 1. **Delete all four files first**, then run a clean `/swe` pass (recommended for a fresh benchmark).
   > 2. **Overwrite in place** as each artifact is regenerated (existing files get replaced one by one).
   > 3. **Append a suffix** to the model folder (e.g. `claude-opus-4-7-run2/`) and write the new run there, leaving the prior run intact.
   > 4. **Abort** - keep everything as-is and exit the skill.

3. Only proceed once the user picks an option:
   - **Option 1 (delete first):** remove the four files (and only those four; do not touch sibling folders or the cloned `repo/`). Print the `rm` commands you ran.
   - **Option 2 (overwrite in place):** continue, overwriting each artifact when its step writes the file.
   - **Option 3 (append suffix):** ask the user to confirm the suffix, then create `{model-name}-{suffix}/` and treat that as the new target folder for the rest of the run.
   - **Option 4 (abort):** stop the skill cleanly, do not modify anything, do not create empty folders.

Even if all four files are present and the user picks option 2, do not "merge" with prior content - each new step writes the new artifact end-to-end. The prior file is replaced, not edited in place.

If `benchmarks/swe-benchmark-data/{repo-name}/{problem-name}/{model-name}/` exists but is **empty**, no confirmation is needed; proceed normally.

Example for the same problem solved by two models inside the same repository:

```
benchmarks/swe-benchmark-data/mcp-gateway-registry/remove-faiss/
├── claude-opus-4-7/
│   ├── github-issue.md
│   ├── lld.md
│   ├── review.md
│   └── testing.md
└── claude-sonnet-4-6/
    ├── github-issue.md
    ├── lld.md
    ├── review.md
    └── testing.md
```

## Step 4: Write GitHub Issue (github-issue.md)

Create a comprehensive GitHub issue specification. This is the artifact that would be filed against the upstream repo to track the task.

### Template

```markdown
# GitHub Issue: {Feature / Task Title}

## Title
{concise title for the issue}

## Labels
- {appropriate labels: enhancement, bug, refactor, infra, docs, etc.}

## Description

### Problem Statement
{What problem does this solve? Why is it needed?}

### Proposed Solution
{High-level description of the solution}

### User Stories
- As a {user type}, I want to {action} so that {benefit}

### Acceptance Criteria
- [ ] {Criterion 1}
- [ ] {Criterion 2}

### Out of Scope
- {What is explicitly NOT included}

### Dependencies
- {Any dependent issues or external dependencies}

### Related Issues
- #{issue numbers if any}
```

## Step 5: Deep Codebase Analysis

**CRITICAL:** Before writing the LLD, you MUST thoroughly understand all relevant code in the cloned `repo/`. A design that ignores existing patterns will fail when an implementer picks it up.

### What to Analyze

1. **Existing Models and Data Structures** - Pydantic models, dataclasses, schemas
2. **Service / Business Logic Patterns** - how logic is organized, error handling, logging, caching
3. **Route / CLI / Entrypoint Patterns** - request/response shapes, argparse layouts
4. **Storage / IO Layer** - persistence, file IO, network calls
5. **Configuration and Constants** - env vars, settings classes, feature flags
6. **Existing Tests** - testing patterns, fixtures, mocking conventions

### How to Analyze

Use the Agent tool with `subagent_type=Explore` for thorough investigation. Read actual code, not just file names. Note TODOs and known issues.

### Document Your Findings

Capture in your LLD:
- Key files reviewed
- Patterns identified
- Integration points for the new change
- Constraints or limitations discovered

## Step 6: Write Low-Level Design (lld.md)

Create a detailed technical design document. This is the most critical document - it should contain enough detail for an entry-level developer to implement the change later.

```markdown
# Low-Level Design: {Feature Name}

*Created: {date}*
*Author: Claude*
*Status: Draft*

## Table of Contents
1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Data Models](#data-models)
5. [API / CLI Design](#api--cli-design)
6. [Configuration Parameters](#configuration-parameters)
7. [New Dependencies](#new-dependencies)
8. [Implementation Details](#implementation-details)
9. [Observability](#observability)
10. [Scaling Considerations](#scaling-considerations)
11. [File Changes](#file-changes)
12. [Testing Strategy](#testing-strategy)
13. [Alternatives Considered](#alternatives-considered)
14. [Rollout Plan](#rollout-plan)

## Overview
### Problem Statement
{Detailed problem description}

### Goals
- {Goal 1}

### Non-Goals
- {What this design explicitly does NOT address}

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `{path}` | {Description} | {How it relates} |

### Existing Patterns Identified
1. **Pattern Name**: {Description}
   - Files: `{file1}`, `{file2}`
   - How a future implementer should follow this: {How}

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| {Existing component} | {Extends/Uses/Depends on} | {Specific details} |

### Constraints and Limitations Discovered
- {Constraint}: {How it affects the design}

## Architecture

### System Context Diagram
{ASCII diagram showing how this fits into the overall system}

### Sequence Diagram
{Show the flow of requests/data}

### Component Diagram
{Show internal components and their relationships}

## Data Models

### New Models
```python
class NewModel(BaseModel):
    """Description."""

    field_name: str = Field(
        ...,
        description="What this field represents",
        min_length=1,
        max_length=100
    )
```

### Model Changes
{Changes to existing models}

## API / CLI Design

### New Endpoints / Commands
**Description:** {What it does}

**Request / Invocation:**
```bash
uv run python -m {module} --param value
```

**Expected Response / Output:**
```json
{ "id": "123", "status": "success" }
```

**Error Cases:**
- 400 / nonzero exit: {when}

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `FEATURE_ENABLED` | bool | `true` | No | Enable/disable the feature |

### Settings / Config Class Updates
```python
feature_enabled: bool = Field(
    default=True,
    description="Enable/disable feature X"
)
```

### Deployment Surface Checklist
List every surface where this parameter must appear (`.env.example`, `docker-compose.yml`, Terraform vars, Helm values, etc.) so an implementer can tick them off later.

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `package-name` | `latest` | {Why needed} |

If no new dependencies are required, explicitly state: "This change uses only existing dependencies."

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: {First Step}
**File:** `path/to/file.py`
**Lines:** {approximate line numbers or "new file"}

```python
def new_function(
    param1: str,
    param2: int
) -> dict:
    """Description."""
    if not param1:
        raise ValueError("param1 is required")
    return {"status": "success", "data": process(param1, param2)}
```

### Error Handling
{How errors should be handled}

### Logging
{What should be logged and at what level}

## Observability
### Tracing / Metrics / Logging Points
{Spans, metrics, key log events}

## Scaling Considerations
- Current load assumptions
- Horizontal scaling
- Bottlenecks
- Caching strategy

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `src/feature/new_module.py` | {What it does} |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `src/main.py` | ~50 | {What changes} |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~{X} |
| New tests | ~{X} |
| Modified code | ~{X} |
| **Total** | **~{X}** |

## Testing Strategy
{Pointer to testing.md - the full plan lives there}

## Alternatives Considered

### Alternative 1: {Name}
**Description:** ...
**Pros / Cons:** ...
**Why Rejected:** ...

### Comparison Matrix

| Criteria | Chosen | Alt 1 | Alt 2 |
|----------|--------|-------|-------|
| Complexity | Low | Med | High |

## Rollout Plan
- Phase 1: Implementation (out of scope for this skill)
- Phase 2: Testing
- Phase 3: Deployment

## Open Questions
- {Unresolved}

## References
- {Docs / similar implementations}
```

## Step 7: Expert Review (review.md)

Create a review document with feedback from multiple expert personas:

| Role | Reviewer | Focus |
|------|----------|-------|
| Frontend Engineer | Pixel | UI/UX, components, state, API integration |
| Backend Engineer | Byte | API design, data models, business logic, performance |
| SRE/DevOps Engineer | Circuit | Deployment, monitoring, scaling, infrastructure |
| Security Engineer | Cipher | AuthN/AuthZ, validation, OWASP, data protection |
| SMTS (Overall) | Sage | Architecture, code quality, maintainability |

For each reviewer, capture:
- **Strengths** observed in the design
- **Concerns** identified
- **New libraries / infra dependencies** required (with justification)
- **Better alternatives considered**
- **Recommendations**
- **Questions for author**
- **Verdict:** APPROVED / APPROVED WITH CHANGES / NEEDS REVISION

End with a Review Summary table and Next Steps. Reviews must be realistic, identifying actual issues rather than just praise.

## Step 8: Write Testing Plan (testing.md)

Create a comprehensive testing plan with **executable, copy-pasteable tests** covering every externally observable change. A future implementer should be able to walk through this document and verify the change works end-to-end without inventing test cases.

### When Each Test Category Applies

| Category | Include When |
|----------|--------------|
| Functional Tests (CLI / curl) | Change adds/modifies any HTTP endpoint or CLI command |
| Backwards Compatibility Tests | Change touches an existing endpoint, schema, CLI command, default, or model |
| UX Tests | Change adds/modifies any UI surface (web UI, CLI output, error messages) |
| Deployment Surface Tests (Docker, ECS, Helm) | Change adds/modifies any config parameter on any surface |
| E2E Tests | Change adds a workflow that spans multiple endpoints or services |

Always include the heading for each category. If a category does not apply, replace the body with: `**Not Applicable** - {one-line justification}`.

### Testing Plan Template (high level)

```markdown
# Testing Plan: {Feature Name}

*Created: {date}*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview
### Scope of Testing
{1-2 sentences describing what is being tested and why}

### Prerequisites
- [ ] {Service running}
- [ ] {Auth tokens / fixtures available}

### Shared Variables
```bash
export REGISTRY_URL="http://localhost"
export ACCESS_TOKEN=$(jq -r '.access_token' .oauth-tokens/ingress.json)
```

## 1. Functional Tests
### 1.1 curl / HTTP Tests
{One subsection per new or modified endpoint with command, expected status, expected response, assertions, and a negative case}

### 1.2 CLI Tests
{One subsection per new or modified CLI command with exact invocation and expected output}

## 2. Backwards Compatibility Tests
{Pre-change request shapes still accepted; CLI without new flags behaves as before; defaults preserve prior behavior}

## 3. UX Tests
{Web UI flows; CLI output / error message clarity}

## 4. Deployment Surface Tests
### 4.1 Docker wiring
### 4.2 Terraform / ECS wiring
### 4.3 Helm / EKS wiring
### 4.4 Deploy and verify
### 4.5 Rollback verification

## 5. End-to-End API Tests
{Multi-step scenarios that exercise full business workflows}

## 6. Test Execution Checklist
- [ ] Section 1 (Functional) passes
- [ ] Section 2 (Backwards Compat) verified or marked Not Applicable
- [ ] Section 3 (UX) verified or marked Not Applicable
- [ ] Section 4 (Deployment) verified or marked Not Applicable
- [ ] Section 5 (E2E) verified or marked Not Applicable
- [ ] Unit tests added under `tests/unit/`
- [ ] Integration tests added under `tests/integration/`
- [ ] `uv run pytest tests/` passes with no regressions
```

### Guidance for Generating testing.md

1. Make tests copy-pasteable. Match the env var conventions used by existing scripts.
2. Cover every new endpoint and every new CLI command described in the LLD.
3. Anchor deployment tests on concrete files - reference exact Terraform/Helm/Docker file paths.
4. Mark Not Applicable explicitly. Do not silently omit sections.
5. Align with backwards-compat rules. Pre-change shapes must still be tested.
6. Do not invent endpoints or flags. Every URL, flag, and Terraform variable must exist in the LLD or codebase.

## Step 9: Present Summary & Seek Guidance

After producing the four artifacts, present a clear summary to the user. **Do not implement, run tests, push, commit, or open a PR.** This skill ends at delivery of the design package.

```markdown
## Delivery Summary

### Documents Created

| Document | Location | Description |
|----------|----------|-------------|
| GitHub Issue | `benchmarks/swe-benchmark-data/{repo}/{problem}/{model}/github-issue.md` | Issue specification |
| Low-Level Design | `benchmarks/swe-benchmark-data/{repo}/{problem}/{model}/lld.md` | Technical design |
| Expert Review | `benchmarks/swe-benchmark-data/{repo}/{problem}/{model}/review.md` | Multi-persona review |
| Testing Plan | `benchmarks/swe-benchmark-data/{repo}/{problem}/{model}/testing.md` | All test categories |

### Review Verdicts

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | {verdict} | {count} | {summary} |
| Backend (Byte) | {verdict} | {count} | {summary} |
| SRE (Circuit) | {verdict} | {count} | {summary} |
| Security (Cipher) | {verdict} | {count} | {summary} |
| SMTS (Sage) | {verdict} | {count} | {summary} |

### Configuration Parameters Proposed

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `PARAM_NAME` | type | value | {description} |

### New Dependencies Proposed

| Package | Type | Required By |
|---------|------|-------------|
| `package-name` | Python | Backend |

### Estimated Effort (for a future implementer)

| Category | Lines of Code |
|----------|---------------|
| New code | ~{X} |
| Tests | ~{X} |
| Modified | ~{X} |
| **Total** | **~{X}** |
```

### Seeking Guidance

After the summary, ask the user:

1. Are there any blockers from the expert review you want me to address by revising the design?
2. Would you like me to refine any artifact (e.g. expand a specific LLD section, add more test cases, tighten the issue spec)?
3. Should I open the GitHub issue against the upstream repo using `github-issue.md`?

Do not implement code, run tests, push, commit, or open a PR until the user explicitly authorizes it as a separate request.

---

## Important Guidelines

### Design Principles
- Favor simple designs over unnecessary complexity
- Prefer straightforward code over clever solutions
- Design for maintainability by entry-level developers
- Add observability from the start, not as an afterthought

### Documentation Quality
1. **Be Thorough**: The LLD should be detailed enough that someone unfamiliar with the codebase can implement it
2. **Use Diagrams**: ASCII diagrams help visualize the design
3. **Include Code**: Show actual or pseudo-code for key functions
4. **Specify Files**: Always mention which files to create/modify and approximate line numbers
5. **Consider All Aspects**: Think about error handling, logging, testing, and deployment
6. **Expert Reviews**: Make the reviews realistic - identify actual issues, not just praise

### Hard Stops
1. **Do not implement code.** This skill produces design artifacts only.
2. **Do not run tests, linters, or builds against `repo/`.** Read the code, do not execute it.
3. **Do not commit, push, or open a PR.**
4. **Do not modify the cloned `repo/` tree.** It is gitignored input, not a workspace.

## Example Usage

User: "Run task 1 for mcp-gateway-registry with claude-opus-4-7."

1. Look up task 1 in `benchmarks/swe-benchmark-data/README.md` (`remove-faiss`). Confirm `repo-name = mcp-gateway-registry`, `problem-name = remove-faiss`, `model-name = claude-opus-4-7`. Check that `benchmarks/swe-benchmark-data/mcp-gateway-registry/repo/` exists; if it does not, ask the user for the GitHub URL and tag, announce the clone command, and wait for approval.
2. Quick codebase review of `repo/` to find every FAISS reference (imports, dependencies, configs, docs)
3. Create `benchmarks/swe-benchmark-data/mcp-gateway-registry/remove-faiss/claude-opus-4-7/`
4. Write `github-issue.md` describing the FAISS removal task (problem, scope, acceptance criteria, out-of-scope)
5. Deep code analysis of FAISS usage and the maintained replacement
6. Write `lld.md` covering: files to edit, dependency removals, doc updates, fallback path
7. Write `review.md` with backend, SRE, security, and SMTS verdicts
8. Write `testing.md` with import-removal greps, backwards-compat tests, and a build/test pass plan
9. Present the four-artifact summary and ask whether to refine anything or open the GitHub issue upstream

When the same problem is later run with a different model (e.g. `claude-sonnet-4-6`), repeat the workflow into `benchmarks/swe-benchmark-data/mcp-gateway-registry/remove-faiss/claude-sonnet-4-6/`. The two folders make per-model artifacts directly comparable inside the same repository.

---

## Constraints

- **No implementation.** Stop at the four design artifacts.
- **No execution.** Do not run pytest, ruff, mypy, terraform, docker, or any build/test command against the cloned `repo/`. Reading the code is fine; running it is out of scope.
- **No emojis, clever code, or em-dashes** in any output.
- **Naming**: always "Amazon Bedrock" (never "AWS Bedrock").
- **Best Practices**: design recommendations should follow `CLAUDE.md` (logging, Pydantic, modularity) so that a future implementer's work will pass review.
