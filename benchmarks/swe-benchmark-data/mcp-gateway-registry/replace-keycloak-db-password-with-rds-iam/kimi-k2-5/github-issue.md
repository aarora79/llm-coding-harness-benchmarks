# GitHub Issue: Replace Keycloak Database Password Authentication with RDS IAM Authentication

## Title
Replace Keycloak database password authentication with RDS IAM authentication

## Labels
- enhancement
- security
- infrastructure
- aws
- database

## Description

### Problem Statement
Currently, the MCP Gateway deployment uses static password-based authentication for the Keycloak database (Aurora MySQL). The database password is:

1. Stored as a Terraform variable (`keycloak_database_password`)
2. Written to AWS Secrets Manager via Terraform
3. Rotated every 30 days via a Lambda function
4. Passed to the Keycloak ECS task via Secrets Manager reference

This approach has several security drawbacks:
- Static credentials stored in Terraform state
- Secret rotation complexity requiring Lambda functions
- Password-based authentication is less secure than IAM-based authentication
- The RDS Proxy is currently configured with `iam_auth = "DISABLED"`

### Proposed Solution
Migrate the Keycloak database authentication from password-based to RDS IAM authentication. This involves:

1. **Enable RDS IAM authentication** on the Aurora MySQL cluster
2. **Create database IAM users** in MySQL for the ECS task
3. **Configure the ECS task** to generate short-lived RDS IAM auth tokens
4. **Update IAM roles/policies** to grant `rds-db:connect` permission
5. **Remove static password variables** from Terraform configuration
6. **Update the RDS Proxy** to use IAM authentication (if supported)
7. **Remove the password rotation Lambda** (no longer needed)

### User Stories
- As a security engineer, I want to eliminate static database credentials so that credential rotation is automatic and credential leakage risk is minimized
- As an operations engineer, I want to simplify the infrastructure by removing the password rotation Lambda so that there are fewer moving parts to maintain
- As a compliance officer, I want to use IAM-based database authentication so that we meet security best practices for AWS deployments

### Acceptance Criteria
- [ ] RDS IAM authentication is enabled on the Aurora MySQL cluster (`iam_database_authentication_enabled = true`)
- [ ] Database IAM user(s) are created in MySQL with appropriate permissions
- [ ] ECS task IAM role has `rds-db:connect` permission for the database IAM user
- [ ] ECS task generates RDS IAM auth tokens (15-minute validity) at runtime
- [ ] Keycloak container uses IAM auth token as the database password
- [ ] Static `keycloak_database_password` variable is removed from Terraform
- [ ] Password rotation Lambda and related resources are removed or disabled
- [ ] RDS Proxy is updated to use IAM authentication (if Aurora MySQL Serverless v2 supports it)
- [ ] Secrets Manager secret for database credentials is removed or repurposed
- [ ] Terraform `plan`/`apply` works without the password variable
- [ ] Keycloak starts successfully and can connect to the database
- [ ] Documentation is updated to reflect the new authentication method

### Out of Scope
- Changes to DocumentDB authentication (separate issue)
- Migration of existing deployments (focus on new deployments)
- Changes to Keycloak admin credentials (KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD)

### Dependencies
- Issue #1026 (completed) - Database credential sourcing from Secrets Manager
- Requires Aurora MySQL 8.0+ (already using 8.0.mysql_aurora.3.10.3)

### Related Issues
- #1026 - Database credentials sourced from Secrets Manager
- #1122 - Keycloak 25 hostname configuration

### Technical Notes

**RDS IAM Auth Token Generation:**
RDS IAM auth tokens are signed URLs that are valid for 15 minutes. The token can be generated using AWS CLI or SDK:

```bash
aws rds generate-db-auth-token \
  --hostname <cluster-endpoint> \
  --port 3306 \
  --region <region> \
  --username <db-iam-user>
```

**MySQL IAM User Creation:**
```sql
CREATE USER 'keycloak_iam'@'%' IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS';
GRANT ALL PRIVILEGES ON keycloak.* TO 'keycloak_iam'@'%';
FLUSH PRIVILEGES;
```

**IAM Policy for ECS Task:**
```json
{
  "Effect": "Allow",
  "Action": "rds-db:connect",
  "Resource": "arn:aws:rds-db:<region>:<account>:dbuser:<cluster-resource-id>/keycloak_iam"
}
```

### Implementation Hints

1. **Database User Management**: Consider using Terraform `null_resource` with `local-exec` provisioner or a Lambda-backed custom resource to create the IAM database user

2. **Token Generation in ECS**: Options include:
   - Sidecar container that generates tokens and writes to shared volume
   - Entrypoint script in Keycloak container that generates token before starting Keycloak
   - Use AWS SDK for Java directly in Keycloak (would require custom extension)

3. **Keycloak Configuration**: Keycloak expects `KC_DB_USERNAME` and `KC_DB_PASSWORD`. For IAM auth, the password is the IAM auth token.

4. **RDS Proxy Consideration**: Aurora Serverless v2 supports IAM authentication, but RDS Proxy support for IAM auth with Aurora MySQL needs verification

### Risk Assessment
- **Low Risk**: The change is additive - existing password authentication can remain as a fallback during migration
- **Medium Risk**: Token generation failure would prevent Keycloak from connecting to the database
- **Mitigation**: Implement proper health checks and fallback mechanisms

### Testing Requirements
1. Unit tests for IAM policy templates
2. Integration test: ECS task can generate valid auth token
3. Integration test: Keycloak can connect using IAM auth token
4. Regression test: Keycloak database connectivity and functionality
