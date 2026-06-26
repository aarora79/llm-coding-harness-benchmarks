# GitHub Issue: Migrate Sensitive ECS Environment Variables to AWS Secrets Manager

## Title
Migrate sensitive ECS environment variables to AWS Secrets Manager (Issue #1134)

## Labels
- security
- enhancement
- terraform
- aws
- secrets-management

## Description

### Problem Statement
The MCP Gateway Registry's ECS task definitions currently pass sensitive credentials (API keys, tokens, passwords, encryption keys) as plaintext environment variables via the `environment` block. This presents several security risks:

1. **Credential Exposure**: Plaintext secrets are visible in ECS console, CloudFormation stacks, and container metadata
2. **No Centralized Rotation**: Each secret requires application redeployment to rotate
3. **Limited Audit Trail**: No granular access logging for secret usage
4. **Inconsistent Pattern**: Some secrets already use Secrets Manager (`secrets` block) while others remain in plaintext

### Scope of Migration

Migrate the following sensitive environment variables from the `environment` block to the `secrets` block with proper AWS Secrets Manager resources:

#### Auth Server Service (`ecs_service_auth`)
| Variable | Type | Secret Pattern |
|----------|------|----------------|
| `AUTH0_MANAGEMENT_API_TOKEN` | API Token | `auth0_management_api_token` |
| `REGISTRY_API_TOKEN` | Static Token | `registry_api_token` |
| `REGISTRY_API_KEYS` | JSON Keys Config | `registry_api_keys` |
| `FEDERATION_STATIC_TOKEN` | Bearer Token | `federation_static_token` |
| `FEDERATION_ENCRYPTION_KEY` | Fernet Key | `federation_encryption_key` |
| `ANS_API_KEY` | API Key | `ans_api_key` |
| `ANS_API_SECRET` | API Secret | `ans_api_secret` |

#### Registry Service (`ecs_service_registry`)  
| Variable | Type | Secret Pattern |
|----------|------|----------------|
| `AUTH0_MANAGEMENT_API_TOKEN` | API Token | `auth0_management_api_token` |
| `KEYCLOAK_ADMIN` + `KEYCLOAK_ADMIN_PASSWORD` | Admin Creds | `keycloak_admin_credentials` |
| `REGISTRY_API_TOKEN` | Static Token | `registry_api_token` |
| `REGISTRY_API_KEYS` | JSON Keys Config | `registry_api_keys` |
| `FEDERATION_STATIC_TOKEN` | Bearer Token | `federation_static_token` |
| `FEDERATION_ENCRYPTION_KEY` | Fernet Key | `federation_encryption_key` |
| `ANS_API_KEY` | API Key | `ans_api_key` |
| `ANS_API_SECRET` | API Secret | `ans_api_secret` |
| `REGISTRATION_WEBHOOK_AUTH_TOKEN` | Webhook Token | `registration_webhook_auth_token` |
| `MONGODB_CONNECTION_STRING` | DB URI | `mongodb_connection_string` |

#### MCPGW Service (`ecs_service_mcpgw`)
| Variable | Type | Secret Pattern |
|----------|------|----------------|
| `REGISTRY_PASSWORD` | Basic Auth Password | `registry_password` |

### Proposed Solution

1. **Create Secrets Manager Resources** (`secrets.tf`)
   - Add `aws_secretsmanager_secret` and `aws_secretsmanager_secret_version` for each new secret
   - Use KMS key for encryption
   - Mark with `sensitive = true` and Checkov skip annotations where appropriate

2. **Update ECS Task Definitions** (`ecs-services.tf`)
   - Move identified variables from `environment` to `secrets` block
   - Use `valueFrom` with secret ARN

3. **Update IAM Policy** (`iam.tf`)
   - Add new secret ARNs to `ecs_secrets_access` policy
   - Ensure task execution role can read new secrets

4. **Update Variables** (`variables.tf` at root)
   - Mark sensitive variables with `sensitive = true`
   - Update descriptions to note Secrets Manager preference

### Acceptance Criteria

- [ ] All identified sensitive env vars migrated from `environment` to `secrets` block
- [ ] Secrets Manager resources created with proper naming and KMS encryption
- [ ] IAM policy updated to allow task execution role access to new secrets
- [ ] Terraform `plan` shows no plaintext secret values in environment blocks for migrated vars
- [ ] ECS task definitions properly reference secrets via `valueFrom`
- [ ] Backwards compatibility: existing deployments continue working (no force recreation)

### Out of Scope

- Rotating existing secrets (future enhancement)
- Automated secret rotation via Lambda (separate issue)
- Secrets for demo servers (currenttime, realserverfaketools)
- Non-sensitive configuration (keep in environment block)

### Dependencies

- AWS KMS key for encryption (already exists: `aws_kms_key.secrets`)
- Terraform AWS provider >= 5.0 (already configured)

### Related Issues

- Issue #947: MongoDB connection string override (Secrets Manager variant)
- Issue #1000: Extra environment variables (use `secrets` instead for sensitive values)

### Security Considerations

1. **Least Privilege**: IAM policy should only allow reading secrets needed by each service
2. **Encryption at Rest**: All secrets encrypted with customer-managed KMS key
3. **No State Exposure**: Mark Terraform variables as `sensitive` to avoid logging
4. **Checkov Compliance**: Add appropriate skip annotations for application-managed secrets

### Testing Requirements

1. **Terraform Plan**: Verify no plaintext secrets in task definitions
2. **ECS Console**: Confirm secrets appear as "Hidden" in container definitions
3. **Application Startup**: Verify services correctly load secrets from Secrets Manager
4. **IAM Policy**: Test task execution role can access all required secrets
