# GitHub Issue: Add a CONTRIBUTING.md guide

## Title
Add a CONTRIBUTING.md to document how to contribute to this repository

## Labels
- documentation
- good first issue
- enhancement

## Description

### Problem Statement
The repository currently contains only a `README` and offers no guidance for people who want to contribute. Contributors have nowhere to look for how to file an issue, how to open a pull request, or what is expected of a contribution. `CONTRIBUTING.md` is the standard, well-known location that GitHub surfaces automatically (in the issue and pull request creation flows), so its absence means both new and returning contributors are left guessing about the project's process.

### Proposed Solution
Add a short, self-contained `CONTRIBUTING.md` at the repository root written in plain Markdown. It should cover three things clearly and concisely:

1. **How to file an issue** - where to go, what to search for first, and what information to include (steps to reproduce, expected vs. actual behavior).
2. **How to open a pull request** - the fork / branch / commit / push / PR flow, and how to link a PR to an issue.
3. **Basic expectations for a contribution** - keep changes focused, write clear commit messages and PR descriptions, and be respectful in discussions.

The document must be documentation-only. It introduces no build system, tests, code, or tooling.

### User Stories
- As a **new open-source contributor**, I want a single document that tells me how to report a problem, so that I can file a useful issue without guessing the project's conventions.
- As a **returning contributor**, I want a clear description of the pull request flow and contribution expectations, so that my changes are easy to review and merge.
- As a **maintainer**, I want contributors to follow a documented process, so that issues and pull requests arrive with consistent, actionable information.

### Acceptance Criteria
- [ ] A new file `CONTRIBUTING.md` exists at the repository root.
- [ ] The file is valid, well-formed GitHub-flavored Markdown that renders cleanly on GitHub.
- [ ] It contains a section explaining **how to file an issue**.
- [ ] It contains a section explaining **how to open a pull request** (fork, branch, commit, push, PR).
- [ ] It contains a section describing **basic expectations** for a contribution.
- [ ] The guide is short (roughly one screen; target under ~150 lines) and readable by a first-time contributor.
- [ ] No code, build configuration, tests, or dependencies are added or modified.
- [ ] The existing `README` is left unchanged (an optional one-line link to the guide is out of scope for this issue).

### Out of Scope
- Issue and pull request templates under `.github/` (`ISSUE_TEMPLATE`, `PULL_REQUEST_TEMPLATE.md`).
- A `CODE_OF_CONDUCT.md`, `SECURITY.md`, or license file.
- Any CI, linting, or automated Markdown validation.
- Editing the `README` or any other existing file.
- Any language-, framework-, or environment-specific contribution instructions (this repository has no code to build or test).

### Dependencies
- None. This is a single, standalone documentation file.

### Related Issues
- None.
