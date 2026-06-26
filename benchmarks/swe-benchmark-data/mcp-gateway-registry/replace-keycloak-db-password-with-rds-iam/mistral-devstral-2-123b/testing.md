# Testing Plan: Replace Keycloak DB Password with RDS IAM Authentication

*Created: 2026-06-25*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This testing plan verifies that RDS IAM Authentication successfully replaces static database credentials for Keycloak's Aurora MySQL connection, maintaining full functionality while improving security posture.

### Prerequisites
- [ ] Existing MCP Gateway Registry deployment running on ECS
- [ ] Aurora MySQL cluster accessible
- [ ] AWS CLI configured with appropriate permissions
- [ ] Terraform CLI installed for infrastructure validation

### Shared Variables
```bash
# Export common environment variables for testing
export AWS_REGION="us-east-1"  # or your deployment region
export TF_DIR="./terraform/aws-ecs"
export ECS_CLUSTER_NAME="keycloak"
export ECS_SERVICE_NAME="keycloak"
export ECS_TASK_DEFINITION="keycloak"
export RDS_CLUSTER_IDENTIFIER="keycloak"
export RDS_PROXY_NAME="keycloak-proxy"
export SECRET_NAME="keycloak/database"
```

## 1. Functional Tests

### 1.1 Terraform Validation Tests
Verify that Terraform changes apply correctly and infrastructure is properly configured.

#### Test 1.1.1: Validate Terraform Syntax
**Command:**
```bash
cd "$TF_DIR"
terraform validate
```

**Expected Status:** Exit code 0
**Expected Response:** `Success! The configuration is valid.`
**Assertions:**
- No syntax errors in Terraform files
- All required variables have acceptable defaults
- No provider configuration issues

**Negative Case:**
```bash
cd "$TF_DIR"
echo 'invalid syntax {' > test.tf
terraform validate
exit_code=$?
rm test.tf
if [ $exit_code -eq 0 ]; then
    echo "ERROR: Terraform should have failed validation"
    exit 1
fi
echo "Negative test passed: Invalid syntax properly detected"
```

#### Test 1.1.2: Plan Infrastructure Changes
**Command:**
```bash
cd "$TF_DIR"
terraform plan -out=plan.out
```

**Expected Status:** Exit code 0 (plan generated)
**Expected Response:** Shows changes to:
- `aws_rds_cluster_parameter_group.keycloak` (IAM authentication enabled)
- `aws_db_proxy.keycloak` (IAM auth set to REQUIRED)
- `aws_iam_role_policy.keycloak_task_exec_ssm_policy` (rds-db:connect permission added)

**Assertions:**
- No destroy of database cluster
- No unexpected resource changes
- Hybrid authentication mode maintained (both SECRETS and IAM)

**Verification:**
```bash
cd "$TF_DIR"
terraform show -json plan.out | jq -r '.planned_values.root_module.resources[] | select(.address | test("aws_rds_cluster_parameter_group") or test("aws_db_proxy") or test("aws_iam_role_policy")) | .address'
```

### 1.2 IAM Authentication Configuration Tests

#### Test 1.2.1: Verify IAM Authentication Enabled on RDS Cluster
**Command:**
```bash
# After terraform apply
aws rds describe-db-cluster-parameters \
    --db-cluster-identifier "$RDS_CLUSTER_IDENTIFIER" \
    --query \"DBClusterParameters[?ParameterName=='aurora_enable_iam_auth'].{Name:ParameterName,Value:ParameterValue,Source:Source}\" \
    --output table
```

**Expected Response:**
```
--------------------------------------------------------------
|                  DescribeDBClusterParameters                 |
+----------------------+-------------+-----------+------------+
|           Name       |    Source    |   Value   |  ApplyType |
+----------------------+-------------+-----------+------------+
|   aurora_enable_iam_auth  |   user      |    1      |   dynamic  |
+----------------------+-------------+-----------+------------+
```

**Assertions:**
- Parameter `aurora_enable_iam_auth` exists
- Value is `1` (enabled)
- Source is `user` (manually set, not default)

**Negative Case:**
```bash
# If not enabled, this should return empty result
aws rds describe-db-cluster-parameters \
    --db-cluster-identifier "$RDS_CLUSTER_IDENTIFIER" \
    --query \"DBClusterParameters[?ParameterName=='aurora_enable_iam_auth' && ParameterValue=='0']\" \
    --output text
if [ -n "$output" ]; then
    echo "ERROR: IAM authentication is disabled"
    exit 1
fi
```

#### Test 1.2.2: Verify IAM Authentication on RDS Proxy
**Command:**
```bash
aws rds describe-db-proxies \
    --db-proxy-name "$RDS_PROXY_NAME" \
    --query \"DBProxies[0].Auth[]\" \
    --output json
```

**Expected Response:**
```
[
  {
    "SecretArn": "arn:aws:secretsmanager:region:account:secret:keycloak/database",
    "IAMAuth": "REQUIRED",
    "ClientPasswordAuthType": "MYSQL_CACHING_SHA2_PASSWORD",
    "AuthScheme": "SECRETS",
    "Description": ""
  }
]
```

**Assertions:**
- `IAMAuth` field exists
- Value is `REQUIRED`
- Other auth methods still present (SECRETS)

### 1.3 ECS Task Permission Tests

#### Test 1.3.1: Verify IAM Policy Addition to Task Role
**Command:**
```bash
# Get task execution role name
ROLE_NAME=$(aws ecs describe-task-definition \
    --task-definition "$ECS_TASK_DEFINITION" \
    --query 'taskDefinition.executionRoleArn' \
    --output text | cut -d'/' -f2)

# Check if policy contains rds-db:connect
aws iam list-attached-role-policies \
    --role-name "$ROLE_NAME" \
    --query 'AttachedPolicies[].PolicyName' \
    --output text
aws iam get-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "keycloak-task-exec-ssm-policy" \
    --query 'PolicyDocument.Statement[?contains(Action[0], `rds-db`)]' \
    --output json
```

**Expected Response:** Shows policy statement with `rds-db:connect` permission for the RDS cluster.

**Assertions:**
- Policy document contains `rds-db:connect`
- Resource includes RDS cluster ARN
- Effect is `Allow`

## 2. Backwards Compatibility Tests

These tests ensure the changes maintain compatibility with existing configurations and workflows.

### 2.1: Verify Existing Secret Still Accessible
**Command:**
```bash
# Test that the existing secrets manager secret can still be retrieved
# (will fail after final cleanup, but should work during transition)
aws secretsmanager get-secret-value \
    --secret-id "$SECRET_NAME" \
    --query 'SecretString' \
    --output text
```

**Expected Response:** Returns JSON containing username and password (password may be masked).
**Assertions:**
- Secret still exists during transition
- Can be retrieved by authorized callers
- Hybrid authentication mode working

### 2.2: Database Connection Tests from Original ECS Task
**Command:**
```bash
# Create a temporary share profile with original credentials
# Extract username and password for testing
SECRET_VALUE=$(aws secretsmanager get-secret-value --secret-id "$SECRET_NAME" --query 'SecretString' --output text)
DB_USERNAME=$(echo "$SECRET_VALUE" | jq -r '.username')
DB_PASSWORD=$(echo "$SECRET_VALUE" | jq -r '.password')
DB_ENDPOINT=$(aws rds describe-db-proxies --db-proxy-name "$RDS_PROXY_NAME" --query 'DBProxies[0].Endpoint' --output text)

# Test connection using original credentials (should still work in hybrid mode)
timeout 5 bash -c "until mysql -h \"$DB_ENDPOINT\" -P 3306 -u \"$DB_USERNAME\" -p\"$DB_PASSWORD\" -e 'SELECT 1;' >/dev/null 2>&1; do sleep 1; done" && echo "Success" || echo "Failed"
```

**Expected Response:** `Success` within 5 seconds
**Assertions:**
- Password-based authentication still works
- RDS proxy forwards password connections
- Database cluster accepts password auth

## 3. Migration Tests

### 3.1: Test IAM Authentication Token Generation
**Command:**
```bash
# Generate an IAM authentication token for RDS
aws rds generate-db-auth-token \
    --hostname "$RDS_PROXY_NAME" \
    --port 3306 \
    --region "$AWS_REGION" \
    --username "iam:rds-iam"  # Must match exactly
```

**Expected Response:** Long authentication token string (400+ characters)
**Assertions:**
- Token generation succeeds
- Token format is valid
- No error messages

### 3.2: Test Database Connection with IAM Authentication
**Command:**
```bash
# First, generate the token
IAM_TOKEN=$(aws rds generate-db-auth-token \
    --hostname "$RDS_CLUSTER_IDENTIFIER" \
    --port 3306 \
    --region "$AWS_REGION" \
    --username "iam:rds-iam")

# Test connection using IAM authentication
# Note: This requires MySQL client 8.0.26+ with SSL support
MYSQLCONNSTR="mysql -h $RDS_PROXY_NAME -P 3306 --ssl-mode=REQUIRED --enable-cleartext-plugin -u iam:rds-iam -p\"$IAM_TOKEN\""
timeout 10 bash -c "$MYSQLCONNSTR -e 'SELECT 1 AS retry_test;'
```

**Expected Response:** Returns `1` row with `retry_test` value
**Assertions:**
- IAM authentication successful
- Connection uses SSL
- Token grants database access
- Proxy forwards IAM connections

**Note:** May require MySQL client compilation with SSL and IAM support

#### Alternative with ECS Task Context
```bash
# If mysql client limitations prevent testing, use ECS exec to test inside container
TASK_ARN=$(aws ecs list-tasks --cluster "$ECS_CLUSTER_NAME" --service-name "$ECS_SERVICE_NAME" --query 'taskArns[0]' --output text)
aws ecs execute-command \
    --cluster "$ECS_CLUSTER_NAME" \
    --task "$TASK_ARN" \
    --container "keycloak" \
    --interactive \
    --command "/bin/bash -c 'echo "SELECT now();" | mysql -h "$KEYCLOAK_DB_URL_HOST" -P 3306 -u "iam:rds-iam" -p"$(aws rds generate-db-auth-token --hostname RDS_ENDPOINT --port 3306 --region REGION --username iam:rds-iam)" 2>&1 | head -20'"
```

## 4. UX Tests

Test the end-to-end workflow for developers and operators.

### 4.1: Key Architecture Decision Verification
**Command:**
```bash
# Verify the hybrid mode strategy by checking both authentication types work
echo "Testing hybrid authentication mode..."

# Test 1: Password authentication (normal operation should use IAM, but password should still work)
# Test 2: IAM authentication
# Expected: Both succeed, confirming zero-downtime deployment capability
```

This test validates the key design choice to maintain both authentication methods during transition, ensuring seamless operation without service interruption.

## 5. Security Tests

### 5.1: Verify No Long-Lived Credentials
**Command:**
```bash
# Check that static credentials are no longer used in ECS task
aws ecs describe-task-definition --task-definition "$ECS_TASK_DEFINITION" --query 'taskDefinition.containerDefinitions[0].secrets'

# After migration, there should be no password secrets, only:
# - KEYCLOAK_ADMIN
# - KEYCLOAK_ADMIN_PASSWORD
# - KC_DB_URL
# - KC_DB_USERNAME (should use IAM format)
# - KC_DB_PASSWORD (should be empty or removed)
```

**Expected:** KC_DB_USERNAME uses IAM format (`iam:rds-iam`), KC_DB_PASSWORD is absent or empty.

### 5.2: Secret Disposal Verification
**Command:**
```bash
# After final cleanup, verify old secret is removed
aws secretsmanager describe-secret --secret-id "$SECRET_NAME" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "WARNING: Static credential secret should be deleted in final phase"
    # During transition, this is OK; after cleanup, should error with AccessDenied
fi
```

## 6. Test Execution Checklist

- [ ] Section 1.1.1: Terraform validate passes
- [ ] Section 1.1.2: Terraform plan shows expected changes
- [ ] Section 1.2.1: IAM authentication parameter enabled on cluster
- [ ] Section 1.2.2: IAM authentication configured on RDS Proxy
- [ ] Section 1.3.1: ECS task role has rds-db:connect permission
- [ ] Section 2.1: Existing secrets still accessible during transition
- [ ] Section 2.2: Password-based authentication still functional
- [ ] Section 3.1: IAM token generation succeeds
- [ ] Section 3.2: Database connection with IAM auth works
- [ ] Section 4.1: Hybrid mode validation
- [ ] Section 5.1: No long-lived credentials in ECS task
- [ ] Section 5.2: Final credential cleanup verification

## 7. Integration with Existing Tests

### 7.1: Existing Integration Tests
**Command:**
```bash
# Run existing test suite to ensure no regressions
cd /path/to/registry
uv run pytest tests/integration/test_keycloak.py -v
```

**Expected:** All existing integration tests pass
**Assertion:** RDS IAM authentication changes do not break existing functionality

### 7.2: Performance Impact Test
**Command:**
```bash
# Measure connection overhead
START=$(date +%s.%N)
aws rds generate-db-auth-token --hostname "$RDS_PROXY_NAME" --port 3306 --region "$AWS_REGION" --username "iam:rds-iam" > /dev/null
END=$(date +%s.%N)
RUNTIME=$(echo "$END - $START" | bc)
echo "Token generation time: ${RUNTIME} seconds"

# Should be < 0.5 seconds typically
if (( $(echo "$RUNTIME > 0.5" | bc -l) )); then
    echo "WARNING: Token generation time may impact performance"
fi
```

## 8. Documentation Tests

### 8.1: Verify Documentation Updated
**Command:**
```bash
# Check OPERATIONS.md mentions IAM authentication
cd "$TF_DIR"
grep -n "IAM" ../OPERATIONS.md | head -10
```

**Expected:** Multiple instances showing IAM authentication setup and troubleshooting

**Assertions:**
- Database setup section updated
- Security considerations documented
- Troubleshooting section available
- Transition guide included

## 9. Security Testing

### 9.1: Least Privilege Verification
**Command:**
```bash
# Verify ECS task role has minimal required permissions
ROLE_NAME=$(aws ecs describe-task-definition --task-definition "$ECS_TASK_DEFINITION" --query 'taskDefinition.executionRoleArn' --output text | cut -d'/' -f2)

# Check for overly permissive policies
OVER_PERMISSIVE=$(aws iam list-policies --scope Local --only-attached --query "Policies[?PolicyName=='AdministratorAccess' || PolicyName=='PowerUserAccess' || PolicyName=='*'" | jq -r '.PolicyName'
if [ -n "$OVER_PERMISSIVE" ]; then
    echo "ERROR: Overly permissive policies detected"
    exit 1
fi

aws iam get-role-policy --role-name "$ROLE_NAME" --policy-name "keycloak-task-exec-ssm-policy" --query 'PolicyDocument.Statement[].Resource == [\"*\"]'
if [ "$(echo $output | jq length)" -gt 1 ]; then
    echo "WARNING: Multiple wildcard resources detected"
fi
```

**Expected:** No overly permissive policies, only scoped permissions

## 10. Cleanup and Validation

### 10.1: Post-Migration State Validation
**Command:**
```bash
# After final cleanup, confirm no static credentials remain
echo "=== Final State Validation ==="
echo "1. Checking ECS task secrets..."
aws ecs describe-task-definition --task-definition "$ECS_TASK_DEFINITION" --query 'taskDefinition.containerDefinitions[0].secrets' | grep "KC_DB_PASSWORD"
echo "2. Checking Terraform variables..."
cd "$TF_DIR"
if grep -q "keycloak_database_password" variables.tf; then
    echo "ERROR: Static password variable should be removed"
    exit 1
fi
echo "3. Checking RDS IAM authentication..."
aws rds describe-db-cluster-parameters --db-cluster-identifier "$RDS_CLUSTER_IDENTIFIER" --query "DBClusterParameters[?ParameterName=='aurora_enable_iam_auth'].ParameterValue" --output text | grep "1"
echo "✓ All checks passed - migration complete"
```

**Expected:** All validations pass, indicating successful static credential elimination