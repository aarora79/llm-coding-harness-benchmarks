# GitHub Issue: Migrate Sensitive ECS Environment Variables to AWS Secrets Manager

## Title
Migrate sensitive ECS environment variables to AWS Secrets Manager (Issue #1134)

## Labels
- security
- infrastructure
- terraform
- aws
- secrets-management

## Description

### Problem Statement
Multiple environment variables in ECS task definitions contain sensitive data (API keys, tokens, passwords, client secrets) that are currently passed as plaintext via the `environment` block. This is a security vulnerability because:
1. Plaintext secrets appear in ECS task definitions stored in Terraform state
2. Secrets are visible in AWS Console ECS task details
3. No audit trail for secret access
4. CloudWatch logs may contain exposed secrets
5. Cannot rotate secrets without code changes

The issue references #1134 for this migration work.

### Proposed Solution
Migrate all sensitive environment variables from the `environment` block to the `secrets` block in ECS task definitions. This involves:
1. Creating AWS Secrets Manager secrets in Terraform for each sensitive env var
2. Updating ECS container definitions to reference secrets via `valueFrom`
3. Ensuring IAM task execution role has permissions to read the secrets
4. Maintaining backwards compatibility during migration (no breaking changes)

### User Stories
- As a **Security Engineer**, I want all secrets stored in AWS Secrets Manager so that access is audited and secrets can be rotated without code changes.
- As an **Infrastructure Engineer**, I want to use Terraform to manage secrets so that the infrastructure configuration is complete and version-controlled.
- As a **DevOps Engineer**, I want secrets to be injected at container runtime so that plaintext secrets never appear in task definitions or logs.

### Acceptance Criteria
- [ ] All sensitive environment variables are moved to Secrets Manager secrets
- [ ] ECS task definitions use the `secrets` block (not `environment`) for sensitive values
- [ ] IAM task execution role can read all Secrets Manager secrets
- [ ] Terraform plan and apply succeed without errors
- [ ] Existing deployments continue to work (backwards compatibility)
- [ ] No plaintext secrets appear in:
  - ECS task definition JSON
  - CloudWatch Logs
  - Terraform state (as plaintext values)
- [ ] Documentation is updated to reflect the new secrets management approach

### Out of Scope
- Rotating secrets (handled by separate issue)
- Using AWS Parameter Store (Secrets Manager only)
- Migrating non-sensitive environment variables
- Changing secret values (only migration from env vars to secrets)

### Dependencies
- Issue #1134 tracks this work
- Existing Secrets Manager infrastructure in `secrets.tf` should be extended

### Related Issues
- #1134 - Original issue for this work
- #1282 - SSRF hardening (may share some secret patterns)