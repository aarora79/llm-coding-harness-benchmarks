# Testing Plan: CONTRIBUTING.md Guide

*Created: 2026-07-23*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This change adds a single static Markdown file (`CONTRIBUTING.md`) at the repository root. There is no code, service, endpoint, or CLI, so verification consists of confirming the file exists, is well-formed GitHub-flavored Markdown, contains the three required sections, links resolve, and no other file was modified. All checks below are copy-pasteable shell commands plus a manual GitHub-render check.

### Prerequisites
- [ ] A local checkout of the repository at the branch containing the change.
- [ ] `git` available on the PATH.
- [ ] Optional: a Markdown linter (`markdownlint-cli` via `npx`) and a link checker for the deeper checks in Section 3. These are optional conveniences, not required by the design.

### Shared Variables
```bash
# Path to the repository checkout under test. Adjust to your local clone.
export REPO_DIR="/tmp/swe-b0qqf_ay/Hello-World"
export GUIDE="$REPO_DIR/CONTRIBUTING.md"
```

## 1. Functional Tests

### 1.1 curl / HTTP Tests
**Not Applicable** - this change adds no HTTP endpoint. The file is served only by GitHub's static rendering, which is exercised by the manual render check in Section 3.1.

### 1.2 CLI Tests
**Not Applicable** - this change adds no CLI command. The shell checks below verify a static file, not a program under test.

### 1.3 File-Presence and Content Checks (static-artifact equivalent of functional tests)

These are the primary functional verifications for a documentation-only change.

**Check 1 - the file exists at the repository root:**
```bash
test -f "$GUIDE" && echo "PASS: CONTRIBUTING.md exists" || echo "FAIL: CONTRIBUTING.md missing"
```
Expected output: `PASS: CONTRIBUTING.md exists`

**Check 2 - it uses the .md extension (so GitHub renders it):**
```bash
ls "$REPO_DIR"/CONTRIBUTING.md >/dev/null 2>&1 && echo "PASS: .md extension" || echo "FAIL: wrong or missing extension"
```
Expected output: `PASS: .md extension`

**Check 3 - the three required sections are present.** Assert on the section headings from the issue's acceptance criteria:
```bash
grep -qiE '^##[[:space:]]+How to file an issue'        "$GUIDE" && echo "PASS: issue section"        || echo "FAIL: issue section missing"
grep -qiE '^##[[:space:]]+How to open a pull request'  "$GUIDE" && echo "PASS: pull request section" || echo "FAIL: pull request section missing"
grep -qiE '^##[[:space:]]+(What we expect|Expectations|Contribution expectations)' "$GUIDE" && echo "PASS: expectations section" || echo "FAIL: expectations section missing"
```
Expected output: three `PASS` lines.

**Check 4 - the guide is short (target under ~150 lines):**
```bash
lines=$(wc -l < "$GUIDE"); echo "line count: $lines"; [ "$lines" -le 150 ] && echo "PASS: within length target" || echo "WARN: longer than target"
```
Expected output: a line count at or under 150, then `PASS: within length target`.

**Check 5 - a single top-level H1 title exists (clean outline):**
```bash
h1=$(grep -cE '^#[[:space:]]' "$GUIDE"); echo "H1 count: $h1"; [ "$h1" -eq 1 ] && echo "PASS: exactly one H1" || echo "FAIL: expected exactly one H1"
```
Expected output: `H1 count: 1` then `PASS: exactly one H1`.

## 2. Backwards Compatibility Tests

The change is purely additive - one new file, no modifications - so "backwards compatibility" means proving nothing else changed.

**Check 6 - only CONTRIBUTING.md is added; no tracked file is modified or deleted.** Run from within the checkout on the change branch:
```bash
cd "$REPO_DIR"
echo "=== staged/working changes vs HEAD~1 (or the base branch) ==="
# If the change is committed, compare against the previous commit:
git diff --name-status HEAD~1 HEAD
# Expected: a single line ->  A  CONTRIBUTING.md
```
Expected output: exactly one line, `A\tCONTRIBUTING.md`. No `M` (modified) or `D` (deleted) entries, in particular no change to `README`.

**Check 7 - the README is byte-for-byte unchanged:**
```bash
cd "$REPO_DIR"
git diff HEAD~1 HEAD -- README | head -20
# Expected: no output (README not part of the diff)
echo "README content:"; cat README
```
Expected: empty diff for `README`; `README` still contains `Hello World!`.

## 3. UX Tests

The only UX surface is the rendered Markdown as a contributor reads it.

### 3.1 GitHub render check (manual)
1. Push the branch and open the file on GitHub (or use the "Preview" tab in the GitHub editor / a local Markdown previewer).
2. Confirm:
   - [ ] The H1 title renders as the page heading and the `##` sections appear in GitHub's auto-generated outline.
   - [ ] The numbered steps under "How to file an issue" and "How to open a pull request" render as ordered lists, with sub-bullets correctly indented under their parent step (no broken/flattened nesting).
   - [ ] The `[issues]` reference link resolves to the repository's Issues tab.
   - [ ] There are no emojis, no em-dashes, and no broken Markdown artifacts.

### 3.2 Optional automated Markdown lint
```bash
# Optional: catches malformed lists, headings, and inconsistent nesting.
npx --yes markdownlint-cli "$GUIDE" && echo "PASS: markdownlint clean" || echo "REVIEW: markdownlint findings above"
```
Expected: no errors, or only style-preference findings that do not affect rendering.

### 3.3 Readability spot-check (manual)
- [ ] A first-time contributor can answer "how do I report a bug?" and "how do I propose a change?" after one read.
- [ ] Language is welcoming and free of unexplained jargon.

## 4. Deployment Surface Tests
**Not Applicable** - this change adds no configuration parameter on any surface. There is no Docker, ECS/Terraform, or Helm/EKS wiring. "Deployment" is simply merging the file to the default branch (`master`), after which GitHub renders it and links it from the issue/PR flows automatically. The relevant post-merge verification (the `../../issues` link resolving on the default branch) is covered by the UX render check in Section 3.1.

## 5. End-to-End API Tests
**Not Applicable** - there is no multi-endpoint or multi-service workflow. The closest end-to-end behavior is GitHub surfacing the guide in the issue/PR creation flow; verify it manually once merged:
- [ ] On GitHub, click "New issue" and confirm the contributing-guidelines link appears (GitHub shows this when a root-level `CONTRIBUTING.md` exists).
- [ ] Start a new pull request and confirm the "guidelines for contributing" banner references `CONTRIBUTING.md`.

## 6. Test Execution Checklist
- [ ] Section 1 (Functional / static-file checks) passes: file exists, `.md` extension, three required sections, length target, single H1.
- [ ] Section 2 (Backwards Compat) verified: diff shows only `A CONTRIBUTING.md`; `README` unchanged.
- [ ] Section 3 (UX) verified: GitHub render is clean; lists and links resolve; no emojis or em-dashes.
- [ ] Section 4 (Deployment) marked Not Applicable (no config surface).
- [ ] Section 5 (E2E) verified post-merge: GitHub links the guide from the issue/PR flows.
- [ ] Unit tests under `tests/unit/` - **Not Applicable** (no code).
- [ ] Integration tests under `tests/integration/` - **Not Applicable** (no code).
- [ ] `uv run pytest tests/` - **Not Applicable** (no Python code or test suite in this repository).
