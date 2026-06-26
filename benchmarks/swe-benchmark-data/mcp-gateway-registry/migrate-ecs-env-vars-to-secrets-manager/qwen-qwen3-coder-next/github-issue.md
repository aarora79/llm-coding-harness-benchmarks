# GitHub Issue: Migrate Sensitive ECS Environment Variables to AWS Secrets Manager

## Title
Migrate Sensitive ECS Environment Variables to AWS Secrets Manager

## Labels
- enhancement
- security
- infrastructure
- terraform

## Description

### Problem Statement
Currently, sensitive configuration values such as API keys, passwords, client secrets, and tokens are stored as plaintext environment variables in ECS task definitions across multiple services (auth-server, registry, mcpgw, and Keycloak). This includes:

- **Auth Server**: Keycloak client secrets, Auth0/Admin OKta credentials, static API tokens, webhook credentials
- **Registry**: Embeddings API keys, Keycloak admin password, MongoDB connection strings, GitHub credentials, federation tokens, registration gate credentials
- **Keycloak**: Admin credentials, database credentials stored in SSM Parameter Store with plain text access

Storing secrets in plaintext environment variables poses security risks:
- Secrets are visible in ECS task definitions in AWS Console
- Secrets are stored in Terraform state files (even with sensitive=true, they're still in state)
- Secrets are accessible to anyone with DescribeTaskDefinition permissions
- Secrets are exposed in CloudWatch Logs if the container logs environment variables

### Proposed Solution
Migrate all sensitive environment variables to AWS Secrets Manager and reference them in ECS task definitions using the `secrets` block. This approach:

1. Creates Secrets Manager resources for each sensitive value
2. Updates ECS task definitions to use the `secrets` block instead of `environment` for sensitive values
3. Updates IAM task execution roles to grant `secretsmanager:GetSecretValue` permissions
4. Removes sensitive values from environment variable arrays

### User Stories
- As a security engineer, I want secrets to be stored in AWS Secrets Manager so that they are encrypted at rest with KMS and access is auditable
- As a DevOps engineer, I want ECS tasks to retrieve secrets at container startup so that sensitive data is not exposed in the task definition
- As an administrator, I want to rotate secrets without updating task definitions so that I can use Secrets Manager's automatic rotation capabilities

### Acceptance Criteria
- [ ] All sensitive environment variables are identified across all ECS services
- [ ] Every sensitive variable has a corresponding Secrets Manager secret
- [ ] ECS task definitions use the `secrets` block for all sensitive values
- [ ] IAM task execution roles allow `secretsmanager:GetSecretValue` on the new secrets
- [ ] No plaintext secrets remain in environment variable arrays
- [ ] `terraform plan` shows no secret values in output
- [ ] The service continues to function correctly after migration

### Out of Scope
- Implementing automatic secret rotation for Secrets Manager secrets (this can be added later)
- Migrating non-sensitive environment variables
- Changing the application code to use different secret retrieval mechanisms (ECS retrieves and injects secrets automatically)
- Modifying the Keycloak database password (handled via separate RDS IAM auth migration per issue #1303)

### Dependencies
- Issue #1134 (original issue reference)

### Related Issues
- Issue #1303: Replace Keycloak DB password with RDS IAM authentication (separate migration for database credentials)
