# Expert Review: Migrate Sensitive ECS Environment Variables to AWS Secrets Manager

*Created: 2026-06-25*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED | 0 | None - this is a backend-only change |
| Backend (Byte) | APPROVED WITH CHANGES | 1 | Add migration script for existing secrets |
| SRE (Circuit) | APPROVED WITH CHANGES | 2 | Add monitoring alerts for secret retrieval failures |
| Security (Cipher) | APPROVED | 0 | Consider adding secret access auditing |
| SMTS (Sage) | APPROVED WITH CHANGES | 1 | Add Terraform version constraints for Secrets Manager features |

---

## Frontend Engineer (Pixel)

### Strengths
- This change is purely backend infrastructure; no UI changes required
- The `secrets` block in ECS task definitions is transparent to frontend code
- No breaking changes to API contracts or user-facing functionality

### Concerns
- None identified

### New Dependencies
- None required

### Better Alternatives Considered
- No alternative UI impact found

### Recommendations
- No action required

### Questions for Author
- None

### Verdict
**APPROVED** - This is a backend-only change with no UI impact.

---

## Backend Engineer (Byte)

### Strengths
- Well-structured plan following existing patterns in `keycloak-ecs.tf`
- Proper use of conditional secret injection based on provider flags
- Clear separation of concerns (secrets vs. configuration)

### Concerns

1. **Migration of Existing Secrets (BLOCKER)**
   The current implementation only covers *new* secrets. There are already plaintext secrets in Terraform state files from previous deployments. These must be migrated:
   - `embeddings_api_key` (already has `sensitive = true` but stored in state)
   - `entra_client_secret` (already has `sensitive = true`)
   - `keycloak_admin_password` (var, not yet in Secrets Manager)
   
   **Recommendation:** Add a migration script that:
   1. Reads current secret values from Terraform state
   2. Creates Secrets Manager secrets with those values
   3. Updates task definitions
   4. Outputs confirmation of migration completion

### New Dependencies
- None required - AWS SDK handles Secrets Manager API calls

### Better Alternatives Considered
- Using AWS Systems Manager Parameter Store with `SecureString` type - rejected in favor of Secrets Manager for better security features

### Recommendations
1. Add migration script to handle existing secrets in Terraform state
2. Document the migration process clearly for operational teams
3. Consider using `depends_on` to ensure secrets are created before task definitions

### Questions for Author
1. Will there be a migration plan for existing secrets stored in Terraform state?
2. How will we handle the case where a secret value needs to be rotated mid-deployment?

### Verdict
**APPROVED WITH CHANGES** - Cannot proceed without migration plan for existing state-stored secrets.

---

## SRE/DevOps Engineer (Circuit)

### Strengths
- Clear file change plan with line estimates
- Proper use of IAM policies for least-privilege secret access
- Follows AWS best practices for ECS secret management

### Concerns

1. **Secret Retrieval Failure Monitoring (BLOCKER)**
   When a secret cannot be retrieved, ECS tasks will fail to start. We need visibility into these failures:
   
   **Recommendation:** Add CloudWatch Logs filter and alarm:
   ```
   Pattern: "Failed to retrieve secret" OR "secretsmanager:GetSecretValue"
   ```
   
   And CloudWatch Alarm:
   ```tf
   resource "aws_cloudwatch_metric_alarm" "secret_retrieval_failures" {
     alarm_name        = "mcp-gateway-secret-retrieval-failures"
     comparison_operator = "GreaterThanThreshold"
     evaluation_periods  = "1"
     metric_name         = "SecretRetrievalFailures"
     namespace           = "AWS/SecretsManager"
     period              = "300"
     statistic           = "Sum"
     threshold           = "1"
     alarm_description   = "This metric alerts on failed secret retrievals from Secrets Manager"
   }
   ```

2. **Task Definition Revision Cleanup (BLOCKER)**
   Each `terraform apply` will create a new task definition revision. Without cleanup, this could lead to hitting AWS service limits (10,000 revisions per task definition).
   
   **Recommendation:** Add a retention policy to clean up old revisions:
   ```tf
   resource "aws_ecs_task_definition" "keycloak" {
     # ... existing config ...
     
     lifecycle {
       ignore_changes = [revision]
     }
   }
   ```

### New Dependencies
- None - CloudWatch alarms can be added natively in Terraform

### Better Alternatives Considered
- None - the proposed monitoring approach is standard AWS practice

### Recommendations
1. Add CloudWatch alarms for secret retrieval failures
2. Implement task definition revision cleanup policy
3. Document rollback procedures for secret-related failures
4. Consider adding health check endpoints that verify secret access

### Questions for Author
1. How will we monitor for secret retrieval failures?
2. What is the rollback plan if a secret cannot be retrieved during deployment?

### Verdict
**APPROVED WITH CHANGES** - Cannot proceed without monitoring and cleanup strategy.

---

## Security Engineer (Cipher)

### Strengths
- Secrets Manager provides encryption at rest using KMS
- Secrets are not visible in ECS task definition JSON
- IAM policies implement least-privilege access
- All sensitive variables are marked with `sensitive = true`

### Concerns

1. **Secret Access Auditing (LOW severity)**
   While not a blocker, we should enable auditing of secret access:
   
   **Recommendation:** Enable CloudTrail for Secrets Manager and create an alarm for unusual access patterns:
   ```tf
   resource "aws_cloudtrail" "secrets_manager_trail" {
     name                          = "mcp-gateway-secrets-trail"
     s3_bucket_name                = aws_s3_bucket.cloudtrail_logs.id
     include_global_service_events = true
     event_selector {
       read_write_type           = "ReadOnly"
       include_management_events = true
       
       data_resource {
         type   = "AWS::SecretsManager::Secret"
         values = [aws_secretsmanager_secret.*.arn]
       }
     }
   }
   ```

2. **Secret Rotation Policy**
   Consider enabling automatic rotation for credentials that change frequently (API keys, tokens). Keycloak database credentials should use RDS IAM auth (separate task per issue #1303).

### New Dependencies
- None required - CloudTrail can be configured with existing infrastructure

### Better Alternatives Considered
- HashiCorp Vault - rejected (overkill for this use case, adds operational complexity)
- AWS KMS directly - rejected (Secrets Manager provides better features)

### Recommendations
1. Enable CloudTrail for Secrets Manager auditing
2. Create alerts for secret access from unusual IP addresses
3. Consider rotation policies for frequently-changing credentials

### Questions for Author
1. Have we considered implementing secret rotation for frequently-changing credentials?
2. Should we add auditing for secret access patterns?

### Verdict
**APPROVED** - Security requirements are met. Auditing is recommended but not required.

---

## SMTS (Overall Architecture) (Sage)

### Strengths
- Clean separation of concerns between configuration and secrets
- Follows established patterns from `keycloak-ecs.tf`
- Proper use of Terraform conditionals for provider-specific secrets
- Clear documentation with diagrams and code examples

### Concerns

1. **Terraform Provider Version Constraints (BLOCKER)**
   The current implementation uses `aws_secretsmanager_secret` and `aws_secretsmanager_secret_version` resources. We need to verify these are available in the minimum required Terraform AWS provider version:
   
   **Recommendation:** Add explicit version constraint:
   ```tf
   terraform {
     required_providers {
       aws = {
         source  = "hashicorp/aws"
         version = "~> 5.0"  # Secrets Manager resources require v3.50+
       }
     }
   }
   ```
   
   **Action Required:** Verify `terraform plan` succeeds with the current AWS provider version in use.

2. **Remote State Considerations**
   If Terraform state is stored remotely (S3), ensure the secrets are not leaked through state file access. The `sensitive = true` flag helps, but additional safeguards may be needed.

### New Dependencies
- None - requires only existing Terraform provider

### Better Alternatives Considered
- None - Terraform AWS provider is the standard for AWS resource management

### Recommendations
1. Add explicit Terraform provider version constraints
2. Document state file security requirements
3. Consider using `terraform state rm` to clean up old secrets before re-creating

### Questions for Author
1. What version of the Terraform AWS provider is currently in use?
2. How is Terraform state stored and secured?

### Verdict
**APPROVED WITH CHANGES** - Cannot proceed without verifying Terraform provider compatibility.

---

## Summary

### Overall Verdict: APPROVED WITH CHANGES

The design is solid and follows AWS best practices. However, there are blocking issues that must be addressed before deployment:

| Issue | Severity | Owner |
|-------|----------|-------|
| Migration of existing secrets from Terraform state | High | Backend Engineer |
| Secret retrieval failure monitoring | High | SRE |
| Task definition revision cleanup | High | SRE |
| Terraform provider version constraints | Medium | SMTS |

### Next Steps

1. **Address Blocking Issues:**
   - Implement migration script for existing secrets
   - Add CloudWatch monitoring for secret retrieval failures
   - Add task definition revision cleanup
   - Verify Terraform provider version compatibility

2. **Testing:**
   - Deploy to staging environment
   - Verify all services start correctly with new secrets
   - Test secret rotation (if applicable)

3. **Deployment:**
   - Plan maintenance window for production deployment
   - Monitor closely after deployment
   - Keep rollback plan ready

4. **Documentation:**
   - Update runbooks with new secret management procedures
   - Document how to rotate secrets in the future
   - Add this pattern to the engineering playbook

---

## Appendix: Migration Checklist

Before deployment, verify:
- [ ] All existing secrets migrated from Terraform state
- [ ] CloudWatch alarms configured for secret retrieval failures
- [ ] Task definition revision cleanup policy implemented
- [ ] Terraform provider version confirmed compatible
- [ ] IAM policies tested for least-privilege access
- [ ] Staging environment deployment successful
- [ ] Rollback plan documented and tested
- [ ] Runbooks updated with new procedures
