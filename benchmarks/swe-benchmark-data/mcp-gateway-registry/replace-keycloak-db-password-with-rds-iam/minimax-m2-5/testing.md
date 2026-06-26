# Testing Plan: Replace Keycloak DB Password with RDS IAM Authentication

*Created: 2026-06-25*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing

This testing plan validates the migration from static database password authentication to AWS RDS IAM authentication for the Keycloak ECS service. The tests verify that:
1. Keycloak can connect to the database using IAM authentication
2. Existing functionality remains intact
3. Zero-downtime cutover is achieved
4. Deployment surface changes are validated

### Prerequisites

- [ ] Terraform CLI installed (version 1.5+)
- [ ] AWS CLI configured with appropriate permissions
- [ ] Access to the target AWS account
- [ ] Existing Keycloak deployment to migrate
- [ ] Staging environment for pre-production testing

### Shared Variables

```bash
export AWS_REGION="us-east-1"
export CLUSTER_IDENTIFIER="keycloak"
export PROXY_NAME="keycloak-proxy"
export DB_USERNAME="keycloak"
export TF_STATE_BUCKET="your-terraform-state-bucket"
export TASK_DEFINITION_FAMILY="keycloak"
```

---

## 1. Functional Tests

### 1.1 Terraform Validation Tests

**Purpose:** Validate Terraform configuration changes compile correctly.

```bash
# Navigate to Terraform directory
cd terraform/aws-ecs

# Initialize Terraform (if needed)
terraform init -backend-config="bucket=${TF_STATE_BUCKET}"

# Validate Terraform syntax
terraform validate

# Plan the change (should show IAM auth enabled)
terraform plan -var="keycloak_use_iam_auth=true" \
  -var="keycloak_database_username=${DB_USERNAME}" \
  -out=iam-auth.tfplan

# Verify the plan shows:
# - aws_rds_cluster.keycloak: iam_authentication_enabled = true
# - aws_iam_role_policy.keycloak_task_exec_rds_iam_policy: rds-db:connect added
# - aws_secretsmanager_secret_version.keycloak_db_secret: password removed
```

**Expected Result:** Terraform validates successfully, plan shows IAM auth enabled.

### 1.2 IAM Policy Verification

**Purpose:** Verify the ECS task execution role has the required permissions.

```bash
# Get the task execution role name
ROLE_NAME=$(aws iam get-role --role-name "keycloak-task-exec-role-${AWS_REGION}" \
  --query 'Role.RoleName' --output text)

# List attached policies
aws iam list-attached-role-policies --role-name "${ROLE_NAME}"

# Get the RDS IAM policy
aws iam get-role-policy --role-name "${ROLE_NAME}" \
  --policy-name "keycloak-task-rds-iam-policy"

# Verify the policy contains rds-db:connect
aws iam get-role-policy --role-name "${ROLE_NAME}" \
  --policy-name "keycloak-task-rds-iam-policy" \
  --query 'PolicyDocument.Statement[0].Action'
# Should return: ["rds-db:connect"]
```

**Expected Result:** Policy includes `rds-db:connect` action scoped to the keycloak database user.

### 1.3 Database User Verification

**Purpose:** Verify the database user was created with IAM authentication support.

```bash
# Connect to the database via RDS Proxy or direct connection
# Using AWS CLI (requires awscli.rds option)

# Execute statement to verify user authentication method
aws rds execute-db-statement \
  --db-cluster-identifier "${CLUSTER_IDENTIFIER}" \
  --region "${AWS_REGION}" \
  --sql "SELECT user, host, plugin FROM mysql.user WHERE user='${DB_USERNAME}';"

# Alternative: Show create user to see full definition
aws rds execute-db-statement \
  --db-cluster-identifier "${CLUSTER_IDENTIFIER}" \
  --region "${AWS_REGION}" \
  --sql "SHOW CREATE USER '${DB_USERNAME}'@'%';"
```

**Expected Result:** User is configured with `AWSAUTH` or IAM authentication plugin.

### 1.4 RDS Proxy Authentication Verification

**Purpose:** Verify the RDS Proxy is configured to use IAM authentication.

```bash
# Get RDS Proxy configuration
aws rds describe-db-proxies --db-proxy-name "${PROXY_NAME}" \
  --query 'DBProxies[0].Auth'

# Should show IAM auth enabled:
# {
#   "AuthScheme": "iam",
#   "IAMAuth": "required" (or "enabled")
# }
```

**Expected Result:** RDS Proxy auth includes IAM authentication.

### 1.5 ECS Task Definition Verification

**Purpose:** Verify the ECS task definition no longer includes KC_DB_PASSWORD secret.

```bash
# Get latest task definition
TASK_DEF_ARN=$(aws ecs describe-task-definition \
  --task-definition "${TASK_DEFINITION_FAMILY}" \
  --query 'taskDefinition.taskDefinitionArn' --output text)

# Get container definitions
aws ecs describe-task-definition \
  --task-definition "${TASK_DEFINITION_FAMILY}" \
  --query 'taskDefinition.containerDefinitions[0].secrets'

# Verify KC_DB_PASSWORD is NOT present
# Only KEYCLOAK_ADMIN, KEYCLOAK_ADMIN_PASSWORD, KC_DB_URL, KC_DB_USERNAME should exist
```

**Expected Result:** KC_DB_PASSWORD is not in the secrets list.

### 1.6 Entrypoint Script Verification

**Purpose:** Verify the custom entrypoint script is present in the container image.

```bash
# Pull the container image (or use existing)
docker pull ${KEYCLOAK_IMAGE_URI}

# Extract and verify entrypoint
docker inspect ${KEYCLOAK_IMAGE_URI} --format='{{json .Config.Entrypoint}}'

# Should show custom entrypoint: ["/usr/local/bin/keycloak-entrypoint.sh"]

# Verify script content exists
docker run --rm ${KEYCLOAK_IMAGE_URI} cat /usr/local/bin/keycloak-entrypoint.sh
```

**Expected Result:** Custom entrypoint script exists and contains token generation logic.

### 1.7 Token Generation Test

**Purpose:** Verify the entrypoint script can generate a valid IAM token.

```bash
# Run entrypoint with dry-run or test mode (if implemented)
# Or manually test token generation:

# Set up test environment
export KC_DB_URL="jdbc:mysql://keycloak.cluster-abc123.us-east-1.rds.amazonaws.com:3306/keycloak"
export KC_DB_USERNAME="keycloak"
export AWS_REGION="us-east-1"

# Extract hostname
DB_HOST=$(echo "${KC_DB_URL}" | sed -n 's|.*://\([^:]*\):.*|\1|p')
echo "DB Host: ${DB_HOST}"

# Generate token
TOKEN=$(aws rds generate-db-auth-token \
  --hostname "${DB_HOST}" \
  --port 3306 \
  --username "${DB_USERNAME}" \
  --region "${AWS_REGION}")

# Verify token is generated (JWT format)
echo "${TOKEN}" | head -c 50
# Should show: keycloak.cluster-xxx:3306/?Action=connect&DBUser=...
```

**Expected Result:** Valid IAM token is generated without errors.

### 1.8 Keycloak Startup Test

**Purpose:** Verify Keycloak starts successfully with IAM authentication.

```bash
# Deploy to staging and check logs
aws ecs update-service --cluster keycloak \
  --service keycloak --force-new-deployment

# Wait for deployment
aws ecs wait services-stable --cluster keycloak \
  --services keycloak

# Check logs for successful startup
LOG_STREAM=$(aws logs get-log-events \
  --log-group-name /ecs/keycloak \
  --query 'logEvents[-1].timestamp' --output text)

# Stream recent logs
aws logs get-log-events \
  --log-group-name /ecs/keycloak \
  --log-stream-prefix ecs \
  --query 'logEvents[*].message' | grep -i "started\|ready\|running"

# Look for IAM-related log entries
aws logs get-log-events \
  --log-group-name /ecs/keycloak \
  --log-stream-prefix ecs \
  --query 'logEvents[*].message' | grep -i "IAM\|token\|auth"
```

**Expected Result:** Keycloak starts without errors, logs show successful DB connection.

---

## 2. Backwards Compatibility Tests

**Purpose:** Verify existing functionality continues to work after migration.

### 2.1 Keycloak Admin Console

```bash
# Navigate to Keycloak admin console
open "https://${KEYCLOAK_HOST}/admin/"

# Verify login works
# Username: admin (from SSM)
# Password: (from SSM)
# Login should succeed
```

**Expected Result:** Admin can log in successfully.

### 2.2 Realm Configuration

```bash
# Access realm settings
open "https://${KEYCLOAK_HOST}/admin/master/console/#/master/settings"

# Verify realm loads correctly
# All settings should be visible and editable
```

**Expected Result:** Realm settings load without errors.

### 2.3 User Creation

```bash
# Create a test user via API
curl -X POST "https://${KEYCLOAK_HOST}/admin/realms/master/users" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "test-user-iam",
    "enabled": true,
    "email": "test@example.com"
  }'
```

**Expected Result:** User created successfully in database.

### 2.4 Client Creation

```bash
# Create an OIDC client
curl -X POST "https://${KEYCLOAK_HOST}/admin/realms/master/clients" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "clientId": "test-client-iam",
    "enabled": true,
    "protocol": "openid-connect",
    "publicClient": false,
    "serviceAccountsEnabled": true,
    "directAccessGrantsEnabled": true
  }'
```

**Expected Result:** Client created successfully.

### 2.5 Token Exchange (if enabled)

```bash
# Test token exchange flow (KC_FEATURES=token-exchange)
# This verifies complex Keycloak functionality works with IAM auth
```

**Expected Result:** Token exchange works correctly.

---

## 3. UX Tests

**Purpose:** Verify operational experience is good.

### 3.1 Container Logs Clarity

```bash
# Verify entrypoint logs are clear and actionable
aws logs get-log-events \
  --log-group-name /ecs/keycloak \
  --log-stream-prefix ecs \
  --query 'logEvents[*].message' | grep -E "INFO.*IAM|ERROR"

# Should see:
# [INFO] Generating RDS IAM auth token...
# [INFO] IAM token generated, starting Keycloak...
```

**Expected Result:** Logs clearly show IAM token generation steps.

### 3.2 Error Messages

```bash
# Test missing IAM permission error handling
# Temporarily remove rds-db:connect policy
# Verify error message is clear in logs

# After test, restore policy
```

**Expected Result:** Error messages are actionable and help with debugging.

---

## 4. Deployment Surface Tests

### 4.1 Terraform Variable Changes

```bash
# Verify keycloak_database_password variable is removed
grep -n "keycloak_database_password" terraform/aws-ecs/variables.tf
# Should return: (not found) or error

# Verify new variable exists
grep -n "keycloak_use_iam_auth" terraform/aws-ecs/variables.tf
# Should return: variable definition
```

**Expected Result:** Old variable removed, new variable present.

### 4.2 Secrets Manager Secret Update

```bash
# Get the secret value
aws secretsmanager get-secret-value \
  --secret-id keycloak/database \
  --query 'SecretString'

# Should show only username:
# {"username": "keycloak"}
# Password field should be gone
```

**Expected Result:** Secret contains only username, no password.

### 4.3 ECS Task Definition Changes

```bash
# Verify the task definition family exists
aws ecs describe-task-definition \
  --task-definition "${TASK_DEFINITION_FAMILY}" \
  --query 'taskDefinition.family'

# Verify execution role ARN is present
aws ecs describe-task-definition \
  --task-definition "${TASK_DEFINITION_FAMILY}" \
  --query 'taskDefinition.executionRoleArn'
```

**Expected Result:** Task definition is valid and complete.

---

## 5. End-to-End API Tests

### 5.1 Full Keycloak Login Flow

```bash
# Get initial token
TOKEN_RESPONSE=$(curl -X POST "https://${KEYCLOAK_HOST}/realms/master/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin" \
  -d "password=${ADMIN_PASSWORD}" \
  -d "grant_type=password" \
  -d "client_id=admin-cli")

ACCESS_TOKEN=$(echo ${TOKEN_RESPONSE} | jq -r '.access_token')

# Verify token is valid
curl -X GET "https://${KEYCLOAK_HOST}/realms/master" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

**Expected Result:** Full login flow works correctly with IAM-authenticated database.

### 5.2 Database Connection Verification

```bash
# Check Keycloak database connections
# Access the Keycloak management endpoint
curl -k "https://localhost:9000/health/ready" 2>/dev/null || \
  aws ecs execute-command --cluster keycloak \
    --task "$(aws ecs list-tasks --cluster keycloak --query 'taskArns[0]' --output text | cut -d/ -f3)" \
    --container keycloak \
    --interactive --command "/opt/keycloak/bin/kc.sh show-config" | grep -i db
```

**Expected Result:** Keycloak shows database connection using MySQL vendor.

---

## 6. Rollback Testing

**Purpose:** Verify rollback procedure works if needed.

### 6.1 Rollback to Password Auth

```bash
# If migration fails, rollback procedure:
# 1. Set keycloak_use_iam_auth = false
# 2. terraform apply
# 3. Redeploy old task definition

# Test rollback
cd terraform/aws-ecs
terraform plan -var="keycloak_use_iam_auth=false" -out=rollback.tfplan
terraform apply rollback.tfplan

# Force new deployment
aws ecs update-service --cluster keycloak \
  --service keycloak --force-new-deployment

# Verify old password-based auth works
```

**Expected Result:** Rollback succeeds, Keycloak uses password auth again.

---

## 7. Test Execution Checklist

- [ ] Section 1.1 (Terraform Validation) passes
- [ ] Section 1.2 (IAM Policy) verified
- [ ] Section 1.3 (DB User) verified
- [ ] Section 1.4 (RDS Proxy) verified
- [ ] Section 1.5 (Task Def) verified
- [ ] Section 1.6 (Entrypoint) verified
- [ ] Section 1.7 (Token Gen) passes
- [ ] Section 1.8 (Startup) passes
- [ ] Section 2.1 (Admin Console) passes
- [ ] Section 2.2 (Realm Config) passes
- [ ] Section 2.3 (User Creation) passes
- [ ] Section 2.4 (Client Creation) passes
- [ ] Section 2.5 (Token Exchange) passes or N/A
- [ ] Section 3.1 (Logs) passes
- [ ] Section 3.2 (Errors) passes
- [ ] Section 4.1 (Terraform Vars) passes
- [ ] Section 4.2 (Secrets Manager) passes
- [ ] Section 4.3 (ECS Task) passes
- [ ] Section 5.1 (Full Login Flow) passes
- [ ] Section 5.2 (DB Connection) passes
- [ ] Section 6.1 (Rollback) tested (or marked Not Applicable)

---

## 8. Known Limitations

1. **Local Development**: docker-compose local dev still uses PostgreSQL with password auth - not affected by this change
2. **Staging**: Requires separate RDS cluster or careful testing to avoid impacting staging Keycloak
3. **Rollback Time**: Full rollback takes ~10-15 minutes (Terraform apply + ECS deployment)

---

## 9. Sign-off

| Test Category | Tester | Date | Status |
|---------------|--------|------|--------|
| Functional | | | |
| Backwards Compat | | | |
| Deployment | | | |
| E2E | | | |

**Overall Sign-off:** ___