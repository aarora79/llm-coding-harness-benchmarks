# GitHub Issue: Migrate ECS Environment Variables to AWS Secrets Manager

## Labels
- security
- infrastructure
- refactor

## Description

### Problem Statement
Plaintext secrets are currently stored as ECS environment variables in Terraform, which poses a significant security risk. This approach lacks encryption, rotation capabilities, and audit trails for sensitive data like database passwords, API keys, OAuth client secrets, and admin passwords.

### Proposed Solution
Migrate sensitive ECS environment variables to AWS Secrets Manager to add encryption, automatic rotation, and audit trails. This involves:
1. Identifying all sensitive environment variables in ECS task definitions
2. Creating Secrets Manager resources in Terraform
3. Updating ECS task definitions to pull from Secrets Manager via the `secrets` block
4. Updating IAM task execution roles to allow reading those secrets
5. Keeping the plaintext environment variable path as a fallback during migration

### User Stories
- As an operator deploying the registry on AWS ECS + Terraform, I want to securely manage sensitive configuration so that my system meets security compliance requirements
- As a security engineer, I want to ensure that sensitive data is encrypted and rotated automatically so that data protection standards are met
- As a DevOps practitioner, I want to maintain backward compatibility during the migration so that deployments continue to function

### Acceptance Criteria
- [ ] All sensitive environment variables are migrated to AWS Secrets Manager
- [ ] Terraform configurations are updated to create Secrets Manager resources
- [ ] ECS task definitions are updated to use the `secrets` block instead of `environment` for sensitive variables
- [ ] IAM roles are updated to allow reading the new Secrets Manager secrets
- [ ] Plaintext environment variables are maintained as fallback during migration
- [ ] Migration supports AWS Secrets Manager rotation and cross-account access

### Out of Scope
- Helm/EKS deployment configurations (only ECS/Terraform)
- Database schema changes or migrations
- Application-level secret management (outside of ECS)

### Dependencies
- AWS account with appropriate permissions for Secrets Manager and ECS
- Existing Terraform infrastructure for ECS services

### Related Issues
- #1134