# Testing Plan: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-07-22*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview
### Scope of Testing
This testing plan covers all aspects of migrating Keycloak's database authentication from static passwords to RDS IAM authentication while maintaining backward compatibility. Tests will verify both authentication methods work correctly and that the transition is seamless.

### Prerequisites
- [ ] Keycloak ECS task running with IAM authentication enabled
- [ ] Aurora MySQL cluster with IAM database authentication enabled
- [ ] Proper IAM roles and policies configured
- [ ] Test database with appropriate user accounts

### Shared Variables
```bash
export KEYCLOAK_DB_HOST="aurora-cluster-endpoint.region.rds.amazonaws.com"
export KEYCLOAK_DB_PORT="3306"
export KEYCLOAK_DB_NAME="keycloak"
export KEYCLOAK_DB_USERNAME="keycloak_user"
export KEYCLOAK_DB_PASSWORD="legacy_password"
export AWS_REGION="us-east-1"
```

## 1. Functional Tests
### 1.1 Database Connection Tests
**Test Case 1: Password Authentication Enabled**
```bash
# Set environment for password auth
export KEYCLOAK_DB_IAM_AUTH_ENABLED=false
export KEYCLOAK_FEATURE_FLAG_ENABLED=true

# Verify Keycloak can connect using password
uv run python -m keycloak.db_test --auth-method=password
```

**Expected Result:** Connection successful, no errors

**Test Case 2: IAM Authentication Enabled**
```bash
# Set environment for IAM auth
export KEYCLOAK_DB_IAM_AUTH_ENABLED=true
export KEYCLOAK_FEATURE_FLAG_ENABLED=true

# Verify Keycloak can connect using IAM auth
uv run python -m keycloak.db_test --auth-method=iam
```

**Expected Result:** Connection successful, IAM token generated and used

**Test Case 3: Feature Flag Disabled**
```bash
# Disable feature flag
export KEYCLOAK_FEATURE_FLAG_ENABLED=false

# Verify Keycloak falls back to password auth
uv run python -m keycloak.db_test --auth-method=password
```

**Expected Result:** Connection successful using password (feature flag disabled)

### 1.2 IAM Token Generation Tests
**Test Case 4: IAM Token Generation Success**
```bash
# Test IAM token generation directly
uv run python -c "
import boto3
client = boto3.client('rds', region='us-east-1')
token = client.generate_db_auth_token(
    DBHostname='$KEYCLOAK_DB_HOST',
    Port=$KEYCLOAK_DB_PORT,
    DBUsername='$KEYCLOAK_DB_USERNAME'
)
print('Token generated successfully:', len(token) > 0)
"
```

**Expected Result:** Token generated successfully with valid length

**Test Case 5: IAM Token Generation Failure**
```bash
# Test IAM token generation with invalid parameters
uv run python -c "
import boto3
try:
    client = boto3.client('rds', region='us-east-1')
    token = client.generate_db_auth_token(
        DBHostname='invalid-host',
        Port=3306,
        DBUsername='invalid-user'
    )
    print('Unexpected success')
except Exception as e:
    print('Expected failure:', type(e).__name__)
"
```

**Expected Result:** Expected failure due to invalid parameters

## 2. Backwards Compatibility Tests
### 2.1 Environment Variable Compatibility
**Test Case 6: Legacy Environment Variables**
```bash
# Test with only legacy environment variables
unset KEYCLOAK_DB_IAM_AUTH_ENABLED
unset KEYCLOAK_FEATURE_FLAG_ENABLED

# Verify backward compatibility
uv run python -m keycloak.config_test
```

**Expected Result:** Configuration loads successfully with default password auth

### 2.2 Configuration Migration
**Test Case 7: Mixed Configuration**
```bash
# Test with both old and new environment variables
export KEYCLOAK_DB_PASSWORD="legacy_password"
export KEYCLOAK_DB_IAM_AUTH_ENABLED=true
export KEYCLOAK_FEATURE_FLAG_ENABLED=true

# Verify proper precedence
uv run python -m keycloak.config_test
```

**Expected Result:** IAM auth takes precedence when enabled

## 3. UX Tests
### 3.1 Admin UI Tests
**Test Case 8: Admin UI Authentication Status**
```bash
# Test that admin UI shows correct authentication method
curl -H "Authorization: Bearer $ADMIN_TOKEN" http://keycloak-admin/api/auth-status
```

**Expected Result:** API returns correct authentication method (password/IAM)

### 3.2 Error Message Tests
**Test Case 9: Connection Error Messages**
```bash
# Test error messages when database connection fails
export KEYCLOAK_DB_HOST="invalid-host"
uv run python -m keycloak.db_test 2>&1 | grep -i "connection failed\|authentication failed"
```

**Expected Result:** Clear error messages indicating the authentication method used

## 4. Deployment Surface Tests
### 4.1 Terraform Configuration Tests
**Test Case 10: Terraform Variable Validation**
```bash
# Validate Terraform variables
cd terraform/aws-ecs/
terraform validate
terraform plan -var="keycloak_db_iam_auth_enabled=true"
```

**Expected Result:** No validation errors, plan shows correct configuration

### 4.2 ECS Task Definition Tests
**Test Case 11: ECS Task Environment Variables**
```bash
# Check ECS task definition contains new variables
aws ecs describe-task-definition --task-definition keycloak-task
```

**Expected Result:** Environment variables for IAM auth present

### 4.3 IAM Policy Tests
**Test Case 12: IAM Role Permissions**
```bash
# Verify IAM role has required permissions
aws iam get-role-policy --role-name keycloak-role --policy-name keycloak-rds-access
```

**Expected Result:** Policy contains rds:GenerateDBAuthToken action

## 5. End-to-End API Tests
### 5.1 Authentication Flow Test
**Test Case 13: Complete Authentication Flow**
```bash
# Test full authentication flow
export KEYCLOAK_DB_IAM_AUTH_ENABLED=true
export KEYCLOAK_FEATURE_FLAG_ENABLED=true

# Start Keycloak with IAM auth
uv run python -m keycloak.start

# Verify connection to database
curl http://keycloak:8080/health

# Verify logs show IAM authentication
grep "IAM auth" /var/log/keycloak.log
```

**Expected Result:** Keycloak starts successfully, connects to database, logs show IAM auth usage

### 5.2 Transition Period Test
**Test Case 14: Gradual Transition**
```bash
# Test gradual transition from password to IAM
export KEYCLOAK_DB_IAM_AUTH_ENABLED=false
export KEYCLOAK_FEATURE_FLAG_ENABLED=true

# Verify password auth works
uv run python -m keycloak.db_test --auth-method=password

# Enable IAM auth
export KEYCLOAK_DB_IAM_AUTH_ENABLED=true

# Verify IAM auth works
uv run python -m keycloak.db_test --auth-method=iam
```

**Expected Result:** Smooth transition with no downtime

## 6. Test Execution Checklist
- [ ] Section 1 (Functional) passes
- [ ] Section 2 (Backwards Compat) verified or marked Not Applicable
- [ ] Section 3 (UX) verified or marked Not Applicable
- [ ] Section 4 (Deployment) verified or marked Not Applicable
- [ ] Section 5 (E2E) verified or marked Not Applicable
- [ ] Unit tests added under `tests/unit/`
- [ ] Integration tests added under `tests/integration/`
- [ ] `uv run pytest tests/` passes with no regressions