# Expert Review: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-06-25*
*Author: Multi-Persona Review Team*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | NOT APPLICABLE | 0 | N/A - No UI changes |
| Backend (Byte) | **APPROVED WITH CHANGES** | 2 | Add fallback mechanism, proper error handling |
| SRE (Circuit) | **APPROVED WITH CHANGES** | 2 | Consider sidecar pattern, documentation |
| Security (Cipher) | **APPROVED** | 0 | IAM auth is a security improvement |
| SMTS (Sage) | **APPROVED WITH CHANGES** | 1 | Ensure backward compatibility, testing strategy |

---

## Reviewer Details

### Frontend Engineer (Pixel)

**Focus:** UI/UX, components, state, API integration

**Assessment:** NOT APPLICABLE - This is a backend infrastructure change with no user-facing UI components.

**Verdict:** N/A

---

### Backend Engineer (Byte)

**Focus:** API design, data models, business logic, performance

**Strengths:**
- IAM database authentication is the AWS-recommended approach
- The design correctly identifies all the files that need modification
- The token generation approach is sound

**Concerns:**
1. **Token Lifetime Limitation (HIGH):** IAM tokens expire after 15 minutes. If Keycloak holds long-running database connections, they will fail when the token expires. This needs a token refresh mechanism.

2. **ECS Init Container Complexity (MEDIUM):** The init container approach requires EFS volume sharing. If the task doesn't have EFS attached, this will fail. Need a fallback or alternative.

**Recommendations:**
1. Add a token refresh sidecar that updates the token in a shared volume before expiry
2. Implement retry logic with exponential backoff for failed token generations
3. Consider using a Lambda function that pre-generates the token before ECS task starts

**Questions for Author:**
- How will Keycloak handle a token that expires during a database transaction?
- What happens if the IAM token generation fails due to network issues?

**Verdict:** APPROVED WITH CHANGES

---

### SRE / DevOps Engineer (Circuit)

**Focus:** Deployment, monitoring, scaling, infrastructure

**Strengths:**
- The Terraform changes follow existing patterns in the codebase
- IAM auth is well-supported in AWS and requires minimal infrastructure changes
- The deployment surface checklist is comprehensive

**Concerns:**
1. **Rollback Complexity (HIGH):** If IAM auth fails in production, rolling back to password auth requires:
   - Reverting Terraform changes
   - Regenerating the Secrets Manager secret with the password
   - Restarting Keycloak tasks
   
   This could cause downtime. Consider a dual-mode approach.

2. **Monitoring Gaps (MEDIUM):** No mention of new CloudWatch metrics or alarms for IAM auth failures. Need to track:
   - IAM token generation failures
   - Token refresh failures
   - Database connection failures due to expired tokens

**Recommendations:**
1. Create CloudWatch alarms for:
   - `IAMTokenGenerationFailed` - Lambda/task script failures
   - `DBConnectionFailed-IAM` - Password auth failures (if dual-mode)
2. Add tracing spans for IAM token generation and database connection
3. Consider the sidecar pattern for token refresh instead of init container

**Deployment Checklist:**
- [ ] Test IAM auth in a non-production environment first
- [ ] Document rollback procedure
- [ ] Set up monitoring before deploying to production
- [ ] Verify IAM permissions are correct before deployment

**Verdict:** APPROVED WITH CHANGES

---

### Security Engineer (Cipher)

**Focus:** AuthN/AuthZ, validation, OWASP, data protection

**Strengths:**
- IAM database authentication is a significant security improvement over static passwords
- Eliminates the risk of password leakage through Secrets Manager
- Tokens are short-lived and automatically rotated

**Concerns:**
1. **IAM Policy Scope (LOW):** The `rds-db:connect` permission uses a wildcard for the DB user pattern. Consider constraining this further:

   ```hcl
   # More restrictive approach
   Resource = "arn:aws:rds-db:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:dbuser/${aws_rds_cluster.keycloak.master_username}/keycloak"
   ```

2. **Credential Exposure Risk (MEDIUM):** If the IAM token generation script fails, it should not log or expose the token in plaintext. The token should only be accessible to the Keycloak container.

**Recommendations:**
1. Use least-privilege IAM policies - constrain the resource ARN to specific DB users
2. Ensure token generation logs don't capture the actual token value
3. Add WAF rules to prevent unauthorized access to IAM auth endpoints

**Better Alternatives Considered:**
- AWS Secrets Manager with rotation - less secure than IAM auth
- ECS Secrets with KMS encryption - still involves storing passwords

**Verdict:** APPROVED

---

### SMTS / Architect (Sage)

**Focus:** Architecture, code quality, maintainability

**Strengths:**
- The solution aligns with AWS architectural best practices
- The separation of concerns is clear (Terraform, ECS, IAM)
- Documentation is comprehensive

**Concerns:**
1. **Maintainability (HIGH):** The new IAM auth mode adds complexity to the deployment. Consider:
   - How easy will it be for a new engineer to understand and debug this?
   - Are there runbooks for common failures?
   - Will the documentation stay up to date?

2. **Migration Path (MEDIUM):** The LLD mentions removing the password but doesn't clearly define:
   - What happens during a mixed-mode transition?
   - How do we verify the new auth method is working?
   - What metrics indicate successful migration?

**Recommendations:**
1. Create a migration runbook documenting:
   - Pre-migration checklist
   - Deployment steps
   - Post-migration verification
   - Rollback procedure
2. Add a status endpoint or health check that reports:
   - Current auth method (password vs IAM)
   - Token expiry time
   - Last successful connection
3. Consider setting up a smoke test that validates IAM auth works end-to-end

**Verdict:** APPROVED WITH CHANGES

---

## Summary Table

| Category | Status | Notes |
|----------|--------|-------|
| Security | IMPROVED | IAM auth is more secure than static passwords |
| Maintainability | MODERATE | New complexity requires documentation |
| Deployment Risk | LOW-MEDIUM | Rollback requires manual intervention |
| Monitoring | MISSING | Need new CloudWatch metrics/alarm |

---

## Blockers Summary

| Issue | Severity | Resolution |
|-------|----------|------------|
| Token expiry during long-running transactions | HIGH | Implement token refresh sidecar |
| Rollback complexity | MEDIUM | Document rollback procedure, consider dual-mode |

---

## Next Steps

1. **Address High-Priority Items:**
   - Implement token refresh mechanism (sidecar or init container)
   - Define and document rollback procedure

2. **Add Observability:**
   - Create CloudWatch metrics for IAM token generation
   - Set up alarms for authentication failures

3. **Prepare for Production:**
   - Deploy to staging environment
   - Run security audit on IAM policies
   - Create runbook and documentation

4. **Finalize Design:**
   - Update LLD with token refresh approach
   - Add testing plan for IAM-specific failure scenarios
