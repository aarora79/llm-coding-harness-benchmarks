# Expert Review: Migrate ECS Environment Variables to AWS Secrets Manager

*Created: 2026-07-06*
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
- The design correctly preserves all environment variable names so the application code does not need any changes. This is a major win for maintainability.
- The per-service split (auth-server vs registry) with appropriate conditional gates for each feature flag is well thought out.
- The LLD accurately maps which secrets already have resources versus which need new ones, avoiding redundant resource creation.

### Concerns
- The auth-server and registry services share the same secrets block entries for some secrets (OKTA, AUTH0, ANS, etc.). The LLD lists these separately for each service, which is correct since each container needs its own mapping, but the code duplication in the `secrets` concat blocks could be reduced by extracting common secrets into a local variable.
- The `random_password.grafana_admin_password` generates a new random password on every `terraform apply` when `var.grafana_admin_password` is empty. This will cause Grafana to become inaccessible after every Terraform run. The design should use a stable default or make the variable mandatory when Grafana is enabled.
- The `lifecycle { ignore_changes = [secret_string] }` pattern is used for all user-provided secrets. This is correct for IdP-managed secrets but may cause confusion for application secrets like `registry_api_token` -- if the user rotates the token externally (e.g., in AWS console), Terraform will not detect the drift.

### Recommendations
1. Extract common secrets into a local in locals.tf:
   ```hcl
   locals {
     common_secrets = var.okta_enabled ? concat([...]) : []
   }
   ```
2. Use `random_password` with `override_special` for Grafana only if a meaningful default is acceptable, otherwise make `grafana_admin_password` a required variable when `enable_observability = true`.
3. Document the `ignore_changes` behavior clearly so operators know when Terraform will and won't update secret values.

### Verdict: APPROVED WITH CHANGES

---

## SRE/DevOps Engineer (Circuit)

### Strengths
- The phased rollout plan (non-production first, then production staged) is the correct approach for a security-sensitive change.
- The IAM policy update correctly uses `concat()` with conditional expressions, maintaining the existing pattern.
- The estimated line counts are realistic and the file changes are focused (only 3 files in the module).
- CloudTrail monitoring for `GetSecretValue` calls is noted as an observability control.

### Concerns
- The IAM policy `ecs_secrets_access` will grow significantly. With ~28 total secret ARNs in the Resource list, the policy JSON will approach 3-4 KB. This is under the 6144-byte soft limit but leaves little headroom for future growth. Consider using IAM Permission Boundaries or a separate policy per service if more secrets are added later.
- No mention of Secrets Manager access logging (via CloudTrail Data Events). Without data plane logging, you cannot audit which services accessed which secrets. This is a common compliance requirement (SOC2, PCI-DSS).
- The Docker Compose migration is deferred with "document as future work." However, Docker Compose is commonly used for local development and CI. If secrets in `.env` files are not also addressed, developers will have plaintext secrets in their local environments and CI configurations.
- The `terraform plan` output will now include changes to ~25 environment variable removals across two services. This is a large plan that could mask unintended changes. Suggest using `terraform plan -target` for staged rollout.

### Recommendations
1. Enable CloudTrail Data Events for the Secrets Manager KMS key to audit all secret access:
   ```yaml
   # AWS Config rule or CloudFormation template addition
   EnableLogging: true for the KMS key's CloudTrail
   ```
2. Create a separate IAM policy for each service's unique secrets (e.g., `registry_secrets_access`, `auth_server_secrets_access`) and attach only the relevant ones. This follows least-privilege.
3. Address Docker Compose secrets in this PR or create a tracking issue with a deadline.
4. Add a `lifecycle { prevent_destroy = true }` to all Secrets Manager resources to prevent accidental deletion.

### Verdict: APPROVED WITH CHANGES

---

## Security Engineer (Cipher)

### Strengths
- This is a significant security improvement. Moving ~20 sensitive values from plaintext environment variables to Secrets Manager eliminates exposure in:
  - Terraform state files (encrypted at rest, but still readable)
  - `terraform plan` output (visible in CI logs)
  - ECS task definition API responses (publicly describable)
  - CloudFormation template outputs
- All new secrets use the same KMS key as existing secrets, maintaining consistent encryption.
- The `sensitive = true` attribute is already present on most variables, correctly preventing them from appearing in `terraform output`.
- The conditional creation pattern (`count = var.<feature>_enabled ? 1 : 0`) correctly prevents unused secrets from being created.
- The design correctly identifies that Helm charts already use Kubernetes Secret objects (no change needed) and Docker Compose needs separate handling.

### Concerns
- The `GITHUB_APP_PRIVATE_KEY` is a PEM-formatted private key with full GitHub repository access. Storing it in the same KMS key as application secrets is fine from a technical standpoint, but the risk profile is higher. A compromise of the Secrets Manager access would grant the attacker full GitHub repository access.
- The `FEDERATION_ENCRYPTION_KEY` is a Fernet key used to encrypt data at rest in MongoDB. If this key is rotated in Secrets Manager without updating MongoDB, all federation data becomes unreadable. The design does not address key versioning or migration for this critical secret.
- The `auth0_management_api_token` expires after 24 hours (as noted in the variable description). Storing it in Secrets Manager is the right approach, but there is no rotation schedule defined. Someone must manually update this secret daily, which is error-prone.
- No mention of Secrets Manager access policies beyond IAM. Consider adding a resource-based policy on the Secrets Manager secrets that restricts access to specific IAM principals or conditions (e.g., MFA required for secret read).
- The design does not address secret versioning. When a user updates a secret via Terraform, the old version is still accessible in Secrets Manager. For compliance, consider setting `recovery_window_in_days = 0` (already done) and implementing a secret rotation schedule.

### Recommendations
1. Add a separate KMS key for GitHub-related secrets to limit blast radius if the key is compromised.
2. Implement a documented process for `FEDERATION_ENCRYPTION_KEY` rotation that includes MongoDB migration steps.
3. Create an AWS Lambda function or CloudWatch Events rule that alerts when `auth0_management_api_token` is older than 23 hours.
4. Add a Secrets Manager resource-based policy requiring MFA for secret reads:
   ```json
   {
     "Condition": {"Bool": {"aws:MultiFactorAuthPresent": "true"}}
   }
   ```
5. Implement secret rotation for high-value secrets (registry_api_token, github_pat) using AWS Secrets Manager rotation Lambda templates.
6. Add `prevent_destroy = true` lifecycle rule to all critical secrets to prevent accidental deletion.

### Verdict: APPROVED WITH CHANGES

---

## SMTS (Sage) -- Overall Architecture Review

### Strengths
- The design is thorough and follows the established patterns in the codebase. The use of `concat()` for building the secrets list, conditional creation with `count`, and the per-secret Secrets Manager resources are all consistent with existing code.
- The LLD provides concrete code examples for each change, making it actionable for an implementer.
- The file-by-file breakdown with line number estimates is realistic.
- The alternatives analysis (SSM, single JSON, External Secrets Operator) is well-reasoned.
- The migration plan acknowledges that Docker Compose and Helm have different concerns and treats them appropriately.

### Concerns
- The scope of this change is significant: ~280 lines of new code in secrets.tf, ~30 lines of modifications across three files. The `terraform plan` output will be noisy with many additions and removals. Suggest breaking this into two PRs:
  1. PR 1: Add new secrets resources + update IAM (infrastructure change only)
  2. PR 2: Remove plain-text env vars + add ECS secrets block entries (deployment change)
  This allows review of the infrastructure changes independently from the deployment changes.
- The `secrets` block entries in ecs-services.tf are duplicated between the auth-server and registry services. Consider extracting common entries into a local variable in locals.tf to reduce duplication and maintenance burden.
- The LLD does not address the `keycloak_db_secret` and `keycloak_admin_password` used in the Keycloak ECS deployment (`keycloak-ecs.tf`), which may also have plain-text env vars that should be migrated. This is likely out of scope but should be noted.
- The estimated effort (~300 lines) is reasonable, but the review effort is disproportionate. A single PR with this many changes will require extensive review across multiple personas.

### Recommendations
1. Split into two PRs as described above to improve review quality.
2. Extract common secrets entries into a local variable in locals.tf.
3. Consider creating a `.github/CODEOWNERS` entry for `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` and `iam.tf` so that security team members are automatically requested as reviewers for any secrets-related changes.
4. Add a changelog entry documenting this security improvement.

### Verdict: APPROVED WITH CHANGES

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | N/A | 0 | Not applicable -- no UI changes |
| Backend (Byte) | APPROVED WITH CHANGES | 1 | Fix Grafana random_password behavior; extract common secrets to local |
| SRE (Circuit) | APPROVED WITH CHANGES | 2 | Enable CloudTrail Data Events for Secrets Manager; address Docker Compose; separate IAM policies per service |
| Security (Cipher) | APPROVED WITH CHANGES | 3 | Separate KMS key for GitHub secrets; implement rotation for high-value secrets; add MFA condition; implement key versioning for FEDERATION_ENCRYPTION_KEY |
| SMTS (Sage) | APPROVED WITH CHANGES | 1 | Split into two PRs; extract common secrets to locals.tf |

### Combined Blockers (prioritized)

1. **Grafana random_password must not regenerate on every apply** (Byte) -- This is a production-blocking issue.
2. **Enable CloudTrail Data Events for Secrets Manager** (Circuit) -- Compliance requirement.
3. **Separate KMS key for GitHub-related secrets** (Cipher) -- Security best practice for high-privilege credentials.
4. **Split into two PRs** (Sage) -- Improves review quality for a large change.
5. **Address Docker Compose** (Circuit) -- Local development security gap.

### Next Steps

1. Address the Grafana random_password issue in the LLD (either make grafana_admin_password mandatory or use a stable default).
2. Implement the two-PR split: PR 1 for secrets resources + IAM, PR 2 for ECS container definition changes.
3. Extract common secrets entries into a local variable in locals.tf.
4. Create a tracking issue for Docker Compose migration.
5. Enable CloudTrail Data Events for the Secrets Manager KMS key before or immediately after deployment.