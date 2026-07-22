# GitHub Issue: Migrate Sensitive ECS Environment Variables to AWS Secrets Manager

## Title
Migrate all sensitive ECS environment variables from plaintext to AWS Secrets Manager with rotation support and fallback path

## Labels
- security
- enhancement
- infrastructure
- terraform

## Description

### Problem Statement

The MCP Gateway Registry deploys multiple ECS services (auth-server, registry, mcpgw, demo servers, observability) via Terraform in `terraform/aws-ecs/modules/mcp-gateway/`. Sensitive values such as DB passwords, API keys, OAuth client secrets, federation tokens, and admin passwords are passed as plaintext in the ECS `environment` blocks of container definitions.

While Terraform variables are marked `sensitive = true` (preventing display in `terraform output`), the values are still stored as plaintext in the ECS task definition JSON. This means they are exposed via:

- `aws ecs describe-task-definition` API responses (any IAM principal with read access can retrieve them)
- `aws ecs describe-services` and `aws ecs describe-tasks` outputs
- `terraform plan` diffs (unless all callers filter sensitive output)
- CloudWatch log group attachments if container startup scripts echo env vars
- Terraform state files in the `jsonencode` container definition blocks

Some secrets already have `aws_secretsmanager_secret` resources in `secrets.tf` and are already referenced in ECS `secrets` blocks (SECRET_KEY, KEYCLOAK_CLIENT_SECRET, KEYCLOAK_M2M_CLIENT_SECRET, KEYCLOAK_ADMIN_PASSWORD, DOCUMENTDB_USERNAME, DOCUMENTDB_PASSWORD, EMBEDDINGS_API_KEY). However, many other sensitive variables remain in the `environment` blocks as plaintext with no Secrets Manager resource.

### Proposed Solution

1. Create `aws_secretsmanager_secret` + `aws_secretsmanager_secret_version` resources for all sensitive variables that do not yet have one (see full inventory below).
2. Add `secrets` block entries in the ECS container definitions for ALL sensitive variables (both those that already have a resource and new ones).
3. Remove the sensitive variables from the `environment` blocks, replacing with a conditional fallback that preserves the plaintext value as a migration bridge.
4. Update the IAM `ecs_secrets_access` policy to include ARNs for all new secrets, with support for cross-account secret access via KMS key policy grants.
5. Add a `sensitive_rotation` variable (default `true`) per secret to enable or disable automated rotation for supported secrets.
6. Add a central `enable_secrets_manager` variable (default `true`) to toggle all secrets on or off at once.
7. Mark `auth0_management_api_token` with a new `aws_secretsmanager_secret` resource (currently missing despite being a sensitive variable).
8. (Optional, for follow-up) Update the application config loader (`registry/core/config.py`) to support reading secrets from a file path injected by Secrets Manager, providing an additional fallback path for local Docker Compose development.

### Sensitive Variable Inventory

**Already covered by Secrets Manager (no new resource needed):**
- SECRET_KEY (`aws_secretsmanager_secret.secret_key`)
- KEYCLOAK_CLIENT_SECRET (`aws_secretsmanager_secret.keycloak_client_secret`)
- KEYCLOAK_M2M_CLIENT_SECRET (`aws_secretsmanager_secret.keycloak_m2m_client_secret`)
- KEYCLOAK_ADMIN_PASSWORD (`aws_secretsmanager_secret.keycloak_admin_password`)
- DOCUMENTDB_USERNAME / DOCUMENTDB_PASSWORD (via `var.documentdb_credentials_secret_arn`)
- EMBEDDINGS_API_KEY (`aws_secretsmanager_secret.embeddings_api_key`)
- OKTA_CLIENT_SECRET (`aws_secretsmanager_secret.okta_client_secret`)
- OKTA_M2M_CLIENT_SECRET (`aws_secretsmanager_secret.okta_m2m_client_secret`)
- OKTA_API_TOKEN (`aws_secretsmanager_secret.okta_api_token`)
- ENTRA_CLIENT_SECRET (`aws_secretsmanager_secret.entra_client_secret`)
- AUTH0_CLIENT_SECRET (`aws_secretsmanager_secret.auth0_client_secret`)
- AUTH0_M2M_CLIENT_SECRET (`aws_secretsmanager_secret.auth0_m2m_client_secret`)
- METRICS_API_KEY (`aws_secretsmanager_secret.metrics_api_key`)
- OTEL_EXPORTER_OTLP_HEADERS (`aws_secretsmanager_secret.otlp_exporter_headers`)

**Need new Secrets Manager resources:**
- AUTH0_MANAGEMENT_API_TOKEN - Auth0 Management API token (expires after 24h)
- REGISTRY_API_TOKEN - Static API key for registry API access
- REGISTRY_API_KEYS - Multi-key static tokens JSON
- FEDERATION_STATIC_TOKEN - Federation peer-to-peer sync token
- FEDERATION_ENCRYPTION_KEY - Fernet encryption key for federation tokens
- REGISTRATION_WEBHOOK_AUTH_TOKEN - Webhook authentication token
- REGISTRATION_GATE_AUTH_CREDENTIAL - Admission control credential
- REGISTRATION_GATE_OAUTH2_CLIENT_SECRET - Gate OAuth2 client secret
- ANS_API_KEY - Agent Naming Service API key
- ANS_API_SECRET - Agent Naming Service API secret
- GITHUB_PAT - GitHub Personal Access Token
- GITHUB_APP_PRIVATE_KEY - GitHub App private key (PEM)
- GRAFANA_ADMIN_PASSWORD - Grafana admin password (in observability path)

**Total: 13 new secrets to create.**

### User Stories

- As a security engineer, I want all sensitive values in ECS task definitions to be sourced from AWS Secrets Manager so that they are encrypted at rest with KMS, have CloudTrail audit trails, and support rotation.
- As an operator, I want a zero-downtime migration that keeps the plaintext env-var path as a fallback so existing deployments are not disrupted.
- As a compliance auditor, I want to verify via `aws ecs describe-task-definition` that no secret-containing environment variable names have a plaintext `value` field.
- As a DevOps engineer, I want rotation support for secrets that support it (database credentials, API keys) and cross-account access for secrets shared across AWS accounts.

### Acceptance Criteria

- [ ] `aws_secretsmanager_secret` resources exist in Terraform for every sensitive variable across auth-server, registry, mcpgw, and grafana container definitions (13 new secrets).
- [ ] Every sensitive variable is injected via the ECS `secrets` block (not the `environment` block).
- [ ] A central `enable_secrets_manager` variable (default `true`) controls whether secrets are loaded from Secrets Manager. When `false`, all services fall back to plaintext environment variables.
- [ ] A conditional plaintext fallback is preserved in the `environment` block using `var.<name> != "" ? [{ name = "...", value = var.<name> }] : []` so existing deployments continue to work.
- [ ] The `ecs_secrets_access` IAM policy grants `secretsmanager:GetSecretValue` on every new secret ARN.
- [ ] The KMS key policy supports cross-account secret access via a grant condition on `aws:PrincipalArn`.
- [ ] Rotation configuration (`sensitive_rotation`) is added as a parameter per secret where rotation is applicable.
- [ ] All sensitive variables are marked `sensitive = true` in `variables.tf`.
- [ ] `terraform validate` and `terraform plan` succeed with no unexpected resource changes.
- [ ] No secret-containing environment variables appear in `terraform plan` output (values should be marked as sensitive).

### Out of Scope

- Docker Compose migration (Docker Compose lacks native Secrets Manager integration).
- Helm chart changes (already uses Kubernetes Secret objects).
- Automatic rotation for IdP-managed secrets (Auth0, Okta, Entra client secrets managed externally).
- Migration of the keycloak-ecs.tf service (separate task).
- Telemetry-collector module secrets (separate Terraform module).

### Dependencies

- Existing `aws_kms_key.secrets` in `secrets.tf` (used by all current and new secrets).
- Existing `aws_iam_policy.ecs_secrets_access` in `iam.tf`.
- Existing `random_password` resources for auto-generated secrets.

### Related Issues

- Issue #1026: Keycloak DB credentials rotation (already uses Secrets Manager for KC DB secrets)
- Issue #1134: Reference issue for this migration

## References

- https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-container-definition.html
- https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html
- https://docs.aws.amazon.com/secretsmanager/latest/userguide/rotating-secrets.html