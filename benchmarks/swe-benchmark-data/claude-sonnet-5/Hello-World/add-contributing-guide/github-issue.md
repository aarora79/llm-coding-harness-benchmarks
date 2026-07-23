# GitHub Issue: Add a CONTRIBUTING.md guide

## Title
Add CONTRIBUTING.md with issue and pull request guidance

## Labels
- documentation

## Description

### Problem Statement
The repository currently contains only a `README` file and provides no guidance for people who want to contribute. New and returning contributors have no documented path for filing an issue, opening a pull request, or understanding what a reviewer will expect from a contribution. This creates friction and inconsistent contributions, and it is standard open-source practice to document this in a `CONTRIBUTING.md` at the repository root.

### Proposed Solution
Add a single `CONTRIBUTING.md` file at the repository root that covers:
- How to file an issue (where to look first, what information to include).
- How to open a pull request (fork/branch/commit/PR steps).
- Basic expectations for a contribution (scope, commit message clarity, keeping changes minimal, linking related issues).

This is a documentation-only change. No source code, build system, or test suite is affected.

### User Stories
- As a new contributor, I want a `CONTRIBUTING.md` so that I know how to report a bug or propose a change without guessing at the process.
- As a returning contributor, I want a documented pull request workflow so that my submissions match maintainer expectations on the first try.
- As a maintainer, I want a written set of contribution expectations so that I can point contributors to a single reference instead of repeating guidance in every issue or PR.

### Acceptance Criteria
- [ ] A `CONTRIBUTING.md` file exists at the repository root.
- [ ] The file explains how to file an issue, including what information to include.
- [ ] The file explains how to open a pull request, from forking through submission.
- [ ] The file states basic expectations for a contribution (e.g., one change per PR, clear commit messages, linking related issues).
- [ ] The file is plain Markdown, renders correctly on GitHub, and contains no broken links.
- [ ] No existing file (`README`) is modified or removed.

### Out of Scope
- Any code, build tooling, CI, or automated checks.
- A Code of Conduct file (`CODE_OF_CONDUCT.md`) or issue/PR templates.
- Governance, licensing, or security-disclosure policy documentation.
- Enforcement of the guidance (e.g., PR linting bots).

### Dependencies
- None. This is a standalone documentation addition.

### Related Issues
- None.
