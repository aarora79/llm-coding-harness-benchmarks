# Expert Review: Migrate remaining sensitive ECS env vars to AWS Secrets Manager

*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

Five reviewer personas evaluate the design. Reviews are deliberately critical and identify real risks, not just praise.

---

## 1. Backend Engineer - "Byte"

**Focus:** Task-definition wiring, variable plumbing, module behavior.

### Strengths
- Correctly recognizes the repo already has a Secrets Manager pattern and extends it rather than reinventing one. The `secrets = concat([...], <conditional>)` and `Resource = concat([...], <conditional>)` shapes are reused verbatim, which keeps the diff reviewable.
- Catches that `sensitive = true` does not keep values out of the rendered task definition or state - a subtle but central point that a weaker design would miss.
- The sentinel-vs-conditional decision rule is articulated clearly and matches existing precedent (`embeddings_api_key` sentinel vs `entra_client_secret` count).

### Concerns
1. **Duplicate-env-name failure mode is real and easy to hit.** The `terraform-aws-modules/ecs/aws` module merges `environment` and `secrets`; a name present in both fails apply. The LLD says to remove and add together, but with ~12 names across two large containers this is exactly where a copy-paste slip happens. Recommend a grep-based assertion in CI (see testing.md) and editing one name at a time.
2. **`REGISTRY_API_KEYS` is a JSON map, not a single token.** Storing it as a whole-value secret is fine, but the value contains embedded quotes/braces. Confirm the secret round-trips byte-for-byte through `valueFrom` (it does - the whole value is injected verbatim - but the implementer should not be tempted to use the `:key::` JSON-extraction syntax here).
3. **Sentinel value `"not-configured"` reaches the app for unconfigured optional features.** Today an unset `var.ans_api_key` injects an empty string; after migration it injects `"not-configured"`. If any code path does `if os.environ.get("ANS_API_KEY"):` it still sees truthy. This is a behavior change for empty-string-sensitive code. Recommend verifying each consumer treats `"not-configured"` the same as empty, or use `count` (conditional) for the optional ones gated by an `*_enabled` flag.

### New libraries / infra dependencies required
None. Reuses existing provider resources. Good.

### Better alternatives considered
For the optional, feature-gated secrets (ANS, federation when disabled, registration gate), prefer the **conditional** (`count`) variant over the sentinel to preserve exact empty-string behavior. Use the sentinel only for always-on secrets.

### Recommendations
- Adopt conditional creation for feature-gated secrets to avoid the `"not-configured"` behavior change.
- Add a CI grep gate that fails if any known secret name appears under `environment`.

### Questions for author
- Have you confirmed no consumer distinguishes empty string from `"not-configured"`? (Open Question 3 territory.)

### Verdict: **APPROVED WITH CHANGES**

---

## 2. SRE / DevOps Engineer - "Circuit"

**Focus:** Deployment, rollout, rollback, operational toil.

### Strengths
- Staged rollout (non-prod first, set values out-of-band, force new deployment, smoke test) is realistic and matches how `ignore_changes` secrets actually behave operationally.
- Rollback story is concrete: revert Terraform or roll the ECS service to the previous task-definition revision. Keeping the prior revision is the right call.
- Notes the cold-start impact (parallel `GetSecretValue` per task start) and correctly concludes it is negligible.

### Concerns
1. **The Grafana execution-role question is a latent apply/runtime failure.** This is the biggest operational risk. If the Grafana service uses a different execution role and the IAM grant is not attached to it, the task will fail to start with a `ResourceInitializationError: unable to pull secrets` - and it will fail at *runtime*, not at `plan` time, so it sails through validation and breaks in the environment. This must be resolved before apply, not discovered after. Elevate Open Question 1 to a blocker.
2. **First apply leaves real secrets unset.** Because versions use `ignore_changes = [secret_string]` with a `"not-configured"`/placeholder seed, the very first deployment runs with placeholder secrets until an operator sets them in the console. For `GF_SECURITY_ADMIN_PASSWORD` that means Grafana briefly has a known placeholder admin password. Document a hard step: set real values immediately after first apply and before exposing the service, or seed from the variable on first apply.
3. **Task-definition revision churn rolls every service.** Moving entries changes the task def, so auth-server, registry, and grafana all get new revisions and roll. Fine, but call out that this is a rolling restart of the whole stack, to be done in a window.
4. **No rotation for the new secrets.** Acceptable per scope, but operationally these are now "set once, forget" secrets. Recommend tagging them (e.g. `rotation = "manual"`) so an audit can distinguish them from the rotated DB secrets.

### New libraries / infra dependencies required
None.

### Recommendations
- **Blocker:** confirm and wire the Grafana execution-role grant before apply.
- Add an explicit "set real secret values now" runbook step in OPERATIONS.md with the exact `aws secretsmanager put-secret-value` commands.
- Add a `rotation = "manual"` tag to the new secrets for auditability.

### Questions for author
- What is the exact `aws ecs update-service` rollback command you expect operators to use? (Pin the previous revision ARN in the runbook.)

### Verdict: **NEEDS REVISION** (until the Grafana IAM grant is resolved)

---

## 3. Security Engineer - "Cipher"

**Focus:** Exposure surface, least privilege, secret lifecycle.

### Strengths
- Directly closes the stated exposure: secrets leave `environment`, so they no longer appear in `ecs:DescribeTaskDefinition`, task-def revision history, or (for `ignore_changes` values set out-of-band) Terraform state.
- Least privilege is preserved: the IAM grant lists explicit ARNs and reuses the single KMS key already scoped to `*task-exec*` principals. No wildcard `secretsmanager:*` or `Resource = "*"`.
- Correctly scopes the grant to the **execution** role (used by the ECS agent to fetch secrets at start), not the task role - this is the right ECS semantics and avoids over-granting the application runtime.

### Concerns
1. **Secrets still in env vars at runtime (residual risk).** The `secrets` block injects values as process environment variables. Anything that can read `/proc/<pid>/environ` inside the container, or a crash dump, or a verbose log of `os.environ`, still sees them. This design's threat model is "no plaintext in the control plane / state," which it meets; it does not defend against in-container compromise. That is the Alt-3 (in-app SDK) tradeoff and should be stated as a known residual, not silently implied.
2. **Values transit Terraform state when seeded from variables.** Even with `ignore_changes`, the *initial* `secret_string` from `var.x` is written to state on first apply. For true "never in state" handling, seed with a placeholder and set the real value only via console/CLI. The LLD mentions this but should make it the recommended default for the most sensitive items (`GITHUB_APP_PRIVATE_KEY`, `FEDERATION_ENCRYPTION_KEY`).
3. **`recovery_window_in_days = 0`** (inherited convention) means deleted secrets are unrecoverable. Fine for ephemeral/dev, riskier for prod federation keys whose loss could strand encrypted data (`FEDERATION_ENCRYPTION_KEY` decrypts stored federation tokens - losing it is data loss). Recommend a non-zero recovery window for the encryption key specifically, or at minimum a documented warning.
4. **Placeholder admin password window** (also raised by Circuit) is a genuine security gap for Grafana.

### New libraries / infra dependencies required
None.

### Better alternatives considered
For `FEDERATION_ENCRYPTION_KEY` and `GITHUB_APP_PRIVATE_KEY`, consider console-only seeding (placeholder in TF, real value set out-of-band) so the material never lands in state. This is a per-secret policy, not a blanket one.

### Recommendations
- State the in-container residual risk explicitly in the issue/LLD (done partially in Alt 3 - make it a first-class "Security residual" note).
- Use placeholder-seed + console-set for the highest-sensitivity secrets; reserve a non-zero `recovery_window_in_days` for `FEDERATION_ENCRYPTION_KEY`.
- Verify no application log line dumps full `os.environ` (CLAUDE.md already forbids logging secrets).

### Verdict: **APPROVED WITH CHANGES**

---

## 4. SMTS / Overall Architect - "Sage"

**Focus:** Architecture, maintainability, scope discipline.

### Strengths
- Excellent scope discipline: the design separates "already done" (15 secrets) from "still to do" (the plaintext leftovers) and resists the temptation to re-architect. This is the single most important judgment call in the task and it is correct.
- The deliberate divergence from the benchmark table's "update application code at runtime" wording is explicitly justified (Alt 3) rather than ignored - the ECS `secrets` block delivers the same security outcome at far lower risk. Good engineering judgment, transparently argued.
- Decision rules (sentinel vs conditional, Option A vs B for Mongo) are written for a future implementer and tie back to concrete existing precedents.

### Concerns
1. **The design is ~12 near-identical resource blocks.** Maintainable but verbose. A `for_each` over a map of `{name => {var, condition}}` could collapse `secrets.tf` additions and keep the IAM list in sync automatically, reducing the copy-paste risk Byte flagged. The LLD chose explicit blocks for consistency with the existing file (which is all explicit blocks) - a defensible call, but worth noting the `for_each` alternative for the IAM resource list at least, since that is where drift bugs hide.
2. **Two sources of truth for "which secrets exist."** `secrets.tf` (definitions) and `iam.tf` (grants) must stay in lockstep; the existing code already has this coupling and the design preserves it. A `for_each` keyed on a shared local would eliminate the class of "added a secret, forgot the IAM grant" bug. Recommend at least a shared `locals` list consumed by both.
3. **Reserved-env-name coupling** is correctly flagged but easy to under-deliver. Since these names were already chart-managed, they are probably present, but "probably" should be "verified" with the helm unittest suite.

### New libraries / infra dependencies required
None.

### Recommendations
- Consider a shared `local.migrated_secrets` map driving both the resource `for_each` and the IAM `concat`, to make definition and grant a matched pair by construction.
- Run the helm unittest suite to confirm reserved-name lists are consistent.

### Questions for author
- Is the explicit-block style a hard constraint (match existing file) or would maintainers accept a `for_each` refactor for the new additions?

### Verdict: **APPROVED WITH CHANGES**

---

## 5. Frontend Engineer - "Pixel"

**Focus:** UI/UX surfaces.

### Assessment
This is an infrastructure-only change with no frontend component. The only user-visible surface is the **Grafana login**: after migration the admin password is delivered via Secrets Manager rather than a plaintext task-def env var, but the login UX is unchanged provided operators set the real password promptly (see Circuit/Cipher's placeholder-window concern). No React/SPA, component, or API-integration changes.

One adjacent UX note: operator-facing **documentation** is a UX surface. The runbook step "set the real secret value in the console after first apply" must be unambiguous, or operators will leave placeholders in place. Recommend copy-paste `aws secretsmanager put-secret-value` snippets in OPERATIONS.md.

### New libraries / infra dependencies required
None.

### Verdict: **APPROVED** (not applicable beyond the docs/runbook note)

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Backend (Byte) | APPROVED WITH CHANGES | 0 | Use `count` for feature-gated secrets to preserve empty-string behavior; CI grep gate for duplicate names |
| SRE (Circuit) | NEEDS REVISION | 1 | Resolve Grafana execution-role grant before apply; runbook for setting real values; rolling-restart window |
| Security (Cipher) | APPROVED WITH CHANGES | 0 | State in-container residual risk; placeholder-seed high-sensitivity secrets; non-zero recovery window for `FEDERATION_ENCRYPTION_KEY` |
| SMTS (Sage) | APPROVED WITH CHANGES | 0 | Shared `locals` map / `for_each` to keep defs and IAM grants in lockstep; run helm unittests |
| Frontend (Pixel) | APPROVED | 0 | Make the "set real value" runbook step copy-paste explicit |

**Net verdict:** APPROVED WITH CHANGES, with one must-fix blocker from SRE.

## Blockers to resolve before implementation

1. **(SRE, blocker)** Confirm which ECS task execution role the Grafana service uses and ensure `secretsmanager:GetSecretValue` on the Grafana password ARN is attached to *that* role. A missing grant fails at task start (runtime), not at plan time.

## Recommended (non-blocking) changes

1. Use the conditional (`count`) variant for feature-gated optional secrets (ANS, registration gate) to preserve exact empty-vs-set behavior; reserve the sentinel for always-on secrets. (Byte, Sage)
2. Introduce a shared `local.migrated_secrets` consumed by both `secrets.tf` (`for_each`) and `iam.tf` (`concat`) so a new secret cannot be added without its IAM grant. (Sage)
3. For `FEDERATION_ENCRYPTION_KEY` (and ideally `GITHUB_APP_PRIVATE_KEY`): placeholder-seed in Terraform and set the real value out-of-band; use a non-zero `recovery_window_in_days` for the encryption key to avoid irrecoverable data loss. (Cipher)
4. Add a runbook in OPERATIONS.md with copy-paste `aws secretsmanager put-secret-value` commands and an explicit "do this before exposing the service" warning. (Circuit, Pixel)
5. Add a CI grep gate asserting no known secret name appears under any `environment` block. (Byte)
6. Run the helm unittest suite to confirm reserved-env-name consistency. (Sage)

## Next Steps

1. Resolve the Grafana execution-role blocker (Open Question 1).
2. Decide sentinel vs conditional per secret (default conditional for feature-gated).
3. Decide Mongo Option A vs B (LLD recommends A).
4. Proceed to implementation per `lld.md` Steps 1-8, then execute `testing.md`.
