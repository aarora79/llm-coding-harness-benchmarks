# Testing Plan: Replace Keycloak DB Password with RDS IAM Authentication

*Created: 2026-07-06*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
Verify that the Keycloak Aurora MySQL database authentication is fully migrated from password-based to RDS IAM authentication, that no residual password references remain in Terraform or ECS configuration, and that the deployment surfaces (Docker Compose, ECS, Terraform) all function correctly after the change.

### Prerequisites
- [ ] Terraform state for the target environment is accessible
- [ ] AWS credentials with permissions to manage RDS, ECS, IAM, Secrets Manager, and SSM
- [ ] The `mcp-gateway-registry` repo is checked out at tag `1.24.4`
- [ ] A backup of the Terraform state is created before any `terraform apply`

### Shared Variables
```bash
export TF_VAR_aws_region="us-east-1"
export TF_VAR_name="keycloak"
export KEYCLOAK_DB_USERNAME="keycloak"
```

## 1. Functional Tests

### 1.1 Terraform Plan Validation

**Purpose:** Verify that `terraform plan` succeeds with all changes and produces the expected resource deltas.

```bash
cd terraform/aws-ecs
terraform init
terraform plan -out=tfplan \
  -var="aws_region=${TF_VAR_aws_region}" \
  -var="name=${TF_VAR_name}" \
  -var="keycloak_database_username=${KEYCLOAK_DB_USERNAME}"
```

**Expected Output:**
- Plan succeeds with exit code 0.
- Resources to create: `aws_rds_cluster_instance` (if needed), `null_resource.keycloak_iam_user` (or equivalent).
- Resources to update: `aws_rds_cluster.keycloak` (enable_http_authentication = true), `aws_db_proxy.keycloak` (auth block), `aws_ecs_task_definition.keycloak` (container definitions), `aws_ssm_parameter.keycloak_database_url` (JDBC URL with SSL params).
- Resources to add: `aws_iam_role_policy.keycloak_task_rds_db_policy` (rds-db:Connect).
- Resources to destroy: `aws_secretsmanager_secret.keycloak_db_secret`, `aws_secretsmanager_secret_version.keycloak_db_secret`, `aws_secretsmanager_secret_rotation.keycloak_db_secret`, `aws_lambda_function.rds_rotation`, `aws_cloudwatch_log_group.rds_rotation`, and associated resources.

**Assertions:**
- No unexpected resources are created or destroyed.
- The number of resources to update is within expected range (approximately 5-8 resources).
- The `master_password` field on `aws_rds_cluster.keycloak` is NOT removed (it is retained but sourced differently).

**Negative Case:**
```bash
# Verify that keycloak_database_password is no longer referenced
grep -r "keycloak_database_password" . || echo "PASS: No references to keycloak_database_password"
```

Expected: grep finds no matches (or only matches in comments/documentation explaining deprecation).

### 1.2 RDS Cluster IAM Auth Verification

**Purpose:** Verify that IAM authentication is enabled on the Aurora MySQL cluster.

```bash
aws rds describe-db-clusters \
  --db-cluster-identifier keycloak \
  --query 'DBClusters[0].EnableHttpAuthentication' \
  --output text
```

**Expected Output:** `True`

**Assertions:**
- `EnableHttpAuthentication` is `True`.
- `Status` is `available`.

**Negative Case:**
```bash
result=$(aws rds describe-db-clusters \
  --db-cluster-identifier keycloak \
  --query 'DBClusters[0].EnableHttpAuthentication' \
  --output text)
if [ "$result" != "True" ]; then
  echo "FAIL: IAM authentication is not enabled"
  exit 1
fi
```

### 1.3 MySQL User IAM Authentication Verification

**Purpose:** Verify that the `keycloak` MySQL user is created with `AWSAuthenticationPlugin`.

```bash
mysql -h <cluster-endpoint> -u keycloak -p -e \
  "SELECT User, Host, plugin, account_locked FROM mysql.user WHERE User='keycloak';"
```

**Expected Output:**
```
+----------+-----------+----------------------------+--------------+
| User     | Host      | plugin                     | account_locked |
+----------+-----------+----------------------------+--------------+
| keycloak | %         | AWSAuthenticationPlugin    | N            |
+----------+-----------+----------------------------+--------------+
```

**Assertions:**
- `plugin` is `AWSAuthenticationPlugin`.
- `account_locked` is `N`.
- `IAMAuth` status is `Y` (if the `iam_auth` column exists in the MySQL version).

### 1.4 RDS Proxy IAM Auth Verification

**Purpose:** Verify that the RDS Proxy is configured with IAM authentication.

```bash
aws rds describe-db-proxies \
  --db-proxy-name keycloak-proxy \
  --query 'DBProxies[0].Auth[0].AuthScheme' \
  --output text
```

**Expected Output:** `AWS_IAM`

**Assertions:**
- `AuthScheme` is `AWS_IAM`.
- `IAMAuth` is `ENABLED`.

**Negative Case:**
```bash
scheme=$(aws rds describe-db-proxies \
  --db-proxy-name keycloak-proxy \
  --query 'DBProxies[0].Auth[0].AuthScheme' \
  --output text)
if [ "$scheme" = "SECRETS" ]; then
  echo "FAIL: RDS Proxy is still using SECRETS auth scheme"
  exit 1
fi
```

### 1.5 ECS Task IAM Policy Verification

**Purpose:** Verify that the ECS task role has the `rds-db:Connect` permission.

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
        "Action": "rds-db:Connect",
        "Resource": "arn:aws:rds-db:us-east-1:<account>:dbuser:keycloak/keycloak"
      }
    ]
  }
}
```

**Assertions:**
- The policy exists and contains `rds-db:Connect`.
- The resource ARN matches the expected format.

### 1.6 ECS Task Definition Verification

**Purpose:** Verify that the ECS task definition no longer includes `KC_DB_PASSWORD` from Secrets Manager and that `KC_DB_URL` includes SSL parameters.

```bash
aws ecs describe-task-definition \
  --task-definition keycloak \
  --query 'taskDefinition.containerDefinitions[0].environment' \
  --output json | jq '[.[] | select(.name == "KC_DB_URL" or .name == "KC_DB_USERNAME" or .name == "KC_DB_PASSWORD")]'
```

**Expected Output:**
- `KC_DB_PASSWORD` is NOT present in the environment list.
- `KC_DB_URL` contains `ssl=true` and `sslmode=require`.
- `KC_DB_USERNAME` is present (either from env or secret).

**Negative Case:**
```bash
password_in_task=$(aws ecs describe-task-definition \
  --task-definition keycloak \
  --query 'taskDefinition.containerDefinitions[0].secrets' \
  --output json | jq '[.[] | select(.name == "KC_DB_PASSWORD")] | length')
if [ "$password_in_task" -gt 0 ]; then
  echo "FAIL: KC_DB_PASSWORD is still in the task definition secrets"
  exit 1
fi
```

### 1.7 Secrets Manager Secret Removal Verification

**Purpose:** Verify that the `keycloak/database` Secrets Manager secret has been removed (or repurposed).

```bash
aws secretsmanager describe-secret \
  --secret-id keycloak/database 2>&1 || echo "Secret not found (expected)"
```

**Expected Output:** `ResourceNotFoundException` (exit code non-zero, which `||` handles).

**Assertions:**
- The secret does not exist or is in `DELETED` state.
- The rotation configuration does not reference the secret.

### 1.8 Rotation Lambda Removal Verification

**Purpose:** Verify that the `rotate-rds` Lambda function and its associated resources have been removed.

```bash
aws lambda get-function --function-name keycloak-rotate-rds 2>&1 || echo "Lambda not found (expected)"
```

**Expected Output:** `ResourceNotFoundException` (Lambda no longer exists).

**Assertions:**
- The Lambda function does not exist.
- The CloudWatch log group `/aws/lambda/keycloak-rotate-rds` does not exist.
- The IAM role policy does not reference the deleted secret.

## 2. Backwards Compatibility Tests

### 2.1 ECS Task Role Still Has SSM Access

**Purpose:** Verify that the ECS task execution role still has SSM access for `KEYCLOAK_ADMIN`, `KEYCLOAK_ADMIN_PASSWORD`, and `KC_DB_URL`.

```bash
aws iam get-role-policy \
  --role-name keycloak-task-exec-role-${TF_VAR_aws_region} \
  --policy-name keycloak-task-exec-ssm-policy
```

**Assertions:**
- The policy still grants `ssm:GetParameter` for the admin and database URL SSM parameters.
- No SSM-related permissions were accidentally removed.

### 2.2 Keycloak Admin Credentials Still Work

**Purpose:** Verify that Keycloak admin login still works via SSM-sourced credentials (this change does not affect admin auth).

```bash
ADMIN=$(aws ssm get-parameter --name /keycloak/admin --with-decryption --query 'Parameter.Value' --output text)
ADMIN_PASS=$(aws ssm get-parameter --name /keycloak/admin_password --with-decryption --query 'Parameter.Value' --output text)
curl -s -o /dev/null -w "%{http_code}" \
  -X POST "http://localhost:8080/realms/mcp-gateway/protocol/openid-connect/token" \
  -d "grant_type=password&username=${ADMIN}&password=${ADMIN_PASS}&client_id=admin-cli"
```

**Expected Output:** HTTP 200

### 2.3 Other Terraform Resources Unaffected

**Purpose:** Verify that non-Keycloak resources (DocumentDB, registry, ALB, CloudFront) are not impacted.

```bash
terraform plan -target=aws_docdb_cluster.registry
terraform plan -target=aws_ecs_service.registry
terraform plan -target=aws_lb_listener.keycloak_https
```

**Assertions:**
- Each targeted plan shows no unexpected changes.

**Not Applicable** -- These are dry-run plans that verify no drift on unrelated resources. The Terraform state for these resources should remain unchanged.

## 3. UX Tests

### 3.1 CLI Output Clarity

**Purpose:** Verify that `terraform plan` output clearly explains what is changing.

```bash
terraform plan -var="aws_region=${TF_VAR_aws_region}" -var="name=${TF_VAR_name}" \
  -var="keycloak_database_username=${KEYCLOAK_DB_USERNAME}" 2>&1 | tee /tmp/terraform-plan.txt
```

**Assertions:**
- The plan output mentions `enable_http_authentication` changing from `false` to `true` on the RDS cluster.
- The plan output mentions the RDS Proxy auth scheme change.
- The plan output mentions the ECS task definition container changes.
- The plan output mentions the deletion of the Secrets Manager secret and rotation Lambda.
- No warnings about deprecated attributes or provider versions.

### 3.2 Error Message Clarity

**Purpose:** Verify that Terraform provides clear error messages if IAM auth is misconfigured.

**Not Applicable** -- Terraform plan-time validation catches configuration errors before apply. Runtime errors (ECS task failing to connect) will produce standard RDS authentication error messages in CloudWatch Logs.

## 4. Deployment Surface Tests

### 4.1 Docker Compose Wiring

**Purpose:** Verify that the docker-compose local development setup is not affected.

```bash
docker compose up -d keycloak keycloak-db
sleep 30
curl -f http://localhost:8080/health/ready || echo "FAIL: Keycloak not healthy"
```

**Assertions:**
- Keycloak starts successfully.
- Keycloak connects to PostgreSQL successfully.
- No errors related to missing `KEYCLOAK_DB_PASSWORD` environment variable (it is still used in docker-compose).

### 4.2 Terraform / ECS Wiring

**Purpose:** Verify that the Terraform changes deploy correctly to ECS.

```bash
cd terraform/aws-ecs
terraform apply -auto-approve \
  -var="aws_region=${TF_VAR_aws_region}" \
  -var="name=${TF_VAR_name}" \
  -var="keycloak_database_username=${KEYCLOAK_DB_USERNAME}"
```

**Assertions:**
- `terraform apply` completes with exit code 0.
- No errors related to RDS cluster modification.
- The ECS task definition is updated.

### 4.3 Helm / EKS Wiring

**Purpose:** Verify that no Helm chart changes are needed (Keycloak is deployed via ECS, not EKS).

```bash
grep -r "keycloak" charts/ --include='*.yaml' | grep -v "KEYCLOAK_ADMIN\|KEYCLOAK_URL\|KEYCLOAK_REALM\|KEYCLOAK_CLIENT" || echo "PASS: No Helm chart references to Keycloak DB"
```

**Assertion:** Helm charts do not reference the Keycloak database password or IAM configuration. If they do (e.g., via `extraEnv`), they need updating.

**Not Applicable** -- The Keycloak service is managed by Terraform/ECS, not by Helm charts. Helm charts reference Keycloak as an external IdP but do not configure its database.

### 4.4 Deploy and Verify

**Purpose:** End-to-end deployment verification.

```bash
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
- ECS service status is `ACTIVE`.
- All tasks are `RUNNING`.
- Health checks pass (check CloudWatch Logs for health endpoint responses).

### 4.5 Rollback Verification

**Purpose:** Verify that the changes can be rolled back if something goes wrong.

```bash
# Revert Terraform to the previous state (password-based auth)
# This requires having a backup of the previous Terraform state
terraform plan -var="aws_region=${TF_VAR_aws_region}" \
  -var="name=${TF_VAR_name}" \
  -var="keycloak_database_username=${KEYCLOAK_DB_USERNAME}" \
  -var="keycloak_database_password=<backup-password>" \
  -out=tfplan-rollback
terraform apply -auto-approve tfplan-rollback
aws ecs update-service \
  --cluster keycloak \
  --service keycloak \
  --task-definition keycloak:<previous-revision> \
  --force-new-deployment
```

**Assertions:**
- The rollback `terraform plan` succeeds.
- The ECS service stabilizes with the previous task definition.
- Keycloak connects to the database using password auth.

## 5. End-to-End API Tests

### 5.1 Full Keycloak Login Flow with IAM Auth

**Purpose:** Verify that end users can authenticate through Keycloak after the IAM auth migration.

```bash
TOKEN_RESPONSE=$(curl -s -X POST "http://localhost:8080/realms/mcp-gateway/protocol/openid-connect/token" \
  -d "grant_type=password&username=testuser&password=testpassword&client_id=mcp-gateway-web")
ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.access_token')
REGISTRY_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "http://localhost:8888/api/v1/servers")
echo "Registry API status: ${REGISTRY_RESPONSE}"
```

**Expected Output:**
- `TOKEN_RESPONSE` contains a valid `access_token`.
- `REGISTRY_RESPONSE` is `200`.

**Assertions:**
- The access token is valid and not expired.
- The registry API accepts the token and returns a response.
- No database connection errors in the Keycloak CloudWatch logs.

### 5.2 Token Refresh Under Load

**Purpose:** Verify that Keycloak handles concurrent connections with IAM auth.

```bash
for i in $(seq 1 10); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST "http://localhost:8080/realms/mcp-gateway/protocol/openid-connect/token" \
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

## 6. Test Execution Checklist

- [ ] Section 1 (Functional): All 8 tests pass
  - [ ] 1.1 Terraform plan validation
  - [ ] 1.2 RDS cluster IAM auth verification
  - [ ] 1.3 MySQL user IAM authentication verification
  - [ ] 1.4 RDS proxy IAM auth verification
  - [ ] 1.5 ECS task IAM policy verification
  - [ ] 1.6 ECS task definition verification
  - [ ] 1.7 Secrets Manager secret removal verification
  - [ ] 1.8 Rotation Lambda removal verification
- [ ] Section 2 (Backwards Compat): All 3 tests pass or marked Not Applicable
  - [ ] 2.1 ECS task role SSM access
  - [ ] 2.2 Keycloak admin credentials
  - [ ] 2.3 Other Terraform resources unaffected
- [ ] Section 3 (UX): All tests pass or marked Not Applicable
  - [ ] 3.1 CLI output clarity
  - [ ] 3.2 Error message clarity
- [ ] Section 4 (Deployment): All 5 tests pass
  - [ ] 4.1 Docker compose wiring
  - [ ] 4.2 Terraform / ECS wiring
  - [ ] 4.3 Helm / EKS wiring
  - [ ] 4.4 Deploy and verify
  - [ ] 4.5 Rollback verification
- [ ] Section 5 (E2E): All 3 tests pass
  - [ ] 5.1 Full Keycloak login flow
  - [ ] 5.2 Token refresh under load
  - [ ] 5.3 RDS proxy connection pooling
- [ ] Unit tests: No Python unit tests needed (infrastructure-only change)
- [ ] Integration tests: The Terraform plan validation (1.1) serves as the integration test
- [ ] `terraform plan` passes with no unexpected changes