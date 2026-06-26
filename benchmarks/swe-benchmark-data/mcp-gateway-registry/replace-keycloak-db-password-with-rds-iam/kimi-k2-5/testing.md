# Testing Plan: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-06-25*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This plan covers testing the migration from static password-based authentication to RDS IAM authentication for the Keycloak Aurora MySQL database. Testing must verify IAM token generation, database connectivity, ECS task role permissions, and backwards compatibility.

### Prerequisites
- [ ] AWS CLI installed and configured with appropriate credentials
- [ ] Terraform >= 1.2 installed
- [ ] Docker installed for container image testing
- [ ] Access to AWS account with permissions for RDS, ECS, IAM, Secrets Manager
- [ ] Existing MCP Gateway deployment for testing

### Shared Variables
```bash
export AWS_REGION="us-west-2"
export TF_VAR_aws_region="us-west-2"
export TF_VAR_keycloak_database_iam_auth_enabled="true"
export TF_VAR_keycloak_database_iam_user="keycloak_iam"
export BENCH_REPO="/Users/prsinp/claude-code-multi-model/benchmarks/swe-benchmark-data/mcp-gateway-registry/repo"
```

---

## 1. Functional Tests

### 1.1 Terraform Validation Tests

#### Test 1.1.1: Variable Validation - IAM Auth Disabled (Default)

**Purpose:** Verify existing password-based auth still works when feature flag is off.

**Command:**
```bash
cd $BENCH_REPO/terraform/aws-ecs

# Test with IAM auth disabled (existing behavior)
terraform plan \
  -var="keycloak_database_iam_auth_enabled=false" \
  -var="keycloak_database_password=TestPassword123" \
  -var="keycloak_admin_password=AdminPass456" \
  -var="documentdb_admin_password=DocDBPass789"
```

**Expected Status:** Success (exit code 0)

**Expected Output:** No validation errors.

**Assertions:**
- [x] Plan succeeds without errors
- [x] RDS Proxy resources are created (count=1)
- [x] Password rotation Lambda is created

---

#### Test 1.1.2: Variable Validation - IAM Auth Enabled Without Password

**Purpose:** Verify IAM auth works without password variable (new behavior).

**Command:**
```bash
cd $BENCH_REPO/terraform/aws-ecs

# Test with IAM auth enabled (new behavior)
terraform plan \
  -var="keycloak_database_iam_auth_enabled=true" \
  -var="keycloak_database_password=" \
  -var="keycloak_admin_password=AdminPass456" \
  -var="documentdb_admin_password=DocDBPass789"
```

**Expected Status:** Success (exit code 0)

**Expected Output:** Plan succeeds, shows conditional resources with count=0.

**Assertions:**
- [x] Plan succeeds without errors
- [x] RDS Proxy resources have count=0
- [x] Password rotation Lambda has count=0
- [x] Secret version has count=0
- [x] IAM auth policy is created

**Negative Case:**

**Command:**
```bash
# Test with IAM auth disabled but no password
terraform plan \
  -var="keycloak_database_iam_auth_enabled=false" \
  -var="keycloak_database_password=" \
  -var="keycloak_admin_password=AdminPass456" \
  -var="documentdb_admin_password=DocDBPass789" \
  2>&1
```

**Expected Status:** Failure (exit code 1)

**Expected Output:** `keycloak_database_password is required when keycloak_database_iam_auth_enabled is false`

---

#### Test 1.1.3: Terraform Format and Validation

**Command:**
```bash
cd $BENCH_REPO/terraform/aws-ecs
terraform fmt -check
terraform validate
```

**Expected Status:** Success (exit code 0)

---

### 1.2 Docker Image Tests

#### Test 1.2.1: Build Keycloak Image with Custom Entrypoint

**Purpose:** Verify Docker image builds with AWS CLI and custom entrypoint.

**Command:**
```bash
cd $BENCH_REPO/docker/keycloak

docker build -t keycloak-iam-auth:test .
```

**Expected Status:** Success

**Assertions:**
- [x] Image builds without errors

---

#### Test 1.2.2: Verify AWS CLI Installation

**Purpose:** Verify AWS CLI is available in container.

**Command:**
```bash
docker run --rm keycloak-iam-auth:test aws --version
```

**Expected Status:** Success

**Expected Output:**
```
aws-cli/2.x.x Python/3.x.x Linux/x86_64 source/x86_64.xxxx
```

**Assertions:**
- [x] AWS CLI version is printed

---

#### Test 1.2.3: Verify Entrypoint Script Exists

**Purpose:** Verify custom entrypoint script is in container.

**Command:**
```bash
docker run --rm keycloak-iam-auth:test ls -la /opt/keycloak/bin/keycloak-entrypoint.sh
```

**Expected Status:** Success

**Expected Output:**
```
-rwxr-xr-x 1 keycloak keycloak ... /opt/keycloak/bin/keycloak-entrypoint.sh
```

**Assertions:**
- [x] Entrypoint script exists
- [x] Script is executable

---

#### Test 1.2.4: Entrypoint Script Syntax Check

**Purpose:** Verify entrypoint script has valid bash syntax.

**Command:**
```bash
docker run --rm keycloak-iam-auth:test bash -n /opt/keycloak/bin/keycloak-entrypoint.sh
echo $?
```

**Expected Status:** Success (exit code 0)

---

### 1.3 IAM Policy Tests

#### Test 1.3.1: IAM Policy JSON Validity

**Purpose:** Verify IAM policy templates compile to valid JSON.

**Command:**
```bash
cd $BENCH_REPO/terraform/aws-ecs

# Extract policy from Terraform and validate
terraform console <<< 'jsonencode({
  Version = "2012-10-17"
  Statement = [
    {
      Effect = "Allow"
      Action = "rds-db:connect"
      Resource = "arn:aws:rds-db:us-west-2:123456789:dbuser:cluster-123/keycloak_iam"
    }
  ]
})'
```

**Expected Output:** Valid JSON.

**Negative Case - Invalid ARN:**

Try using cluster_id instead of cluster_resource_id:
```bash
# This should be caught during review, not automated
echo "Resource: arn:aws:rds-db:us-west-2:123456789:dbuser:keycloak/keycloak_iam"
```

**Expected:** This uses cluster name instead of resource ID - would fail at runtime.

---

## 2. Backwards Compatibility Tests

### Test 2.1: Existing Deployment Compatibility

**Purpose:** Verify existing password-based deployments continue to work.

**Command:**
```bash
cd $BENCH_REPO/terraform/aws-ecs

# Plan with existing configuration
terraform plan \
  -var="keycloak_database_iam_auth_enabled=false" \
  -var="keycloak_database_password=TestPassword123" \
  -var="keycloak_database_username=keycloak" \
  -var="keycloak_admin_password=AdminPass456" \
  -var="documentdb_admin_password=DocDBPass789"
```

**Expected Status:** Success

**Expected Changes:** None for existing resources.

**Assertions:**
- [x] Plan shows no changes if no other variables changed
- [x] RDS Proxy resources show no changes
- [x] Secret rotation resources show no changes

---

### Test 2.2: Mixed Mode Protection

**Purpose:** Verify cannot accidentally enable both auth methods.

**Command:**
```bash
# Attempt to use IAM auth but still pass password
terraform plan \
  -var="keycloak_database_iam_auth_enabled=true" \
  -var="keycloak_database_password=TestPassword123" \
  2>&1 | grep -i "warning\|error" || echo "No warnings found"
```

**Expected:** Terraform should warn or ignore password when IAM auth enabled.

---

### Test 2.3: Variable Ordering

**Purpose:** Verify variable order doesn't affect behavior.

**Command:**
```bash
# Test with variables in different order
terraform plan \
  -var="keycloak_database_password=" \
  -var="keycloak_database_iam_auth_enabled=true" \
  -var="keycloak_admin_password=AdminPass456" \
  -var="documentdb_admin_password=DocDBPass789"
```

**Expected Status:** Success

---

### Test 2.4: Default Value Handling

**Purpose:** Verify default values work correctly.

**Command:**
```bash
# Test with only required variables
terraform plan \
  -var="keycloak_database_iam_auth_enabled=false" \
  -var="keycloak_database_password=TestPassword123" \
  -var="keycloak_admin_password=AdminPass456" \
  -var="documentdb_admin_password=DocDBPass789"
```

**Expected Status:** Success

**Assertions:**
- [x] Defaults for new variables (keycloak_database_iam_user) are applied

---

## 3. UX Tests

### Test 3.1: Error Message Clarity

**Purpose:** Verify error messages are helpful when validation fails.

**Command:**
```bash
cd $BENCH_REPO/terraform/aws-ecs

terraform plan \
  -var="keycloak_database_iam_auth_enabled=false" \
  -var="keycloak_database_password=" \
  2>&1 | head -20
```

**Expected Output:**
```
Error: Invalid value for variable

...keycloak_database_password is required when keycloak_database_iam_auth_enabled is false
```

**Assertions:**
- [x] Error message clearly states the requirement
- [x] Error message references both variables

---

### Test 3.2: Terraform Plan Output Readability

**Purpose:** Verify conditional resource changes are clear in plan output.

**Command:**
```bash
terraform plan \
  -var="keycloak_database_iam_auth_enabled=true" \
  -var="keycloak_database_password=" \
  -var="keycloak_admin_password=AdminPass456" \
  -var="documentdb_admin_password=DocDBPass789" \
  2>&1 | grep -E "(will be created|will be destroyed|Plan:)"
```

**Expected Output:** Clear indication of resources to be created/destroyed.

---

## 4. Deployment Surface Tests

### 4.1 Docker Wiring

**Test:** Verify Dockerfile changes are complete.

**Command:**
```bash
grep -n "keycloak-entrypoint.sh\|awscli" $BENCH_REPO/docker/keycloak/Dockerfile
```

**Expected Output:**
```
5:COPY keycloak-entrypoint.sh /opt/keycloak/bin/
```

**Assertions:**
- [x] Dockerfile references entrypoint script
- [x] Dockerfile installs AWS CLI

---

### 4.2 Terraform Wiring

**Test:** Verify all Terraform resources have conditional count.

**Command:**
```bash
cd $BENCH_REPO/terraform/aws-ecs

# Check for resources that should be conditional
grep -n "keycloak_database_iam_auth_enabled" *.tf
```

**Expected Output:** References in:
- variables.tf (variable definition)
- keycloak-database.tf (RDS cluster, proxy conditional)
- keycloak-ecs.tf (task definition, IAM policy)
- secret-rotation.tf (rotation config conditional)

---

### 4.3 Variable Documentation

**Test:** Verify all new variables are documented.

**Command:**
```bash
grep -A 5 "keycloak_database_iam_auth_enabled" $BENCH_REPO/terraform/aws-ecs/variables.tf | head -10
```

**Expected Output:** Description is present.

---

### 4.4 ECS Task Definition Verification

**Test:** Verify ECS task definition references IAM auth vars.

**Expected in keycloak-ecs.tf:**
```hcl
keycloak_container_env = [
  {
    name  = "KC_DB_IAM_AUTH_ENABLED"
    value = "true"
  },
  {
    name  = "KC_DB_IAM_USER"
    value = var.keycloak_database_iam_user
  },
]
```

---

## 5. End-to-End Integration Tests

### Prerequisites
- [ ] AWS account with RDS, ECS, IAM permissions
- [ ] Terraform backend configured
- [ ] VPC and subnets exist

### Test 5.1: RDS IAM Auth Token Generation

**Purpose:** Verify token can be generated with correct IAM permissions.

**Command:**
```bash
# First, ensure task role has proper permissions
# Then test token generation:
aws rds generate-db-auth-token \
  --hostname keycloak.cluster-xxx.us-west-2.rds.amazonaws.com \
  --port 3306 \
  --region us-west-2 \
  --username keycloak_iam
```

**Expected Status:** Success

**Expected Output:**
```
keycloak.cluster-xxx.us-west-2.rds.amazonaws.com:3306/?Action=connect...
```

**Assertions:**
- [x] Token starts with endpoint hostname
- [x] Token contains Action=connect
- [x] Token is valid URL format

---

### Test 5.2: Manual Database Connection with IAM Token

**Purpose:** Verify IAM authentication works directly.

**Prerequisites:**
- IAM database user created in MySQL
- ECS task role has rds-db:connect permission

**Command:**
```bash
# Generate token
TOKEN=$(aws rds generate-db-auth-token \
  --hostname keycloak.cluster-xxx.us-west-2.rds.amazonaws.com \
  --port 3306 \
  --region us-west-2 \
  --username keycloak_iam)

# Connect (requires mysql client)
mysql -h keycloak.cluster-xxx.us-west-2.rds.amazonaws.com \
  -P 3306 \
  -u keycloak_iam \
  -p"$TOKEN" \
  -e "SELECT 1"
```

**Expected Status:** Success

**Expected Output:**
```
+---+
| 1 |
+---+
| 1 |
+---+
```

**Assertions:**
- [x] Connection succeeds
- [x] Query executes without error

---

### Test 5.3: ECS Task IAM Role Permissions

**Purpose:** Verify ECS task role can generate tokens.

**Command:**
```bash
# Get task role ARN
TASK_ROLE_ARN=$(aws ecs describe-services \
  --cluster keycloak \
  --services keycloak \
  --query 'services[0].deployments[0].taskDefinition' \
  --output text)

# Verify role policy contains rds-db:connect
aws iam get-role-policy \
  --role-name $(echo $TASK_ROLE_ARN | cut -d'/' -f2) \
  --policy-name keycloak-task-rds-iam-auth | grep -i rds-db
```

**Expected Output:** Contains `rds-db:connect` permission.

---

### Test 5.4: Keycloak Container Startup

**Purpose:** Verify Keycloak container starts with IAM auth.

**Command:**
```bash
# Monitor task startup
aws logs tail /ecs/keycloak --follow
```

**Expected Log Output:**
```
Generating RDS IAM authentication token...
RDS IAM auth token generated successfully
KC 25.0 ... started in ...ms.
```

**Assertions:**
- [x] Token generation message appears
- [x] Keycloak startup completes
- [x] No connection errors in logs

---

### Test 5.5: Database Connectivity Through Keycloak

**Purpose:** Verify Keycloak can read/write to database.

**Test Steps:**
1. Access Keycloak admin console
2. Create a test realm
3. Verify realm persists after container restart

**Expected:** Realm creation succeeds and persists.

---

### Test 5.6: Token Refresh Cycle

**Purpose:** Verify Keycloak handles connection drops gracefully.

**Test Steps:**
1. Start Keycloak with IAM auth
2. Wait > 15 minutes (token expiry)
3. Perform database operation
4. Verify new connections work

**Expected:** New connections using fresh tokens (generated on restart) work.

**Note:** Keycloak maintains persistent connections. Token expiration only affects new connections.

---

## 6. Performance Tests

### Test 6.1: Container Startup Time

**Purpose:** Measure cold start impact of token generation.

**Test:**
```bash
# Time container startup
for i in 1 2 3; do
  time docker run --rm \
    -e KC_DB_IAM_AUTH_ENABLED=true \
    -e AWS_REGION=us-west-2 \
    -e KC_DB_URL="jdbc:mysql://test:3306/keycloak" \
    keycloak-iam-auth:test \
    bash -c 'aws rds generate-db-auth-token --hostname test --port 3306 --region us-west-2 --username keycloak_iam 2>/dev/null || echo "TOKEN"'
done
```

**Expected:** Token generation adds < 3 seconds to startup.

---

### Test 6.2: Concurrent Connection Handling

**Purpose:** Verify connection limits with IAM auth.

**Test:** Configure Keycloak with max 20 connections per task, run 4 tasks, verify total connections < 100.

**Command:**
```bash
# Monitor RDS connections
aws rds describe-db-clusters \
  --db-cluster-identifier keycloak \
  --query 'DBClusters[0].DBClusterMembers'

# Or query MySQL directly for connection count
```

**Expected:** Connections stay within Aurora Serverless v2 limits (default 2000).

---

## 7. Security Tests

### Test 7.1: Token Expiration

**Purpose:** Verify tokens expire after 15 minutes.

**Test:**
```bash
# Generate token
TOKEN=$(aws rds generate-db-auth-token ...)

# Try to use immediately - should work

# Wait 15+ minutes
sleep 901

# Try to use again - should fail
echo "Testing expired token..."
```

**Expected:** Token authentication fails after 15 minutes.

---

### Test 7.2: Unauthorized Access Prevention

**Purpose:** Verify only authorized roles can generate tokens.

**Test:**
```bash
# Attempt to generate token with unauthorized role
aws sts assume-role --role-arn arn:aws:iam::...:role/UnauthorizedRole ...
aws rds generate-db-auth-token ...
```

**Expected:** AccessDenied error.

---

### Test 7.3: CloudTrail Logging

**Purpose:** Verify rds-db:connect calls are logged.

**Command:**
```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=Connect \
  --max-results 10
```

**Expected:** Events for RDS IAM authentication calls appear in CloudTrail.

---

## 8. Regression Tests

### Test 8.1: Password Auth Still Works

**Purpose:** Verify existing password auth still works when flag is false.

**Test:**
```bash
cd $BENCH_REPO/terraform/aws-ecs

terraform apply \
  -var="keycloak_database_iam_auth_enabled=false" \
  -var="keycloak_database_password=TestPassword123" \
  -var="keycloak_admin_password=AdminPass456" \
  -var="documentdb_admin_password=DocDBPass789"

# Verify Keycloak starts normally
```

**Expected:** Deployment works exactly as before.

---

### Test 8.2: Module Dependencies

**Purpose:** Verify Terraform module dependencies still resolve.

**Command:**
```bash
terraform graph | head -50
```

**Expected:** Graph shows proper dependency order. No cycles.

---

## 9. Cleanup Tests

### Test 9.1: Terraform Destroy

**Purpose:** Verify resources can be cleanly destroyed.

**Command:**
```bash
terraform destroy \
  -var="keycloak_database_iam_auth_enabled=true" \
  -var="keycloak_admin_password=AdminPass456" \
  -var="documentdb_admin_password=DocDBPass789" \
  -auto-approve
```

**Expected Status:** Success

**Assertions:**
- [x] All resources destroyed
- [x] No orphaned resources
- [x] No state errors

---

## 10. Test Execution Checklist

### Pre-Deployment
- [ ] Section 1 (Terraform Validation) passes
- [ ] Section 1.2 (Docker Image) passes
- [ ] Section 2 (Backwards Compatibility) verified
- [ ] Section 3 (UX) verified
- [ ] Section 4 (Deployment Surface) verified

### Integration (Staging)
- [ ] Section 5 (E2E Integration) passes
- [ ] Section 6 (Performance) passes
- [ ] Section 7 (Security) passes

### Production
- [ ] Section 8 (Regression) verified
- [ ] Section 9 (Cleanup) verified
- [ ] Monitoring dashboards verified
- [ ] Runbooks updated
- [ ] On-call team notified

### Documentation
- [ ] `terraform/aws-ecs/README.md` updated
- [ ] `CHANGELOG.md` updated
- [ ] `docs/database-auth.md` created
- [ ] ADR document created

---

## Appendix: Quick Reference Commands

### Generate RDS Auth Token
```bash
aws rds generate-db-auth-token \
  --hostname HOSTNAME \
  --port 3306 \
  --region us-west-2 \
  --username keycloak_iam
```

### Connect with Token
```bash
mysql -h HOSTNAME -P 3306 -u keycloak_iam -p"TOKEN" -e "SELECT 1"
```

### Check ECS Task Role
```bash
aws ecs describe-tasks --cluster keycloak --tasks TASK_ID \
  --query 'tasks[0].attachments[0].details[?name==`iamRole`].value'
```

### View Keycloak Logs
```bash
aws logs tail /ecs/keycloak --follow
```

### Verify IAM Policy
```bash
aws iam get-role-policy --role-name ROLE_NAME --policy-name POLICY_NAME
```
