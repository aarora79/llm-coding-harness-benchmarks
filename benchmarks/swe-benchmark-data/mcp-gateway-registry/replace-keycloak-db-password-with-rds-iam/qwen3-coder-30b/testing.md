# Testing Plan: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-07-22*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview
### Scope of Testing
This testing plan covers verification of the Keycloak database authentication transition from static passwords to RDS IAM authentication. The tests ensure both the new IAM authentication method works correctly and that backwards compatibility with password authentication is maintained.

### Prerequisites
- [ ] AWS account with appropriate permissions for RDS and ECS operations
- [ ] Existing MCP Gateway Registry deployment with Keycloak
- [ ] Access to Terraform configuration files
- [ ] Keycloak database credentials and access to Aurora cluster

### Shared Variables
```bash
export AWS_REGION="us-west-2"
export KEYCLOAK_CLUSTER_NAME="keycloak"
export KEYCLOAK_TASK_FAMILY="keycloak"
export TEST_DATABASE_USER="keycloak"
```

## 1. Functional Tests
### 1.1 Terraform Configuration Tests
#### Test 1: Enable IAM Authentication on Aurora Cluster
```bash
# Verify that IAM database authentication is enabled in Terraform
cd terraform/aws-ecs
terraform plan -var=keycloak_database_iam_enabled=true

# Expected: Should show changes to enable iam_database_authentication_enabled = true
# Expected: Should not show any breaking changes to existing configuration
```

#### Test 2: IAM Role Policy Update
```bash
# Verify IAM role policy includes rds:GenerateDBAuthToken permission
terraform show | grep -A 5 -B 5 "GenerateDBAuthToken"

# Expected: Should show the new permission in the task execution role policy
# Expected: Should show the permission scoped to the RDS cluster
```

### 1.2 Keycloak ECS Task Tests
#### Test 3: Environment Variable Configuration
```bash
# Check that new environment variable is properly configured
aws ecs describe-task-definition \
    --task-definition keycloak \
    --region $AWS_REGION \
    | jq '.taskDefinition.containerDefinitions[].environment[] | select(.name=="KEYCLOAK_DATABASE_IAM_ENABLED")'

# Expected: Should return the environment variable with value "true" when enabled
```

#### Test 4: IAM Authentication Token Generation
```bash
# Test that the Keycloak ECS task can generate IAM auth tokens
# This would require running a task and checking logs
aws ecs execute-command \
    --cluster keycloak \
    --task <task-id> \
    --command "aws rds generate-db-auth-token --hostname <aurora-hostname> --port 3306 --username <db-user> --region $AWS_REGION" \
    --region $AWS_REGION

# Expected: Should return a valid short-lived token
```

## 2. Backwards Compatibility Tests
### 2.1 Password Authentication Retention
#### Test 5: Password Authentication Still Works
```bash
# Deploy with IAM disabled (default behavior)
terraform apply -var=keycloak_database_iam_enabled=false

# Verify Keycloak can connect using password authentication
# Check Keycloak logs for successful database connection
aws logs tail /ecs/keycloak --region $AWS_REGION

# Expected: Should see successful database connection messages
# Expected: Should not see any IAM-related errors
```

#### Test 6: Feature Flag Toggle
```bash
# Toggle between IAM and password authentication
terraform apply -var=keycloak_database_iam_enabled=true
# Verify IAM authentication works

terraform apply -var=keycloak_database_iam_enabled=false
# Verify password authentication still works

# Expected: Successful transitions between both modes
# Expected: No downtime during the toggle
```

## 3. UX Tests
### 3.1 Deployment Process
#### Test 7: Deployment Configuration
```bash
# Verify that deployment configuration files are updated correctly
grep -r "KEYCLOAK_DATABASE_IAM_ENABLED" terraform/

# Expected: Should find the environment variable in ECS task definition
# Expected: Should find the variable in variables.tf
```

### 3.2 Operator Experience
#### Test 8: Documentation and Error Messages
```bash
# Check that documentation is updated and clear
grep -r "IAM" terraform/aws-ecs/README.md

# Expected: Should show clear instructions for enabling IAM authentication
# Expected: Should explain the backwards compatibility feature
```

## 4. Deployment Surface Tests
### 4.1 Terraform Configuration
#### Test 9: Terraform Variable Validation
```bash
# Test that new variable is properly validated
terraform validate

# Expected: Should pass without errors
# Expected: Should show the new variable in help text
```

### 4.2 ECS Task Definition
#### Test 10: ECS Task Configuration
```bash
# Verify ECS task definition includes all required components
aws ecs describe-task-definition \
    --task-definition keycloak \
    --region $AWS_REGION \
    | jq '.taskDefinition.containerDefinitions[].secrets[] | select(.name=="KC_DB_PASSWORD")'

# Expected: Should show that KC_DB_PASSWORD is still referenced for backwards compatibility
```

### 4.3 IAM Permissions
#### Test 11: IAM Policy Permissions
```bash
# Validate that IAM policies are correctly configured
aws iam get-role-policy \
    --role-name keycloak-task-exec-role-$AWS_REGION \
    --policy-name keycloak-task-exec-secrets-policy \
    --region $AWS_REGION

# Expected: Should show the rds:GenerateDBAuthToken permission
# Expected: Should show appropriate resource restrictions
```

### 4.4 Deployment Verification
#### Test 12: Deployment Success
```bash
# Verify deployment completes successfully
terraform apply -var=keycloak_database_iam_enabled=true

# Check ECS service status
aws ecs describe-services \
    --cluster keycloak \
    --services keycloak \
    --region $AWS_REGION

# Expected: Service status should be ACTIVE
# Expected: No deployment failures
```

## 5. End-to-End API Tests
### 5.1 Authentication Flow Test
#### Test 13: Full Authentication Cycle
```bash
# 1. Deploy with IAM enabled
terraform apply -var=keycloak_database_iam_enabled=true

# 2. Verify Keycloak starts successfully
# 3. Verify Keycloak can connect to database
# 4. Verify Keycloak authentication works
# 5. Verify Keycloak database operations work

# Expected: All steps should complete successfully
# Expected: No authentication failures
# Expected: Database operations should succeed
```

### 5.2 Rollback Test
#### Test 14: Backwards Compatibility Rollback
```bash
# 1. Deploy with IAM enabled
terraform apply -var=keycloak_database_iam_enabled=true

# 2. Verify everything works
# 3. Rollback to password authentication
terraform apply -var=keycloak_database_iam_enabled=false

# 4. Verify everything still works
# 5. Verify no data loss or corruption

# Expected: Smooth rollback process
# Expected: No service interruption
# Expected: All data intact
```

## 6. Test Execution Checklist
- [ ] Section 1 (Functional) passes
- [ ] Section 2 (Backwards Compat) verified or marked Not Applicable
- [ ] Section 3 (UX) verified or marked Not Applicable
- [ ] Section 4 (Deployment) verified or marked Not Applicable
- [ ] Section 5 (E2E) verified or marked Not Applicable
- [ ] Unit tests added under `tests/unit/`
- [ ] Integration tests added under `tests/integration/`
- [ ] `uv run pytest tests/` passes with no regressions