# Expert Review: Replace Keycloak DB Password with RDS IAM Authentication

*Created: 2026-07-06*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Reviewers

| Role | Reviewer | Focus |
|------|----------|-------|
| Backend Engineer | Byte | API design, data models, business logic, performance |
| SRE/DevOps Engineer | Circuit | Deployment, monitoring, scaling, infrastructure |
| Security Engineer | Cipher | AuthN/AuthZ, validation, OWASP, data protection |
| SMTS (Overall) | Sage | Architecture, code quality, maintainability |

---

## Backend Engineer (Byte)

### Strengths
- Clear identification of all files that reference the Keycloak database password.
- The step-by-step implementation plan is detailed enough for an implementer to follow.
- Correctly identifies that docker-compose (PostgreSQL) is not affected -- important separation of concerns.

### Concerns
1. **TC01: Missing `master_password` handling for existing clusters.** The LLD acknowledges this in "Open Questions" but does not provide a definitive migration path. AWS RDS does not allow removing `master_password` from an existing cluster -- it is a required field. The workaround is to keep the field but stop referencing the Terraform variable, and instead derive the password from the Secrets Manager secret at plan time. This needs a concrete approach.
2. **TC02: Token generation uses the AWS CLI in the entrypoint.** The LLD assumes the ECS container image includes the AWS CLI. The official Keycloak image (quay.io/keycloak/keycloak) does NOT include the AWS CLI or boto3. The image would need to be custom-built or the token generation would need to happen via an init container / sidecar. This is a significant oversight.
3. **TC03: RDS Proxy IAM auth for MySQL.** The LLD proposes updating the RDS Proxy to `auth_scheme = "AWS_IAM"`. However, RDS Proxy IAM authentication for MySQL requires MySQL 5.7+ or Aurora MySQL 3.0+ with specific configuration. The current cluster runs `aurora-mysql8.0` which should be compatible, but this should be explicitly verified.

### Recommendations
- Use a sidecar container or init container for token generation, OR build a custom Keycloak image with the AWS CLI pre-installed.
- Keep `master_password` in the cluster resource but source it from the Secrets Manager secret at apply time (not from a variable). Document that the password is no longer used for authentication but is retained to satisfy the RDS API requirement.

### Questions for Author
- Q1: Does the Keycloak Docker image include the AWS CLI? If not, how will the entrypoint generate tokens?
- Q2: What is the exact migration path for removing `master_password` from an existing RDS cluster?

### Verdict
**APPROVED WITH CHANGES** -- 2 blockers (TC01, TC02), 1 moderate (TC03).

---

## SRE/DevOps Engineer (Circuit)

### Strengths
- Good identification of the RDS Proxy configuration changes needed.
- The rollback plan is reasonable: revert Terraform, redeploy previous task definition.
- Correctly notes that the rotation Lambda handles only Keycloak DB (not DocumentDB), so it can be fully deleted.

### Concerns
1. **TC01: Deployment window required.** Removing `master_password` from the cluster or changing the RDS Proxy auth scheme are breaking changes that require a maintenance window. The rollout plan should explicitly call this out and include a fallback strategy (e.g., keep password auth as a temporary parallel path during the transition).
2. **TC02: Token expiration during long-running connections.** The LLD notes that tokens expire in 15 minutes but does not address what happens if Keycloak's database connection is long-lived and the token expires. Keycloak does not have a built-in mechanism to refresh the database password at runtime. The container would need to either: (a) restart before the token expires (not reliably controllable on Fargate), or (b) have a mechanism to refresh the token while running.
3. **TC03: No monitoring/alerting for IAM auth failures.** The LLD does not define any CloudWatch alarms for RDS authentication failures. If IAM auth stops working (e.g., IAM policy misconfigured), all Keycloak connections will fail silently.

### Recommendations
- Add a CloudWatch alarm on the `UnauthorizedAccess` and `DBUserNotAuthorized` RDS metrics.
- Implement token refresh in the entrypoint or as a periodic cron job (e.g., a separate Lambda that runs every 10 minutes to keep the token fresh).
- Consider keeping the password-based auth as a fallback during a parallel-run period (e.g., both auth methods enabled for 24 hours).

### Verdict
**APPROVED WITH CHANGES** -- 2 blockers (TC01, TC02), 1 moderate (TC03).

---

## Security Engineer (Cipher)

### Strengths
- Excellent security improvement: eliminating static credentials from the entire stack.
- Correctly scopes the `rds-db:Connect` permission to the specific user ARN (principle of least privilege).
- Good identification that the Secrets Manager secret, rotation Lambda, and associated IAM policies should all be removed.
- The checkov skip removal (CKV_AWS_162) is the right thing to do.

### Concerns
1. **TC01: Token in environment variables.** The LLD proposes passing the IAM auth token via `KC_DB_PASSWORD` environment variable. Environment variables are visible in the ECS task metadata endpoint, in CloudWatch Logs if any process dumps the environment, and in `ecs describe-tasks` API calls. While the token expires in 15 minutes, this is still a transient exposure surface. Consider using a secret (AWS Secrets Manager or SSM SecureString) that the entrypoint writes to and Keycloak reads from via file mount instead.
2. **TC02: KMS key retention.** The KMS key `aws_kms_key.rds` is used for RDS encryption AND for encrypting the SSM parameters and Secrets Manager secrets. After removing the secrets, check if the KMS key is still referenced by anything (the RDS cluster's `kms_key_id` keeps it alive, so it should be fine).
3. **TC03: MySQL user creation via manual SQL.** The LLD suggests documenting SQL commands for manual execution. Manual steps are a security anti-pattern -- there should be an audit trail. A Lambda that creates the user and logs the operation provides both automation and auditability.

### Recommendations
- Write the token to a file (e.g., `/run/secrets/kc_db_password`) and mount it as a secret in the container, rather than passing it as an environment variable.
- Use an automated Lambda for MySQL user creation with CloudTrail audit logging.
- Verify that the RDS Proxy's `require_tls = false` is acceptable with IAM auth. IAM auth tokens are inherently short-lived and scoped, but adding TLS adds defense-in-depth.

### Verdict
**APPROVED WITH CHANGES** -- 1 blocker (TC01), 2 moderate (TC02, TC03).

---

## SMTS (Sage)

### Strengths
- The design is thorough and well-structured.
- Good use of existing patterns (Terraform locals for ARN construction, SSM parameters for configuration).
- The alternatives considered section shows thoughtful analysis of different approaches.
- Clear file-level change map with estimated lines of code.

### Concerns
1. **TC01: The AWS CLI in the Keycloak image problem is fundamental.** This is not a minor implementation detail -- it is a blocker. The official Keycloak image does not include the AWS CLI. The LLD must decide: (a) build a custom Keycloak image, (b) use an init container, or (c) use a sidecar. Each has different operational implications. This decision affects the Dockerfile, the ECS task definition, and the deployment process.
2. **TC02: Parallel transition period is missing.** The LLD jumps from "enable IAM auth" to "remove password." A safer approach is a parallel period where both auth methods work, allowing verification before the cutover. This is especially important for Keycloak, which is the identity provider for the entire platform.
3. **TC03: The `null_resource` approach for MySQL user creation is not mentioned.** The LLD mentions it as Option C but dismisses it. However, `null_resource` with a remote-exec provisioner (if the cluster has SSH access) or with a `local-exec` provisioner running a Terraform-provisioned Lambda is the most Terraform-idiomatic approach.

### Recommendations
- Decide on the token generation approach (custom image / init container / sidecar) before starting implementation -- this is a prerequisite for the LLD.
- Add a "parallel run" phase to the rollout plan where both auth methods are enabled.
- Consider using a Terraform data source to read the current `master_password` from the Secrets Manager secret to avoid API errors when removing the variable.

### Verdict
**APPROVED WITH CHANGES** -- 1 blocker (TC01), 2 moderate (TC02, TC03).

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Backend (Byte) | APPROVED WITH CHANGES | 2 | Add AWS CLI to Keycloak image; clarify master_password migration |
| SRE (Circuit) | APPROVED WITH CHANGES | 2 | Add CloudWatch alarms; handle token refresh during runtime |
| Security (Cipher) | APPROVED WITH CHANGES | 1 | Pass token via file mount, not env var |
| SMTS (Sage) | APPROVED WITH CHANGES | 1 | Decide token generation approach before implementation |

### Consolidated Blockers (across all reviewers)
1. **AWS CLI availability in Keycloak image** (Byte, Sage): The official Keycloak image does not include the AWS CLI. A decision must be made on how to generate IAM auth tokens.
2. **master_password cannot be removed from existing RDS cluster** (Byte): AWS RDS requires `master_password` on all clusters. It must be retained but sourced from a secret rather than a variable.
3. **Token expiration during long-running connections** (Circuit): Keycloak does not refresh the DB password at runtime. A token refresh mechanism is needed.
4. **Token in environment variables** (Cipher): IAM tokens in env vars are visible in ECS metadata. Use a file mount instead.

### Moderate Concerns
1. RDS Proxy IAM auth compatibility with Aurora MySQL 8.0 (Byte).
2. No monitoring for IAM auth failures (Circuit).
3. Manual SQL for MySQL user creation should be automated (Cipher).
4. No parallel transition period (Sage).

### Next Steps
1. Resolve all 4 blockers before implementation begins.
2. The LLD needs a Section 8 update to address the token generation approach.
3. Add a CloudWatch alarm definition to the Terraform (Section 11 file changes).
4. Revise the rollout plan to include a parallel-run phase.