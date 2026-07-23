# Testing Plan: Add CONTRIBUTING.md

*Created: 2026-07-23*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This change adds a single static Markdown file, `CONTRIBUTING.md`, to the repository root. There is no code, no build system, and no runtime component. Testing is limited to content verification, Markdown rendering checks, and confirming that no existing file is affected.

### Prerequisites
- [ ] A local checkout of the repository with the new `CONTRIBUTING.md` file added at the root.
- [ ] A Markdown previewer available (a code editor's built-in preview, or GitHub itself after pushing to a branch).

### Shared Variables
```bash
export REPO_DIR="/path/to/local/checkout/Hello-World"
export CONTRIBUTING_FILE="$REPO_DIR/CONTRIBUTING.md"
```

## 1. Functional Tests

### 1.1 curl / HTTP Tests
**Not Applicable** - this change adds no HTTP endpoint.

### 1.2 CLI Tests
**Not Applicable** - this change adds no CLI command. The closest equivalent "functional" checks are file-presence and content checks, covered below.

#### 1.2.1 File exists at repository root
```bash
test -f "$CONTRIBUTING_FILE" && echo "PASS: CONTRIBUTING.md exists" || echo "FAIL: CONTRIBUTING.md missing"
```
**Expected:** `PASS: CONTRIBUTING.md exists`

#### 1.2.2 Required sections are present
```bash
for heading in "Reporting Issues" "Submitting Pull Requests" "Contribution Expectations"; do
  if grep -q "$heading" "$CONTRIBUTING_FILE"; then
    echo "PASS: found section '$heading'"
  else
    echo "FAIL: missing section '$heading'"
  fi
done
```
**Expected:** `PASS` for all three headings.

#### 1.2.3 File is non-empty and reasonably sized
```bash
LINE_COUNT=$(wc -l < "$CONTRIBUTING_FILE")
echo "Line count: $LINE_COUNT"
[ "$LINE_COUNT" -ge 20 ] && echo "PASS: file has substantive content" || echo "FAIL: file too short"
```
**Expected:** `PASS: file has substantive content` (skeleton is ~50 lines; anything under 20 suggests missing sections).

**Negative case:**
```bash
grep -q "TODO" "$CONTRIBUTING_FILE" && echo "FAIL: unresolved TODO placeholder found" || echo "PASS: no placeholder text"
```
**Expected:** `PASS: no placeholder text`

## 2. Backwards Compatibility Tests

### 2.1 README is untouched
```bash
git -C "$REPO_DIR" diff --name-only HEAD~1 HEAD | grep -x "README" && echo "FAIL: README was modified" || echo "PASS: README untouched"
```
**Expected:** `PASS: README untouched`

### 2.2 No other existing files changed
```bash
git -C "$REPO_DIR" diff --name-status HEAD~1 HEAD
```
**Expected:** Output shows exactly one line, `A       CONTRIBUTING.md` (added), and nothing else. Any other file listed is a regression.

## 3. UX Tests

### 3.1 Markdown renders correctly on GitHub
Push the branch containing `CONTRIBUTING.md` and open it in the GitHub web UI (or a local Markdown previewer).

**Steps:**
1. Open `CONTRIBUTING.md` in the GitHub file viewer.
2. Confirm headings render as headings (not literal `#` characters).
3. Confirm the numbered PR-workflow list renders as a numbered list.
4. Confirm the bulleted lists render as bullets.
5. Confirm the code fence (if any example commands are shown) renders as a monospace block.

**Expected:** All Markdown elements render as intended with no visible raw syntax (`#`, `-`, `` ``` ``) leaking into the rendered view.

### 3.2 GitHub surfaces the file on issue/PR creation
**Steps:**
1. On GitHub, navigate to the repository's "Issues" tab and click "New issue".
2. Navigate to the "Pull requests" tab and start a new pull request.

**Expected:** GitHub displays a banner or link referencing the repository's contributing guidelines (standard GitHub behavior once a root-level `CONTRIBUTING.md` exists).

### 3.3 No broken links
```bash
grep -oE '\[[^]]+\]\(([^)]+)\)' "$CONTRIBUTING_FILE" || echo "No links found in file"
```
**Expected:** If any links are present, manually follow each one and confirm it resolves (HTTP 200 for external links, or a valid in-repo path for relative links). If the final file contains no links, this check trivially passes.

## 4. Deployment Surface Tests

### 4.1 Docker wiring
**Not Applicable** - no Docker configuration exists in this repository and none is introduced by this change.

### 4.2 Terraform / ECS wiring
**Not Applicable** - no infrastructure-as-code exists in this repository and none is introduced by this change.

### 4.3 Helm / EKS wiring
**Not Applicable** - no Helm charts or Kubernetes manifests exist in this repository and none is introduced by this change.

### 4.4 Deploy and verify
**Not Applicable** - there is no deployment pipeline. Verification is limited to merging the file to the default branch (`master`) and confirming it renders correctly on GitHub (see Section 3).

### 4.5 Rollback verification
```bash
git -C "$REPO_DIR" revert --no-commit HEAD
test -f "$CONTRIBUTING_FILE" && echo "FAIL: file still present after revert" || echo "PASS: revert removes the file cleanly"
git -C "$REPO_DIR" revert --abort
```
**Expected:** `PASS: revert removes the file cleanly`. Confirms that if the content needs correction after merge, a straightforward `git revert` cleanly removes the addition with no side effects on other files.

## 5. End-to-End API Tests
**Not Applicable** - this change does not add a workflow spanning multiple endpoints or services. The end-to-end scenario for a documentation change is the manual walkthrough in Section 3 (a contributor reads the guide, then successfully files an issue or opens a pull request following it).

## 6. Test Execution Checklist
- [ ] Section 1 (Functional) passes - file exists, contains required sections, no placeholder text.
- [ ] Section 2 (Backwards Compatibility) verified - `README` and all other files are untouched.
- [ ] Section 3 (UX) verified - Markdown renders correctly on GitHub; GitHub surfaces the file on issue/PR creation; no broken links.
- [ ] Section 4 (Deployment) marked Not Applicable except 4.5 (rollback), which is verified.
- [ ] Section 5 (E2E) marked Not Applicable with justification.
- [ ] No unit or integration tests are added, since this repository has no test suite and the change introduces no code (`uv run pytest` is not applicable here).
