# GitHub Issue: Migrate remaining plaintext ECS environment variables to AWS Secrets Manager

## Title
Migrate sensitive ECS environment variables to AWS Secrets Manager (with plaintext env-var fallback)

## Labels
- enhancement
- security
- infra
- terraform

## Description

### Problem Statement

The AWS ECS Terraform stack under `terraform/aws-ecs/` passes a number of sensitive values to containers as plaintext `environment` entries in the ECS task definitions. Even though the underlying Terraform variables are declared `sensitive = true`, values placed in a container's `environment` block are rendered into the task definition JSON and stored in Terraform state in cleartext. They are visible to anyone with `ecs:DescribeTaskDefinition` or read access to the state backend, are not encrypted at rest with a customer-managed key, are not audited on read, and cannot be rotated without editing Terraform and redeploying.

The repository has already migrated the highest-value secrets (application `SECRET_KEY`, DocumentDB credentials, Keycloak client/admin secrets, IdP client secrets for Entra/Okta/Auth0, the metrics API key, and OTLP exporter headers) to AWS Secrets Manager, wired into ECS via the container `secrets` block. A second tier of sensitive values is still passed as plaintext `environment` on the `auth-server`, `registry`, and `grafana` task definitions:

| Env var | Container(s) | Source variable | Location |
|---|---|---|---|
| `GF_SECURITY_ADMIN_PASSWORD` | grafana, grafana-config | `var.grafana_admin_password` | `observability.tf:584`, `:645` |
| `AUTH0_MANAGEMENT_API_TOKEN` | auth-server, registry | `var.auth0_management_api_token` | `ecs-services.tf:213`, `:814` |
| `REGISTRY_API_TOKEN` | auth-server, registry | `var.registry_api_token` | `ecs-services.tf:237`, `:1080` |
| `REGISTRY_API_KEYS` | auth-server, registry | `var.registry_api_keys` | `ecs-services.tf:241`, `:1084` |
| `FEDERATION_STATIC_TOKEN` | auth-server, registry | `var.federation_static_token` | `ecs-services.tf:259`, `:952` |
| `FEDERATION_ENCRYPTION_KEY` | auth-server, registry | `var.federation_encryption_key` | `ecs-services.tf:263`, `:956` |
| `ANS_API_KEY` | auth-server, registry | `var.ans_api_key` | `ecs-services.tf:275`, `:973` |
| `ANS_API_SECRET` | auth-server, registry | `var.ans_api_secret` | `ecs-services.tf:279`, `:977` |
| `REGISTRATION_WEBHOOK_AUTH_TOKEN` | registry | `var.registration_webhook_auth_token` | `ecs-services.tf:1106` |
| `REGISTRATION_GATE_AUTH_CREDENTIAL` | registry | `var.registration_gate_auth_credential` | `ecs-services.tf:1160` |
| `REGISTRATION_GATE_OAUTH2_CLIENT_SECRET` | registry | `var.registration_gate_oauth2_client_secret` | `ecs-services.tf:1184` |
| `GITHUB_PAT` | registry | `var.github_pat` | `ecs-services.tf:1251` |
| `GITHUB_APP_PRIVATE_KEY` | registry | `var.github_app_private_key` | `ecs-services.tf:1263` |

These are OAuth client secrets, API keys, static bearer tokens, a Fernet encryption key, a GitHub PAT, a GitHub App private key (PEM), and the Grafana admin password. They should be treated exactly like the secrets already migrated.

### Proposed Solution

Extend the existing Secrets Manager pattern (in `modules/mcp-gateway/secrets.tf` and `modules/mcp-gateway/iam.tf`) to cover the remaining sensitive values:

1. For each remaining plaintext secret, create an `aws_secretsmanager_secret` + `aws_secretsmanager_secret_version` in the module, encrypted with the existing `aws_kms_key.secrets`, following the established `name_prefix` / `recovery_window_in_days = 0` / `checkov:skip` conventions. Each optional secret is created only when its source variable is non-empty (so unused features do not create empty secrets).
2. Update the ECS task definitions to pull each value from the `secrets` block via `valueFrom` instead of `environment`, reusing the exact per-service `secrets = concat(...)` pattern already present on `auth-server` and `registry`. Grafana currently has no `secrets` block, so add one.
3. Add each new secret ARN to the `Resource` list of `aws_iam_policy.ecs_secrets_access` in `modules/mcp-gateway/iam.tf` (already attached to both the task execution role and task role for every module service) so ECS can read them. Grafana's execution/task roles must also be confirmed to carry this policy.
4. Provide a fallback toggle so operators can migrate incrementally. A single feature flag (`var.use_secrets_manager_for_env`, default `true`) selects, per secret, whether the value is injected via the `secrets` block (Secrets Manager) or the legacy `environment` block (plaintext). ECS forbids the same variable name appearing in both blocks, so the choice must be mutually exclusive per variable. When the flag is `false`, behavior is byte-for-byte identical to today.

No application code change is required: every one of these values is read from a process environment variable (via Pydantic `BaseSettings` in `registry/core/config.py` or direct `os.environ` reads in `auth_server/`), and ECS injects `secrets` as ordinary environment variables under the same name. The plaintext and Secrets Manager paths are therefore transparent to the app.

### User Stories

- As an operator deploying the registry on AWS ECS with Terraform, I want all sensitive values pulled from AWS Secrets Manager so they are encrypted at rest, access-audited via CloudTrail, and never rendered as cleartext in the task definition or Terraform state.
- As a security engineer, I want a single IAM policy that enumerates exactly which secret ARNs the ECS roles may read, so least-privilege is auditable.
- As an operator mid-migration, I want to flip a single flag to fall back to the previous plaintext `environment` behavior if a Secrets Manager issue blocks a deploy, without editing multiple task definitions.

### Acceptance Criteria

- [ ] Every sensitive env var in the table above is created as an `aws_secretsmanager_secret` (+ version) in `modules/mcp-gateway/secrets.tf`, encrypted with `aws_kms_key.secrets`.
- [ ] Each optional secret is created only when its source variable is non-empty (no empty/placeholder secrets for unused features).
- [ ] The `auth-server`, `registry`, and `grafana` task definitions inject these values via the `secrets` block `valueFrom` (with the `:jsonkey::` suffix where a JSON secret is used) when the feature flag is on.
- [ ] `aws_iam_policy.ecs_secrets_access` `Resource` list includes every new secret ARN, gated by the same conditionals used to create the secrets.
- [ ] Grafana's ECS execution and task roles carry `secretsmanager:GetSecretValue` for the Grafana admin password secret and `kms:Decrypt` on `aws_kms_key.secrets`.
- [ ] A `var.use_secrets_manager_for_env` flag (default `true`) toggles between the Secrets Manager path and the legacy plaintext `environment` path for these variables, and the same variable name never appears in both `environment` and `secrets` for any container.
- [ ] With the flag set to `false`, `terraform plan` shows no change to the injected values versus the pre-change behavior (byte-for-byte fallback).
- [ ] No application source code changes are required; the app reads the same env-var names in both modes.
- [ ] `terraform validate` passes and `terraform plan` is clean for a representative `terraform.tfvars`.

### Out of Scope

- Helm / EKS charts (`charts/`) and any non-ECS deployment surface.
- Automatic rotation of these application secrets (the existing rotation lambdas cover only the DocumentDB and Keycloak database credentials; these app secrets are managed in external dashboards or generated by the app and remain non-rotatable, marked with `checkov:skip=CKV2_AWS_57`).
- Migrating values already sourced from Secrets Manager or SSM (`SECRET_KEY`, DocumentDB creds, Keycloak secrets, IdP client secrets, metrics API key, OTLP headers).
- Changing how the application itself reads configuration (still environment-variable based).
- The `*_extra_env` passthrough variables (`auth_server_extra_env`, `registry_extra_env`, `mcpgw_extra_env`): these are free-form user-supplied lists and are documented as a caveat, not migrated automatically.
- The `telemetry-collector` Terraform stack.

### Dependencies

- Existing module resources: `aws_kms_key.secrets`, `aws_iam_policy.ecs_secrets_access`, and the per-service `container_definitions` maps in `modules/mcp-gateway/ecs-services.tf` and `observability.tf`.
- The community `terraform-aws-modules/ecs//modules/service` module, which creates the ECS execution/task roles the policy is attached to.

### Related Issues

- Prior migrations this builds on: #851 (M2M registration), #947 (MongoDB connection string secret), #1000 (extra env vars), #1026 (Keycloak DB creds moved from SSM to Secrets Manager).
- Reference issue: agentic-community/mcp-gateway-registry#1134
