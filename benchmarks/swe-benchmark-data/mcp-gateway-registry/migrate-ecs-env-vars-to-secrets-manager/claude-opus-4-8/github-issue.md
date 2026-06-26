# GitHub Issue: Migrate remaining sensitive ECS environment variables to AWS Secrets Manager

## Title
Migrate remaining plaintext secrets in ECS task definitions to AWS Secrets Manager (`secrets` block)

## Labels
- security
- infra
- terraform
- enhancement

## Description

### Problem Statement

The `terraform/aws-ecs` deployment already has a mature AWS Secrets Manager foundation: a dedicated KMS key (`aws_kms_key.secrets`), a set of `aws_secretsmanager_secret` resources in `modules/mcp-gateway/secrets.tf`, an execution-role policy (`aws_iam_policy.ecs_secrets_access`) that scopes `secretsmanager:GetSecretValue` to specific ARNs, and ECS task definitions that already pull roughly fifteen secrets through the container `secrets` block (`SECRET_KEY`, `KEYCLOAK_CLIENT_SECRET`, `DOCUMENTDB_PASSWORD`, the conditional IdP client secrets, etc.).

Despite that foundation, a number of genuinely sensitive values are **still passed to containers as plaintext** through the container `environment` block. Because ECS renders `environment` values directly into the task definition, these secrets are visible to anyone with `ecs:DescribeTaskDefinition`, are stored unencrypted in the task definition revision history, and are written verbatim into Terraform state. This is precisely the exposure that AWS Secrets Manager integration is meant to eliminate, and it is the subject of issue #1134.

The following secret-bearing variables are still wired into `environment` (not `secrets`) in `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` (auth-server and registry containers) and `observability.tf` (Grafana container):

| Env var | Service(s) | Terraform source | Sensitivity |
|---------|-----------|------------------|-------------|
| `REGISTRY_API_TOKEN` | auth-server, registry | `var.registry_api_token` | Static bearer token for Registry API |
| `REGISTRY_API_KEYS` | auth-server, registry | `var.registry_api_keys` | JSON map of API keys -> groups |
| `FEDERATION_STATIC_TOKEN` | auth-server, registry | `var.federation_static_token` | Peer-registry bearer token |
| `FEDERATION_ENCRYPTION_KEY` | auth-server, registry | `var.federation_encryption_key` | Fernet key encrypting stored federation tokens |
| `ANS_API_KEY` | auth-server, registry | `var.ans_api_key` | Agent Naming Service API key |
| `ANS_API_SECRET` | auth-server, registry | `var.ans_api_secret` | Agent Naming Service API secret |
| `REGISTRATION_WEBHOOK_AUTH_TOKEN` | registry | `var.registration_webhook_auth_token` | Webhook auth token |
| `REGISTRATION_GATE_AUTH_CREDENTIAL` | registry | `var.registration_gate_auth_credential` | Gate api-key/bearer credential |
| `REGISTRATION_GATE_OAUTH2_CLIENT_SECRET` | registry | `var.registration_gate_oauth2_client_secret` | OAuth2 client secret for gate |
| `GITHUB_PAT` | registry | `var.github_pat` | GitHub Personal Access Token |
| `GITHUB_APP_PRIVATE_KEY` | registry | `var.github_app_private_key` | GitHub App PEM private key |
| `GF_SECURITY_ADMIN_PASSWORD` | grafana | `var.grafana_admin_password` | Grafana admin password |
| `MONGODB_CONNECTION_STRING` | auth-server, registry | `var.mongodb_connection_string` | Full Mongo URI (may embed credentials) |

`MONGODB_CONNECTION_STRING` is a partial case: it is already supported via the `secrets` block when `var.mongodb_connection_string_secret_arn` is supplied, but it still falls back to a plaintext `environment` entry when only `var.mongodb_connection_string` is set. The goal is to remove the plaintext fallback path.

All of the listed variables are already declared `sensitive = true` in `variables.tf`, which keeps them out of CLI/plan output, but `sensitive = true` does **not** keep them out of the rendered ECS task definition or Terraform state. Only moving them into Secrets Manager and referencing them via `valueFrom` achieves that.

### Proposed Solution

Extend the existing Secrets Manager pattern (do not invent a new one) to cover the remaining plaintext secrets:

1. For each secret listed above, add an `aws_secretsmanager_secret` + `aws_secretsmanager_secret_version` pair in `modules/mcp-gateway/secrets.tf`, following the established conventions (`name_prefix = "${local.name_prefix}-..."`, `kms_key_id = aws_kms_key.secrets.id`, `recovery_window_in_days = 0`, `tags = local.common_tags`, `lifecycle { ignore_changes = [secret_string] }` for externally-managed values, and the appropriate `#checkov:skip=CKV2_AWS_57` justification).
2. Create each secret conditionally so that empty/unconfigured optional secrets do not create dangling resources (mirror the `count = var.feature_enabled ? 1 : 0` pattern already used for the IdP secrets), or create them unconditionally with a `"not-configured"` sentinel where the consuming feature is always present (mirror `embeddings_api_key`).
3. Move each variable out of the container `environment` block and into the `secrets` block of every container that consumes it (auth-server, registry, grafana), using `valueFrom = <arn>` for whole-value secrets.
4. Extend `aws_iam_policy.ecs_secrets_access` so the ECS **task execution role** is granted `secretsmanager:GetSecretValue` on each new secret ARN, using the same conditional `concat(...)` structure already in `iam.tf`. KMS decrypt is already covered because every new secret uses the existing `aws_kms_key.secrets`.
5. Remove the now-unused plaintext `environment` entries and the plaintext `MONGODB_CONNECTION_STRING` fallback.
6. Update `terraform.tfvars.example` and `OPERATIONS.md`/`README.md` to document that these values are now stored in Secrets Manager.

This is an infrastructure-only change. No application code change is required: containers continue to receive the same environment variable names with the same values at runtime; only the injection mechanism changes from plaintext `environment` to `secrets`/`valueFrom`. (Note: the benchmark task table phrases this as "update application code to read secrets at runtime"; the ECS `secrets` block is the idiomatic, lower-risk equivalent and is what this issue pursues. The application already reads these values from `os.environ`, so no code change is needed.)

### User Stories

- As a **security engineer**, I want no secret values to appear in rendered ECS task definitions or `ecs:DescribeTaskDefinition` output, so that read-only console/API access cannot leak credentials.
- As a **platform operator**, I want all secrets injected through one consistent Secrets Manager mechanism, so that rotation, auditing, and access control are uniform across the stack.
- As a **compliance reviewer**, I want secret material kept out of Terraform state and task-definition revision history, so that state-file access does not equal credential access.

### Acceptance Criteria

- [ ] Every secret in the table above is created as an `aws_secretsmanager_secret` in `modules/mcp-gateway/secrets.tf`, following existing naming/KMS/tag/lifecycle conventions.
- [ ] No `environment` entry in any container definition references a `sensitive = true` variable carrying a secret value. (The plaintext entries listed above are removed.)
- [ ] Each migrated secret is injected via the container `secrets` block with a `valueFrom` ARN, for every service that previously consumed it as plaintext.
- [ ] `aws_iam_policy.ecs_secrets_access` grants `secretsmanager:GetSecretValue` on every new secret ARN, conditionally where the secret is conditional.
- [ ] The plaintext `MONGODB_CONNECTION_STRING` `environment` fallback is removed; the value is only ever delivered via Secrets Manager.
- [ ] `terraform validate` passes and `terraform plan` succeeds with no errors for the default configuration and for a configuration with all optional features enabled.
- [ ] `terraform plan` shows the listed env vars removed from `environment` and present in `secrets`, and shows the new Secrets Manager resources to be created.
- [ ] `grep` of the rendered plan / task definition JSON shows zero secret literals in `environment`.
- [ ] `terraform.tfvars.example` and operator docs are updated to reflect Secrets Manager storage.
- [ ] No regression for deployments that leave optional secrets empty (no secret is created with a dangling/invalid ARN, and no container references a non-existent secret).

### Out of Scope

- Automatic rotation for the newly migrated secrets (these are externally-managed third-party tokens; rotation is a follow-up, mirroring the existing `#checkov:skip=CKV2_AWS_57` treatment). Database-credential rotation already exists for Keycloak/DocumentDB and is untouched.
- Migrating the Keycloak task definition (`keycloak-ecs.tf`), which already uses SSM Parameter Store + Secrets Manager for its sensitive values.
- Replacing static tokens with a different auth mechanism (e.g. RDS IAM auth - that is issue #1303).
- Any application/runtime code change. Containers keep reading the same env var names.
- Non-secret configuration values (hostnames, ports, feature flags) - these stay in `environment`.

### Dependencies

- Existing `aws_kms_key.secrets` and `aws_kms_alias.secrets` (modules/mcp-gateway/secrets.tf).
- Existing `aws_iam_policy.ecs_secrets_access` and its attachment to the task execution role.
- The `terraform-aws-modules/ecs/aws` service module that renders `environment` and `secrets` blocks.

### Related Issues

- #1134 (this task)
- #1303 (Keycloak DB password -> RDS IAM auth; complementary secret-handling work)
- #1026 (Keycloak DB secret rotation Lambda; established the rotation pattern)
