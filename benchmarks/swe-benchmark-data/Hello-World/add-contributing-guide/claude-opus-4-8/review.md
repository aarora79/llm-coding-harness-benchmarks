# Expert Review: CONTRIBUTING.md Guide

*Created: 2026-07-23*
*Reviewers: Pixel (Frontend), Byte (Backend), Circuit (SRE/DevOps), Cipher (Security), Sage (SMTS)*
*Artifacts reviewed: `./github-issue.md`, `./lld.md`*

---

## Pixel - Frontend Engineer

**Focus:** UI/UX, components, state, API integration.

For this change, the "UI" is the rendered Markdown as GitHub displays it, and the reader is the human contributor.

### Strengths
- Clean, conventional heading hierarchy (`#` title, `##` sections) produces a readable auto-generated outline on GitHub.
- Numbered steps for the issue and pull request flows are scannable and match how contributors actually work.
- Reference-style relative link (`../../issues`) renders as a working link without hardcoding a repository URL.

### Concerns
- Ordered lists that contain multi-line sub-bullets can render with inconsistent indentation on GitHub if the continuation lines are not indented correctly. The draft indents them properly, but this is the single most common Markdown rendering bug, so it must be verified in the live preview.
- No explicit "table of contents" - acceptable at this length, but if the guide grows, one should be added.

### New libraries / infra dependencies
- None. Plain Markdown only.

### Better alternatives considered
- None needed. A single Markdown page is the correct "component" for this content.

### Recommendations
- Render the file in GitHub's preview (or a Markdown previewer) before merging to confirm the nested lists and the reference link resolve correctly.

### Questions for author
- Do we want a short table of contents? (Recommendation: no, the document is under one screen.)

### Verdict: APPROVED

---

## Byte - Backend Engineer

**Focus:** API design, data models, business logic, performance.

### Strengths
- The LLD correctly and explicitly marks Data Models, API/CLI, Dependencies, and Configuration as Not Applicable rather than inventing content. That honesty is exactly right for a documentation-only change.
- Scope is tightly bounded to one new file with zero modifications, which eliminates any risk of regressions.

### Concerns
- None from a backend perspective; there is no code, data, or API surface. My only note is process, not substance: ensure the file uses `.md` (so GitHub renders it) rather than matching the extensionless `README`. The LLD already calls this out.

### New libraries / infra dependencies
- None.

### Better alternatives considered
- The LLD's Alternative 1 (README section) is correctly rejected; a dedicated file is what GitHub's flows key off.

### Recommendations
- Keep the commit-message guidance generic (as drafted). Prescribing a strict convention (e.g. Conventional Commits) would be over-engineering for a repo with no such history.

### Questions for author
- None.

### Verdict: APPROVED

---

## Circuit - SRE/DevOps Engineer

**Focus:** Deployment, monitoring, scaling, infrastructure.

### Strengths
- "Deployment" is accurately described as "merge to default branch and GitHub renders it" - no pipeline, no infra, no rollback machinery. The LLD does not over-engineer a deployment story that does not exist.
- The Deployment Surface Checklist and Scaling sections are correctly Not Applicable with justification, not silently omitted.

### Concerns
- The LLD notes the default branch is `master`. Confirm the pull request targets `master` (this repository's default), not `main`, so the merge and GitHub's auto-linking behave as expected.
- No automated Markdown link-check or lint runs in CI - acceptable given the "no CI" constraint, but it means the one manual rendering check is the only safety net. Make that check a required step in `testing.md` (it is).

### New libraries / infra dependencies
- None.

### Better alternatives considered
- A CI Markdown linter (e.g. markdownlint) was implicitly out of scope; agree with excluding it for a single hand-written file.

### Recommendations
- Verify the relative `../../issues` link works on the deployed default branch after merge, since that is the one runtime-ish behavior in the document.

### Questions for author
- None.

### Verdict: APPROVED

---

## Cipher - Security Engineer

**Focus:** AuthN/AuthZ, validation, OWASP, data protection.

### Strengths
- No secrets, credentials, endpoints, or executable content are introduced. Attack surface is effectively zero.
- Encouraging contributors to search existing issues and to describe reproduction steps indirectly improves triage quality without soliciting sensitive data.

### Concerns
- Minor: the guide points all reports (including potential security issues) to public issues. For a demonstration repository this is fine, but if this guide were reused on a real project, a one-line "for security-sensitive reports, do not open a public issue - see SECURITY.md / contact the maintainers privately" note would be the responsible pattern. `SECURITY.md` is explicitly out of scope here, so this is a note for future work, not a blocker.
- Ensure no real email addresses, tokens, or internal URLs are pasted into the guide (the draft contains none).

### New libraries / infra dependencies
- None.

### Better alternatives considered
- None applicable.

### Recommendations
- If this repo ever accepts security-relevant contributions, add a `SECURITY.md` and reference it from the guide. Out of scope for this issue but worth tracking.

### Verdict: APPROVED WITH CHANGES (non-blocking: add a private-disclosure pointer only if/when a `SECURITY.md` is introduced)

---

## Sage - SMTS (Overall)

**Focus:** Architecture, code quality, maintainability.

### Strengths
- Excellent scope discipline: one new file, zero modifications, all inapplicable LLD sections marked Not Applicable with justification rather than padded with invented content.
- The draft content covers all three required flows (file an issue, open a PR, expectations) clearly and at the right reading level for a first-time contributor.
- Alternatives are genuinely weighed (README section vs. dedicated file vs. full `.github/` suite vs. subdirectory placement) with a clear, correct rationale for the chosen approach.
- The relative-link technique for the Issues tab is a nice, portable touch that avoids hardcoding owner/repo.

### Concerns
- The guide and the README are decoupled; a brand-new visitor landing on the README still has no pointer to the guide. The LLD acknowledges this as an open question and keeps it out of scope, which is defensible, but I would flag the README pointer as a strong candidate for an immediate follow-up.
- Content in the LLD is illustrative-but-complete. Make sure the implementer copies it faithfully and runs the rendering check, since there is no automated gate.

### Recommendations
- Ship as designed. Track two trivial follow-ups: (1) a one-line README link to `CONTRIBUTING.md`, and (2) optional `.github/` templates if contribution volume grows.

### Questions for author
- Confirm whether the README pointer should be folded into this change or tracked separately. (Design keeps it separate to honor the "leave README unchanged" constraint.)

### Verdict: APPROVED

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED | 0 | Verify nested list and relative link render correctly in GitHub preview. |
| Backend (Byte) | APPROVED | 0 | Use `.md` extension; keep commit guidance generic. |
| SRE (Circuit) | APPROVED | 0 | Target the `master` default branch; confirm `../../issues` link post-merge. |
| Security (Cipher) | APPROVED WITH CHANGES | 0 | Non-blocking: add a private-disclosure pointer if a `SECURITY.md` is later added. |
| SMTS (Sage) | APPROVED | 0 | Ship as designed; track README pointer and optional `.github/` templates as follow-ups. |

## Next Steps
1. Implement `CONTRIBUTING.md` at the repository root using the LLD's content (out of scope for this skill).
2. Run the verification checks in `testing.md` (file presence, rendering, required sections, no other files changed).
3. Optionally track two follow-ups: a one-line README link to the guide, and future `.github/` community-health files. Neither blocks this change.
