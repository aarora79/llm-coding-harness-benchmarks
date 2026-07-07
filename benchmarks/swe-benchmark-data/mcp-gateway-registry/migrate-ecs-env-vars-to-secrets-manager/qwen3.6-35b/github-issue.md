# GitHub Issue: Migrate ECS Environment Variables to AWS Secrets Manager

## Title
Migrate remaining ECS environment variables to AWS Secrets Manager for all services

## Labels
- security
- enhancement
- infrastructure
- terraform

## Description

### Problem Statement

The MCP Gateway Registry ECS services currently pass numerous sensitive values as plain-text environment variables in the ECS task definitions. While AWS Secrets Manager resources exist in `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` for many secrets, the following categories of sensitive values are still transmitted as plaintext in the `environment` blocks of the ECS container definitions:

1. API tokens and keys (registration webhook auth, registration gate credentials, Ansible API credentials, GitHub PAT and app private key)
2. Federation secrets (static token, encryption key)
3. Registry API authentication (REGISTRY_API_TOKEN, REGISTRY_API_KEYS)
4. Third-party integration secrets (Grafana admin password, OTLP exporter headers)
5. Already-managed secrets passed as plaintext env vars (AUTH0_MANAGEMENT_API_TOKEN, OKTA_CLIENT_SECRET, OKTA_M2M_CLIENT_SECRET, OKTA_API_TOKEN, ENTRA_CLIENT_SECRET)

These values appear in:
- Terraform state files
- CloudWatch logs (if any logging includes env var dumps)
- ECS task definition API responses
- Docker API responses during debugging

This creates a security risk: sensitive credentials are stored and transmitted in plaintext form in multiple operational surfaces.

### Proposed Solution

Migrate all sensitive environment variables to use the ECS `secrets` block, which fetches values directly from AWS Secrets Manager at task launch. The ECS container definition supports a `secrets` block that maps environment variable names to Secrets Manager ARNs, keeping values encrypted in transit and at rest.

The existing pattern already used for many secrets (SECRET_KEY, Keycloak secrets, Okta secrets, Auth0 secrets, etc.) will be extended to cover all remaining secrets.

### Architecture Decision: Per-Secret Secrets Manager Entries

Each sensitive variable will have its own `aws_secretsmanager_secret` + `aws_secretsmanager_secret_version` resource in `secrets.tf`. This follows the existing pattern where every secret has a named entry, enabling:

- Individual access control per secret via IAM
- Audit logging per secret access
- Clear naming convention: `{prefix}-{variable_name}`
- Independent rotation schedules per secret

**Note:** Some secrets are already created as Secrets Manager resources in `secrets.tf` but are still passed as plain-text environment variables in the ECS container definitions. These require only the ECS-side change (removing from `environment` block, adding to `secrets` block).

### User Stories

- As a security engineer, I want all sensitive values in ECS tasks to be stored in AWS Secrets Manager so that they are never exposed in plaintext in terraform state, CloudWatch logs, or API responses.
- As an SRE, I want to be able to rotate individual secrets without changing the ECS task definition so that credential rotation is decoupled from infrastructure changes.
- As a developer, I want clear documentation of which secrets are stored where and how to set them for local development.
- As a compliance auditor, I want to verify that all secrets in ECS tasks reference AWS Secrets Manager ARNs rather than plaintext values.

### Acceptance Criteria

- [ ] All 20+ sensitive environment variables in the ECS `environment` blocks are removed and mapped via the ECS `secrets` block instead
- [ ] New `aws_secretsmanager_secret` and `aws_secretsmanager_secret_version` resources are created in `secrets.tf` for secrets that do not yet have a resource
- [ ] IAM policy `ecs_secrets_access` is updated to include ARNs for all new secrets
- [ ] Variables in `variables.tf` are marked with `sensitive = true` for all sensitive inputs
- [ ] Conditional secrets (Okta, Auth0, Entra, observability) use the correct `count` gate in the ECS `secrets` block
- [ ] README.md is updated to document the new secret setup requirements
- [ ] `.env.example` is updated to reflect which values must now be set in AWS Secrets Manager
- [ ] No existing plain-text secret values remain in the ECS `environment` blocks for the registry and auth-server services
- [ ] The `secrets` block in `ecs-services.tf` uses the correct Secrets Manager ARN format for each secret (simple string for single-value secrets, JSON path format for nested secrets)
- [ ] `terraform plan` produces no unexpected changes to non-secret resources

### Out of Scope

- Docker Compose migration (a separate task; Docker Compose lacks native Secrets Manager integration)
- Helm chart changes (already uses Kubernetes Secret objects; no change needed)
- Automatic secret rotation for application secrets (rotation requires Lambda functions; only database secrets currently have rotation Lambdas)
- Changes to the application code that consumes these environment variables
- Secrets Manager access logging or CloudTrail integration changes

### Dependencies

- AWS provider >= 5.0 (for latest Secrets Manager resource features)
- `random_password` resources already exist for auto-generated secrets (secret_key, metrics_api_key)
- Existing `aws_iam_policy.ecs_secrets_access` in `iam.tf` will be updated with new ARNs
- Existing IAM policies for ECS tasks already include `SecretsManagerAccess` policy attachment

### Related Issues

- Issue #947: MongoDB connection string secret ARN (already partially implemented)
- Issue #955: DocumentDB IAM authentication (related security hardening)