# GitHub Issue: Migrate remaining plaintext ECS environment-variable secrets to AWS Secrets Manager

## Title
Migrate remaining plaintext ECS task-definition secrets to AWS Secrets Manager

## Labels
- enhancement
- security
- infra

## Description

### Problem Statement
The `terraform/aws-ecs` stack already migrated most sensitive credentials (Keycloak client secrets, DocumentDB username/password, the app `SECRET_KEY`, IdP client secrets for Entra/Okta/Auth0, the embeddings API key, and the metrics API key) to AWS Secrets Manager, wired into the ECS task definitions through the `secrets` block in `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`. This gives those values encryption at rest, IAM-scoped read access, and (for DocumentDB and the Keycloak database) automatic rotation.

However, a second batch of sensitive values is still passed as **plaintext ECS `environment` entries** on the `auth-server` and `registry` containers, and on the Grafana container. Terraform marks the corresponding input variables `sensitive = true`, which only hides them from `plan`/`apply` CLI output and state diffs - it does not stop them from being written in cleartext into the ECS task definition JSON (visible to anyone who can call `ecs:DescribeTaskDefinition`) or into the container's process environment (visible via ECS Exec, `docker inspect`, or a core dump). Concretely, in `ecs-services.tf`:

- `AUTH0_MANAGEMENT_API_TOKEN` (auth-server line 212-214, registry line 813)
- `REGISTRY_API_TOKEN` (auth-server line 236, registry line 1080)
- `REGISTRY_API_KEYS` (auth-server line 240, registry line 1084)
- `FEDERATION_STATIC_TOKEN` (auth-server line 258, registry line 952)
- `FEDERATION_ENCRYPTION_KEY` (auth-server line 262, registry line 956)
- `ANS_API_KEY` / `ANS_API_SECRET` (auth-server lines 274/278, registry lines 973/977)
- `REGISTRATION_WEBHOOK_AUTH_TOKEN` (registry line 1106)
- `REGISTRATION_GATE_AUTH_CREDENTIAL` (registry line 1160)
- `REGISTRATION_GATE_OAUTH2_CLIENT_SECRET` (registry line 1184)
- `GITHUB_PAT` (registry line 1251)
- `GITHUB_APP_PRIVATE_KEY` (registry line 1263) - a PEM-format private key, plaintext in the task definition
- `GF_SECURITY_ADMIN_PASSWORD` (Grafana, `observability.tf` line 582 and the `grafana-config` sidecar line 645)

Moving these into the same `secrets` block mechanism the repo already uses closes the remaining gap and gives the whole credential surface consistent encryption, audit trail (CloudTrail `GetSecretValue` calls), and a path to future rotation.

### Proposed Solution
For each plaintext secret above, introduce one new Terraform variable per secret ending in `_secret_arn` (mirroring the existing `mongodb_connection_string` / `mongodb_connection_string_secret_arn` pair) and use it to switch the value's source from a plaintext `environment` entry to a Secrets Manager-backed `secrets` block entry:

1. Add a new `<name>_secret_arn` variable (default `""`) alongside each existing plaintext variable, in both the root (`terraform/aws-ecs/variables.tf`) and module (`terraform/aws-ecs/modules/mcp-gateway/variables.tf`) variable files.
2. In `ecs-services.tf` (and `observability.tf` for Grafana), gate the existing plaintext `environment` entry on `<plaintext_var> != "" && <name>_secret_arn == ""`, and add a matching `secrets` block entry (`{ name = "<ENV_VAR>", valueFrom = var.<name>_secret_arn }`) gated on `<name>_secret_arn != ""`. Only one of the two is ever emitted for a given deployment. The environment variable name is unchanged either way, so the Python application code (`registry/core/config.py::Settings`, `auth_server`'s ad hoc `os.environ.get` reads) needs zero changes - ECS resolves the secret to a plain env var before the process starts.
3. Add each populated `<name>_secret_arn` to the `Resource` list in `aws_iam_policy.ecs_secrets_access` (`terraform/aws-ecs/modules/mcp-gateway/iam.tf`), following the existing `documentdb_credentials_secret_arn` precedent, so the task execution role can read externally-supplied secrets it does not own.
4. Apply the same `secrets`/IAM/fallback treatment to the Grafana module's `GF_SECURITY_ADMIN_PASSWORD` (used by both the `grafana` container and the `grafana-config` sidecar), including attaching `ecs_secrets_access` to Grafana's task-exec role, which does not currently have it.

This deliberately does **not** create new Terraform-owned `aws_secretsmanager_secret` resources for these 12 values (unlike the existing `entra_client_secret`/`okta_client_secret` pattern, which stores the secret value directly from a Terraform variable). Creating Terraform-owned secrets would still require the plaintext value to pass through `terraform.tfvars` and Terraform state to seed `secret_string`, which does not reduce plaintext exposure for the fallback path and does not support operators who want to reference a secret managed by an external pipeline (e.g. one with its own rotation or cross-account resource policy). Pointing at an externally-managed secret ARN is required to satisfy the stated need for rotation support and cross-account access (see Out of Scope below for what this issue does and does not deliver against that need).

### User Stories
- As an operator deploying the registry on AWS ECS via Terraform, I want every sensitive credential in the ECS task definitions read from Secrets Manager, so a leaked task-definition JSON or `ecs:DescribeTaskDefinition` call does not expose plaintext tokens or keys.
- As an operator upgrading from an existing deployment, I want the plaintext env-var path to keep working unchanged so I am not forced to create Secrets Manager entries and re-apply before my next `terraform apply` succeeds.
- As a security reviewer, I want IAM access to each new secret scoped only to the task execution roles that need it, consistent with the existing `ecs_secrets_access` policy.

### Acceptance Criteria
- [ ] Every env var listed in the Problem Statement has a matching `<name>_secret_arn` Terraform variable that can point at an operator-supplied AWS Secrets Manager secret ARN.
- [ ] Every one of those env vars is emitted via the `secrets` block (not `environment`) on the `auth-server` and/or `registry` container definitions whenever its corresponding `_secret_arn` variable is set; `GF_SECURITY_ADMIN_PASSWORD` is migrated the same way on the `grafana` and `grafana-config` containers.
- [ ] A new `*_secret_arn` Terraform variable exists for each migrated secret, defaulting to `""`.
- [ ] When a `*_secret_arn` variable is empty, the existing plaintext `environment` entry (driven by the existing plaintext variable) is emitted unchanged - existing deployments with populated tfvars keep working with no required changes.
- [ ] When a `*_secret_arn` variable is non-empty, the plaintext `environment` entry for that variable is omitted and the `secrets` block entry is used instead.
- [ ] `aws_iam_policy.ecs_secrets_access` grants `secretsmanager:GetSecretValue` on every new secret ARN, gated the same way the existing conditional secrets are (only include the ARN when the corresponding feature/flag is active or the ARN variable is non-empty).
- [ ] `terraform.tfvars.example` documents the new `*_secret_arn` variables next to the existing plaintext variables they replace, with a note recommending the Secrets Manager path for new deployments.
- [ ] No changes are required in `registry/core/config.py`, `auth_server/`, or any other Python source - the migration is Terraform-only.
- [ ] `terraform validate` and `terraform plan` succeed with an unmodified existing `terraform.tfvars` (backwards compatibility) and with the new `*_secret_arn` variables populated instead.

### Out of Scope
- **Automatic rotation** for the 12 non-Grafana secrets in scope. Reconciling against the stated need for rotation support: most of these are third-party-issued credentials (Auth0 management token, GitHub PAT/App key, ANS key/secret) with no AWS-native rotation protocol Terraform can drive, so rotation for them is inherently out of scope regardless of storage mechanism. A few are app-generated values (`registry_api_token`, `registry_api_keys`, `federation_static_token`) that *could* follow the existing `random_password` + Secrets Manager pattern already used for `secret_key`/`metrics_api_key` if Terraform owned their generation - this issue does not do that, since the `_secret_arn`-only design (see Proposed Solution) intentionally lets the operator supply an externally-managed secret instead. A follow-up issue should evaluate Terraform-owned generation with rotation for the app-generated subset specifically.
- **Cross-account access** is achieved implicitly by this design in that any `_secret_arn` variable can point at a secret ARN in another account with an appropriate resource policy and a cross-account IAM trust relationship on the task execution role - this issue does not add or validate any specific cross-account resource-policy configuration, since no cross-account secret-sharing pattern exists anywhere else in this codebase to model it on. Validating a concrete cross-account setup is left as a follow-up.
- Migrating the Keycloak service's own task definition (`terraform/aws-ecs/keycloak-ecs.tf`) - it already sources every credential from Secrets Manager/SSM and is out of scope.
- Changing the Docker Compose or Helm chart deployment surfaces - this issue is scoped to the Terraform/ECS stack only, per the stated constraints.
- Adding a runtime (in-Python) Secrets Manager client/fallback - ECS already resolves `secrets` block entries to plain environment variables before the process starts, so no application code changes are needed or in scope.
- Removing the plaintext env-var path outright - it must remain functional as a fallback during migration, per the stated constraints.
- Creating new Terraform-owned `aws_secretsmanager_secret` resources for these 12 values (see Proposed Solution for why the `_secret_arn` externally-supplied pattern was chosen instead).

### Dependencies
- None. All required infrastructure (`aws_kms_key.secrets`, `aws_iam_policy.ecs_secrets_access`) already exists in `terraform/aws-ecs/modules/mcp-gateway/`.

### Related Issues
- #1134 (reference issue driving this task)
- PR #947 (introduced the `mongodb_connection_string` / `mongodb_connection_string_secret_arn` fallback pattern this issue replicates)
- Issue #955 (introduced the `is_aws_documentdb` conditional-resource pattern referenced for gating new secrets)
