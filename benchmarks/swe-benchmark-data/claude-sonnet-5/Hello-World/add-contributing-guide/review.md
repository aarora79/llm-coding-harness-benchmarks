# Expert Review: Add CONTRIBUTING.md

*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Frontend Engineer (Pixel)

**Strengths:**
- Content structure (Reporting Issues, Submitting PRs, Contribution Expectations, Questions) maps cleanly to H1/H2 hierarchy, which renders well in GitHub's Markdown viewer and populates a readable sidebar table-of-contents automatically.
- Numbered list for the PR workflow and bulleted lists elsewhere is the right choice - sequential steps vs. non-sequential expectations are visually distinguished.
- Correctly leverages `CONTRIBUTING.md` at repo root, which GitHub auto-links from the "New issue" / "New pull request" UI banners - good discoverability for a document meant to be read.
- Line-wrapped prose in the skeleton (~72-80 char lines) is friendly to diff review, though GitHub's renderer will reflow it anyway.

**Concerns:**
- No link text is specified anywhere (e.g., a link to the issues page, or an anchor back to a section) - for an accessibility-of-link-text check, there is nothing to evaluate because the draft avoids links entirely; worth confirming that is intentional rather than an oversight, since "Search existing issues" is a natural candidate for a real hyperlink.
- No mention of heading capitalization/style consistency (Title Case vs. sentence case) - minor, but worth fixing before merge for polish.
- The code fence uses `markdown` as the language tag for the skeleton, which is correct, but the LLD does not confirm the final file itself avoids nested code-fence rendering issues (unlikely, but worth a rendering smoke-test).

**New libraries / infra dependencies required:**
- None. Correctly identified as N/A - pure static Markdown, no build step, no renderer beyond GitHub's native one.

**Better alternatives considered:**
- The LLD already evaluates the README-append and templates-only alternatives and rejects both with sound reasoning (discoverability, scope match). No better alternative from a presentation standpoint.

**Recommendations:**
- Add one real hyperlink (to the repo's Issues tab) as a concrete, testable "no broken links" acceptance item, per the testing strategy.
- Keep line lengths under ~80 chars in the actual file, consistent with the skeleton.

**Questions for author:**
- Should "Search existing issues" be a hyperlink to `../../issues`?

**Verdict:** APPROVED

## Backend Engineer (Byte)

**Strengths:**
- Correctly recognizes this is a pure documentation change with no runtime, API, or data model - sections for Data Models, API/CLI, Configuration, Observability, and Scaling are all honestly marked "Not applicable" rather than padded with fake content.
- File-change plan is unambiguous: single new file, exact path, section order, and a full example skeleton an implementer could paste in with minimal edits.
- Non-Goals are explicit and consistent with the GitHub issue's Out of Scope list (no CoC, no templates, no CI, no enforcement) - no scope drift between issue and LLD.
- Default-branch note (`master`, not `main`) shows the author actually inspected repo state rather than assuming.

**Concerns:**
- The PR workflow example (`git checkout -b my-fix`, "push branch to fork") does not state the default branch name (`master`) inline even though it was discovered during codebase analysis - a minor but real gap for an implementer copying the skeleton verbatim.
- The Testing Strategy section defers entirely to `testing.md`; from a backend lens there is no mention of who or what actually validates the link-integrity check (manual vs. a tool), which is a legitimate process question even for docs.

**New libraries / infra dependencies required:**
- None. Confirmed correctly - no packages, CI, or tooling introduced.

**Better alternatives considered:**
- Both alternatives (README section, templates-only) are reasonable and correctly rejected with clear rationale; no better option missed.

**Recommendations:**
- Note explicitly that the branch is `master` (already known) inside the PR workflow example text, not just in Codebase Analysis.
- Optionally flag in the Rollout Plan that a future PR template or CI markdown-lint check would be a natural, separate follow-up - the LLD defers this correctly but a one-line forward pointer would help.

**Questions for author:**
- Should the guide mention any expectation around signing commits or license (since the repo has no LICENSE file), or is that intentionally deferred?

**Verdict:** APPROVED

## SRE/DevOps Engineer (Circuit)

**Strengths:**
- The LLD correctly and repeatedly states "not applicable" for Architecture, Data Models, API/CLI, Configuration, Observability, and Scaling - this is the accurate answer for a static Markdown file in a repo with no build system, and honest N/A is preferable to invented infra sections.
- The Rollout Plan's "merge directly to master, no deployment step" is the right call. There is genuinely nothing to deploy, no environment to promote through, no artifact to version.
- The Deployment Surface Checklist explicitly confirms no pipeline/container/IaC exists rather than silently skipping the question.

**Concerns:**
- The Rollout Plan has no rollback step. Even for docs, "revert commit if content is wrong" is one line and costs nothing to add.
- No mention of branch protection or required review on `master` before the direct merge - worth a one-line check since `master` (rather than `main`) suggests an older or default configuration that may or may not require a PR review gate.
- "Merge directly to master" and the PR guidance in the file itself (which tells contributors to fork and open PRs) are slightly inconsistent in tone - the maintainer bypasses the very process being documented. Not wrong, but worth a footnote acknowledging it.

**New libraries / infra dependencies required:**
- None. Correctly identified as none - no CI, no linter, no infra.

**Better alternatives considered:**
- The LLD's own Alternatives section (README section, templates-only) is adequate; no additional infra alternative exists since there is no infra dimension to this change.

**Recommendations:**
- Add a one-line rollback note to the Rollout Plan ("revert commit if content needs correction") for completeness, even though risk is negligible.
- Confirm whether `master` has branch protection; if so, note the merge still needs a PR/approval rather than a direct push.

**Questions for author:**
- Does `master` require PR review, or can this literally be pushed directly? The plan should say so explicitly either way.

**Verdict:** APPROVED

## Security Engineer (Cipher)

**Strengths:**
- Correctly scopes security-disclosure policy (`SECURITY.md`) as explicitly out-of-scope/non-blocking, avoiding false urgency on an unrelated concern.
- No secrets, credentials, internal URLs, or PII in either artifact; the example skeleton is generic placeholder content only.
- The design has no authN/authZ, input validation, or OWASP-relevant surface - accurately reflected by both docs ("Not applicable" for Architecture, Error Handling, Observability, API/CLI).
- The fork-and-PR workflow described is the standard low-privilege contribution model (external forks plus PR review), which is itself the correct default posture - no direct-push or elevated-permission workflow is proposed.

**Concerns:**
- Neither artifact instructs contributors not to include secrets, tokens, or credentials in issues, commits, or PRs. For a doc-only repo this is low-severity, but it is a one-line addition with real value once real code lands, and it costs nothing now.
- "Environment details where relevant" in the bug-report guidance could implicitly invite pasting logs or environment dumps that contain secrets or PII - not flagged anywhere.
- Link validation before merge is enforced only by "visual review," which is a manual, non-repeatable control (minor, non-security-critical).

**New libraries / infra dependencies required:**
- None.

**Better alternatives considered:**
- The design already rejected folding guidance into the README and a templates-only approach - no additional security-relevant alternative to raise.

**Recommendations:**
- Add a one-sentence note under "Reporting Issues": redact credentials, tokens, or secrets from logs, config, or environment details before posting.
- Keep `SECURITY.md` as a separate future issue, as already scoped.

**Questions for author:**
- None security-relevant; scope is appropriately minimal.

**Verdict:** APPROVED

## SMTS - Overall (Sage)

**Strengths:**
- The actual `CONTRIBUTING.md` skeleton is solid: covers issue filing (search first, reproduction steps), the PR workflow (fork/branch/commit/push/PR/link issue), and contribution expectations (single-purpose PRs, clear commit messages, responsiveness). Meets all acceptance criteria.
- The LLD correctly marks Architecture, Data Models, API Design, Observability, and Scaling as "Not applicable" rather than fabricating content to fill them - no invented complexity.
- The Alternatives section is genuinely useful (README-append vs. templates-only), with a real, defensible rationale for choosing `CONTRIBUTING.md` (GitHub's auto-link behavior on issue/PR pages).

**Concerns:**
- The template itself is the core problem: fourteen sections for a one-file, ~50-line doc change is heavy scaffolding. Even though each N/A section is short, the document's structure signals a false sense of engineering rigor that does not match the task, and a reader has to scroll through boilerplate to reach the two sections that matter (Implementation Details, Alternatives).
- The sequence/system-context "diagrams" for a static Markdown file add no information beyond one sentence - pure padding.
- Content gaps in the skeleton: no license/CLA note, no mention of code style or DCO (arguably fine given the "no code" scope, but worth an explicit call-out that this was considered and dropped, not just omitted).
- No mention of how to handle the default-branch-name ambiguity - the issue does not specify `master` vs. `main`, and the LLD assumes `master` from `.git/`; this should be flagged as something the implementer must verify at execution time, not silently baked into the final template text.

**New libraries / infra dependencies required:**
- None.

**Better alternatives considered:**
- The Alternatives section is adequate; no better option missed.

**Recommendations:**
- Use a lightweight "docs-only change" LLD template variant instead of the full architecture template for tasks like this.
- Drop the diagrams; replace with a one-line description.

**Questions for author:**
- Should the template's default-branch reference be parameterized/verified rather than hardcoded to `master`?

**Verdict:** APPROVED WITH CHANGES

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|----------------------|
| Frontend (Pixel) | APPROVED | 0 | Add a real hyperlink to the Issues tab; keep skeleton line lengths under ~80 chars. |
| Backend (Byte) | APPROVED | 0 | State the `master` branch name inline in the PR workflow example; note a future PR-template/CI-lint follow-up. |
| SRE (Circuit) | APPROVED | 0 | Add a one-line rollback note to the Rollout Plan; confirm whether `master` has branch protection. |
| Security (Cipher) | APPROVED | 0 | Add a one-sentence reminder to redact secrets/tokens from issue reports; keep `SECURITY.md` as a separate future issue. |
| SMTS (Sage) | APPROVED WITH CHANGES | 0 | Trim the LLD template for docs-only changes (drop diagrams); explicitly verify the default branch name before finalizing the file. |

No reviewer raised a blocking issue. Sage's "APPROVED WITH CHANGES" is about the LLD's documentation overhead, not the correctness of the proposed `CONTRIBUTING.md` content itself.

## Next Steps
- Optional polish before implementation: add a hyperlink to the Issues tab, mention the `master` branch name explicitly in the PR example, add a one-line secrets/credentials reminder, and add a one-line rollback note to the Rollout Plan.
- Verify the default branch name (`master`) still holds at implementation time rather than assuming it from this design pass.
- No blockers identified; the design is ready for implementation as-is if the above polish items are deferred.
