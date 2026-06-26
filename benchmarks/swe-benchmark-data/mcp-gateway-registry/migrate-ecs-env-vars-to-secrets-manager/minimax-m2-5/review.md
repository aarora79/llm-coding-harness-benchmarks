# Expert Review: Migrate ECS Environment Variables to AWS Secrets Manager

*Created: 2026-06-25*
*Author: Claude (minimax-m2-5)*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

---

## Reviewers

| Role | Reviewer | Focus |
|------|----------|-------|
| Frontend Engineer | Pixel | N/A - No UI changes |
| Backend Engineer | Byte | API impact, secret management |
| SRE/DevOps Engineer | Circuit | Deployment, Terraform, ECS |
| Security Engineer | Cipher | Security implications, secret handling |
| SMTS (Overall) | Sage | Architecture, integration, maintainability |

---

## 1. Frontend Engineer (Pixel)

### Verdict: NOT APPLICABLE

**Review:**
This is an infrastructure-only change affecting Terraform and ECS task definitions. There are no frontend or UI components involved.

---

## 2. Backend Engineer (Byte)

### Strengths
1. Detailed code examples for Terraform resources
2. Clear separation of secrets by service (auth-server, registry)
3. Backward compatibility approach (keeping env vars during transition)
4. Comprehensive list of secrets identified

### Concerns

**Issue 1: Missing `secret_key` rotation consideration**
The LLD mentions rotation is out of scope but does not address that some migrated secrets (like `registry_api_token`) cannot be rotated without service impact.

**Issue 2: Empty string handling**
The design does not specify what happens when a secret variable is empty. Should an empty secret be created or should the secret block be omitted entirely?

**Issue 3: JSON vs plain string secrets**
- `registry_api_keys` appears to be a JSON string, which requires special handling in the LLD to specify the correct JSON path in `valueFrom`.

### New Libraries / Infra Dependencies Required
None - uses existing Terraform AWS provider.

### Better Alternatives Considered
Using AWS Parameter Store for non-sensitive tokens was considered but rejected appropriately per issue requirements.

### Recommendations
1. Add a conditional to omit secrets block entries when the corresponding variable is empty
2. Document the expected format for JSON secrets (e.g., `registry_api_keys`)
3. Add a validation step to warn if secrets are empty during deployment

### Questions for Author
1. What is the expected format of `REGISTRY_API_KEYS`? Is it JSON or a newline-separated string?
2. Should we create "placeholder" secrets for production deployments to avoid empty secrets?

### Verdict: APPROVED WITH CHANGES

**Blockers:** 1 (need clarification on empty string handling)
**Key Recommendation:** Add conditional logic for empty variable values

---

## 3. SRE/DevOps Engineer (Circuit)

### Strengths
1. Comprehensive file change list with approximate line numbers
2. Good understanding of existing patterns in `secrets.tf` and `iam.tf`
3. Backward compatibility approach is sound
4. Existing IAM policy structure is well understood

### Concerns

**Issue 1: Terraform state migration**
When existing deployments migrate to Secrets Manager, Terraform will try to recreate the task definitions. Need to handle the state properly:
```
- The "create before destroy" lifecycle may be needed
- May need to use `ignore_changes` for secret version
```

**Issue 2: Secrets Manager secret naming**
The LLD uses `name_prefix` which creates secrets with random suffixes. For auditability, consider using deterministic names:
```
# Current approach (random suffix)
name_prefix = "${local.name_prefix}-registry-api-token-"

# Alternative (deterministic)
name = "mcp-gateway-registry-api-token"
```

**Issue 3: `kms:Decrypt` permission already exists**
The existing IAM policy already includes `kms:Decrypt` for the secrets KMS key. Confirm this covers the new secrets (it does, but should be verified).

**Issue 4: `terraform.tfvars.example` update missing**
The LLD mentions updating `.tfvars.example` but should also update the main `variables.tf` to mark secrets as `sensitive`.

### New Libraries / Infra Dependencies Required
None.

### Better Alternatives Considered
None - standard AWS/Secrets Manager approach is appropriate.

### Recommendations
1. Use `name` instead of `name_prefix` for deterministic secret names
2. Add `lifecycle { prevent_destroy = false }` for secrets that may need to be recreated
3. Add explicit KMS key permissions for new secrets (already covered by existing policy)
4. Add `.tfvars.example` updates to the implementation steps

### Questions for Author
1. Does the team prefer random suffixes (name_prefix) or deterministic names (name)?
2. Should secrets have a retention policy other than default?

### Verdict: APPROVED WITH CHANGES

**Blockers:** 0 (minor issues can be addressed during implementation)
**Key Recommendation:** Switch from `name_prefix` to deterministic `name` for auditability

---

## 4. Security Engineer (Cipher)

### Strengths
1. Problem statement clearly identifies security risks
2. All sensitive environment variables are identified
3. Backward compatibility approach prevents service disruption
4. IAM policy updates are comprehensive

### Concerns

**Issue 1: Secret value in Terraform state**
The LLD mentions "Terraform state (as plaintext values)" as something to avoid, but using `secret_string = var.secret_var` still stores the **plaintext value in Terraform state**.

**Mitigation:** This is a known limitation of Terraform. Options include:
- Accept the risk (current approach)
- Use Secrets Manager to generate initial value (random passwords)
- Use external secrets operator (out of scope)

**Issue 2: Secret access audit**
The design should ensure CloudTrail captures secret access events. The IAM policy includes the correct actions, but verify CloudWatch logs from ECS tasks do not capture secret values.

**Issue 3: Secret versions**
When secrets rotate, the ECS task definition references an old version. The LLD does not address version handling in the `valueFrom` reference.

```hcl
# Current approach - always gets latest version
valueFrom = aws_secretsmanager_secret.secret.arn

# With specific version (not recommended for most cases)
valueFrom = "${aws_secretsmanager_secret.secret.arn}:AWSCURRENT:"
```

**Issue 4: Over-privileged IAM policy**
The LLD adds 10 new secrets to the IAM policy. Consider using separate policies or limiting by service role if different services need different secrets.

### New Libraries / Infra Dependencies Required
None.

### Better Alternatives Considered
1. **External Secrets Operator (ESO):** More sophisticated secret management, but adds complexity
2. **AWS Secrets Manager with rotation:** Not all secrets can rotate automatically

### Recommendations
1. Document the Terraform state limitation explicitly
2. Add note about using `AWSCURRENT` version (default behavior)
3. Group secrets by service in IAM policy for least privilege
4. Add CloudTrail audit logging verification to testing plan

### Questions for Author
1. Is the team comfortable with plaintext values in Terraform state for these secrets?
2. Should secrets be scoped by service (auth-server only gets its secrets)?

### Verdict: APPROVED WITH CHANGES

**Blockers:** 0 (state limitation is known trade-off)
**Key Recommendation:** Document Terraform state trade-off clearly

---

## 5. SMTS (Sage)

### Strengths
1. Comprehensive identification of all secrets to migrate
2. Good understanding of existing codebase patterns
3. Backward compatibility is prioritized
4. Clear migration path with phases

### Concerns

**Issue 1: Scope creep**
The LLD includes `AUTH0_MANAGEMENT_API_TOKEN` but this variable was not found in the environment variables grep. Need to verify this is actually needed.

**Issue 2: Testing verification**
The LLD references `testing.md` but does not include specific terraform validation commands. Add `terraform validate` and basic plan verification.

**Issue 3: Error handling for missing secrets**
When deploying to an environment that previously had no value for a secret (empty), the container may fail to start if it expects the secret to exist.

**Issue 4: Documentation update scope**
The LLD mentions updating `docs/` but should specify which files need updates.

### New Libraries / Infra Dependencies Required
None.

### Better Alternatives Considered
None - the approach is standard and well-understood.

### Recommendations
1. Verify all 10 secrets are actually passed via environment (run actual grep on the deployed config)
2. Add specific terraform validation to testing plan
3. Add a "graceful degradation" option for optional secrets
4. Create a checklist of docs to update

### Questions for Author
1. How do we handle migration for existing production deployments?
2. Should there be a "dry run" flag to validate secret injection before full rollout?

### Verdict: APPROVED WITH CHANGES

**Blockers:** 0
**Key Recommendation:** Add terraform validation commands to testing section

---

## Review Summary Table

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | N/A | 0 | N/A |
| Backend (Byte) | APPROVED WITH CHANGES | 1 | Add conditional empty string handling |
| SRE (Circuit) | APPROVED WITH CHANGES | 0 | Use deterministic secret names |
| Security (Cipher) | APPROVED WITH CHANGES | 0 | Document Terraform state trade-off |
| SMTS (Sage) | APPROVED WITH CHANGES | 0 | Add terraform validation |

### Overall Verdict: APPROVED WITH CHANGES

The design is sound. The identified issues are improvements rather than blockers:
- Backend: Conditional empty string handling
- SRE: Deterministic secret names
- Security: Document state trade-off
- SMTS: Add terraform validation

### Next Steps

1. Address the concerns in the implementation phase
2. Add conditional logic for empty secrets
3. Verify each of the 10 secrets is actually in the environment block
4. Add terraform validation to testing plan
5. Proceed with implementation following the LLD