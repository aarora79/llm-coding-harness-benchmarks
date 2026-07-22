# Testing Plan: Migrate ECS Environment Variables to AWS Secrets Manager

*Created: 2026-07-22*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This plan verifies that migrating sensitive environment variables from ECS `environment` blocks to the ECS `secrets` block works correctly, maintains backwards compatibility, and properly secures all sensitive values through AWS Secrets Manager with rotation scaffolding and cross-account access support.

### Prerequisites
- [ ] Terraform CLI installed (>= 1.5)
- [ ] AWS credentials with: secretsmanager, iam, ecs, kms
- [ ] Target AWS account with existing mcp-gateway-registry infrastructure
- [ ] Docker Compose installed (for surface testing)
- [ ] jq installed (for JSON parsing)

### Shared Variables
```bash
export TF_VAR_name="mcp-gateway-test"
export TF_VAR_domain_name="test.example.com"
export TF_VAR_keycloak_domain="kc.example.com"
export TF_VAR_documentdb_endpoint="cluster-abc123.cluster-abc123.us-east-1.docdb.amazonaws.com"
export TF_VAR_documentdb_credentials_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:docdb-creds-ABC123"
export TF_VAR_vpc_id="vpc-12345678"
export TF_VAR_private_subnet_ids='["subnet-aaa","subnet-bbb"]'
export TF_VAR_public_subnet_ids='["subnet-ccc","subnet-ddd"]'
export TF_VAR_ecs_cluster_arn="arn:aws:ecs:us-east-1:123456789012:cluster/mcp-gateway"
export TF_VAR_ecs_cluster_name="mcp-gateway"
export TF_VAR_alb_logs_bucket="mcp-gateway-alb-logs"
export SECRET_KEY="test-secret-key-for-local-development-only"
export KEYCLOAK_ADMIN_PASSWORD="test-admin-password-123"
```

## 1. Functional Tests

### 1.1 Terraform Validation

#### 1.1.1 terraform validate

**Command:**
```bash
cd terraform/aws-ecs
terraform init -backend=false
terraform validate
```
**Expected:** Exit code 0, "Success! The configuration is valid."

#### 1.1.2 terraform plan - Dry Run with enable_secrets_manager = true

**Command:**
```bash
cd terraform/aws-ecs
terraform plan \
  -var="name=${TF_VAR_name}" \
  -var="keycloak_domain=${TF_VAR_keycloak_domain}" \
  -var="documentdb_endpoint=${TF_VAR_documentdb_endpoint}" \
  -var="documentdb_credentials_secret_arn=${TF_VAR_documentdb_credentials_secret_arn}" \
  -var="keycloak_admin_password=${KEYCLOAK_ADMIN_PASSWORD}" \
  -var="auth0_enabled=true" -var="auth0_client_secret=test" \
    -var="auth0_m2m_client_secret=test" -var="auth0_management_api_token=test" \
  -var="okta_enabled=true" -var="okta_client_secret=test" \
    -var="okta_m2m_client_secret=test" -var="okta_api_token=test" \
  -var="entra_enabled=true" -var="entra_client_secret=test" \
  -var="secret_key=${SECRET_KEY}" \
  -var="registry_api_token=test-token" \
  -var="federation_static_token=test-token" \
  -var="federation_encryption_key=test-key" \
  -var="ans_api_key=test-key" -var="ans_api_secret=test-secret" \
  -var="github_pat=test-pat" -var="github_app_private_key=test-key" \
  -var="grafana_admin_password=test-password" \
  -var="enable_observability=true" \
  -var="enable_secrets_manager=true" \
  -var="vpc_id=${TF_VAR_vpc_id}" \
  -var="private_subnet_ids=${TF_VAR_private_subnet_ids}" \
  -var="public_subnet_ids=${TF_VAR_public_subnet_ids}" \
  -var="ecs_cluster_arn=${TF_VAR_ecs_cluster_arn}" \
  -var="ecs_cluster_name=${TF_VAR_ecs_cluster_name}" \
  -var="alb_logs_bucket=${TF_VAR_alb_logs_bucket}" \
  -out=tfplan
```

**Expected:** Exit code 0. Plan shows only new resources created (no existing resources modified/destroyed).

**Assertions:**
```bash
# No existing resources destroyed
terraform plan -out=tfplan | grep -c "Destroy" || true
# Expected: 0

# New secret resources created (14: 13 secrets + 1 random_password)
terraform plan -out=tfplan | grep -c "aws_secretsmanager_secret\."
# Expected: 14

# Random password created
terraform plan -out=tfplan | grep -c "random_password.grafana_admin_password"
# Expected: 1

# IAM policy updated (not replaced)
terraform plan -out=tfplan | grep -c "aws_iam_policy.ecs_secrets_access"
# Expected: 1

# New variables detected
terraform plan -out=tfplan | grep -c "enable_secrets_manager"
# Expected: at least 1 (in locals.tf shared_secrets)
```

#### 1.1.3 terraform plan -detailed-exitcode

**Expected:** Exit code 0 (no changes after apply) or 2 (changes detected on first run). Never 1 (error).

### 1.2 enable_secrets_manager Toggle Tests

#### 1.2.1 enable_secrets_manager = true: Secrets in Task Definition

**Command:**
```bash
cd terraform/aws-ecs
terraform plan \
  -var="enable_secrets_manager=true" \
  -var="name=${TF_VAR_name}" \
  -var="keycloak_domain=${TF_VAR_keycloak_domain}" \
  -var="documentdb_endpoint=${TF_VAR_documentdb_endpoint}" \
  -var="documentdb_credentials_secret_arn=${TF_VAR_documentdb_credentials_secret_arn}" \
  -var="keycloak_admin_password=${KEYCLOAK_ADMIN_PASSWORD}" \
  -var="secret_key=${SECRET_KEY}" \
  -var="vpc_id=${TF_VAR_vpc_id}" \
  -var="private_subnet_ids=${TF_VAR_private_subnet_ids}" \
  -var="public_subnet_ids=${TF_VAR_public_subnet_ids}" \
  -var="ecs_cluster_arn=${TF_VAR_ecs_cluster_arn}" \
  -var="ecs_cluster_name=${TF_VAR_ecs_cluster_name}" \
  -var="alb_logs_bucket=${TF_VAR_alb_logs_bucket}" \
  -out=tfplan
```

**Expected:** Task definitions include `secrets` block entries for all sensitive variables. The `shared_secrets` local expands to include REGISTRY_API_TOKEN, FEDERATION_STATIC_TOKEN, etc.

**Assertions:**
- Plan output shows `secrets` entries for REGISTRY_API_TOKEN, REGISTRY_API_KEYS, FEDERATION_STATIC_TOKEN, FEDERATION_ENCRYPTION_KEY, ANS_API_KEY, ANS_API_SECRET
- Plan output shows `secrets` entries for GITHUB_PAT, GITHUB_APP_PRIVATE_KEY, REGISTRATION_WEBHOOK_AUTH_TOKEN, REGISTRATION_GATE_AUTH_CREDENTIAL, REGISTRATION_GATE_OAUTH2_CLIENT_SECRET
- Plan output shows `secrets` entry for AUTH0_MANAGEMENT_API_TOKEN (only when auth0_enabled=true)

#### 1.2.2 enable_secrets_manager = false: Plaintext Fallback Only

**Command:** Same as 1.2.1 but with `-var="enable_secrets_manager=false"`

**Expected:** Task definitions do NOT include `secrets` block entries for the newly migrated secrets. All sensitive variables are passed via `environment` with fallback values. No new ECS task definition changes beyond secret resource creation.

**Assertions:**
- Plan output does NOT show new `secrets` entries in container definitions
- Plan output shows plaintext `environment` entries for REGISTRY_API_TOKEN, FEDERATION_STATIC_TOKEN, etc. (the fallback values)
- Secret resources are still created (they are infrastructure resources, not gated by enable_secrets_manager)

#### 1.2.3 Variable Toggle Propagates Through Root Module

**Command:** Verify that `enable_secrets_manager` and `secret_rotation_enabled` appear in the root module's `module "mcp_gateway"` block and `variables.tf`.

**Expected:** The root `terraform/aws-ecs/variables.tf` has pass-through variables with the same defaults, and `main.tf` passes them to the module.

### 1.3 ECS Task Definition Inspection

#### 1.3.1 No Plaintext Secrets in Registry Environment

**Command:**
```bash
aws ecs describe-task-definition \
  --task-family "mcp-gateway-test-registry" \
  --query 'taskDefinition.containerDefinitions[0].environment' \
  --output json | jq '.[] | select(.name | contains("_SECRET") or contains("_TOKEN") or contains("_PASSWORD") or contains("_API_KEY") or contains("_API_SECRET") or contains("_CREDENTIAL") or contains("_PRIVATE_KEY") or contains("_ENCRYPTION_KEY"))'
```
**Expected:** Empty output (when `enable_secrets_manager = true`).

#### 1.3.2 Registry Secrets Block Has All Expected Secrets

**Command:**
```bash
aws ecs describe-task-definition \
  --task-family "mcp-gateway-test-registry" \
  --query 'taskDefinition.containerDefinitions[0].secrets[*].name' \
  --output json | sort
```
**Expected (27+ secrets):** SECRET_KEY, KEYCLOAK_CLIENT_SECRET, KEYCLOAK_M2M_CLIENT_SECRET, KEYCLOAK_ADMIN_PASSWORD, EMBEDDINGS_API_KEY, DOCUMENTDB_USERNAME, DOCUMENTDB_PASSWORD, OKTA_CLIENT_SECRET, OKTA_M2M_CLIENT_SECRET, OKTA_API_TOKEN, AUTH0_CLIENT_SECRET, AUTH0_M2M_CLIENT_SECRET, AUTH0_MANAGEMENT_API_TOKEN, ENTRA_CLIENT_SECRET, REGISTRY_API_TOKEN, REGISTRY_API_KEYS, FEDERATION_STATIC_TOKEN, FEDERATION_ENCRYPTION_KEY, REGISTRATION_WEBHOOK_AUTH_TOKEN, REGISTRATION_GATE_AUTH_CREDENTIAL, REGISTRATION_GATE_OAUTH2_CLIENT_SECRET, ANS_API_KEY, ANS_API_SECRET, GITHUB_PAT, GITHUB_APP_PRIVATE_KEY, METRICS_API_KEY

**Assertions:**
- Each secret's `valueFrom` is a valid Secrets Manager ARN (`arn:aws:secretsmanager:...`)
- JSON-nested secrets (client_secret) use the `${arn}:client_secret::` format

#### 1.3.3 Auth Server Secrets Block

**Command:**
```bash
aws ecs describe-task-definition \
  --task-family "mcp-gateway-test-auth-server" \
  --query 'taskDefinition.containerDefinitions[0].secrets[*].name' \
  --output json | sort
```
**Expected (20+ secrets):** SECRET_KEY, KEYCLOAK_CLIENT_SECRET, KEYCLOAK_M2M_CLIENT_SECRET, DOCUMENTDB_USERNAME, DOCUMENTDB_PASSWORD, OKTA_CLIENT_SECRET, OKTA_M2M_CLIENT_SECRET, OKTA_API_TOKEN, AUTH0_CLIENT_SECRET, AUTH0_M2M_CLIENT_SECRET, AUTH0_MANAGEMENT_API_TOKEN, ENTRA_CLIENT_SECRET, REGISTRY_API_TOKEN, REGISTRY_API_KEYS, FEDERATION_STATIC_TOKEN, FEDERATION_ENCRYPTION_KEY, ANS_API_KEY, ANS_API_SECRET, METRICS_API_KEY

#### 1.3.4 Grafana Secrets

**Command:**
```bash
aws ecs describe-task-definition \
  --task-family "mcp-gateway-test-grafana" \
  --query 'taskDefinition.containerDefinitions[0].secrets' \
  --output json
```
**Expected:** `GF_SECURITY_ADMIN_PASSWORD` in the secrets block with a valid Secrets Manager ARN.

#### 1.3.5 Non-Secret Variables Remain in Environment

**Command:**
```bash
aws ecs describe-task-definition \
  --task-family "mcp-gateway-test-registry" \
  --query 'taskDefinition.containerDefinitions[0].environment[*].name' \
  --output json | jq 'sort'
```
**Expected:** `REGISTRY_URL`, `BIND_HOST`, `AUTH_PROVIDER`, `KEYCLOAK_URL`, `DEPLOYMENT_MODE`, `REGISTRY_MODE`, `OKTA_CLIENT_ID`, `AUTH0_CLIENT_ID`, `ENTRA_CLIENT_ID` are present.

### 1.4 IAM Policy Verification

#### 1.4.1 Policy Contains New ARNs

**Command:**
```bash
POLICY_ARN=$(aws iam list-policies --scope Local \
  --query "Policies[?starts_with(PolicyName, 'mcp-gateway-test-ecs-secrets-')].Arn" --output text)

aws iam get-policy-version \
  --policy-arn "${POLICY_ARN}" \
  --version-id "$(aws iam list-policy-versions --policy-arn "${POLICY_ARN}" \
    --query 'PolicyVersions[?IsDefaultVersion==`true`].VersionId' --output text)" \
  --query 'PolicyVersion.Document.Statement[0].Resource[*]' --output text | grep -c "secret:"
```
**Expected:** Count >= 28 secret ARNs.

#### 1.4.2 Only GetSecretValue (No Put/Delete)

**Command:**
```bash
aws iam get-policy-version \
  --policy-arn "${POLICY_ARN}" \
  --version-id ... \
  --query 'PolicyVersion.Document.Statement[0].Action' --output json
```
**Expected:** `["secretsmanager:GetSecretValue"]` only.

#### 1.4.3 KMS Decrypt Permission Present

**Command:**
```bash
aws iam get-policy-version \
  --policy-arn "${POLICY_ARN}" \
  --version-id ... \
  --query 'PolicyVersion.Document.Statement[1].Action' --output json
```
**Expected:** `["kms:Decrypt", "kms:DescribeKey"]`

### 1.5 Variable Sensitivity

**Command:**
```bash
grep -A5 'variable "auth0_management_api_token"' terraform/aws-ecs/modules/mcp-gateway/variables.tf
```
**Expected:** `sensitive = true` present.

**Command:**
```bash
grep -A5 'variable "enable_secrets_manager"' terraform/aws-ecs/modules/mcp-gateway/variables.tf
```
**Expected:** Variable declared with `type = bool` and `default = true`.

## 2. Backwards Compatibility Tests

### 2.1 Deployment Without New Variables

**Command:** Run `terraform plan` with only minimal required variables (no Okta, Auth0, Entra, observability).
**Expected:** Plan succeeds. Default empty-string values used for all new variables. No errors.

### 2.2 Partial Variable Set (Okta Only)

**Command:** Run `terraform plan` with `okta_enabled=true` and Okta credentials, everything else false.
**Expected:** Okta secrets in task definition. Auth0/Entra secrets NOT in task definition.

### 2.3 Environment Variable Names Preserved

**Command:** Check that all secret names in the `secrets` block match the original environment variable names.
**Expected:** No new or renamed variable names.

### 2.4 Existing Secrets Unchanged

**Command:** Run `terraform plan` and check that existing secret resources (SECRET_KEY, KEYCLOAK_CLIENT_SECRET, etc.) show no changes.
**Expected:** `~` (update) only for IAM policy (new ARNs added). Existing `aws_secretsmanager_secret` resources show no changes.

## 3. Deployment Surface Tests

### 3.1 Docker Compose - Not Applicable

**Not Applicable** - Docker Compose lacks native Secrets Manager integration. Migration only affects ECS Terraform deployment.

### 3.2 Helm Charts - No Change Required

**Command:**
```bash
helm dependency update charts/mcp-gateway-registry-stack 2>/dev/null || true
helm template test-release charts/mcp-gateway-registry-stack \
  --set app.domainName=test.example.com \
  --set keycloak.enabled=true \
  --set keycloak.domain=kc.example.com \
  --set keycloak.adminPassword=test > /dev/null
```
**Expected:** Helm template succeeds (no changes needed).

### 3.3 Terraform State - All New Secrets Present

**Command:**
```bash
terraform state list | grep "aws_secretsmanager_secret" | sort
```
**Expected:** All 14 new secret resources in state (13 `aws_secretsmanager_secret` + 1 `random_password`).

### 3.4 No Secrets in terraform output

**Command:**
```bash
terraform output -json 2>/dev/null | jq 'to_entries[] | select(.value | tostring | test("_SECRET|_TOKEN|_PASSWORD"))'
```
**Expected:** Empty output.

### 3.5 State Backup Before Apply

**Command:**
```bash
cd terraform/aws-ecs
terraform state pull > /tmp/secrets-migration-backup.tfstate
echo "State backup size: $(wc -c < /tmp/secrets-migration-backup.tfstate) bytes"
```
**Expected:** Backup file created and readable.

## 4. Security Tests

### 4.1 No Plaintext Secrets in terraform plan

**Command:**
```bash
terraform plan 2>&1 | grep -iE "SECRET_KEY.*=.*[A-Za-z0-9]{10}|REGISTRY_API_TOKEN.*=.*[A-Za-z0-9]{10}" || echo "NO_PLAINTEXT"
```
**Expected:** "NO_PLAINTEXT"

### 4.2 All Secrets Use Same KMS Key

**Command:**
```bash
aws secretsmanager list-secrets \
  --filters "Key=name,Values=mcp-gateway-test-*" \
  --query 'SecretList[].KmsKeyId' --output text | sort -u
```
**Expected:** Single KMS key ID.

### 4.3 Recovery Window Is 0

**Command:** Check `RecoveryWindowInDays` for all new secrets.
**Expected:** 0 for all (per the existing pattern).

### 4.4 IAM Least Privilege

**Command:** Verify policy only has `GetSecretValue`, not `PutSecretValue`, `CreateSecret`, `DeleteSecret`.

### 4.5 CloudTrail Audit

**Command:** After task launch, check CloudTrail for `GetSecretValue` events.
**Expected:** Events logged with `requestParameters.secretId` for each accessed secret.

### 4.6 Cross-Account KMS Grant (if configured)

**Command:**
```bash
aws kms list-grants --key-id <kms-key-id>
```
**Expected:** When `kms_cross_account_principals` is set, a grant exists with the specified account principals and `["Decrypt", "DescribeKey"]` operations. When unset, no cross-account grant exists.

### 4.7 prevent_destroy Lifecycle (for critical secrets)

**Command:** Check for `lifecycle { prevent_destroy = true }` on critical secrets.
**Expected:** `registry_api_token`, `github_pat`, `federation_encryption_key` have `prevent_destroy = true` (if implemented in a follow-up).

## 5. End-to-End Tests

### 5.1 Full Deployment and Health Check

```bash
# Phase 1: Apply with enable_secrets_manager = false (infrastructure only)
terraform apply -auto-approve -var="enable_secrets_manager=false" -var-file="test.tfvars"

# Verify secrets were created
aws secretsmanager list-secrets \
  --filters "Key=name,Values=mcp-gateway-test-registry-api-token-*" \
  --query 'SecretList[].Name' --output json

# Phase 2: Flip toggle to enable secrets loading
terraform apply -auto-approve -var="enable_secrets_manager=true" -var-file="test.tfvars"

# Wait for ECS services
for i in {1..60}; do
  HEALTH=$(aws ecs describe-services \
    --cluster "mcp-gateway-test" \
    --services "mcp-gateway-test-registry" "mcp-gateway-test-auth-server" \
    --query 'services[*].status' --output text)
  [[ "$HEALTH" == *"ACTIVE"* ]] && break
  sleep 10
done

curl -sf "https://test.example.com/health" && echo "Registry health OK"
curl -sf "https://test.example.com/auth/health" && echo "Auth server health OK"
```
**Expected:** Both health endpoints return HTTP 200. No errors in CloudWatch Logs.

### 5.2 Authentication Flow

```bash
curl -sf -X POST "https://test.example.com/auth/realms/mcp-gateway/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=mcp-gateway-web" \
  -d "client_secret=${KEYCLOAK_CLIENT_SECRET}" \
  -d "username=admin" \
  -d "password=${KEYCLOAK_ADMIN_PASSWORD}"
```
**Expected:** JSON response with `access_token` field.

### 5.3 Federation Token Test

```bash
curl -sf "https://test.example.com/api/federation/status" \
  -H "Authorization: Bearer ${FEDERATION_STATIC_TOKEN}"
```
**Expected:** No "Unauthorized" or "Forbidden" in response.

### 5.4 Grafana Login Test

```bash
curl -sf -X POST "https://test.example.com/grafana/login" \
  -H "Content-Type: application/json" \
  -d '{"user":"admin","password":"'"${GRAFANA_ADMIN_PASSWORD}"'"}'
```
**Expected:** Successful login response.

### 5.5 GitHub PAT Access (if configured)

```bash
curl -sf -H "Authorization: Bearer ${GITHUB_PAT}" \
  "https://api.github.com/user" | jq '.login'
```
**Expected:** GitHub username returned (confirms PAT works).

### 5.6 Rotation Scaffolding Verification (if enabled)

**Command:**
```bash
terraform plan -var="secret_rotation_enabled=true" \
  -var="name=${TF_VAR_name}" \
  -var="keycloak_domain=${TF_VAR_keycloak_domain}" \
  -var="documentdb_endpoint=${TF_VAR_documentdb_endpoint}" \
  -var="documentdb_credentials_secret_arn=${TF_VAR_documentdb_credentials_secret_arn}" \
  -var="keycloak_admin_password=${KEYCLOAK_ADMIN_PASSWORD}" \
  -var="secret_key=${SECRET_KEY}" \
  -var="vpc_id=${TF_VAR_vpc_id}" \
  -var="private_subnet_ids=${TF_VAR_private_subnet_ids}" \
  -var="public_subnet_ids=${TF_VAR_public_subnet_ids}" \
  -var="ecs_cluster_arn=${TF_VAR_ecs_cluster_arn}" \
  -var="ecs_cluster_name=${TF_VAR_ecs_cluster_name}" \
  -var="alb_logs_bucket=${TF_VAR_alb_logs_bucket}" \
  -out=tfplan
```
**Expected:** Plan shows rotation schedule expression variable. Rotation Lambda functions are NOT created (scaffolding only; actual Lambda functions are a follow-up).

## 6. Test Execution Checklist

- [ ] Section 1.1 (Terraform validate) passes
- [ ] Section 1.2 (terraform plan dry run) produces expected changes
- [ ] Section 1.2.1 (enable_secrets_manager=true) shows secrets in task definitions
- [ ] Section 1.2.2 (enable_secrets_manager=false) shows plaintext fallback only
- [ ] Section 1.2.3 (Variable propagation) confirmed in root module
- [ ] Section 1.3.1 (No plaintext in registry env) verified
- [ ] Section 1.3.2 (Registry secrets block has all expected secrets) verified
- [ ] Section 1.3.3 (Auth server secrets block) verified
- [ ] Section 1.3.4 (Grafana secrets) verified
- [ ] Section 1.3.5 (Non-secret env vars preserved) verified
- [ ] Section 1.4.1 (IAM policy contains new ARNs) verified
- [ ] Section 1.4.2 (Only GetSecretValue) verified
- [ ] Section 1.4.3 (KMS Decrypt permission) verified
- [ ] Section 1.5 (Variable sensitivity) confirmed
- [ ] Section 2.1 (Backwards compat - no new vars) succeeds
- [ ] Section 2.2 (Backwards compat - partial vars) succeeds
- [ ] Section 2.3 (Env var names preserved) verified
- [ ] Section 2.4 (Existing secrets unchanged) verified
- [ ] Section 3.1 (Docker Compose) marked Not Applicable
- [ ] Section 3.2 (Helm charts) no regression
- [ ] Section 3.3 (Terraform state) shows all new secrets
- [ ] Section 3.4 (No secrets in terraform output) verified
- [ ] Section 3.5 (State backup) verified
- [ ] Section 4.1 (No plaintext in terraform plan) verified
- [ ] Section 4.2 (KMS encryption) verified
- [ ] Section 4.3 (Recovery window) verified
- [ ] Section 4.4 (IAM least privilege) verified
- [ ] Section 4.5 (CloudTrail audit) verified
- [ ] Section 4.6 (Cross-account KMS grant) verified (if configured)
- [ ] Section 5.1 (Full deployment and health check) passes
- [ ] Section 5.2 (Authentication flow) passes
- [ ] Section 5.3 (Federation token) verified
- [ ] Section 5.4 (Grafana login) verified
- [ ] Section 5.6 (Rotation scaffolding) verified
- [ ] `uv run pytest tests/` passes with no regressions