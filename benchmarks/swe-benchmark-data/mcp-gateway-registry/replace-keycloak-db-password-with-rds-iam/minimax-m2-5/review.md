# Expert Review: Replace Keycloak DB Password with RDS IAM Auth

*Created: 2026-06-25*
*Reviewer: Claude (minimax-m2-5)*
*Related LLD: `./lld.md`*

---

## Reviewers

| Role | Reviewer | Focus |
|------|----------|-------|
| Backend Engineer | Byte | API design, data models, business logic, performance |
| SRE/DevOps Engineer | Circuit | Deployment, monitoring, scaling, infrastructure |
| Security Engineer | Cipher | AuthN/AuthZ, validation, OWASP, data protection |

---

## Backend Reviewer: Byte

### Strengths

1. **Clean approach to token generation**: The entrypoint script pattern is well-established in the ECS community and allows short-lived token generation without modifying the Keycloak application code.

2. **Proper handling of DB URL**: Adding `iamauthcache=1` to the JDBC URL ensures tokens are cached within a connection, avoiding per-query token generation overhead.

3. **Feature flagging**: Using a `keycloak_use_iam_auth` variable allows for gradual rollout and easy rollback.

### Concerns

1. **null_resource for DB user creation**: The `null_resource` with `local-exec` to create the IAM-enabled database user is fragile. If the initial apply fails, re-running may fail with "user already exists" errors. The `CREATE USER IF NOT EXISTS` helps, but the AWS CLI call itself could fail for other reasons.

2. **DB URL parsing in entrypoint**: The current approach uses `sed` to extract hostname from `KC_DB_URL`. This is fragile if the JDBC URL format changes. Consider passing the hostname as a separate environment variable.

3. **No connection retry logic**: If the IAM token generation succeeds but the database connection fails (transient network issue), the container exits immediately without retry.

### New Libraries / Infra Dependencies Required

- **AWS CLI**: Already available in ECS-optimized AMI - no new dependency
- **No new libraries**: Uses existing AWS SDK patterns

### Better Alternatives Considered

1. **Use AWS SDK instead of CLI for token generation**: The AWS CLI adds startup time. Using the AWS SDK in Python could be faster, but adds complexity.

2. **Sidecar container for token generation**: Could run a sidecar that writes the token to a shared volume, but adds complexity without much benefit.

### Recommendations

1. **Improve DB user creation**: Use a separate module or terraform data source to handle the user creation idempotently. Consider using `local-exec` with `continue_on_error = true` to handle transient failures.

2. **Add KC_DB_PASSWORD_HOST environment variable**: Instead of parsing KC_DB_URL, pass the hostname directly:
   ```bash
   export KC_DB_PASSWORD=$(aws rds generate-db-auth-token \
       --hostname "${KC_DB_PASSWORD_HOST}" \
       --port 3306 \
       --username "${KC_DB_USERNAME}" \
       --region "${AWS_REGION}")
   ```

3. **Add startup retry**: Consider adding a simple retry loop with backoff in the entrypoint script.

### Questions for Author

1. How do you plan to test this in a non-production environment first? Will you use a separate RDS cluster or enable IAM auth on the existing one?
2. What's the expected cold-start time impact from token generation (~1-2 seconds)?

### Verdict: APPROVED WITH CHANGES

**Blockers:** 1 (null_resource fragility)
**Recommendations:** 3

*Please address the null_resource DB user creation concern before implementation.*

---

## SRE/DevOps Reviewer: Circuit

### Strengths

1. **Zero-downtime design**: The design correctly identifies that ECS will gracefully drain connections during task replacement, preserving Keycloak sessions.

2. **Proper IAM policy scoping**: The `rds-db:connect` permission is correctly scoped to the specific cluster and username, following least privilege.

3. **RDS Proxy support**: The design accounts for the existing RDS Proxy and updates its auth configuration, not just the cluster.

4. **CloudWatch logging**: The entrypoint includes logging for token generation, important for debugging.

### Concerns

1. **Secrets Manager cleanup**: The LLD mentions two options for the Secrets Manager secret (keep username only, or delete entirely), but doesn't specify which to use. The deletion could cause issues if any other system expects the secret to exist.

2. **Password rotation Lambda**: The LLD says "may need modification" but doesn't specify what to do with the existing Lambda. If we keep the secret with only username, the Lambda will fail when trying to rotate a password that no longer exists.

3. **Terraform state concerns**: After removing the password variable, existing Terraform state still contains the old password value. Need to ensure this is handled.

### New Libraries / Infra Dependencies Required

- **None**: Uses existing AWS services and patterns

### Better Alternatives Considered

1. **Complete secret deletion**: Deleting the Secrets Manager secret entirely is cleaner but requires ensuring nothing else depends on it.

2. **Lambda-triggered approach**: Instead of entrypoint-based token generation, could use a Lambda that generates tokens and stores them in Secrets Manager with short TTL. This was rejected because it still stores credentials at rest.

### Recommendations

1. **Explicitly decide on secret handling**: Choose Option A (username only) or Option B (delete secret) and document the decision clearly in the implementation plan.

2. **Handle the rotation Lambda**: If using Option A (username only), the rotation Lambda should be disabled or deleted. If deleting the Lambda, run `terraform destroy` on the rotation resources first.

3. **Add TFSTATE handling**: Document how to handle the existing Terraform state containing the old password (consider `terraform state rm` for the sensitive value or accept that the encrypted state contains the old value).

4. **Add health check verification**: After deployment, verify the RDS Proxy is using IAM auth:
   ```bash
   aws rds describe-db-proxy --db-proxy-name keycloak-proxy \
     --query 'DBProxies[0].Auth[0].IAMAuth'
   ```

### Questions for Author

1. Which secret handling approach (keep username / delete entirely) is recommended?
2. Should the password rotation Lambda be disabled or deleted?
3. What's the rollback timeline if IAM auth fails in production (how fast can you detect and respond)?

### Verdict: APPROVED WITH CHANGES

**Blockers:** 1 (Lambda handling)
**Recommendations:** 4

*Please explicitly document what to do with the Secrets Manager secret and rotation Lambda before implementation.*

---

## Security Engineer: Cipher

### Strengths

1. **Eliminates long-lived credentials**: This is the primary security improvement - no static passwords stored anywhere.

2. **Short-lived tokens**: IAM tokens are valid for ~15 minutes, significantly reducing the window of exposure if credentials are compromised.

3. **Least-privilege IAM**: The `rds-db:connect` permission is scoped to the specific database user, not `*`.

4. **No credentials at rest**: The IAM token is generated at runtime and never stored.

### Concerns

1. **Entrypoint script runs as root**: The entrypoint script runs as root in the container. If an attacker gains container access, they can generate their own tokens. However, this is a minor concern since the IAM role already limits what can be done.

2. **AWS credentials in container**: The entrypoint uses the ECS task role's AWS credentials to generate the DB token. If these credentials are compromised, an attacker could generate tokens for any database the role has access to. This is mitigated by the task role having minimal other permissions.

3. **Token not rotated mid-flight**: Once a container starts with its IAM token, the token remains valid for ~15 minutes even if the ECS task role permissions are revoked. This is acceptable given the short TTL.

4. **Logging token in debug mode**: The LLD mentions "Token generation success" at DEBUG level. Be careful not to accidentally log the actual token value.

### New Libraries / Infra Dependencies Required

- **None**: Uses existing AWS patterns

### Better Alternatives Considered

1. **Use instance metadata service (IMDS) v2**: Ensure ECS task uses IMDSv2 for AWS credential retrieval, preventing credential theft via metadata API (already available in Fargate).

2. **Rotate ECS task role frequently**: Could implement automatic role rotation, but this adds significant complexity for marginal security gain.

### Recommendations

1. **Explicitly disable DEBUG logging of tokens**: Add a check in the entrypoint to ensure the token value is never written to logs, even at DEBUG level:
   ```bash
   # Log only that token was generated, not the token itself
   echo "[INFO] RDS IAM auth token generated"
   ```

2. **Consider adding IP restrictions**: The RDS IAM auth can be restricted to specific VPC or security group. Consider adding this as an additional layer.

3. **Document audit trail**: Ensure security team knows that:
   - `rds-db:connect` calls are logged in CloudTrail
   - Database connections will show as authenticated via IAM
   - The old Secrets Manager access will no longer be needed

4. **Verify IAM user creation**: After implementing, verify the database user was created with IAM auth:
   ```sql
   SELECT user, host, plugin FROM mysql.user WHERE user='keycloak';
   -- Should show: keycloak | % | auth_pam
   ```
   Wait - for Aurora MySQL it's `AWSAUTH`, not `auth_pam`. Verify with:
   ```bash
   aws rds execute-db-statement --sql "SHOW CREATE USER 'keycloak'@'%'"
   ```

### Questions for Author

1. Are there any IP-based restrictions that should be added to the IAM auth policy?
2. How quickly can you revoke access if needed (what's the process to remove the IAM role or policy)?
3. Is there a plan to audit that the old Secrets Manager secret was actually removed/deleted?

### Verdict: APPROVED

**Blockers:** 0
**Recommendations:** 4

*The security improvements are significant and the risks are well-mitigated. No blocking issues.*

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Backend (Byte) | APPROVED WITH CHANGES | 1 | Improve null_resource handling; add separate DB host env var; add retry |
| SRE (Circuit) | APPROVED WITH CHANGES | 1 | Decide on secret deletion; handle rotation Lambda; add TFSTATE note |
| Security (Cipher) | APPROVED | 0 | Ensure no token logging; document audit trail; verify IAM user creation |

### Summary Statistics
- **Total Reviewers:** 3
- **Approved:** 1
- **Approved with Changes:** 2
- **Needs Revision:** 0
- **Total Blockers:** 2
- **Total Recommendations:** 11

### Critical Findings

1. **null_resource DB user creation** (Backend): The current approach may fail on re-runs or have idempotency issues
2. **Rotation Lambda handling** (SRE): Unclear what to do with existing Lambda after IAM migration

### Next Steps

1. Address the two blockers before implementation
2. Decide on Secrets Manager secret handling (keep username or delete)
3. Add environment variable for DB hostname instead of URL parsing
4. Test thoroughly in non-production first
5. Plan rollback procedure with stakeholders