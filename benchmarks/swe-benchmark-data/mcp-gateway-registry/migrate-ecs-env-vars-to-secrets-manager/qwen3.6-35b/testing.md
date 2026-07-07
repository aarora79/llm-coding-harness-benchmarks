# Testing Plan: Migrate ECS Environment Variables to AWS Secrets Manager

*Created: 2026-07-06*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This testing plan verifies that migrating sensitive environment variables from ECS `environment` blocks to the ECS `secrets` block does not break existing functionality, maintains backwards compatibility, and properly secures all sensitive values through AWS Secrets Manager. Tests cover Terraform validation, ECS task definition inspection, deployment surface verification, and security validation.

### Prerequisites
- [ ] Terraform CLI installed (>= 1.5)
- [ ] AWS credentials configured with IAM permissions for: secretsmanager, iam, ecs, kms
- [ ] Target AWS account with existing mcp-gateway-registry infrastructure
- [ ] Access to CloudTrail logs for audit verification
- [ ] Docker Compose installed (for Docker Compose surface testing)

### Shared Variables
```bash
export TF_VAR_name="mcp-gateway-test"
export TF_VAR_domain_name="test.example.com"
export TF_VAR_keycloak_domain="kc.example.com"
export TF_VAR_documentdb_endpoint="cluster-abc123.cluster-abc123.us-east-1.docdb.amazonaws.com"
export TF_VAR_documentdb_credentials_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:docdb-creds-ABC123"
export TF_VAR_environments="dev"
export TF_VAR_vpc_id="vpc-12345678"
export TF_VAR_private_subnet_ids='["subnet-aaa","subnet-bbb"]'
export TF_VAR_public_subnet_ids='["subnet-ccc","subnet-ddd"]'
export TF_VAR_ecs_cluster_arn="arn:aws:ecs:us-east-1:123456789012:cluster/mcp-gateway"
export TF_VAR_ecs_cluster_name="mcp-gateway"
export TF_VAR_alb_logs_bucket="mcp-gateway-alb-logs"
export KEYCLOAK_ADMIN_PASSWORD="test-admin-password-123"
export AUTH0_CLIENT_SECRET="test-auth0-secret"
export AUTH0_M2M_CLIENT_SECRET="test-auth0-m2m-secret"
export AUTH0_MANAGEMENT_API_TOKEN="test-auth0-mgmt-token"
export OKTA_CLIENT_SECRET="test-okta-secret"
export OKTA_M2M_CLIENT_SECRET="test-okta-m2m-secret"
export OKTA_API_TOKEN="test-okta-api-token"
export ENTRA_CLIENT_SECRET="test-entra-secret"
export SECRET_KEY="test-secret-key-for-local-development-only"
export EMBEDDINGS_API_KEY="test-embeddings-key"
export REGISTRY_API_TOKEN="test-registry-api-token"
export REGISTRY_API_KEYS="{}"
export FEDERATION_STATIC_TOKEN="test-federation-token"
export FEDERATION_ENCRYPTION_KEY="test-fernet-encryption-key"
export REGISTRATION_WEBHOOK_AUTH_TOKEN="test-webhook-token"
export REGISTRATION_GATE_AUTH_CREDENTIAL="test-gate-credential"
export REGISTRATION_GATE_OAUTH2_CLIENT_SECRET="test-gate-oauth2-secret"
export ANS_API_KEY="test-ans-api-key"
export ANS_API_SECRET="test-ans-api-secret"
export GITHUB_PAT="test-github-pat"
export GITHUB_APP_PRIVATE_KEY="<placeholder-test-private-key>"
export GRAFANA_ADMIN_PASSWORD="test-grafana-password-123"
export METRICS_API_KEY_AUTH="test-metrics-key"
export METRICS_API_KEY_REGISTRY="test-metrics-key"
```

## 1. Functional Tests

### 1.1 Terraform Validation Tests

#### 1.1.1 terraform validate

**Command:**
```bash
cd terraform/aws-ecs
terraform init -backend=false
terraform validate
```

**Expected Result:**
- Exit code 0
- Output: "Success! The configuration is valid."

**Assertions:**
- No syntax errors
- No missing required variables
- No type mismatches

#### 1.1.2 terraform plan - Dry Run

**Command:**
```bash
cd terraform/aws-ecs
terraform plan \
  -var="name=${TF_VAR_name}" \
  -var="domain_name=${TF_VAR_domain_name}" \
  -var="keycloak_domain=${TF_VAR_keycloak_domain}" \
  -var="documentdb_endpoint=${TF_VAR_documentdb_endpoint}" \
  -var="documentdb_credentials_secret_arn=${TF_VAR_documentdb_credentials_secret_arn}" \
  -var="keycloak_admin_password=${KEYCLOAK_ADMIN_PASSWORD}" \
  -var="auth0_enabled=true" \
  -var="auth0_client_secret=${AUTH0_CLIENT_SECRET}" \
  -var="auth0_m2m_client_secret=${AUTH0_M2M_CLIENT_SECRET}" \
  -var="auth0_management_api_token=${AUTH0_MANAGEMENT_API_TOKEN}" \
  -var="okta_enabled=true" \
  -var="okta_client_secret=${OKTA_CLIENT_SECRET}" \
  -var="okta_m2m_client_secret=${OKTA_M2M_CLIENT_SECRET}" \
  -var="okta_api_token=${OKTA_API_TOKEN}" \
  -var="entra_enabled=true" \
  -var="entra_client_secret=${ENTRA_CLIENT_SECRET}" \
  -var="secret_key=${SECRET_KEY}" \
  -var="embeddings_api_key=${EMBEDDINGS_API_KEY}" \
  -var="registry_api_token=${REGISTRY_API_TOKEN}" \
  -var="registry_api_keys=${REGISTRY_API_KEYS}" \
  -var="federation_static_token=${FEDERATION_STATIC_TOKEN}" \
  -var="federation_encryption_key=${FEDERATION_ENCRYPTION_KEY}" \
  -var="registration_webhook_auth_token=${REGISTRATION_WEBHOOK_AUTH_TOKEN}" \
  -var="registration_gate_auth_credential=${REGISTRATION_GATE_AUTH_CREDENTIAL}" \
  -var="registration_gate_oauth2_client_secret=${REGISTRATION_GATE_OAUTH2_CLIENT_SECRET}" \
  -var="ans_api_key=${ANS_API_KEY}" \
  -var="ans_api_secret=${ANS_API_SECRET}" \
  -var="github_pat=${GITHUB_PAT}" \
  -var="github_app_private_key=${GITHUB_APP_PRIVATE_KEY}" \
  -var="grafana_admin_password=${GRAFANA_ADMIN_PASSWORD}" \
  -var="enable_observability=true" \
  -var="vpc_id=${TF_VAR_vpc_id}" \
  -var="private_subnet_ids=${TF_VAR_private_subnet_ids}" \
  -var="public_subnet_ids=${TF_VAR_public_subnet_ids}" \
  -var="ecs_cluster_arn=${TF_VAR_ecs_cluster_arn}" \
  -var="ecs_cluster_name=${TF_VAR_ecs_cluster_name}" \
  -var="alb_logs_bucket=${TF_VAR_alb_logs_bucket}" \
  -out=tfplan
```

**Expected Result:**
- Exit code 0
- Plan shows only new resources being created (no existing resources modified or destroyed)

**Assertions:**
```bash
# Verify no existing resources are being destroyed
terraform plan -out=tfplan | grep -c "Destroy" || true
# Expected: 0

# Verify new secrets are being created
terraform plan -out=tfplan | grep -c "aws_secretsmanager_secret"
# Expected: 14 (one per new secret)

# Verify new random_password is being created
terraform plan -out=tfplan | grep -c "random_password.grafana_admin_password"
# Expected: 1

# Verify ECS task definitions show environment variable removals
terraform plan -out=tfplan | grep -c "environment.*-=.*"
# Expected: 25+ (auth-server + registry combined)

# Verify ECS task definitions show secrets block additions
terraform plan -out=tfplan | grep -c "secrets.*+="
# Expected: 25+ (auth-server + registry combined)

# Verify IAM policy is being updated
terraform plan -out=tfplan | grep -c "aws_iam_policy.ecs_secrets_access"
# Expected: 1 (updated, not replaced)
```

#### 1.1.3 terraform plan - Detailed Exit Code

**Command:**
```bash
terraform plan -out=tfplan -detailed-exitcode
echo $?
```

**Expected Result:**
- Exit code 0: No changes (if secrets already exist from a prior run)
- Exit code 1: Error (configuration issue)
- Exit code 2: Change detected (expected for first run)

**Assertions:**
- Exit code is 0 or 2 (never 1)

### 1.2 ECS Task Definition Inspection Tests

#### 1.2.1 Verify No Plaintext Secrets in Task Definition Environment

After applying the plan and inspecting the task definition:

**Command:**
```bash
TASK_FAMILY=$(aws ecs describe-task-definition \
  --task-family "${TF_VAR_name}-registry" \
  --query 'taskDefinition.taskDefinitionArn' --output text)

aws ecs describe-task-definition \
  --task-definition "${TASK_FAMILY}" \
  --query 'taskDefinition.containerDefinitions[0].environment' \
  --output json | jq '.[] | select(.name | contains("_SECRET") or contains("_TOKEN") or contains("_PASSWORD") or contains("_API_KEY") or contains("_API_SECRET") or contains("_CREDENTIAL") or contains("_PRIVATE_KEY"))'
```

**Expected Result:**
- Empty output (no secret-containing environment variables in the `environment` block)

**Assertions:**
- `jq` returns no results (empty output)
- Variables like `REGISTRY_API_TOKEN`, `FEDERATION_STATIC_TOKEN`, `GITHUB_PAT` are NOT in the environment block

#### 1.2.2 Verify Secrets Are in the ECS Secrets Block

**Command:**
```bash
aws ecs describe-task-definition \
  --task-family "${TASK_FAMILY}" \
  --query 'taskDefinition.containerDefinitions[0].secrets' \
  --output json | jq '.[].name' | sort
```

**Expected Result:**
All expected secret names present:

```
ANS_API_KEY
ANS_API_SECRET
AUTH0_CLIENT_SECRET
AUTH0_MANAGEMENT_API_TOKEN
AUTH0_M2M_CLIENT_SECRET
EMBEDDINGS_API_KEY
ENTRA_CLIENT_SECRET
FEDERATION_ENCRYPTION_KEY
FEDERATION_STATIC_TOKEN
GITHUB_APP_PRIVATE_KEY
GITHUB_PAT
KEYCLOAK_ADMIN_PASSWORD
KEYCLOAK_CLIENT_SECRET
KEYCLOAK_M2M_CLIENT_SECRET
METRICS_API_KEY
OKTA_API_TOKEN
OKTA_CLIENT_SECRET
OKTA_M2M_CLIENT_SECRET
REGISTRATION_GATE_AUTH_CREDENTIAL
REGISTRATION_GATE_OAUTH2_CLIENT_SECRET
REGISTRATION_WEBHOOK_AUTH_TOKEN
REGISTRY_API_KEYS
REGISTRY_API_TOKEN
SECRET_KEY
```

**Assertions:**
- Count >= 24 secrets in the registry container
- Each secret's `valueFrom` is a valid Secrets Manager ARN (matches `arn:aws:secretsmanager:...`)

#### 1.2.3 Verify Auth Server Secrets

**Command:**
```bash
AUTH_TASK_FAMILY=$(aws ecs describe-task-definition \
  --task-family "${TF_VAR_name}-auth-server" \
  --query 'taskDefinition.taskDefinitionArn' --output text)

aws ecs describe-task-definition \
  --task-definition "${AUTH_TASK_FAMILY}" \
  --query 'taskDefinition.containerDefinitions[0].secrets' \
  --output json | jq '.[].name' | sort
```

**Expected Result:**
All expected secret names present for auth server:

```
ANS_API_KEY
ANS_API_SECRET
AUTH0_CLIENT_SECRET
AUTH0_MANAGEMENT_API_TOKEN
AUTH0_M2M_CLIENT_SECRET
EMBEDDINGS_API_KEY
ENTRA_CLIENT_SECRET
FEDERATION_ENCRYPTION_KEY
FEDERATION_STATIC_TOKEN
KEYCLOAK_ADMIN_PASSWORD
KEYCLOAK_CLIENT_SECRET
KEYCLOAK_M2M_CLIENT_SECRET
METRICS_API_KEY
OKTA_API_TOKEN
OKTA_CLIENT_SECRET
OKTA_M2M_CLIENT_SECRET
REGISTRY_API_KEYS
REGISTRY_API_TOKEN
SECRET_KEY
```

**Assertions:**
- Count >= 19 secrets in the auth-server container
- Each secret's `valueFrom` is a valid Secrets Manager ARN

#### 1.2.4 Verify Non-Secret Variables Remain in Environment Block

**Command:**
```bash
aws ecs describe-task-definition \
  --task-family "${TASK_FAMILY}" \
  --query 'taskDefinition.containerDefinitions[0].environment' \
  --output json | jq '.[].name' | sort
```

**Expected Result:**
Non-sensitive variables like `REGISTRY_URL`, `BIND_HOST`, `AUTH_PROVIDER`, `KEYCLOAK_URL`, `ENTRA_TENANT_ID`, etc. remain in the `environment` block.

**Assertions:**
- `REGISTRY_URL` is present
- `KEYCLOAK_URL` is present
- `ENTRA_TENANT_ID` is present
- `DEPLOYMENT_MODE` is present
- `REGISTRY_MODE` is present

### 1.3 IAM Policy Verification

#### 1.3.1 Verify ECS Secrets Access Policy Contains New ARNs

**Command:**
```bash
POLICY_ARN=$(aws iam list-policies \
  --scope Local \
  --query "Policies[?starts_with(PolicyName, '${TF_VAR_name}-ecs-secrets-')].Arn" \
  --output text)

aws iam get-policy-version \
  --policy-arn "${POLICY_ARN}" \
  --version-id $(aws iam list-policy-versions \
    --policy-arn "${POLICY_ARN}" \
    --query 'PolicyVersions[?IsDefaultVersion==`true`].VersionId' \
    --output text) \
  --query 'PolicyVersion.Document.Statement[0].Resource[*]' \
  --output json | jq '.[] | select(contains("secretsmanager"))' | wc -l
```

**Expected Result:**
- Count of secret ARNs >= 28 (all new + existing)

**Assertions:**
- All new secret ARNs present:
  ```bash
  # Check for new secrets
  aws iam get-policy-version --policy-arn "${POLICY_ARN}" ... \
    | grep -c "auth0-mgmt-api-token"
  # Expected: 1
  
  aws iam get-policy-version --policy-arn "${POLICY_ARN}" ... \
    | grep -c "registry-api-token"
  # Expected: 1
  
  aws iam get-policy-version --policy-arn "${POLICY_ARN}" ... \
    | grep -c "github-pat"
  # Expected: 1
  ```

#### 1.3.2 Verify KMS Decrypt Permission

**Command:**
```bash
aws iam get-policy-version \
  --policy-arn "${POLICY_ARN}" \
  --version-id ... \
  --query 'PolicyVersion.Document.Statement[1].Action' \
  --output json
```

**Expected Result:**
```json
["kms:Decrypt", "kms:DescribeKey"]
```

### 1.4 Variable Sensitivity Verification

#### 1.4.1 Verify Sensitive Variables Are Marked

**Command:**
```bash
cd terraform/aws-ecs/modules/mcp-gateway
grep -A3 'variable "auth0_management_api_token"' variables.tf | grep "sensitive"
```

**Expected Result:**
```
  sensitive   = true
```

**Assertions:**
- `auth0_management_api_token` has `sensitive = true`

#### 1.4.2 Verify No Secrets in terraform output

**Command:**
```bash
terraform output -json 2>/dev/null | jq 'to_entries[] | select(.value | tostring | test("_SECRET|_TOKEN|_PASSWORD|_API_KEY|_PRIVATE_KEY"))'
```

**Expected Result:**
- Empty output (no sensitive values in terraform output)

## 2. Backwards Compatibility Tests

### 2.1 Existing Deployments Without New Variables

**Scenario:** An existing deployment that was created before this change and does not have the new variables set.

**Command:**
```bash
# Use minimal variables (existing deployment scenario)
terraform plan \
  -var="name=mcp-gateway-existing" \
  -var="keycloak_domain=kc.example.com" \
  -var="documentdb_endpoint=..." \
  -var="documentdb_credentials_secret_arn=..." \
  -var="keycloak_admin_password=test" \
  -var="auth0_enabled=false" \
  -var="okta_enabled=false" \
  -var="entra_enabled=false" \
  -var="enable_observability=false" \
  -var="secret_key=test-secret" \
  -var="vpc_id=${TF_VAR_vpc_id}" \
  -var="private_subnet_ids=${TF_VAR_private_subnet_ids}" \
  -var="public_subnet_ids=${TF_VAR_public_subnet_ids}" \
  -var="ecs_cluster_arn=${TF_VAR_ecs_cluster_arn}" \
  -var="ecs_cluster_name=${TF_VAR_ecs_cluster_name}" \
  -var="alb_logs_bucket=${TF_VAR_alb_logs_bucket}"
```

**Expected Result:**
- Plan succeeds with no errors
- Default values are used for all new variables (empty strings)
- New secrets are created with empty or placeholder values

**Assertions:**
- Exit code 0
- No "required variable not set" errors
- New secrets show "will be created" with empty secret_string

### 2.2 Existing Deployments With Partial New Variables

**Scenario:** A deployment where some new variables are set (e.g., only Okta is enabled) but others are not.

**Command:**
```bash
terraform plan \
  -var="name=mcp-gateway-partial" \
  -var="okta_enabled=true" \
  -var="okta_client_secret=test-okta-secret" \
  -var="okta_m2m_client_secret=test-okta-m2m-secret" \
  -var="okta_api_token=test-okta-api-token" \
  -var="auth0_enabled=false" \
  -var="entra_enabled=false" \
  -var="enable_observability=false" \
  -var="secret_key=test-secret" \
  -var="vpc_id=${TF_VAR_vpc_id}" \
  -var="private_subnet_ids=${TF_VAR_private_subnet_ids}" \
  -var="public_subnet_ids=${TF_VAR_public_subnet_ids}" \
  -var="ecs_cluster_arn=${TF_VAR_ecs_cluster_arn}" \
  -var="ecs_cluster_name=${TF_VAR_ecs_cluster_name}" \
  -var="alb_logs_bucket=${TF_VAR_alb_logs_bucket}" \
  -var="keycloak_domain=kc.example.com" \
  -var="documentdb_endpoint=..." \
  -var="documentdb_credentials_secret_arn=..." \
  -var="keycloak_admin_password=test"
```

**Expected Result:**
- Plan succeeds
- Only Okta-related secrets are added to the ECS `secrets` block
- Non-Okta secrets are NOT added to the ECS `secrets` block

**Assertions:**
- Okta secrets are in the task definition: `OKTA_CLIENT_SECRET`, `OKTA_M2M_CLIENT_SECRET`, `OKTA_API_TOKEN`
- Auth0 secrets are NOT in the task definition

### 2.3 Verify Environment Variable Names Are Preserved

**Command:**
```bash
# After apply, verify the container still sees the same env var names
aws ecs describe-task-definition \
  --task-family "${TF_VAR_name}-registry" \
  --query 'taskDefinition.containerDefinitions[0].secrets[*].name' \
  --output json
```

**Expected Result:**
All env var names match the existing names (no renaming):
```json
["SECRET_KEY", "KEYCLOAK_CLIENT_SECRET", "OKTA_CLIENT_SECRET", ...]
```

**Assertions:**
- No new variable names introduced
- All previously-seen secret names are preserved

## 3. Deployment Surface Tests

### 3.1 Docker Compose - Not Applicable

**Not Applicable** - Docker Compose does not have native Secrets Manager integration. This migration only affects ECS Terraform deployment. Docker Compose secrets remain in .env files. A separate migration task should address Docker Compose.

### 3.2 Helm Charts - No Change Required

**Command:**
```bash
# Verify Helm charts still function
helm dependency update charts/mcp-gateway-registry-stack
helm template test-release charts/mcp-gateway-registry-stack \
  --set app.domainName=test.example.com \
  --set keycloak.enabled=true \
  --set keycloak.domain=kc.example.com \
  --set keycloak.adminPassword=test-admin-password \
  > /dev/null
```

**Expected Result:**
- Helm template succeeds (no changes needed to Helm charts)
- Kubernetes Secrets still contain the sensitive values
- No regression in Helm deployment surface

### 3.3 Terraform State - Secret ARNs Are Stated

**Command:**
```bash
terraform state list | grep "aws_secretsmanager_secret" | sort
```

**Expected Result:**
All new secrets appear in state:
```
aws_secretsmanager_secret.ans_api_key
aws_secretsmanager_secret.ans_api_secret
aws_secretsmanager_secret.auth0_management_api_token
aws_secretsmanager_secret.federation_encryption_key
aws_secretsmanager_secret.federation_static_token
aws_secretsmanager_secret.github_app_private_key
aws_secretsmanager_secret.github_pat
aws_secretsmanager_secret.grafana_admin_password
aws_secretsmanager_secret.registration_gate_auth_credential
aws_secretsmanager_secret.registration_gate_oauth2_client_secret
aws_secretsmanager_secret.registration_webhook_auth_token
aws_secretsmanager_secret.registry_api_keys
aws_secretsmanager_secret.registry_api_token
```

**Assertions:**
- Count >= 14 new secret resources in state

### 3.4 Terraform State - No Secrets Leaked

**Command:**
```bash
terraform state show 'aws_secretsmanager_secret_version.*' | grep -E "secret_string.*[A-Za-z0-9]" || echo "NO_LEAK"
```

**Expected Result:**
- Either "NO_LEAK" or secret_string values are not displayed (Terraform may redact sensitive values)

**Assertions:**
- No plaintext secrets in `terraform state show` output for sensitive variables

## 4. Security Tests

### 4.1 Verify No Plaintext Secrets in terraform plan Output

**Command:**
```bash
terraform plan 2>&1 | grep -iE "SECRET_KEY.*=.*[A-Za-z0-9]{10}|REGISTRY_API_TOKEN.*=.*[A-Za-z0-9]{10}|GITHUB_PAT.*=.*ghp_[A-Za-z0-9]{36}" || echo "NO_PLAINTEXT_SECRETS"
```

**Expected Result:**
- "NO_PLAINTEXT_SECRETS" (no sensitive values visible in plan output)

### 4.2 Verify KMS Key Encryption

**Command:**
```bash
# Check that all new secrets use the same KMS key as existing secrets
aws secretsmanager list-secrets \
  --filters "Key=name,Values=${TF_VAR_name}-*" \
  --query 'SecretList[].KmsKeyId' \
  --output text | sort -u
```

**Expected Result:**
- Single KMS key ID (all secrets encrypted with the same key)

### 4.3 Verify Secret Recovery Window

**Command:**
```bash
aws secretsmanager list-secrets \
  --filters "Key=name,Values=${TF_VAR_name}-*" \
  --query 'SecretList[].Name' \
  --output text | while read secret_name; do
  aws secretsmanager describe-secret --secret-id "$secret_name" \
    --query 'RecoveryWindowInDays' --output json
done | sort -u
```

**Expected Result:**
- All secrets have `RecoveryWindowInDays: 0` (immediately deletable, no recovery window)

**Assertions:**
- Recovery window is 0 for all new secrets (as designed)

### 4.4 IAM Least Privilege - ECS Task Role Cannot Create Secrets

**Command:**
```bash
TASK_ROLE_ARN=$(aws ecs describe-task-definition \
  --task-family "${TF_VAR_name}-registry" \
  --query 'taskDefinition.executionRoleArn' --output text)

# Get the IAM policy and verify it only has GetSecretValue, not PutSecretValue
aws iam get-policy-version \
  --policy-arn "${POLICY_ARN}" \
  --version-id ... \
  --query 'PolicyVersion.Document.Statement[0].Action' \
  --output json
```

**Expected Result:**
```json
["secretsmanager:GetSecretValue"]
```

**Assertions:**
- Only `GetSecretValue` is present (no `PutSecretValue`, `CreateSecret`, `DeleteSecret`)

### 4.5 CloudTrail Audit Verification

**Command:**
```bash
# After applying and launching a task, verify CloudTrail logs
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=ResourceName,ResourceValue="${TF_VAR_name}-*" \
  --event-time-greater-than-value $(date -u -v-1H +%Y-%m-%dT%H:%M:%S) \
  --max-items 10 \
  --query 'Events[].EventName' \
  --output text | sort -u
```

**Expected Result:**
- `GetSecretValue` events appear in CloudTrail for the new secrets

**Assertions:**
- At least 5 `GetSecretValue` events logged (one per unique secret accessed during test)

## 5. End-to-End API Tests

### 5.1 Full Deployment and Health Check

**Command:**
```bash
# Apply the Terraform changes
terraform apply -auto-approve -var-file="test.tfvars"

# Wait for ECS services to become healthy
for i in {1..60}; do
  HEALTH=$(aws ecs describe-services \
    --cluster "${TF_VAR_name}" \
    --services "${TF_VAR_name}-registry" "${TF_VAR_name}-auth-server" \
    --query 'services[*].status' --output text)
  if [[ "$HEALTH" == "ACTIVE"* ]]; then
    echo "Services are ACTIVE"
    break
  fi
  sleep 10
done

# Check health endpoints
curl -sf "https://${TF_VAR_domain_name}/health" && echo "Registry health OK"
curl -sf "https://${TF_VAR_domain_name}/auth/health" && echo "Auth server health OK"
```

**Expected Result:**
- All ECS services reach ACTIVE status
- Health endpoints return HTTP 200

**Assertions:**
- `echo "Registry health OK"` prints successfully
- `echo "Auth server health OK"` prints successfully

### 5.2 Authentication Flow Test

**Command:**
```bash
# Verify that Keycloak authentication still works after the secret migration
curl -sf -X POST "https://${TF_VAR_domain_name}/auth/realms/mcp-gateway/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=mcp-gateway-web" \
  -d "client_secret=${KEYCLOAK_CLIENT_SECRET}" \
  -d "username=admin" \
  -d "password=${KEYCLOAK_ADMIN_PASSWORD}" \
  -o /tmp/auth-response.json

cat /tmp/auth-response.json | jq '.access_token' | head -1
```

**Expected Result:**
- JSON response with `access_token` field
- Token is a valid JWT (three base64 segments)

**Assertions:**
- Exit code 0
- `access_token` is present and non-empty

### 5.3 Federation Token Test (if enabled)

**Command:**
```bash
# Verify that federation-related services work with secrets-based tokens
curl -sf "https://${TF_VAR_domain_name}/api/federation/status" \
  -H "Authorization: Bearer ${FEDERATION_STATIC_TOKEN}" \
  -o /tmp/federation-status.json

cat /tmp/federation-status.json | jq '.status'
```

**Expected Result:**
- JSON response with `status` field
- No authentication errors

**Assertions:**
- Exit code 0
- Response does not contain "Unauthorized" or "Forbidden"

### 5.4 Registration Webhook Test (if enabled)

**Command:**
```bash
# Verify that the registration webhook uses the secrets-based auth token
curl -sf -X POST "https://${TF_VAR_domain_name}/api/webhook/test" \
  -H "Authorization: Bearer ${REGISTRATION_WEBHOOK_AUTH_TOKEN}" \
  -o /tmp/webhook-response.json

cat /tmp/webhook-response.json | jq '.ok'
```

**Expected Result:**
- JSON response with `ok: true` field (or expected webhook response structure)

**Assertions:**
- Exit code 0
- No "Unauthorized" in response

## 6. Test Execution Checklist

- [ ] Section 1.1 (Terraform validate) passes
- [ ] Section 1.2 (Terraform plan dry run) produces expected changes
- [ ] Section 1.3 (IAM policy verification) shows all new ARNs
- [ ] Section 1.4 (Variable sensitivity) confirms `sensitive = true`
- [ ] Section 2.1 (Backwards compat - no new vars) succeeds
- [ ] Section 2.2 (Backwards compat - partial vars) succeeds
- [ ] Section 2.3 (Env var names preserved) verified
- [ ] Section 3.1 (Docker Compose) marked Not Applicable
- [ ] Section 3.2 (Helm charts) no regression
- [ ] Section 3.3 (Terraform state) shows all new secrets
- [ ] Section 3.4 (No secrets in terraform state output) verified
- [ ] Section 4.1 (No plaintext secrets in terraform plan) verified
- [ ] Section 4.2 (KMS encryption) verified
- [ ] Section 4.3 (Recovery window) verified
- [ ] Section 4.4 (IAM least privilege) verified
- [ ] Section 4.5 (CloudTrail audit) verified
- [ ] Section 5.1 (Full deployment and health check) passes
- [ ] Section 5.2 (Authentication flow) passes
- [ ] Section 5.3 (Federation token) verified (or N/A)
- [ ] Section 5.4 (Registration webhook) verified (or N/A)
- [ ] Unit tests added under `tests/unit/` for any new Terraform module functions
- [ ] Integration tests added under `tests/integration/` for ECS task definition validation
- [ ] `uv run pytest tests/` passes with no regressions