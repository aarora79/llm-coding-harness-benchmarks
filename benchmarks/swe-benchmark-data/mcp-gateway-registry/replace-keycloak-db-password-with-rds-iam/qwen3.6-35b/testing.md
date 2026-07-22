# Testing Plan: Replace Keycloak DB Password with RDS IAM Authentication

*Created: 2026-07-22*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*
*Model: qwen3.6-35b*

## Overview

### Scope of Testing

Verify that the Keycloak Aurora MySQL database authentication is migrated from password-based to RDS IAM authentication on AWS ECS deployments, that the feature flag (`KEYCLOAK_DB_IAM_AUTH_ENABLED`) correctly toggles between IAM and password auth, that no residual password references remain in Terraform or ECS configuration, and that the deployment surfaces (Docker Compose, ECS, Terraform) all function correctly after the change.

### Prerequisites

- [ ] Terraform state for the target environment is accessible
- [ ] AWS credentials with permissions to manage RDS, ECS, IAM, Secrets Manager, SSM, and Lambda
- [ ] The `mcp-gateway-registry` repo is checked out at tag `1.24.4`
- [ ] A backup of the Terraform state is created before any `terraform apply`
- [ ] MySQL CLI client installed (for the `null_resource` verification step)

### Shared Variables

```bash
export TF_VAR_aws_region="us-east-1"
export TF_VAR_name="keycloak"
export KEYCLOAK_DB_USERNAME="keycloak"
export KEYCLOAK_DB_PROXY_ENDPOINT=$(aws rds describe-db-proxies \
  --db-proxy-name keycloak-proxy \
  --query 'DBProxies[0].Endpoint' \
  --output text)
```

## 1. Functional Tests

### 1.1 Terraform Plan Validation (IAM Auth Mode)

**Purpose:** Verify that `terraform plan` succeeds with IAM auth changes and produces the expected resource deltas.

```bash
cd terraform/aws-ecs
terraform init
terraform plan -out=tfplan \
  -var="aws_region=${TF_VAR_aws_region}" \
  -var="name=${TF_VAR_name}" \
  -var="keycloak_db_iam_auth_enabled=true" \
  -var="keycloak_database_username=${KEYCLOAK_DB_USERNAME}"
```

**Expected Output:**
- Plan succeeds with exit code 0.
- Resources to update: `aws_db_proxy.keycloak` (auth block), `aws_ecs_task_definition.keycloak` (container definitions, command, secrets).
- Resources to add: `aws_iam_role_policy.keycloak_task_rds_db_policy` (rds-db:connect), `null_resource.keycloak_mysql_iam_user`.
- Resources to destroy: `aws_lambda_function.rds_rotation`, `aws_lambda_permission.rds_rotation`, `aws_cloudwatch_log_group.rds_rotation`, `data.archive_file.rds_rotation`.
- Resources to remove: `aws_secretsmanager_secret.keycloak_db_secret`, `aws_secretsmanager_secret_version.keycloak_db_secret`, `aws_secretsmanager_secret_rotation.keycloak_db_secret`.

**Assertions:**
- No unexpected resources are created or destroyed.
- The `auth` block on `aws_db_proxy.keycloak` changes from `SECRETS`/`DISABLED` to `DEFAULT`/`ENABLED`.
- The ECS task definition's command changes from `["start"]` to the wrapper script.

**Negative Case:**

```bash
# Verify that keycloak_database_password is not required in IAM auth mode
terraform plan -var="aws_region=${TF_VAR_aws_region}" \
  -var="keycloak_db_iam_auth_enabled=true" 2>&1 | grep "Required variable"
```

Expected: No errors about `keycloak_database_password` being required (it is optional when IAM auth is enabled).

### 1.2 Terraform Plan Validation (Password Auth Fallback)

**Purpose:** Verify that `terraform plan` succeeds with password auth when the feature flag is set to false.

```bash
terraform plan -out=tfplan-fallback \
  -var="aws_region=${TF_VAR_aws_region}" \
  -var="name=${TF_VAR_name}" \
  -var="keycloak_db_iam_auth_enabled=false" \
  -var="keycloak_database_username=${KEYCLOAK_DB_USERNAME}" \
  -var="keycloak_database_password=placeholder-password"
```

**Assertions:**
- Plan succeeds with exit code 0.
- The `aws_iam_role_policy.keycloak_task_rds_db_policy` resource is NOT created (count = 0).
- The ECS task definition still includes `KC_DB_PASSWORD` from Secrets Manager.
- The RDS Proxy auth scheme remains `SECRETS`/`DISABLED` (no changes).

**Negative Case:**

```bash
# Verify that keycloak_database_password IS required when IAM auth is disabled
terraform plan -var="aws_region=${TF_VAR_aws_region}" \
  -var="keycloak_db_iam_auth_enabled=false" 2>&1 | grep "Required variable"
```

Expected: grep finds an error about `keycloak_database_password` being required when `keycloak_db_iam_auth_enabled` is false.

### 1.3 RDS Cluster Verification

**Purpose:** Verify that the Aurora MySQL cluster configuration is correct after apply.

```bash
aws rds describe-db-clusters \
  --db-cluster-identifier keycloak \
  --query 'DBClusters[0].{Engine:Engine,EngineVersion:EngineVersion,Status:Status}' \
  --output json
```

**Expected Output:**
```json
{"Engine": "aurora-mysql", "EngineVersion": "8.0.mysql_aurora.3.10.3", "Status": "available"}
```

**Assertions:**
- `Engine` is `aurora-mysql`.
- `EngineVersion` is unchanged at `8.0.mysql_aurora.3.10.3`.
- `Status` is `available`.

**Note:** There is no Terraform `enable_http_authentication` or `iam_auth` attribute on `aws_rds_cluster` for MySQL IAM auth. The IAM auth is controlled at the MySQL user level.

### 1.4 MySQL User IAM Authentication Verification

**Purpose:** Verify that the `keycloak` MySQL user is created/updated with `AWSAuthenticationPlugin`.

```bash
MYSQL_HOST=$(aws rds describe-db-clusters \
  --db-cluster-identifier keycloak \
  --query 'DBClusters[0].Endpoint' \
  --output text)

MYSQL_PASS=$(aws ssm get-parameter \
  --name /keycloak/database/password \
  --with-decryption \
  --query 'Parameter.Value' \
  --output text 2>/dev/null || \
  aws secretsmanager get-secret-value \
  --secret-id keycloak/database \
  --query 'SecretString' \
  --output text | jq -r '.password')

mysql -h "${MYSQL_HOST}" -u "${KEYCLOAK_DB_USERNAME}" -p"${MYSQL_PASS}" \
  --default-auth=mysql_native_password \
  -e "SELECT User, Host, plugin FROM mysql.global_priv WHERE User='${KEYCLOAK_DB_USERNAME}';" 2>/dev/null || \
mysql -h "${MYSQL_HOST}" -u root -p"${MYSQL_PASS}" \
  -e "SELECT User, Host, plugin FROM mysql.user WHERE User='${KEYCLOAK_DB_USERNAME}';" 2>/dev/null
```

**Expected Output:**
```
+----------+------+-----------------------------+
| User     | Host | plugin                      |
+----------+------+-----------------------------+
| keycloak | %    | AWSAuthenticationPlugin     |
+----------+------+-----------------------------+
```

**Assertions:**
- `plugin` is `AWSAuthenticationPlugin`.
- `Host` is `%` (accessible from any host).
- The query returns exactly one row.

### 1.5 RDS Proxy IAM Auth Verification

**Purpose:** Verify that the RDS Proxy is configured with IAM authentication.

```bash
aws rds describe-db-proxies \
  --db-proxy-name keycloak-proxy \
  --query 'DBProxies[0].{AuthScheme:Auth[0].AuthScheme,IAMAuth:Auth[0].IAMAuth,Status:Status}' \
  --output json
```

**Expected Output:**
```json
{"AuthScheme": "DEFAULT", "IAMAuth": "ENABLED", "Status": "available"}
```

**Assertions:**
- `AuthScheme` is `DEFAULT` (for MySQL, use `DEFAULT` with `IAMAuth = ENABLED`; `AWS_IAM` is only valid for PostgreSQL/SQL Server).
- `IAMAuth` is `ENABLED`.
- `Status` is `available`.

### 1.6 ECS Task IAM Policy Verification

**Purpose:** Verify that the ECS task role has the `rds-db:connect` permission (when IAM auth is enabled).

```bash
aws iam get-role-policy \
  --role-name keycloak-task-role-${TF_VAR_aws_region} \
  --policy-name keycloak-task-rds-db-policy
```

**Expected Output:**
```json
{
  "RoleName": "keycloak-task-role-us-east-1",
  "PolicyName": "keycloak-task-rds-db-policy",
  "PolicyDocument": {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": "rds-db:connect",
        "Resource": "arn:aws:rds-db:us-east-1:<account>:dbuser:keycloak/keycloak"
      }
    ]
  }
}
```

**Assertions:**
- The policy exists and contains `rds-db:connect`.
- The resource ARN matches the expected format (`arn:aws:rds-db:{region}:{account}:dbuser:{cluster}/{username}`).
- No wildcard (`*`) in the resource ARN.

### 1.7 ECS Task Definition Verification (IAM Auth Mode)

**Purpose:** Verify that the ECS task definition uses IAM auth mode correctly.

```bash
aws ecs describe-task-definition \
  --task-definition keycloak \
  --query 'taskDefinition.containerDefinitions[0].{Environment:environment,Secrets:secrets,Command:command}' \
  --output json | jq '.'
```

**Expected Output:**
- `KEYCLOAK_DB_IAM_AUTH_ENABLED` is `true` in the environment list.
- `KC_DB_PASSWORD` is NOT present in the secrets list.
- `KC_DB_USERNAME` uses `value = var.keycloak_database_username` (not a secret reference).
- The command starts with `["/bin/sh", "-c", ...]` and includes `aws rds generate-db-auth-token`.

**Negative Case:**

```bash
# Verify KC_DB_PASSWORD is NOT sourced from Secrets Manager
PASSWORD_SOURCE=$(aws ecs describe-task-definition \
  --task-definition keycloak \
  --query 'taskDefinition.containerDefinitions[0].secrets[?name==`KC_DB_PASSWORD`].valueFrom' \
  --output text)
if echo "$PASSWORD_SOURCE" | grep -q "keycloak/database:password"; then
  echo "FAIL: KC_DB_PASSWORD is still sourced from Secrets Manager"
  exit 1
fi
echo "PASS: KC_DB_PASSWORD is not sourced from Secrets Manager"
```

### 1.8 ECS Task Definition Verification (Password Auth Fallback Mode)

**Purpose:** Verify that the ECS task definition uses password auth when the feature flag is set to false.

```bash
# Deploy a version with KEYCLOAK_DB_IAM_AUTH_ENABLED=false, then describe it
aws ecs describe-task-definition \
  --task-definition keycloak \
  --query 'taskDefinition.containerDefinitions[0].{Environment:environment,Secrets:secrets,Command:command}' \
  --output json | jq '.'
```

**Expected Output (with IAM auth disabled):**
- `KEYCLOAK_DB_IAM_AUTH_ENABLED` is `false` in the environment list.
- `KC_DB_PASSWORD` IS present in the secrets list with `valueFrom` referencing `keycloak/database:password`.
- The command is `["start"]` (no wrapper).
- The `keycloak-task-rds-db-policy` IAM policy does not exist on the task role (or has count = 0).

### 1.9 Secrets Manager Secret Removal Verification

**Purpose:** Verify that the `keycloak/database` Secrets Manager secret has been removed (in IAM auth mode).

```bash
aws secretsmanager describe-secret \
  --secret-id keycloak/database 2>&1 || echo "Secret not found (expected)"
```

**Expected Output:** `ResourceNotFoundException` (exit code non-zero, handled by `||`).

**Assertions:**
- The secret does not exist or is in `DELETED` state.
- No active secret versions remain.

### 1.10 Rotation Lambda Removal Verification

**Purpose:** Verify that the `rotate-rds` Lambda function and its associated resources have been removed.

```bash
aws lambda get-function --function-name keycloak-rotate-rds 2>&1 || echo "Lambda not found (expected)"
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/keycloak-rotate-rds 2>&1 || echo "Log group not found (expected)"
```

**Expected Output:** `ResourceNotFoundException` for both the Lambda and the CloudWatch log group.

**Assertions:**
- The Lambda function does not exist.
- The CloudWatch log group `/aws/lambda/keycloak-rotate-rds` does not exist.
- The `rotate-documentdb` Lambda still exists (should not be removed):

```bash
aws lambda get-function --function-name keycloak-rotate-documentdb 2>&1 | jq '.Configuration.FunctionName'
```

Expected output: `keycloak-rotate-documentdb` (Lambda still exists).

### 1.11 SSM Parameter URL Verification

**Purpose:** Verify that the SSM parameter `/keycloak/database/url` includes SSL parameters.

```bash
aws ssm get-parameter \
  --name /keycloak/database/url \
  --with-decryption \
  --query 'Parameter.Value' \
  --output text
```

**Expected Output:**
```
jdbc:mysql://keycloak-proxy.x.region.rds.amazonaws.com:3306/keycloak?ssl=true&sslmode=require&enabledTLSProtocols=TLSv1.2
```

**Assertions:**
- The URL includes `ssl=true`, `sslmode=require`, and `enabledTLSProtocols=TLSv1.2`.
- The URL points to the RDS Proxy endpoint (not the cluster endpoint).

### 1.12 Checkov Skip Removal Verification

**Purpose:** Verify that the `CKV_AWS_162` checkov skip has been removed.

```bash
grep -r "CKV_AWS_162" terraform/aws-ecs/ || echo "PASS: No CKV_AWS_162 skips found"
```

**Expected Output:** `PASS: No CKV_AWS_162 skips found`

**Assertions:**
- No `.tf` files in `terraform/aws-ecs/` contain `CKV_AWS_162`.

### 1.13 IAM Auth Token Generation Verification

**Purpose:** Verify that a process with the ECS task role can generate an IAM auth token.

```bash
TOKEN=$(aws rds generate-db-auth-token \
  --hostname "${KEYCLOAK_DB_PROXY_ENDPOINT}" \
  --port 3306 \
  --username "${KEYCLOAK_DB_USERNAME}" \
  --region "${TF_VAR_aws_region}")

if [ -z "$TOKEN" ]; then
  echo "FAIL: Failed to generate IAM auth token"
  exit 1
fi
echo "PASS: IAM auth token generated successfully (${#TOKEN} chars)"
```

**Assertions:**
- Token is non-empty and has reasonable length (typically 1500+ characters for a signed JWT).
- The token is a valid JWT (starts with `eyJ`).

### 1.14 Keycloak ECS Service Health Check

**Purpose:** Verify that Keycloak is healthy and running after the deployment.

```bash
aws ecs describe-services \
  --cluster keycloak \
  --services keycloak \
  --query 'services[0].{Status:status,RunningCount:runningCount,DesiredCount:desiredCount}' \
  --output json
```

**Expected Output:**
```json
{"Status": "ACTIVE", "RunningCount": 1, "DesiredCount": 1}
```

**Assertions:**
- Service status is `ACTIVE`.
- `RunningCount` equals `DesiredCount` (at least 1).
- No `RUNNING` tasks have `REASON` indicating failures (e.g., `Essential container in task exited`).

### 1.15 Keycloak OIDC Endpoint Verification

**Purpose:** Verify that Keycloak's OIDC endpoints are accessible after the IAM auth migration.

```bash
curl -sf --max-time 30 "http://<keycloak-domain>/realms/mcp-gateway/.well-known/openid-configuration" | jq '.issuer'
```

**Expected Output:**
```
https://<keycloak-domain>/realms/mcp-gateway
```

**Assertions:**
- The OIDC configuration endpoint returns valid JSON.
- The `issuer` matches the expected Keycloak domain.
- The response includes `.well-known/openid-configuration` fields.

### 1.16 RDS Proxy TLS Verification

**Purpose:** Verify that the RDS Proxy enforces TLS connections.

```bash
aws rds describe-db-proxies \
  --db-proxy-name keycloak-proxy \
  --query 'DBProxies[0].RequireTLS' \
  --output text
```

**Expected Output:** `True`

**Assertions:**
- `RequireTLS` is `True`.

### 1.17 ECS Task Role SSM Policy Unchanged

**Purpose:** Verify that the ECS task execution role's SSM policy still grants access to admin and URL parameters.

```bash
aws iam get-role-policy \
  --role-name keycloak-task-exec-role-${TF_VAR_aws_region} \
  --policy-name keycloak-task-exec-ssm-policy
```

**Assertions:**
- The policy still grants `ssm:GetParameter` for the admin and database URL SSM parameters.
- No SSM-related permissions were accidentally removed.

---

## 2. Backwards Compatibility Tests

### 2.1 Password Auth Fallback Mode

**Purpose:** Verify that the feature flag correctly falls back to password auth when set to false.

```bash
# Deploy with KEYCLOAK_DB_IAM_AUTH_ENABLED=false
terraform apply -auto-approve \
  -var="aws_region=${TF_VAR_aws_region}" \
  -var="keycloak_db_iam_auth_enabled=false" \
  -var="keycloak_database_password=${KEYCLOAK_DB_PASSWORD}" \
  -var="keycloak_database_username=${KEYCLOAK_DB_USERNAME}"

aws ecs update-service --cluster keycloak --service keycloak \
  --force-new-deployment

aws ecs wait services-stable --cluster keycloak --services keycloak

# Verify Keycloak connects using password auth
TOKEN_RESPONSE=$(curl -s --max-time 30 -X POST "http://<keycloak-domain>/realms/mcp-gateway/protocol/openid-connect/token" \
  -d "grant_type=client_credentials&client_id=mcp-gateway-m2m")
echo "$TOKEN_RESPONSE" | jq '.access_token != null'
```

**Expected Output:** `true` (Keycloak serves OIDC requests using password auth).

**Assertions:**
- `terraform apply` succeeds.
- The ECS service stabilizes with `RunningCount == DesiredCount`.
- Keycloak serves OIDC requests successfully.

### 2.2 ECS Task Role SSM Access Unchanged

**Purpose:** Verify that the ECS task execution role still has SSM access for `KEYCLOAK_ADMIN`, `KEYCLOAK_ADMIN_PASSWORD`, and `KC_DB_URL`.

```bash
aws iam get-role-policy \
  --role-name keycloak-task-exec-role-${TF_VAR_aws_region} \
  --policy-name keycloak-task-exec-ssm-policy \
  --query 'PolicyDocument.Statement[].Action' \
  --output text
```

**Assertions:**
- The policy still grants `ssm:GetParameter` for the admin and database URL SSM parameters.
- No SSM-related permissions were accidentally removed.

### 2.3 Keycloak Admin Credentials Still Work

**Purpose:** Verify that Keycloak admin login still works via SSM-sourced credentials (this change does not affect admin auth).

```bash
ADMIN=$(aws ssm get-parameter --name /keycloak/admin --with-decryption --query 'Parameter.Value' --output text)
ADMIN_PASS=$(aws ssm get-parameter --name /keycloak/admin_password --with-decryption --query 'Parameter.Value' --output text)
curl -s --max-time 30 -o /dev/null -w "%{http_code}" \
  -X POST "http://<keycloak-domain>/realms/mcp-gateway/protocol/openid-connect/token" \
  -d "grant_type=password&username=${ADMIN}&password=${ADMIN_PASS}&client_id=admin-cli"
```

**Expected Output:** HTTP 200

### 2.4 Other Terraform Resources Unaffected

**Purpose:** Verify that non-Keycloak resources (DocumentDB, registry, ALB, CloudFront) are not impacted.

```bash
cd terraform/aws-ecs
terraform plan -target=aws_docdb_cluster.registry -var="aws_region=${TF_VAR_aws_region}" \
  -var="name=${TF_VAR_name}" 2>&1 | grep -E "No changes|Changes"
terraform plan -target=aws_ecs_service.registry -var="aws_region=${TF_VAR_aws_region}" \
  -var="name=${TF_VAR_name}" 2>&1 | grep -E "No changes|Changes"
terraform plan -target=aws_lb_listener.keycloak_https -var="aws_region=${TF_VAR_aws_region}" \
  -var="name=${TF_VAR_name}" 2>&1 | grep -E "No changes|Changes"
```

**Assertions:**
- Each targeted plan shows no unexpected changes.

### 2.5 Rotation Lambda for DocumentDB Still Works

**Purpose:** Verify that the DocumentDB rotation Lambda was not affected by the Keycloak rotation Lambda removal.

```bash
aws lambda get-function --function-name keycloak-rotate-documentdb 2>&1 | jq '.Configuration.FunctionName'
```

**Expected Output:** `keycloak-rotate-documentdb` (Lambda still exists).

### 2.6 Feature Flag Toggle Without Redeployment

**Purpose:** Verify that the feature flag value is correctly read by the ECS task definition.

```bash
# Check the task definition for the feature flag
aws ecs describe-task-definition \
  --task-definition keycloak \
  --query 'taskDefinition.containerDefinitions[0].environment[?name==`KEYCLOAK_DB_IAM_AUTH_ENABLED`].value' \
  --output text
```

**Assertions:**
- The value matches the expected setting (`true` or `false`).
- Changing the flag value via Terraform and doing a `force-new-deployment` correctly switches the auth mode.

---

## 3. UX Tests

### 3.1 CLI Output Clarity

**Purpose:** Verify that `terraform plan` output clearly explains what is changing.

```bash
terraform plan -var="aws_region=${TF_VAR_aws_region}" \
  -var="keycloak_db_iam_auth_enabled=true" \
  -var="keycloak_database_username=${KEYCLOAK_DB_USERNAME}" 2>&1 | tee /tmp/terraform-plan.txt
```

**Assertions:**
- The plan output mentions the RDS Proxy auth scheme change.
- The plan output mentions the ECS task definition container changes.
- The plan output mentions the deletion of the rotation Lambda and Secrets Manager secret.
- The plan output mentions the addition of the `rds-db:connect` IAM policy.
- No warnings about deprecated attributes or provider versions.

### 3.2 Error Message Clarity

**Purpose:** Verify that Terraform provides clear error messages if IAM auth is misconfigured.

```bash
# Test: omit keycloak_database_password when IAM auth is disabled
terraform plan -var="aws_region=${TF_VAR_aws_region}" \
  -var="keycloak_db_iam_auth_enabled=false" \
  -var="keycloak_database_username=${KEYCLOAK_DB_USERNAME}" 2>&1
```

**Expected Output:** A clear error about `keycloak_database_password` being required when `keycloak_db_iam_auth_enabled` is false.

**Assertions:**
- Terraform exits with a non-zero code.
- The error message mentions `keycloak_database_password`.

### 3.3 Feature Flag Documentation

**Purpose:** Verify that the `KEYCLOAK_DB_IAM_AUTH_ENABLED` variable is documented in `.env.example`.

```bash
grep -i "KEYCLOAK_DB_IAM_AUTH" .env.example || echo "FAIL: KEYCLOAK_DB_IAM_AUTH_ENABLED not in .env.example"
```

**Expected Output:** A line documenting the new variable.

**Assertions:**
- The variable appears in `.env.example`.
- It has a comment explaining its purpose.

---

## 4. Deployment Surface Tests

### 4.1 Docker Compose Wiring

**Purpose:** Verify that the docker-compose local development setup is not affected.

```bash
docker compose up -d keycloak keycloak-db
sleep 60
curl -sf --max-time 30 http://localhost:8080/health/ready || echo "FAIL: Keycloak not healthy"
```

**Assertions:**
- Keycloak starts successfully.
- Keycloak connects to PostgreSQL successfully.
- No errors related to missing `KEYCLOAK_DB_PASSWORD` environment variable (it is still used in docker-compose).

### 4.2 Terraform / ECS Wiring (IAM Auth Mode)

**Purpose:** Verify that the Terraform changes deploy correctly to ECS in IAM auth mode.

```bash
cd terraform/aws-ecs
terraform apply -auto-approve \
  -var="aws_region=${TF_VAR_aws_region}" \
  -var="keycloak_db_iam_auth_enabled=true" \
  -var="keycloak_database_username=${KEYCLOAK_DB_USERNAME}"

aws ecs update-service \
  --cluster keycloak \
  --service keycloak \
  --force-new-deployment

aws ecs wait services-stable \
  --cluster keycloak \
  --services keycloak

aws ecs describe-services \
  --cluster keycloak \
  --services keycloak \
  --query 'services[0].status' \
  --output text
```

**Expected Output:** `ACTIVE`

**Assertions:**
- `terraform apply` completes with exit code 0.
- No errors related to RDS cluster modification (RDS Proxy re-creation may take 2-5 minutes).
- The ECS task definition is updated.
- The ECS service status is `ACTIVE` with all tasks `RUNNING`.

### 4.3 Helm / EKS Wiring

**Purpose:** Verify that no Helm chart changes are needed (Keycloak is deployed via ECS, not EKS).

**Not Applicable** -- The Keycloak service is managed by Terraform/ECS, not by Helm charts. Helm charts reference Keycloak as an external IdP but do not configure its database. The following grep confirms no Helm chart references Keycloak DB credentials:

```bash
grep -r "KC_DB_PASSWORD\|keycloak_db_secret\|rotate-rds" charts/ || echo "PASS: No Helm references to Keycloak DB credentials"
```

### 4.4 Deploy and Verify (Full E2E)

**Purpose:** End-to-end deployment verification with IAM auth enabled.

```bash
# Verify Keycloak is serving OIDC requests
TOKEN_RESPONSE=$(curl -s --max-time 30 -X POST "http://<keycloak-domain>/realms/mcp-gateway/protocol/openid-connect/token" \
  -d "grant_type=client_credentials&client_id=mcp-gateway-m2m")

ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.access_token')
if [ "$ACCESS_TOKEN" = "null" ] || [ -z "$ACCESS_TOKEN" ]; then
  echo "FAIL: Could not obtain access token from Keycloak"
  exit 1
fi

# Verify the token works against the registry API
REGISTRY_RESPONSE=$(curl -s --max-time 30 -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "http://<registry-domain>/api/v1/servers")

echo "Registry API status: ${REGISTRY_RESPONSE}"
```

**Expected Output:**
- `TOKEN_RESPONSE` contains a valid `access_token`.
- `REGISTRY_RESPONSE` is `200`.

**Assertions:**
- The access token is valid and not expired.
- The registry API accepts the token and returns a response.
- No database connection errors in the Keycloak CloudWatch logs.

### 4.5 Rollback Verification (Feature Flag)

**Purpose:** Verify that the changes can be rolled back quickly via the feature flag.

```bash
# Revert to password auth mode via the feature flag (fastest rollback)
terraform apply -auto-approve \
  -var="aws_region=${TF_VAR_aws_region}" \
  -var="keycloak_db_iam_auth_enabled=false" \
  -var="keycloak_database_password=${KEYCLOAK_DB_PASSWORD}" \
  -var="keycloak_database_username=${KEYCLOAK_DB_USERNAME}"

aws ecs update-service \
  --cluster keycloak \
  --service keycloak \
  --force-new-deployment

aws ecs wait services-stable \
  --cluster keycloak \
  --services keycloak

# Verify Keycloak connects using password auth
TOKEN_RESPONSE=$(curl -s --max-time 30 -X POST "http://<keycloak-domain>/realms/mcp-gateway/protocol/openid-connect/token" \
  -d "grant_type=client_credentials&client_id=mcp-gateway-m2m")
echo "$TOKEN_RESPONSE" | jq '.access_token != null'
```

**Expected Output:** `true` (Keycloak serves OIDC requests using password auth after rollback).

**Assertions:**
- The rollback `terraform apply` succeeds.
- The ECS service stabilizes with the previous task definition.
- Keycloak connects to the database using password auth.

### 4.6 Full Revert (Terraform State)

**Purpose:** Verify that a full Terraform state revert works (re-creates Secrets Manager, rotation Lambda, etc.).

**Not Applicable** -- A full revert requires restoring Terraform state from backup. This is a disaster-recovery scenario and should be tested in a staging environment before production use. The feature flag rollback (Section 4.5) is the recommended rollback path.

---

## 5. End-to-End API Tests

### 5.1 Full Keycloak Login Flow with IAM Auth

**Purpose:** Verify that end users can authenticate through Keycloak after the IAM auth migration.

```bash
TOKEN_RESPONSE=$(curl -s --max-time 30 -X POST "http://<keycloak-domain>/realms/mcp-gateway/protocol/openid-connect/token" \
  -d "grant_type=password&username=testuser&password=testpassword&client_id=mcp-gateway-web")

ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.access_token')
if [ "$ACCESS_TOKEN" = "null" ] || [ -z "$ACCESS_TOKEN" ]; then
  echo "FAIL: Could not obtain access token"
  exit 1
fi

# Verify the token works against the registry API
REGISTRY_RESPONSE=$(curl -s --max-time 30 -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "http://<registry-domain>/api/v1/servers")

echo "Registry API status: ${REGISTRY_RESPONSE}"
```

**Expected Output:** HTTP 200

**Assertions:**
- The access token is valid and not expired.
- The registry API accepts the token and returns a response.
- No database connection errors in the Keycloak CloudWatch logs.

### 5.2 Token Refresh Under Load

**Purpose:** Verify that Keycloak handles concurrent connections with IAM auth.

```bash
for i in $(seq 1 10); do
  curl -s --max-time 30 -o /dev/null -w "%{http_code}\n" \
    -X POST "http://<keycloak-domain>/realms/mcp-gateway/protocol/openid-connect/token" \
    -d "grant_type=password&username=testuser&password=testpassword&client_id=mcp-gateway-web" &
done
wait
echo "All requests completed"
```

**Assertions:**
- All 10 requests complete with HTTP 200 (or expected status).
- No database connection errors in the Keycloak logs.
- No `DBUserNotAuthorized` errors in RDS CloudWatch metrics.

### 5.3 RDS Proxy Connection Pooling

**Purpose:** Verify that the RDS Proxy is correctly load-balancing connections with IAM auth.

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name ProxyActiveConnections \
  --dimensions Name=DbProxyName,Value=keycloak-proxy \
  --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 \
  --statistics Average
```

**Assertions:**
- `ProxyActiveConnections` is non-zero when Keycloak is running.
- `ProxyGrantedConnections` matches expected active connections.
- No `ProxySpillover` spikes.

### 5.4 Token Expiration Handling

**Purpose:** Verify that Keycloak handles token expiration gracefully. The IAM auth token has a 15-minute TTL. This test verifies the expected behavior.

```bash
# Note: This test requires waiting 15 minutes for token expiration.
# An alternative is to generate a token with a shorter TTL for testing:
# aws rds generate-db-auth-token --duration-seconds 60 ...

echo "Waiting for token expiration test (skipped in automated pipelines)"

# After 15 minutes, attempt an OIDC request
TOKEN_RESPONSE=$(curl -s --max-time 30 -X POST "http://<keycloak-domain>/realms/mcp-gateway/protocol/openid-connect/token" \
  -d "grant_type=client_credentials&client_id=mcp-gateway-m2m")

STATUS=$(echo "$TOKEN_RESPONSE" | jq -r '.access_token != null')
echo "Token expiration test result: ${STATUS}"
```

**Expected Output:**
- If the container was restarted before token expiration: `true` (fresh token generated).
- If the container was NOT restarted: the existing JDBC connection may still work (token was valid at connection time), but new connections will fail.

**Note:** This test validates the 15-minute token TTL behavior. In production, ECS Fargate tasks are replaced on health check failures or rolling updates, which naturally triggers token regeneration.

### 5.5 M2M Service Account Authentication

**Purpose:** Verify that M2M (machine-to-machine) service accounts work with IAM auth.

```bash
M2M_TOKEN=$(curl -s --max-time 30 -X POST "http://<keycloak-domain>/realms/mcp-gateway/protocol/openid-connect/token" \
  -d "grant_type=client_credentials&client_id=mcp-gateway-m2m")

M2M_ACCESS_TOKEN=$(echo "$M2M_TOKEN" | jq -r '.access_token')
if [ "$M2M_ACCESS_TOKEN" = "null" ] || [ -z "$M2M_ACCESS_TOKEN" ]; then
  echo "FAIL: Could not obtain M2M access token"
  exit 1
fi

# Use M2M token for registry operations
REGISTRY_RESPONSE=$(curl -s --max-time 30 -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer ${M2M_ACCESS_TOKEN}" \
  "http://<registry-domain>/api/v1/servers")

echo "M2M registry API status: ${REGISTRY_RESPONSE}"
```

**Expected Output:** HTTP 200

### 5.6 Federation Service Account Authentication

**Purpose:** Verify that federation service accounts (peer-to-peer registries) work with IAM auth.

```bash
FED_TOKEN=$(curl -s --max-time 30 -X POST "http://<keycloak-domain>/realms/mcp-gateway/protocol/openid-connect/token" \
  -d "grant_type=client_credentials&client_id=federation-client")

FED_ACCESS_TOKEN=$(echo "$FED_TOKEN" | jq -r '.access_token')
if [ "$FED_ACCESS_TOKEN" = "null" ] || [ -z "$FED_ACCESS_TOKEN" ]; then
  echo "FAIL: Could not obtain federation access token"
  exit 1
fi

FED_RESPONSE=$(curl -s --max-time 30 -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer ${FED_ACCESS_TOKEN}" \
  "http://<registry-domain>/api/federation/peers")

echo "Federation API status: ${FED_RESPONSE}"
```

**Expected Output:** HTTP 200

---

## 6. Test Execution Checklist

- [ ] Section 1 (Functional): All 17 tests pass
  - [ ] 1.1 Terraform plan validation (IAM auth mode)
  - [ ] 1.2 Terraform plan validation (password auth fallback)
  - [ ] 1.3 RDS cluster verification
  - [ ] 1.4 MySQL user IAM authentication verification
  - [ ] 1.5 RDS proxy IAM auth verification
  - [ ] 1.6 ECS task IAM policy verification
  - [ ] 1.7 ECS task definition verification (IAM auth mode)
  - [ ] 1.8 ECS task definition verification (password auth fallback)
  - [ ] 1.9 Secrets Manager secret removal verification
  - [ ] 1.10 Rotation Lambda removal verification
  - [ ] 1.11 SSM parameter URL verification
  - [ ] 1.12 Checkov skip removal verification
  - [ ] 1.13 IAM auth token generation verification
  - [ ] 1.14 Keycloak ECS service health check
  - [ ] 1.15 Keycloak OIDC endpoint verification
  - [ ] 1.16 RDS Proxy TLS verification
  - [ ] 1.17 ECS task role SSM policy unchanged
- [ ] Section 2 (Backwards Compat): All 6 tests pass or marked Not Applicable
  - [ ] 2.1 Password auth fallback mode
  - [ ] 2.2 ECS task role SSM access unchanged
  - [ ] 2.3 Keycloak admin credentials still work
  - [ ] 2.4 Other Terraform resources unaffected
  - [ ] 2.5 Rotation Lambda for DocumentDB still works
  - [ ] 2.6 Feature flag toggle without redeployment
- [ ] Section 3 (UX): All 3 tests pass
  - [ ] 3.1 CLI output clarity
  - [ ] 3.2 Error message clarity
  - [ ] 3.3 Feature flag documentation
- [ ] Section 4 (Deployment): All 6 tests pass or marked Not Applicable
  - [ ] 4.1 Docker compose wiring
  - [ ] 4.2 Terraform / ECS wiring (IAM auth mode)
  - [ ] 4.3 Helm / EKS wiring
  - [ ] 4.4 Deploy and verify (full E2E)
  - [ ] 4.5 Rollback verification (feature flag)
  - [ ] 4.6 Full revert (Terraform state)
- [ ] Section 5 (E2E): All 6 tests pass
  - [ ] 5.1 Full Keycloak login flow
  - [ ] 5.2 Token refresh under load
  - [ ] 5.3 RDS proxy connection pooling
  - [ ] 5.4 Token expiration handling
  - [ ] 5.5 M2M service account authentication
  - [ ] 5.6 Federation service account authentication
- [ ] Unit tests: No Python unit tests needed (infrastructure-only change)
- [ ] Integration tests: The Terraform plan validation (1.1, 1.2) serves as the integration test
- [ ] `terraform plan` passes with no unexpected changes