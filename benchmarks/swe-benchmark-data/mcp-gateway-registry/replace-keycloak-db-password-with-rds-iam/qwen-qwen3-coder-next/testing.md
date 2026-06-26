# Testing Plan: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-06-25*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview
### Scope of Testing
This plan covers testing for the migration from static database password authentication to IAM database authentication for Keycloak connecting to Aurora MySQL. The tests verify:
- IAM authentication tokens are generated correctly
- Keycloak connects using IAM tokens instead of static passwords
- Token refresh (if implemented) works correctly
- Failover behavior when IAM auth fails

### Prerequisites
- [ ] AWS CLI configured with appropriate credentials
- [ ] Terraform access to the target AWS account
- [ ] Keycloak ECS task IAM role has `rds-db:connect` permission
- [ ] RDS cluster has IAM database authentication enabled
- [ ] Access to CloudWatch Logs for Keycloak

### Shared Variables
```bash
# AWS Configuration
export AWS_REGION="${AWS_REGION:-us-west-2}"
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# RDS Configuration
export RDS_PROXY_ENDPOINT="keycloak-proxy.${AWS_REGION}.rds.amazonaws.com"
export RDS_DB_USER="keycloak"

# IAM Configuration
export IAM_TASK_ROLE="keycloak-task-role-${AWS_REGION}"
export TASK_EXECUTION_ROLE="keycloak-task-exec-role-${AWS_REGION}"

# Keycloak Configuration
export KEYCLOAK_TASK_DEFINITION="keycloak"
export KEYCLOAK_CLUSTER="keycloak"
```

## 1. Functional Tests
### 1.1 IAM Token Generation

#### Test: Generate IAM token using AWS CLI
```bash
# Verify AWS CLI has credentials
aws sts get-caller-identity

# Generate IAM auth token
TOKEN=$(aws rds generate-db-auth-token \
    --hostname "${RDS_PROXY_ENDPOINT}" \
    --port 3306 \
    --username "${RDS_DB_USER}" \
    --region "${AWS_REGION}" \
    2>&1)

echo "Generated token: ${TOKEN:0:50}..."
echo "Token length: ${#TOKEN} characters"

# Verify token format (should be a JWT-like string)
echo "$TOKEN" | jq -r . 2>/dev/null || echo "Token is JWT format"
```

**Expected Result:** Token is generated successfully and is a valid JWT-like string (base64-encoded with 3 parts).

#### Test: Verify IAM token has correct expiry
```bash
# Decode the token (first part is header)
TOKEN_HEADER=$(echo "$TOKEN" | cut -d'.' -f1 | base64 -d 2>/dev/null)
echo "Token header: $TOKEN_HEADER"

# Verify token expiry (claim 'exp' should be ~15 minutes from now)
# Use Python to decode if jq is not available
python3 << EOF
import base64
import json
import time

token = "${TOKEN}"
parts = token.split('.')
if len(parts) == 3:
    header = json.loads(base64.urlsafe_b64decode(parts[0] + '=='))
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=='))
    print(f"Hash: {header.get('alg')}")
    print(f"Expiry (exp): {payload.get('exp')}")
    print(f"Issued at (iat): {payload.get('iat')}")
    print(f"Current time: {int(time.time())}")
else:
    print("Token format is not JWT")
EOF
```

**Expected Result:** Token has `exp` claim set to approximately 15 minutes from the current time.

### 1.2 Database Connection Tests

#### Test: Connect to RDS using IAM token (Python)
```bash
# Test IAM connection using Python and boto3
python3 << EOF
import boto3
import pymysql
import sys

# Generate IAM token
session = boto3.Session(region_name="${AWS_REGION}")
rds_client = session.client('rds')
token = rds_client.generate_db_auth_token(
    DBHostname="${RDS_PROXY_ENDPOINT}",
    Port=3306,
    DBUsername="${RDS_DB_USER}"
)

# Try to connect
try:
    conn = pymysql.connect(
        host="${RDS_PROXY_ENDPOINT}",
        user="${RDS_DB_USER}",
        password=token,
        db="keycloak",
        ssl={'ca': '/etc/ssl/certs/ca-certificates.crt'}
    )
    cursor = conn.cursor()
    cursor.execute("SELECT 1")
    result = cursor.fetchone()
    print(f"Connection successful! Result: {result[0]}")
    conn.close()
except Exception as e:
    print(f"Connection failed: {e}")
    sys.exit(1)
EOF
```

**Expected Result:** Connection succeeds and query returns `1`.

#### Test: Verify IAM auth is required on RDS cluster
```bash
# Check if IAM auth is enabled on the cluster
aws rds describe-db-clusters \
    --db-cluster-identifier keycloak \
    --query 'DBClusters[0].IAMDatabaseAuthenticationEnabled' \
    --output text
```

**Expected Result:** Output is `true`.

### 1.3 Terraform Validation

#### Test: Terraform plan shows IAM auth enabled
```bash
cd terraform/aws-ecs

# Verify IAM auth is enabled in Terraform
grep -r "iam_database_authentication_enabled" keycloak-database.tf | grep "true"

# Verify no password references in RDS cluster
grep -A5 "resource \"aws_rds_cluster\" \"keycloak\"" keycloak-database.tf | grep -i password || echo "No password in cluster definition (expected with IAM)"

# Run terraform plan to verify configuration
terraform plan -var="keycloak_use_iam_auth=true" 2>&1 | tee /tmp/tf-plan.txt

# Check for expected changes
grep " IAM " /tmp/tf-plan.txt || echo "No IAM changes in plan"
```

**Expected Result:** Plan shows `iam_database_authentication_enabled = true`.

#### Test: IAM policy has correct permissions
```bash
# Check IAM policy includes rds-db:connect
grep -r "rds-db:connect" keycloak-ecs.tf

# Verify the resource ARN pattern
grep -A3 "rds-db:connect" keycloak-ecs.tf
```

**Expected Result:** Policy includes `rds-db:connect` with appropriate resource ARN.

---

## 2. Backwards Compatibility Tests

**Note:** If the implementation supports both IAM and password modes (recommended for migration), the following tests verify both modes work.

### 2.1 IAM Auth Mode Tests
```bash
# Test with IAM auth enabled (variable: keycloak_use_iam_auth=true)
export KC_DB_USE_IAM=true

# Verify IAM token generation script works
bash terraform/aws-ecs/scripts/generate-iam-token.sh

# Verify token is valid for database connection
python3 << EOF
import boto3
import os

os.environ['DB_REGION'] = '${AWS_REGION}'
os.environ['DB_HOST'] = '${RDS_PROXY_ENDPOINT}'
os.environ['DB_USER'] = '${RDS_DB_USER}'

# Simulate IAM token generation
session = boto3.Session(region_name=os.environ['DB_REGION'])
rds = session.client('rds')
token = rds.generate_db_auth_token(
    DBHostname=os.environ['DB_HOST'],
    Port=3306,
    DBUsername=os.environ['DB_USER']
)
print(f"Generated IAM token: {token[:50]}...")
EOF
```

### 2.2 Password Auth Mode Tests (if dual-mode supported)
```bash
# Test with password auth enabled (variable: keycloak_use_iam_auth=false)
# Verify password is read from Secrets Manager
aws secretsmanager get-secret-value \
    --secret-id keycloak/database \
    --query SecretString \
    --output text | jq .password

# Verify password format is correct
aws secretsmanager get-secret-value \
    --secret-id keycloak/database \
    --query SecretString \
    --output text | jq -e '.username and .password' > /dev/null && echo "Password secret has both username and password"
```

### 2.3 Rollback Verification
```bash
# Simulate rollback by disabling IAM auth
# 1. Update Secrets Manager to include password
aws secretsmanager update-secret \
    --secret-id keycloak/database \
    --secret-string '{"username":"keycloak","password":"test-password","iam_auth_mode":false}'

# 2. Verify password auth works
python3 << EOF
import pymysql

try:
    conn = pymysql.connect(
        host="${RDS_PROXY_ENDPOINT}",
        user="keycloak",
        password="test-password",
        db="keycloak"
    )
    print("Password auth works")
    conn.close()
except Exception as e:
    print(f"Password auth test failed (expected if IAM required): {e}")
EOF
```

---

## 3. UX Tests

### 3.1 Keycloak Startup Logs
```bash
# Check Keycloak startup logs for IAM-related messages
aws logs filter-log-events \
    --log-group-name "/ecs/keycloak" \
    --filter-pattern "IAM" \
    --max-events 10 \
    --query 'events[].message' \
    --output text
```

**Expected Result:** Logs show:
- `IAM token generated successfully`
- `Attempting database connection with IAM auth`
- `Database connection established`

### 3.2 Health Check Response
```bash
# Keycloak health endpoint should report auth status
curl -s http://localhost:8080/health | jq .

# Expected response includes auth method info
{
  "status": "UP",
  "checks": [
    {
      "name": "database",
      "status": "UP",
      "data": {
        "auth_method": "iam",
        "connected": true
      }
    }
  ]
}
```

**Not Applicable** - The health endpoint may not report auth method details. This is a nice-to-have for better observability.

---

## 4. Deployment Surface Tests

### 4.1 Terraform Deployment Tests

#### Test: Terraform plan completes successfully
```bash
cd terraform/aws-ecs

# Initialize Terraform
terraform init

# Run plan with IAM auth enabled
terraform plan -var="keycloak_use_iam_auth=true" \
    -var="keycloak_database_password=" \
    -var="aws_region=${AWS_REGION}" \
    -out=tfplan

echo "Terraform plan completed"
```

**Expected Result:** Plan shows:
- `aws_rds_cluster.keycloak.iam_database_authentication_enabled: false -> true`
- New IAM policy resource or update to existing policy

#### Test: Apply changes to staging environment
```bash
# Deploy to staging
terraform apply -var="keycloak_use_iam_auth=true" \
    -var="keycloak_database_password=" \
    -var="aws_region=${AWS_REGION}"

# Verify IAM auth is enabled
aws rds describe-db-clusters \
    --db-cluster-identifier keycloak \
    --query 'DBClusters[0].IAMDatabaseAuthenticationEnabled' \
    --output text
```

### 4.2 ECS Task Deployment Tests

#### Test: ECS task starts with IAM auth
```bash
# Check ECS task definition for IAM policy
aws ecs describe-task-definition \
    --task-definition keycloak \
    --query 'taskDefinition.taskRoleArn' \
    --output text

# Get the task role and check policies
TASK_ROLE=$(aws ecs describe-task-definition \
    --task-definition keycloak \
    --query 'taskDefinition.taskRoleArn' \
    --output text | cut -d'/' -f2)

aws iam list-attached-role-policies \
    --role-name "$TASK_ROLE" \
    --query 'AttachedPolicies[?PolicyName==`keycloak-task-exec-ssm-policy`].PolicyArn' \
    --output text

# Check policy details
POLICY_ARN=$(aws iam list-attached-role-policies \
    --role-name "$TASK_ROLE" \
    --query 'AttachedPolicies[?PolicyName==`keycloak-task-exec-ssm-policy`].PolicyArn' \
    --output text)

aws iam get-policy-version \
    --policy-arn "$POLICY_ARN" \
    --version-id $(aws iam list-policy-versions --policy-arn "$POLICY_ARN" --query 'Versions[?IsDefaultVersion==true].VersionId' --output text) \
    --query 'PolicyVersion.Document.Statement[?Action==`rds-db:connect`]'
```

**Expected Result:** Policy includes `rds-db:connect` action.

#### Test: ECS task can generate IAM token
```bash
# Get a running task ID
TASK_ID=$(aws ecs list-tasks \
    --cluster keycloak \
    --service keycloak \
    --desired-status RUNNING \
    --query 'taskArns[0]' \
    --output text | sed 's|.*/||')

# Execute a command to generate IAM token (if ECS Exec is enabled)
aws ecs execute-command \
    --cluster keycloak \
    --task "$TASK_ID" \
    --container keycloak \
    --command "aws rds generate-db-auth-token --hostname ${RDS_PROXY_ENDPOINT} --port 3306 --username ${RDS_DB_USER} --region ${AWS_REGION}" \
    --interactive
```

### 4.3 Docker Compose Tests

#### Test: Docker Compose IAM auth mode
```bash
cd /Users/prsinp/claude-code-multi-model/benchmarks/swe-benchmark-data/mcp-gateway-registry/repo

# Verify docker-compose.yml has IAM auth option
grep -A10 "keycloak:" docker-compose.yml | grep -E "KC_DB_USE_IAM|KC_DB_PASSWORD"

# Test IAM mode configuration
KC_DB_USE_IAM=true \
KC_DB_REGION=${AWS_REGION} \
KEYCLOAK_DB_PASSWORD="" \
docker-compose config | grep -E "KC_DB"
```

**Expected Result:** `KC_DB_USE_IAM` and `KC_DB_REGION` environment variables are present.

---

## 5. End-to-End API Tests

### 5.1 Keycloak Admin API with IAM Auth

#### Test: Verify Keycloak admin API is accessible
```bash
# Get Keycloak admin token
ADMIN_TOKEN=$(curl -s \
    -X POST "http://localhost:8080/realms/master/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=admin" \
    -d "password=admin-password" \
    -d "grant_type=password" \
    -d "client_id=admin-cli" | jq -r .access_token)

# List users
curl -s \
    -X GET "http://localhost:8080/admin/realms/master/users" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    | jq .
```

**Not Applicable** - This test requires the initial admin password to be set. With IAM auth, the first-time setup flow needs adjustment.

### 5.2 Keycloak Management API

#### Test: Keycloak health endpoint
```bash
# Keycloak health check
curl -s http://localhost:8080/health | jq .
curl -s http://localhost:8080/ready | jq .
```

**Expected Result:** `status` is `UP` and database connection is successful.

---

## 6. Test Execution Checklist

- [ ] Section 1 (Functional) passes
- [ ] Section 2 (Backwards Compat) verified or marked Not Applicable
- [ ] Section 3 (UX) verified or marked Not Applicable
- [ ] Section 4 (Deployment) verified or marked Not Applicable
- [ ] Section 5 (E2E) verified or marked Not Applicable
- [ ] Unit tests added under `tests/unit/utils/test_iam_auth.py`
- [ ] Integration tests added under `tests/integration/`
- [ ] `uv run pytest tests/` passes with no regressions

### Automated Test Script (for CI/CD)
```bash
#!/bin/bash
# tests/iam-auth-e2e.sh

set -e

echo "=== IAM Auth E2E Tests ==="

# Test 1: IAM token generation
echo "Test 1: IAM token generation"
TOKEN=$(aws rds generate-db-auth-token \
    --hostname "${RDS_PROXY_ENDPOINT}" \
    --port 3306 \
    --username "${RDS_DB_USER}" \
    --region "${AWS_REGION}")
echo "Token generated: ${#TOKEN} characters"

# Test 2: IAM auth enabled on RDS cluster
echo "Test 2: IAM auth enabled on RDS cluster"
IAM_ENABLED=$(aws rds describe-db-clusters \
    --db-cluster-identifier keycloak \
    --query 'DBClusters[0].IAMDatabaseAuthenticationEnabled' \
    --output text)
[ "$IAM_ENABLED" = "true" ] && echo "IAM auth is enabled" || exit 1

# Test 3: Database connection with IAM token
echo "Test 3: Database connection"
python3 - <<'EOF'
import boto3
import pymysql
import sys

rds = boto3.client('rds', region_name="${AWS_REGION}")
token = rds.generate_db_auth_token(
    DBHostname="${RDS_PROXY_ENDPOINT}",
    Port=3306,
    DBUsername="${RDS_DB_USER}"
)

try:
    conn = pymysql.connect(
        host="${RDS_PROXY_ENDPOINT}",
        user="${RDS_DB_USER}",
        password=token,
        db="keycloak",
        connect_timeout=10
    )
    conn.close()
    print("Database connection successful")
except Exception as e:
    print(f"Database connection failed: {e}")
    sys.exit(1)
EOF

echo "=== All IAM Auth Tests Passed ==="
```

---

## 7. Failure Scenario Tests

### 7.1 IAM Token Generation Failure
```bash
# Test: What happens when IAM role doesn't have rds-db:connect permission?
# 1. Create a test role without IAM auth permission
aws iam create-role \
    --role-name test-keycloak-fail \
    --assume-role-policy-document=file://test-role-trust.json

# 2. Try to generate token (should fail)
aws rds generate-db-auth-token \
    --hostname "${RDS_PROXY_ENDPOINT}" \
    --port 3306 \
    --username "${RDS_DB_USER}" \
    --region "${AWS_REGION}"
# Expected: AccessDenied error

# 3. Clean up
aws iam delete-role --role-name test-keycloak-fail
```

### 7.2 Token Expired Scenario
```bash
# Test: Keycloak handles expired token gracefully
# This requires implementing token refresh or reconnection logic
# Verify Keycloak logs show token refresh or connection retry
```

### 7.3 RDS Unavailable Scenario
```bash
# Test: Keycloak fails gracefully when RDS is unreachable
# Simulate by blocking network access or stopping RDS proxy
# Expected: Keycloak logs clear error message and doesn't crash
```
