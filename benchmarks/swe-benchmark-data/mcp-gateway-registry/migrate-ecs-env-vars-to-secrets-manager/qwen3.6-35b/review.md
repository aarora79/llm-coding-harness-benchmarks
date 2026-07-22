# Expert Review: Migrate ECS Environment Variables to AWS Secrets Manager

*Created: 2026-07-22*
*Related LLD: `./lld.md`*

## Review Personas

| Role | Reviewer | Focus |
|------|----------|-------|
| Backend Engineer | Byte | API design, data models, business logic, performance |
| SRE/DevOps Engineer | Circuit | Deployment, monitoring, scaling, infrastructure |
| Security Engineer | Cipher | AuthN/AuthZ, validation, OWASP, data protection |
| SMTS (Overall) | Sage | Architecture, code quality, maintainability |

---

## Backend Engineer (Byte)

### Strengths

- The design correctly preserves all environment variable names so the application code (`registry/core/config.py`) does not need any changes. The app reads secrets via Pydantic `BaseSettings`, and ECS injects secrets from Secrets Manager into the same environment variable namespace regardless of source. Zero application code changes.

- The LLD accurately identifies 13 new secrets that need resources versus the 13 existing ones already covered. The approach of creating new resources only where needed avoids redundant infrastructure.

- The extraction of common secrets into a `locals.shared_secrets` local in `locals.tf` is a strong design decision. It reduces duplication between the auth-server and registry `secrets` blocks and makes future maintenance simpler.

- The `enable_secrets_manager` central toggle is an excellent addition. Operators can first apply Terraform to create the secrets and update IAM, then flip the toggle to activate secrets loading without another infrastructure change.

- The conditional plaintext fallback pattern (`var.<name> != "" && !var.enable_secrets_manager ? [{ name = "...", value = var.<name> }] : []`) ensures zero-downtime migration. The three-tier fallback approach (secrets block > fallback env var > variable default) is well thought out.

### Concerns

- The `random_password.grafana_admin_password` generates a new random password on the first Terraform apply when `var.grafana_admin_password` is empty. Since `lifecycle { ignore_changes = [secret_string] }` is set, the value stabilizes after the first apply. However, new operators who set `grafana_admin_password = ""` in their `terraform.tfvars` will still get a random password. This should be documented clearly in a migration guide.

- The `auth0_management_api_token` expires after 24 hours (per the variable description). Storing it in Secrets Manager is the right approach, but the rotation scaffolding (`secret_rotation_enabled`) does not define the actual rotation Lambda or CloudWatch Events rule. This is acknowledged as a follow-up but should have a tracking item.

- The `secrets` block entries in `ecs-services.tf` are duplicated between the auth-server and registry services. While the `shared_secrets` local reduces this, the base secrets (SECRET_KEY, KEYCLOAK_*, DOCUMENTDB_*) remain service-specific. The LLD could extract these into a `local.base_secrets` local as well for even more deduplication.

### Recommendations

1. Document the Grafana `random_password` behavior clearly in the README or a migration guide.
2. Create a tracking issue for Auth0 Management API token rotation monitoring with a concrete CloudWatch Events rule.
3. Consider extracting the base secrets (SECRET_KEY, KEYCLOAK_*, DOCUMENTDB_*) into a `local.base_secrets` local as well for more deduplication.

### Verdict: APPROVED WITH CHANGES

---

## SRE/DevOps Engineer (Circuit)

### Strengths

- The `enable_secrets_manager` central toggle is an excellent operational improvement. It allows operators to separate the Terraform state change (creating secrets and IAM policies) from the deployment change (switching to secrets-loaded tasks). This reduces blast radius.

- The phased rollout plan (staging first with `enable_secrets_manager = false`, then apply, then flip to `true`) is the correct approach for a security-sensitive infrastructure change.

- The IAM policy update correctly uses `concat()` with conditional expressions, maintaining the existing pattern. The KMS decrypt permission on `aws_kms_key.secrets.arn` is correctly separated.

- Cross-account support via the optional KMS grant (`aws_kms_grant.cross_account` controlled by `var.kms_cross_account_principals`) is a well-thought-out addition that was missing from the original design.

- The estimated line counts are realistic. The total ~340 lines of new/modified code is concentrated in 8 files, keeping the change reviewable.

- CloudTrail monitoring for `GetSecretValue` calls is noted as an observability control.

### Concerns

- The IAM policy `ecs_secrets_access` will grow to include ~28 secret ARNs. The policy JSON will approach 4 KB, which is under the 6144-byte soft limit but leaves limited headroom. If more secrets are added in the future, this could hit the limit. Consider splitting into multiple IAM policies (e.g., one per service group).

- No mention of enabling CloudTrail Data Events for Secrets Manager. Without data plane logging, you cannot audit which services accessed which secrets. This is a common compliance requirement (SOC2, PCI-DSS, HIPAA). The LLD mentions it as a recommendation but it should be a required item.

- The `terraform plan` output will now include changes to ~40 environment variable entries across three services. This is a large plan that could mask unintended changes. Suggest using `terraform plan -target` for staged rollout (e.g., plan secrets first, then ECS changes separately).

- No mention of Terraform state backup before applying this change. State corruption during a large apply could be catastrophic.

- The rotation scaffolding variables (`secret_rotation_enabled`, `secret_rotation_schedule_expression`) are declared but the actual Lambda rotation functions are not implemented. This creates a configuration gap where operators might expect rotation to work but it does not.

### Recommendations

1. **Required**: Enable CloudTrail Data Events for the Secrets Manager KMS key as part of the rollout.
2. Add a Terraform state backup step (`terraform state pull > backup.tfstate`) before the first production apply.
3. Create a rollback procedure document: if a new secret fails to inject, operators can revert the ECS task definition to the previous version while keeping the new Terraform state.
4. Implement the actual rotation Lambda functions as a follow-up PR, not just scaffolding variables.
5. Add a staging environment and run `terraform plan` there before production to validate the full plan output.

### Verdict: APPROVED WITH CHANGES

---

## Security Engineer (Cipher)

### Strengths

- This is a significant security improvement. Moving 13 sensitive values from plaintext environment variables to Secrets Manager eliminates exposure in:
  - ECS task definition JSON (accessible via `aws ecs describe-task-definition`).
  - ECS task/service describe API responses.
  - `terraform plan` output diffs (values are marked `sensitive`).
  - Terraform state files (values are encrypted in state for sensitive variables).

- All new secrets use the same KMS key (`aws_kms_key.secrets.id`) as existing secrets, maintaining consistent encryption. The KMS key already has key rotation enabled and a proper resource-based policy.

- The `sensitive = true` attribute is already present on all sensitive variables. This correctly prevents them from appearing in `terraform output`.

- The `enable_secrets_manager` toggle provides an audit-safe migration path: operators can verify secrets are created and IAM policies are correct before switching to secrets-loaded tasks.

- The conditional creation pattern (`count = var.<feature>_enabled ? 1 : 0`) correctly prevents unused secrets from being created.

- The IAM policy only grants `secretsmanager:GetSecretValue` (read-only), not `PutSecretValue`, `CreateSecret`, or `DeleteSecret`. This is correct least-privilege for task roles.

- The cross-account KMS grant pattern (`aws_kms_grant.cross_account`) with optional `var.kms_cross_account_principals` is the correct way to enable cross-account access without creating policy conflicts.

- The design correctly excludes non-sensitive configuration variables (e.g., `DOCUMENTDB_HOST`, `BIND_HOST`, `AUTH_SERVER_URL`) from the migration.

### Concerns

- The `GITHUB_APP_PRIVATE_KEY` is a PEM-formatted private key with full GitHub repository access. A compromise of Secrets Manager access would grant the attacker full repository access. Consider a separate KMS key for GitHub-related secrets to limit blast radius.

- The `FEDERATION_ENCRYPTION_KEY` is a Fernet key used to encrypt data at rest in MongoDB. If this key is rotated without updating MongoDB, all federation data becomes unreadable. The LLD does not address key versioning or migration for this critical secret.

- The `auth0_management_api_token` expires after 24 hours. Storing it in Secrets Manager is correct, but there is no rotation schedule. The scaffolding variables exist but the actual rotation mechanism does not. Someone must manually update this secret daily, which is error-prone.

- The `recovery_window_in_days = 0` on all new secrets means they are immediately deletable. For compliance, a non-zero recovery window (e.g., 7 days) for high-value secrets provides a safety net against accidental deletion.

- No mention of Secrets Manager resource-based policies beyond IAM. Consider adding conditions like MFA requirement for secret reads in high-security environments.

- No mention of `prevent_destroy = true` lifecycle rule on critical secrets to prevent accidental deletion via `terraform destroy`.

### Recommendations

1. Consider a separate KMS key for GitHub-related secrets (`github_pat`, `github_app_private_key`) to limit blast radius.
2. Implement a documented process for `FEDERATION_ENCRYPTION_KEY` rotation that includes MongoDB migration steps.
3. Create an alert for `auth0_management_api_token` age (CloudWatch Events rule checking `LastUpdatedDate`).
4. Consider `recovery_window_in_days = 7` for high-value secrets (registry_api_token, github_pat, federation_encryption_key) to prevent accidental data loss.
5. Add `prevent_destroy = true` lifecycle rule to critical secrets to prevent accidental deletion via Terraform.
6. **Required**: Enable CloudTrail Data Events for the Secrets Manager KMS key.
7. Implement actual rotation Lambda functions as a follow-up, not just scaffolding variables.

### Verdict: APPROVED WITH CHANGES

---

## SMTS (Sage) -- Overall Architecture Review

### Strengths

- The design is thorough and follows the established patterns in the codebase. The use of `concat()` for building the secrets list, conditional creation with `count`, `lifecycle { ignore_changes }`, and per-secret Secrets Manager resources are all consistent with existing code.

- The `enable_secrets_manager` central toggle is an excellent architectural improvement over per-secret fallbacks. It provides a single switch for operators to control the migration state of the entire system, making the migration auditable and reversible.

- The extraction of `shared_secrets` into a local variable in `locals.tf` is a strong design decision. It reduces duplication between the auth-server and registry services and makes future additions (new shared secrets) a single-line change.

- The conditional plaintext fallback pattern ensures zero-downtime migration. This is the correct approach for a production system that cannot afford service disruption.

- The cross-account support via `aws_kms_grant` is a valuable addition that addresses a requirement from the original design brief.

- The LLD provides concrete code examples for each change, making it actionable for an implementer. The file-by-file breakdown with line number estimates is realistic and helpful.

- The alternatives analysis (SSM, single JSON, External Secrets Operator) is well-reasoned and appropriately scoped.

- The migration plan acknowledges that Docker Compose and Helm have different concerns and treats them appropriately.

### Concerns

- The scope of this change is significant: ~340 lines of new/modified code across 8 files. The `terraform plan` output will be noisy with many additions, removals, and modifications. The LLD could benefit from being split into two PRs:
  1. PR 1: Add new secrets resources + update IAM + create locals + add variables (infrastructure change only).
  2. PR 2: Remove plaintext env vars + add ECS secrets block entries + enable_secrets_manager gating (deployment change).
  This allows review of the infrastructure changes independently from the deployment changes.

- The LLD does not address the `keycloak-ecs.tf` service, which may also have plaintext secrets. This is likely out of scope but should be noted in a follow-up.

- No mention of Terraform state migration. If existing deployments already have some of these secrets in Secrets Manager (created manually or via a previous migration), `terraform plan` may show an import conflict. A state import strategy should be documented.

- The rotation scaffolding variables (`secret_rotation_enabled`, `secret_rotation_schedule_expression`) are declared but the actual Lambda rotation functions are not implemented. This creates a partial feature gap.

### Recommendations

1. Split into two PRs as described above to improve review quality.
2. Add a `docs/operations/secrets-migration.md` file documenting the migration steps for operators, including state backup procedures.
3. Create a `.github/CODEOWNERS` entry for `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` and `iam.tf` so that security team members are automatically requested as reviewers.
4. Clarify the expected `terraform plan` output for existing deployments (what changes will be seen, what will be no-ops).
5. Document the state import strategy for operators who may have manually created some Secrets Manager secrets.
6. Implement actual rotation Lambda functions as a follow-up PR rather than leaving scaffolding variables.

### Verdict: APPROVED WITH CHANGES

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Backend (Byte) | APPROVED WITH CHANGES | 1 | Document Grafana random_password behavior; consider `local.base_secrets` for more dedup |
| SRE (Circuit) | APPROVED WITH CHANGES | 3 | Enable CloudTrail Data Events; add rollback procedure; implement actual rotation Lambdas |
| Security (Cipher) | APPROVED WITH CHANGES | 4 | Separate KMS key for GitHub secrets; implement rotation monitoring; add prevent_destroy |
| SMTS (Sage) | APPROVED WITH CHANGES | 1 | Split into two PRs; add migration documentation |

### Combined Blockers (prioritized)

1. **Enable CloudTrail Data Events for Secrets Manager KMS key** (Circuit/Cipher) -- Compliance requirement for audit trails.
2. **Implement actual rotation Lambda functions** (Circuit/Cipher) -- Scaffolding without implementation creates a feature gap.
3. **Split into two PRs** (Sage) -- Improves review quality for a large change.
4. **Document Grafana random_password behavior** (Byte) -- Operator clarity.
5. **Add `prevent_destroy` lifecycle rule to critical secrets** (Cipher) -- Prevent accidental deletion.
6. **Document state import strategy for operators** (Sage) -- Handle existing manual secrets.

### Next Steps

1. Address the Grafana random_password documentation gap.
2. Split into two PRs: PR 1 for secrets resources + IAM + variables, PR 2 for ECS container definition changes.
3. Create a tracking issue for Docker Compose migration.
4. Enable CloudTrail Data Events for the Secrets Manager KMS key before or immediately after deployment.
5. Create a migration guide for operators (`docs/operations/secrets-migration.md`).
6. Implement actual rotation Lambda functions as a follow-up PR.