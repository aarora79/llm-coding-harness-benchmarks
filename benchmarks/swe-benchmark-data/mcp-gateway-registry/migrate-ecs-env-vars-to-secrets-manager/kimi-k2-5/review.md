# Expert Review: Migrate Sensitive ECS Environment Variables to AWS Secrets Manager

*Date: 2026-06-25*
*Reviewers: Pixel (Frontend), Byte (Backend), Circuit (SRE), Cipher (Security), Sage (SMTS)*

---

## Review Summary Table

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | N/A | 0 | No frontend impact |
| Backend (Byte) | APPROVED WITH CHANGES | 1 | Add input validation; handle secret versioning |
| SRE (Circuit) | APPROVED WITH CHANGES | 2 | Add drift detection; document rollback procedure |
| Security (Cipher) | APPROVED WITH CHANGES | 2 | Add secret access logging; rotate default secrets |
| SMTS (Sage) | APPROVED WITH CHANGES | 1 | Add migration guide; version pinning |

---

## Frontend Engineer Review (Pixel)

### Scope Assessment
This change is entirely infrastructure-level; no frontend impact.

### Findings
- **No UI Changes Required**: The migration affects only Terraform and ECS task definitions
- **No API Changes**: Application APIs remain unchanged
- **No Breaking Changes**: Frontend behavior identical

### Verdict: N/A
**No action required from frontend team.**

---

## Backend Engineer Review (Byte)

### Strengths
1. **Comprehensive Coverage**: Identifies all sensitive environment variables across three ECS services
2. **Follows Existing Patterns**: Reuses established Secrets Manager resource patterns from `secrets.tf`
3. **Conditional Logic**: Properly uses `count` meta-argument for optional secrets
4. **Minimal Code Changes**: Targets only the necessary environment variables

### Concerns

#### Blocker 1: Missing Input Validation
**Location**: `variables.tf`

Several sensitive variables lack validation that would prevent deployment with weak secrets:

```tf
variable "federation_encryption_key" {
  description = "Fernet encryption key"
  type        = string
  default     = ""
  sensitive   = true
}
```

**Recommendation**: Add validation for minimum length:

```tf
validation {
  condition     = var.federation_encryption_key == "" || length(var.federation_encryption_key) >= 32
  error_message = "Federation encryption key must be at least 32 characters when provided."
}
```

#### Issue 2: Secret Version Handling
When updating `aws_secretsmanager_secret_version`, Terraform will force a new version. This could cause brief downtime during deployment if the application reads the secret at startup.

**Recommendation**: Consider using `lifecycle { ignore_changes = [secret_string] }` for manually-managed secrets, or document that secret updates require application restart.

#### Issue 3: JSON Secret Format
The `keycloak_admin_credentials` secret embeds a JSON object. Applications must parse this JSON to extract credentials.

**Recommendation**: Document that applications must handle JSON-parsed secrets, or use flat key-value pairs:

```tf
secret_string = var.keycloak_admin_password  # Flatten, let app construct JSON
```

### New Libraries / Dependencies
None - uses existing AWS provider.

### Better Alternatives Considered
Consider using AWS ECS Exec to inject secrets at runtime instead of task definition. This would enable hot-swapping without task recreation, but adds operational complexity.

### Recommendations
1. Add validation rules to sensitive variables
2. Document secret format (JSON vs plaintext) for each secret
3. Add unit tests for Terraform conditional logic

### Questions for Author
1. How do containers handle secret updates without restart? (They don't - ECS injects at startup)
2. Should we implement a "secret checksum" to force redeployment when secrets change?

### Verdict: APPROVED WITH CHANGES
**1 Blocker**: Add input validation for sensitive variables.

---

## SRE/DevOps Engineer Review (Circuit)

### Strengths
1. **IAM Least Privilege**: The IAM policy correctly limits secrets to those needed by each service
2. **Conditional Secrets**: Reduces blast radius by not creating unused secrets
3. **KMS Encryption**: Reuses existing KMS key for encryption at rest

### Concerns

#### Blocker 1: No Drift Detection Plan
If someone manually updates a secret in the AWS Console, Terraform will show no drift (due to `ignore_changes`).

**Recommendation**: Add a note about drift detection:

```md
## Drift Detection
Secrets with `ignore_changes` will not detect manual updates in AWS Console.
Run `terraform taint aws_secretsmanager_secret_version.example` to force update.
```

#### Blocker 2: Missing Rollback Procedure
**Critical**: The design does not address how to rollback if a secret migration causes issues.

**Recommendation**: Add rollback procedure to design:

```md
## Rollback Procedure
1. Revert Terraform to previous version
2. Run `terraform apply` to restore plaintext environment variables
3. ECS will redeploy with previous configuration
4. Verify application functionality
```

#### Issue 3: Task Definition Versioning
ECS task definitions are immutable. Each change creates a new revision. The design should specify how many revisions to retain.

**Recommendation**: Add lifecycle rule for old task definitions:

```tf
resource "aws_ecs_task_definition" "service" {
  # ... existing config ...

  lifecycle {
    create_before_destroy = true
  }
}
```

#### Issue 4: Secrets Manager Quotas
AWS Secrets Manager has a default quota of 40,000 secrets per region. While unlikely to be hit, the design should acknowledge quotas.

**Recommendation**: Add monitoring for approaching quotas.

### New Libraries / Dependencies

| Resource | Purpose | Managed By |
|----------|---------|------------|
| Additional Secrets Manager secrets | 8 new secrets | Terraform |

### Recommendations
1. Document drift detection and remediation
2. Add rollback procedure to operational runbook
3. Monitor Secrets Manager API costs (per-secret retrieval pricing)
4. Consider using AWS CloudTrail for secret access auditing

### Questions for Author
1. What is the blast radius if a single secret fails to be retrieved?
2. Should we add a canary deployment strategy for secret migration?

### Verdict: APPROVED WITH CHANGES
**2 Blockers**: Drift detection documentation; rollback procedure.

---

## Security Engineer Review (Cipher)

### Strengths
1. **KMS Encryption**: All secrets encrypted with customer-managed key
2. **IAM Boundaries**: Task execution role limited to specific secrets
3. **Checkov Annotations**: Proper skip annotations for application-managed secrets
4. **Plaintext Elimination**: Removes credential exposure in ECS console

### Concerns

#### Blocker 1: No Secret Access Logging
**Critical**: The design does not specify CloudTrail logging for secret access.

**Recommendation**: Add CloudTrail configuration:

```tf
resource "aws_cloudwatch_log_group" "secrets_access" {
  name              = "/aws/secretsmanager/mcp-gateway"
  retention_in_days = 90
}

# Enable CloudTrail data events for Secrets Manager
```

#### Blocker 2: No Secret Rotation Requirement
While the design acknowledges `ignore_changes` for manually-rotated secrets, it does not enforce rotation.

**Recommendation**: Add rotation requirement to acceptance criteria:

```md
- [ ] Default secrets changed from placeholder/git-committed values
- [ ] Rotation schedule documented for each secret type
- [ ] Automated rotation implemented where applicable (phase 2)
```

#### Issue 3: Encryption in Transit
Secrets Manager uses TLS 1.2+ by default, but the design should explicitly document this requirement.

**Recommendation**: Add security note:

```md
### Encryption in Transit
All Secrets Manager API calls use TLS 1.2+ (enforced by AWS).
```

#### Issue 4: Environment Variable Still Contains Metadata
The `KEYCLOAK_ADMIN` username remains in environment (line 886 in ecs-services.tf), only password moves to secrets.

**Recommendation**: Document that some "less sensitive" values remain in environment, or move to Secrets Manager JSON with full credentials object.

### New Libraries / Dependencies

| Resource | Security Purpose |
|----------|------------------|
| CloudTrail | Secret access audit logging |
| KMS Key | Encryption at rest |

### Recommendations

1. **Enable CloudTrail Data Events** for Secrets Manager
2. **Add Secret Access Alerting** for unauthorized access attempts
3. **Document Secret Classification** (PII vs non-PII)
4. **Review IAM Policies** for overly permissive KMS grants

### Questions for Author
1. Should we implement automatic secret rotation for the registry_api_token (high-risk static token)?
2. Do we need VPC endpoints for Secrets Manager (private subnet access)?

### Verdict: APPROVED WITH CHANGES
**2 Blockers**: Secret access logging; rotation requirements.

---

## SMTS Review (Sage)

### Strengths
1. **Clean Architecture**: Separation of concerns between Terraform resources
2. **Backwards Compatibility**: Conditional creation allows gradual migration
3. **Comprehensive Documentation**: Detailed implementation steps
4. **Pattern Consistency**: Reuses existing codebase patterns

### Concerns

#### Blocker 1: Missing Migration Guide
**Critical**: Existing deployments have plaintext secrets in terraform.tfvars. The design does not explain how to migrate existing values.

**Recommendation**: Add migration guide:

```md
## Migration from Existing Deployments

### Step 1: Extract Secrets
```bash
# Current (plaintext in terraform.tfvars)
federation_encryption_key = "my-secret-key"

# New (move to external source)
# Option A: AWS Console (manual)
# Option B: Use existing values in tfvars, let Terraform import to Secrets Manager
```

### Step 2: Import Existing Secrets
If secrets already exist elsewhere, import them:
```bash
terraform import aws_secretsmanager_secret.federation_encryption_key arn:aws:secretsmanager:...:secret:existing-secret
```

### Step 3: Remove from tfvars
After migration, remove from terraform.tfvars (values now in Secrets Manager)
```

#### Issue 2: Version Pinning
The design uses `version = "~> 6.0"` for the ECS module. Major upgrades could break secrets handling.

**Recommendation**: Pin to specific minor version:

```tf
version = "~> 6.0.0"  # Pin to 6.0.x for stability
```

#### Issue 3: Error Handling
The design acknowledges "ECS will fail to start" if secrets are missing, but does not specify how to debug.

**Recommendation**: Add troubleshooting section:

```md
## Troubleshooting

### "Cannot pull secrets" Error
1. Check IAM task execution role has `secretsmanager:GetSecretValue`
2. Verify secret exists: `aws secretsmanager describe-secret --secret-id ...`
3. Check KMS key grants: Task exec role needs `kms:Decrypt`
4. Review VPC endpoints if using private subnets
```

### Recommendations

1. **Add Migration Guide** for existing deployments
2. **Pin Module Versions** to prevent unexpected changes
3. **Document Debug Procedures** for secret retrieval failures
4. **Consider Terraform Workspace Strategy** for gradual rollout

### Better Alternatives Considered
Consider using Terraform's `moved` block if renaming secrets, to avoid state reconstruction:

```tf
moved {
  from = aws_secretsmanager_secret.old_name
  to   = aws_secretsmanager_secret.new_name
}
```

### Questions for Author
1. How do we handle secret values that are currently in version control (terraform.tfvars)?
2. Should we implement feature flags to disable secrets migration for gradual rollout?

### Verdict: APPROVED WITH CHANGES
**1 Blocker**: Migration guide for existing deployments.

---

## Review Summary

### Critical Actions Required

| Priority | Item | Owner | Due |
|----------|------|-------|-----|
| P0 | Add input validation for sensitive variables | Byte | Before merge |
| P0 | Document drift detection | Circuit | Before merge |
| P0 | Document rollback procedure | Circuit | Before merge |
| P0 | Add secret access logging | Cipher | Phase 1 |
| P0 | Write migration guide | Sage | Before merge |
| P1 | Pin module versions | Sage | Before merge |
| P1 | Add troubleshooting section | Sage | Before merge |

### Consensus Verdict: APPROVED WITH CHANGES

All reviewers agree the design is sound with the following modifications:

1. **Input Validation**: Add Terraform validation rules for secrets
2. **Operational Runbooks**: Document drift detection, rollback, and troubleshooting
3. **Security Hardening**: Enable CloudTrail data events for secret access
4. **Migration Guide**: Document how existing deployments transition to new pattern

### Next Steps

1. Address P0 blockers
2. Re-submit for final SMTS approval
3. Proceed to implementation phase
4. Implement in dev environment first
