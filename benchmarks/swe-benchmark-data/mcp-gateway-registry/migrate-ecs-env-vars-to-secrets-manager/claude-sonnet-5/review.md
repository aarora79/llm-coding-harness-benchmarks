# Expert Review: Migrate Remaining ECS Plaintext Secrets to AWS Secrets Manager

*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Frontend Engineer - Pixel

**Strengths**
- Correctly scoped as Terraform-only; explicitly confirms no changes to `registry/core/config.py` or `auth_server/`, which matches how these secrets are actually consumed (plain env var reads, no client-side exposure).
- The `*_secret_arn` naming convention (`registry_api_token` -> `registry_api_token_secret_arn`) is intuitive and consistent with the existing `mongodb_connection_string_secret_arn` precedent already documented in `terraform.tfvars.example`.
- Backwards-compatible fallback design means no forced operator action - nothing breaks silently.

**Concerns**
- The LLD's Codebase Analysis never checked the admin Settings UI. The registry has an admin Config Panel (backed by `registry/api/config_routes.py`'s config endpoint) that displays several of the exact secrets in scope here as masked values (`registry_api_token`, `registry_api_keys`, `federation_static_token`, `ans_api_key`, `ans_api_secret`). It reads the resolved value by field name regardless of whether ECS populated it from `secrets` or `environment`, so it keeps working correctly - but the LLD should say this explicitly rather than silently omitting that a frontend surface exists at all.
- `GITHUB_APP_PRIVATE_KEY`, `AUTH0_MANAGEMENT_API_TOKEN`, `FEDERATION_ENCRYPTION_KEY`, and `GF_SECURITY_ADMIN_PASSWORD` do not appear to be surfaced in the admin config groups at all - worth confirming whether that is intentional or a pre-existing gap unrelated to this LLD.

**New libraries / infra dependencies required**
None - pure Terraform HCL change, no frontend package or build changes.

**Better alternatives considered**
None applicable from a frontend angle; the LLD's alternatives are backend-only tradeoffs.

**Recommendations**
- Add a line to the LLD's Codebase Analysis confirming the admin Config Panel was checked and is unaffected (source-agnostic masking).
- Apply the "RECOMMENDED for production" tfvars comment format uniformly across all 13 new variable blocks, not just as a single example.

**Questions for author**
- Was the admin Config Panel deliberately checked and found unaffected, or simply not considered?
- Why are `AUTH0_MANAGEMENT_API_TOKEN`, `GITHUB_APP_PRIVATE_KEY`, `FEDERATION_ENCRYPTION_KEY`, and `GF_SECURITY_ADMIN_PASSWORD` absent from the admin UI's config groups - pre-existing gap or intentional?

**Verdict: APPROVED**

---

## Backend Engineer - Byte

**Strengths**
- Correctly reuses the exact `mongodb_connection_string`/`_secret_arn` fallback precedent (PR #947), so the pattern is idiomatic for this codebase rather than invented.
- The Grafana two-container fan-out is correctly identified: the `grafana-config` sidecar interpolates `$${GF_SECURITY_ADMIN_PASSWORD}` directly in its shell entrypoint, so both containers genuinely need the value populated the same way.
- Correctly notes Grafana's task-exec role currently has no `ecs_secrets_access` attached and must have it added.
- The "no Python code change" claim is largely justified - `registry/core/config.py` and `auth_server` read by env-var name, source-agnostic to ECS `secrets` vs `environment`.
- Correctly flags that `AUTH0_MANAGEMENT_API_TOKEN` is unconditionally emitted today (no `auth0_enabled` gate), unlike the other Auth0 secrets.

**Concerns**
- The "no Python changes needed" claim doesn't check whether any code does string-level PEM handling (e.g. unescaping literal `\n` sequences) on `github_app_private_key`. If such a consumer exists, the exact byte format an operator stores in the new Secrets Manager secret (real newlines vs. escaped `\n`) matters and is unaddressed.
- The LLD doesn't check whether any admin-UI/config-export code path represents `_secret_arn`-sourced fields any differently than plaintext-sourced ones - worth a quick check before implementation even if no change turns out to be needed.
- The IAM section adds new `Resource` entries for externally-owned ARNs but doesn't address the case where an externally-supplied secret is encrypted under a different (customer-managed) KMS key than `aws_kms_key.secrets` - the existing `kms:Decrypt` grant is scoped only to that one key, so `GetSecretValue` on an externally-encrypted secret could fail at task launch with no mention of this failure mode in the design.
- 26 near-identical hand-written conditional blocks (13 secrets x 2 services) is copy-paste-prone; a `for_each`-driven map of `{name, plaintext_var, secret_arn_var}` tuples would reduce duplication and mismatch risk.

**New libraries / infra dependencies required**
None.

**Better alternatives considered**
A `for_each`-based generation of the conditional `environment`/`secrets` blocks over a local map, instead of 26 hand-written ternary expressions, would cut the diff size roughly in half and eliminate copy-paste risk.

**Recommendations**
- Verify and document the expected PEM/newline format for `github_app_private_key_secret_arn`'s secret value before implementation.
- Check whether any config-display/export code path needs awareness of the new variables.
- Add an explicit note about cross-KMS-key externally-supplied secrets requiring a separate `kms:Decrypt` grant, or state it out of scope explicitly.
- Consider a `for_each`-driven refactor to reduce duplication.

**Questions for author**
- Should the design state a required KMS policy addition (or explicit non-support) for externally-owned secrets encrypted with a key other than `aws_kms_key.secrets`?
- Was the config-display/export path checked for assumptions about where these values are sourced from?

**Verdict: APPROVED WITH CHANGES**

---

## SRE / DevOps Engineer - Circuit

**Strengths**
- Correctly frames that ECS `secrets` block resolution happens once at task launch (no per-request latency), and that CloudTrail gives an audit trail for `GetSecretValue`.
- Reuses the proven `mongodb_connection_string`/`_secret_arn` fallback pattern rather than inventing a new mechanism.
- Catches the two-container coupling for `GF_SECURITY_ADMIN_PASSWORD` and the missing `ecs_secrets_access` attachment on Grafana's task-exec role.
- Explicitly scopes rotation, Docker Compose/Helm, and runtime Python fallback as non-goals, keeping blast radius contained.

**Concerns**
- Deployment safety is understated: the Rollout Plan mentions "rolling task replacement" in one clause with no discussion of what happens when a `*_secret_arn` is mistyped or points at a nonexistent/inaccessible secret. That failure surfaces as an ECS task-launch error (`ResourceNotFoundException`/`AccessDeniedException` deep in ECS agent logs), not a Terraform-time error, and there is no rollback runbook for "apply succeeded, task launch fails."
- The "fallback" is a config-time choice, not a runtime fallback. Once a `*_secret_arn` is set, if that secret is later deleted or its access revoked, the only way back to plaintext is another `terraform apply` and a full redeploy - not a fast toggle. This operational risk is not called out.
- 13 secrets x 2 variable declarations (root + module) x tfvars documentation is real toil - roughly 65 edit sites across 5 files with no `for_each`/map-based structure to reduce copy-paste risk despite the repo already using `concat()` patterns that could be table-driven.
- No mention of running `checkov`/`tfsec` against the new HCL, despite this being a security-hardening migration where before/after static-analysis findings would be good evidence.
- The Grafana IAM attachment is called out only as a footnote in the implementation steps rather than a first-class item in the Deployment Surface Checklist - omitting it silently breaks Grafana secret resolution with no compile-time warning.
- No monitoring/alerting recommendation for "task failed to start due to secrets resolution error," which is the most likely new operational failure mode this change introduces.

**New libraries / infra dependencies required**
None.

**Better alternatives considered**
A hybrid where Terraform can either create the secret (for net-new deployments) or accept an externally-supplied ARN (for migrations), combined with a `for_each`-driven variable structure, would reduce operator toil versus the current 65-touchpoint design.

**Recommendations**
- Add a CloudWatch alarm or ECS service-event check for secret-resolution failures at task launch, and document a manual rollback runbook as a first-class rollout step.
- Promote the Grafana IAM policy attachment from a footnote to a mandatory checklist item with its own acceptance criterion.
- Add a CI step running `checkov`/`tfsec` against the new HCL and record before/after finding counts.
- Add a canary/staged-apply recommendation (non-prod workspace first, verify `ecs:DescribeTasks` shows RUNNING with no secrets-resolution failure) before promoting to production.

**Questions for author**
- What is the operator-facing signal when a task fails to launch because of a bad `*_secret_arn`, and how quickly would that be noticed without an alarm?
- If a secret referenced by `*_secret_arn` is deleted or its resource policy revoked, does anything detect this before the next task replacement event?

**Verdict: APPROVED WITH CHANGES**

---

## Security Engineer - Cipher

**Strengths**
- Correctly identifies that `sensitive = true` only redacts CLI output, not the plaintext written into the ECS task definition JSON - an accurate framing of the actual risk being fixed.
- Reuses existing, proven patterns rather than inventing new mechanisms, reducing review burden and blast radius.
- Correctly flags that `GITHUB_APP_PRIVATE_KEY` (multi-line PEM) is handled natively by the `secrets` block `valueFrom` mechanism with no extra escaping logic needed at the ECS layer.
- The Alternatives section is honest that the rejected Terraform-managed-secret alternative doesn't actually remove plaintext exposure from state/tfvars either.

**Concerns**
- No sunset plan for the plaintext fallback. The design treats the parallel plaintext path as permanent with no deprecation timeline and no `terraform plan`-time warning when the plaintext path is used. For 13 credentials including a GitHub App private key and a Grafana admin password, an indefinite dual path means most deployments may simply never migrate - Secrets Manager becomes opt-in decoration rather than the new baseline, which undercuts the issue's own stated goal.
- The IAM policy remains a single shared blob, now larger. All 13 new ARNs are appended to the one `aws_iam_policy.ecs_secrets_access` attached to both `auth-server` and `registry`, even though neither service needs every secret it is granted (e.g. `auth-server` doesn't need `GITHUB_PAT`/`GITHUB_APP_PRIVATE_KEY`). A container compromise on either service yields IAM credentials that can read every migrated secret, not just the ones that container's process actually consumes.
- Attaching the entire `ecs_secrets_access` policy to Grafana's task-exec role solely so it can read one password is a disproportionate over-grant for a lower-trust, more network-exposed component (a dashboard UI). A dedicated single-secret policy for Grafana was achievable and should have been the design, not a footnote.
- State file exposure is not resolved for the fallback path itself: the plaintext variable's value still lands in Terraform state in plaintext (`sensitive = true` does not encrypt state) for every deployment that keeps using the plaintext path - the design should say this plainly in `terraform.tfvars.example` rather than implying the redaction of CLI output is sufficient.
- The exact newline encoding expected for the PEM secret's stored value (literal newlines vs. escaped `\n`, matching whatever the app currently expects) is unresolved and could silently break GitHub App auth depending on how an operator populates the secret.

**New libraries / infra dependencies required**
None.

**Better alternatives considered**
Split `ecs_secrets_access` into per-service least-privilege policies (auth-server, registry, Grafana) as part of this same migration, since the policy is already being touched for all 13 new ARNs - the marginal cost is low and the blast-radius reduction is meaningful.

**Recommendations**
- Add a tracked deprecation/sunset milestone for the plaintext fallback rather than treating it as permanent.
- Split the shared IAM policy per service, or at minimum give Grafana a dedicated single-secret policy instead of the full `ecs_secrets_access` grant.
- Document plainly in `terraform.tfvars.example` that the plaintext fallback path leaves values in Terraform state as plaintext.
- Resolve and document the exact newline encoding expected for the GitHub App private key secret before implementation.

**Questions for author**
- Why not scope IAM per-service now, given the policy is already being edited for all 13 new ARNs?
- Does the app's PEM handling expect literal newlines or `\n` escapes, and has that been checked against how operators will populate the new secret?
- Should `terraform plan` emit a diagnostic when a secret is still resolved via the plaintext path, to nudge migration?

**Verdict: APPROVED WITH CHANGES**

---

## SMTS (Overall) - Sage

**Strengths**
- The claim that most secrets were already migrated before this task started is verified true - `secrets.tf` already has Secrets Manager resources for the app `SECRET_KEY`, Keycloak client/M2M/admin credentials, embeddings key, Entra/Okta/Auth0 client secrets, metrics API key, OTLP headers, and DocumentDB/Keycloak-DB rotation.
- Every plaintext env-var/line-number claim across `ecs-services.tf` and `observability.tf` checks out exactly against the real repo - high grounding accuracy.
- The `mongodb_connection_string`/`_secret_arn` fallback precedent is real and correctly cited, and the `documentdb_credentials_secret_arn` gated-IAM pattern is accurately described.
- The "no config-loader changes needed" claim is correctly and verifiably justified - `registry/core/config.py::Settings` and `auth_server` both read via plain environment binding, source-agnostic to how ECS resolved the value.
- The Grafana sidecar interaction and the missing IAM attachment are real, non-obvious findings.

**Concerns**
- **Internal contradiction between the two artifacts.** `github-issue.md`'s Proposed Solution and Acceptance Criteria describe creating new `aws_secretsmanager_secret` resources in `secrets.tf` "following the exact pattern already used for `entra_client_secret`/`okta_client_secret`." The LLD's actual chosen design does the opposite: it explicitly rejects that as Alternative 1 and only accepts externally-supplied `_secret_arn` variables, and `secrets.tf` does not appear in the LLD's Modified Files table at all. The two documents describe materially different implementations of the same step and must be reconciled.
- Clarifying answer 5 (rotation support and cross-account access) is waved off rather than reconciled. Rotation is dismissed as "static tokens with no AWS-native rotation protocol," but several of these values (e.g. the registry API token, the federation static token) are app-generated and could follow the existing `random_password` + Secrets Manager pattern already used for `secret_key`/`metrics_api_key`. Cross-account access is not mentioned anywhere in the design, not even as an explicitly flagged gap.
- The `_secret_arn`-only design means Terraform creates zero new secrets for the 12 non-Grafana values - it only wires ARNs the operator manages by hand. That is a defensible scope reduction, but it should have been surfaced explicitly against clarifying answer 5, not left for a reviewer to notice.

**New libraries / infra dependencies required**
None.

**Better alternatives considered**
A hybrid would be stronger: Terraform-generate secrets for app-owned tokens (rotation-capable, matching the `secret_key`/`metrics_api_key` precedent) while keeping the `_secret_arn` fallback only for genuinely externally-issued values (Auth0/GitHub/ANS credentials that cannot be rotated by this stack). That reconciles both the issue's demand for new `secrets.tf` resources and the rotation ask in clarifying answer 5, rather than dropping both.

**Recommendations**
- Resolve the `github-issue.md` vs `lld.md` contradiction before implementation - align both documents on one design.
- Add an explicit paragraph reconciling clarifying answer 5's cross-account requirement, even if the honest answer is "no cross-account resource-policy pattern exists today; tracked as a follow-up."
- Split the rotation Non-Goal into "no rotation protocol available" (third-party-issued keys) versus "rotation deferred" (app-generated tokens) rather than one blanket statement.

**Questions for author**
- Why does the LLD reject the exact resource-creation approach the GitHub issue's own Proposed Solution and Acceptance Criteria specify?
- Was cross-account access from clarifying answer 5 considered and consciously dropped, or missed?

**Verdict: NEEDS REVISION**

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED | 0 | Document that the admin Config Panel is unaffected |
| Backend (Byte) | APPROVED WITH CHANGES | 0 | Verify PEM newline format; address cross-KMS-key external secrets; consider `for_each` refactor |
| SRE (Circuit) | APPROVED WITH CHANGES | 0 | Add failure-detection/rollback runbook; promote Grafana IAM fix to a checklist item; add checkov/tfsec gate |
| Security (Cipher) | APPROVED WITH CHANGES | 0 | Split IAM policy per service; add sunset plan for plaintext fallback; document state-file exposure |
| SMTS (Sage) | NEEDS REVISION | 1 | Reconcile `github-issue.md` vs `lld.md` contradiction on resource creation; address rotation/cross-account from clarifying answer 5 |

## Next Steps

The single blocking issue - the contradiction between `github-issue.md`'s Proposed Solution (new Terraform-owned `aws_secretsmanager_secret` resources) and `lld.md`'s chosen design (externally-supplied `_secret_arn` variables only) - is addressed as part of self-review below by aligning the issue text with the LLD's actual design and adding explicit reasoning for why Terraform-owned secret generation was not chosen for this batch, plus an explicit reconciliation of the rotation/cross-account question from clarifying answer 5. The non-blocking recommendations from Backend, SRE, and Security (IAM policy scoping, sunset plan for the plaintext path, PEM format documentation, failure-detection runbook) are recorded here as follow-up items for a future implementer rather than applied to the current LLD, since none of them change the core mechanism the LLD proposes.
