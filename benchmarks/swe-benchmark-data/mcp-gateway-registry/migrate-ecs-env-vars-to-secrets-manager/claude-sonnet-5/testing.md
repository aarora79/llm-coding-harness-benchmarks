# Testing Plan: Migrate Remaining ECS Plaintext Secrets to AWS Secrets Manager

*Created: 2026-07-23*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This change is Terraform-only: 13 new `*_secret_arn` variables, conditional `environment`/`secrets` emission in `ecs-services.tf` and `observability.tf`, and an extended IAM policy in `iam.tf`. Testing therefore focuses on `terraform validate`/`plan` correctness (both the unmodified-tfvars backwards-compatibility path and the new-ARN path), the resulting ECS task definition JSON, IAM policy diffs, and post-deploy verification that each container actually receives the expected value at runtime. There are no new HTTP endpoints or CLI commands to test.

### Prerequisites
- [ ] A non-production AWS account/workspace with permission to run `terraform plan`/`apply` against the `terraform/aws-ecs` stack.
- [ ] An existing `terraform.tfvars` (or a copy of `terraform.tfvars.example` with placeholder values) that already populates the 13 plaintext variables in scope, to exercise the backwards-compatible path.
- [ ] AWS CLI configured with credentials for the target account (`aws sts get-caller-identity` succeeds).
- [ ] `terraform >= 1.2` installed (per `required_version` in `main.tf`).
- [ ] For the new-ARN path: 13 test secrets pre-created in AWS Secrets Manager (see 4.2 below for exact `aws secretsmanager create-secret` commands), or a subset if only testing a few variables.

### Shared Variables
```bash
export AWS_REGION="us-east-1"
export TF_STACK_DIR="terraform/aws-ecs"
export TEST_SECRET_PREFIX="mcp-gw-migration-test"
```

## 1. Functional Tests

### 1.1 curl / HTTP Tests
**Not Applicable** - this change adds no new HTTP endpoints. The registry's existing endpoints continue to read the same env var names regardless of source; no endpoint-level behavior changes.

### 1.2 CLI Tests
**Not Applicable** - this change adds no new CLI commands. `terraform plan`/`apply` behavior is covered under Deployment Surface Tests (Section 4) rather than as a general CLI test, since it is the deployment mechanism itself under test.

## 2. Backwards Compatibility Tests

These tests confirm that every existing deployment continues to work unchanged when the new `*_secret_arn` variables are left at their default (`""`).

### 2.1 `terraform validate` with unmodified tfvars
```bash
cd "$TF_STACK_DIR"
terraform init -backend=false
terraform validate
```
**Expected:** `Success! The configuration is valid.` No errors referencing the new `*_secret_arn` variables (they should default cleanly).

### 2.2 `terraform plan` with unmodified tfvars produces no changes to `environment`/`secrets` shape
```bash
cd "$TF_STACK_DIR"
terraform plan -var-file="terraform.tfvars" -out=backwards-compat.plan
terraform show -json backwards-compat.plan > backwards-compat.json
python3 -c "
import json
with open('backwards-compat.json') as f:
    plan = json.load(f)
# Confirm no destructive replacement of ecs_service_auth / ecs_service_registry / ecs_service_grafana
for rc in plan.get('resource_changes', []):
    if 'ecs_service' in rc['address'] and rc['change']['actions'] not in (['no-op'], ['update']):
        print('UNEXPECTED ACTION:', rc['address'], rc['change']['actions'])
print('OK: no unexpected destructive actions on ECS service modules')
"
```
**Expected:** With every `*_secret_arn` variable at its default `""`, the plan shows only `update` (or `no-op`) actions on the ECS service modules - no `delete`/`create` (replace) actions triggered purely by the presence of the new variables. If the 13 plaintext variables were already populated in the existing tfvars, the emitted `environment` list for `auth-server`/`registry`/`grafana` should be byte-for-byte identical to the pre-change plan (diff the container_definitions JSON in the plan output before and after applying this change's HCL edits, with tfvars held constant).

**Assertion:** Every one of the 13 plaintext env vars still appears in the `environment` array (not `secrets`) of the relevant container definitions when its corresponding `_secret_arn` variable is unset.

### 2.3 Schema/type compatibility for existing plaintext variables
```bash
cd "$TF_STACK_DIR"
terraform console -var-file="terraform.tfvars" <<'EOF'
var.registry_api_token
var.registry_api_token_secret_arn
var.github_app_private_key
var.github_app_private_key_secret_arn
var.grafana_admin_password
var.grafana_admin_password_secret_arn
EOF
```
**Expected:** The plaintext variables return their configured values (or `""` if unset); every new `*_secret_arn` variable returns `""` when not explicitly set in tfvars - confirming the default does not break variable resolution for consumers unaware of the new names.

## 3. UX Tests

**Not Applicable** - no UI or CLI output changes. The one UI surface that displays several of these values (the registry's admin Config Panel, which masks sensitive fields and reads them by settings field name) is source-agnostic to whether ECS populated the underlying env var from `secrets` or `environment`, so no UX verification beyond a smoke check is required: confirm the admin Config Panel still renders the masked values correctly after a deployment that uses the new `_secret_arn` path (see 5.1).

## 4. Deployment Surface Tests

### 4.1 Docker wiring
**Not Applicable** - this change does not touch `docker-compose.yml`, `docker-compose.podman.yml`, `docker-compose.prebuilt.yml`, or `docker-compose.dhi.yml`. Per the stated constraints, this migration is scoped to the Terraform/ECS stack only.

### 4.2 Terraform / ECS wiring

#### 4.2.1 Create test secrets in Secrets Manager
```bash
aws secretsmanager create-secret \
  --name "${TEST_SECRET_PREFIX}-registry-api-token" \
  --secret-string "test-registry-api-token-value" \
  --region "$AWS_REGION"

aws secretsmanager create-secret \
  --name "${TEST_SECRET_PREFIX}-federation-static-token" \
  --secret-string "test-federation-token-value" \
  --region "$AWS_REGION"

aws secretsmanager create-secret \
  --name "${TEST_SECRET_PREFIX}-grafana-admin-password" \
  --secret-string "TestGrafanaPassw0rd!" \
  --region "$AWS_REGION"

# Capture ARNs
export REGISTRY_API_TOKEN_ARN=$(aws secretsmanager describe-secret --secret-id "${TEST_SECRET_PREFIX}-registry-api-token" --region "$AWS_REGION" --query ARN --output text)
export FEDERATION_STATIC_TOKEN_ARN=$(aws secretsmanager describe-secret --secret-id "${TEST_SECRET_PREFIX}-federation-static-token" --region "$AWS_REGION" --query ARN --output text)
export GRAFANA_ADMIN_PASSWORD_ARN=$(aws secretsmanager describe-secret --secret-id "${TEST_SECRET_PREFIX}-grafana-admin-password" --region "$AWS_REGION" --query ARN --output text)
```

#### 4.2.2 `terraform plan` with `*_secret_arn` variables populated
```bash
cd "$TF_STACK_DIR"
terraform plan \
  -var-file="terraform.tfvars" \
  -var="registry_api_token_secret_arn=$REGISTRY_API_TOKEN_ARN" \
  -var="federation_static_token_secret_arn=$FEDERATION_STATIC_TOKEN_ARN" \
  -var="grafana_admin_password_secret_arn=$GRAFANA_ADMIN_PASSWORD_ARN" \
  -out=secret-arn.plan
terraform show -json secret-arn.plan > secret-arn.json
```
**Expected:** `terraform validate` and `plan` succeed with no errors. The plan diff shows, for `auth-server` and `registry` container definitions:
- `REGISTRY_API_TOKEN` and `FEDERATION_STATIC_TOKEN` removed from `environment` and added to `secrets` with `valueFrom` matching the supplied ARNs.
- All other plaintext env vars (those whose `_secret_arn` was left unset) remain unchanged in `environment`.
For the `grafana` and `grafana-config` container definitions:
- `GF_SECURITY_ADMIN_PASSWORD` removed from `environment` and added to `secrets` on **both** containers, with `valueFrom` matching `$GRAFANA_ADMIN_PASSWORD_ARN`.

**Assertion script:**
```bash
python3 -c "
import json
with open('secret-arn.json') as f:
    plan = json.load(f)

def find_container_def(plan, module_name, container_name):
    for rc in plan['resource_changes']:
        if module_name in rc['address'] and 'container_definitions' in rc.get('address', ''):
            pass
    return None

print('Manually inspect secret-arn.json for the auth-server/registry/grafana module.ecs_service_* resource changes')
print('Confirm REGISTRY_API_TOKEN, FEDERATION_STATIC_TOKEN, GF_SECURITY_ADMIN_PASSWORD appear under secrets[], not environment[]')
"
```

#### 4.2.3 IAM policy diff verification
```bash
cd "$TF_STACK_DIR"
terraform show -json secret-arn.plan | python3 -c "
import json, sys
plan = json.load(sys.stdin)
for rc in plan['resource_changes']:
    if rc['address'].endswith('aws_iam_policy.ecs_secrets_access'):
        print(json.dumps(rc['change']['after'], indent=2))
"
```
**Expected:** The policy document's `Resource` list for the `secretsmanager:GetSecretValue` statement includes `$REGISTRY_API_TOKEN_ARN`, `$FEDERATION_STATIC_TOKEN_ARN`, and (if the Grafana IAM attachment step from the LLD was implemented) the Grafana-specific policy includes `$GRAFANA_ADMIN_PASSWORD_ARN`.

**Negative check:** ARNs for `_secret_arn` variables left unset (e.g. `ans_api_key_secret_arn`) must NOT appear in the policy - confirms the conditional `!= ""` gating is correctly excluding unpopulated variables.

#### 4.2.4 `terraform apply` and task-definition inspection
```bash
cd "$TF_STACK_DIR"
terraform apply secret-arn.plan

aws ecs describe-task-definition \
  --task-definition "$(terraform output -raw registry_task_definition_family 2>/dev/null || echo 'mcp-gateway-v2-registry')" \
  --region "$AWS_REGION" \
  --query 'taskDefinition.containerDefinitions[?name==`registry`].{environment: environment, secrets: secrets}'
```
**Expected:** `REGISTRY_API_TOKEN` and `FEDERATION_STATIC_TOKEN` appear in the `secrets` array with `valueFrom` set to the test ARNs, and are absent from the `environment` array. No plaintext value for either is visible anywhere in the returned JSON.

### 4.3 Helm / EKS wiring
**Not Applicable** - per the stated constraints, this task is scoped to the Terraform/ECS stack only; the Helm charts are out of scope for this issue.

### 4.4 Deploy and verify
```bash
# Wait for the new task definition revision to roll out
aws ecs wait services-stable \
  --cluster "$(terraform output -raw ecs_cluster_name)" \
  --services "$(terraform output -raw registry_service_name)" \
  --region "$AWS_REGION"

# Confirm the running task actually received the resolved secret value
TASK_ARN=$(aws ecs list-tasks --cluster "$(terraform output -raw ecs_cluster_name)" --service-name "$(terraform output -raw registry_service_name)" --region "$AWS_REGION" --query 'taskArns[0]' --output text)

aws ecs execute-command \
  --cluster "$(terraform output -raw ecs_cluster_name)" \
  --task "$TASK_ARN" \
  --container registry \
  --interactive \
  --command "/bin/sh -c 'echo REGISTRY_API_TOKEN is set: ${REGISTRY_API_TOKEN:+yes}; echo value length: ${#REGISTRY_API_TOKEN}'"
```
**Expected:** The container reports `REGISTRY_API_TOKEN is set: yes` and a length matching `test-registry-api-token-value` (30 characters) - confirming ECS resolved the Secrets Manager value into the container's process environment under the original env var name, with the application requiring no changes to consume it.

**Grafana verification:**
```bash
GRAFANA_TASK_ARN=$(aws ecs list-tasks --cluster "$(terraform output -raw ecs_cluster_name)" --service-name "mcp-gateway-v2-grafana" --region "$AWS_REGION" --query 'taskArns[0]' --output text)
curl -s -u "admin:TestGrafanaPassw0rd!" "https://<grafana-url>/grafana/api/health"
```
**Expected:** HTTP 200 - confirms the Grafana admin credential resolved from Secrets Manager matches what the `grafana-config` sidecar used to provision the API, and that both containers received the identical value.

### 4.5 Rollback verification
```bash
cd "$TF_STACK_DIR"
terraform plan \
  -var-file="terraform.tfvars" \
  -var="registry_api_token_secret_arn=" \
  -var="federation_static_token_secret_arn=" \
  -var="grafana_admin_password_secret_arn=" \
  -out=rollback.plan
terraform apply rollback.plan
```
**Expected:** Reverting each `_secret_arn` variable back to `""` restores the original plaintext `environment` entries (using whatever value the corresponding plaintext variable still holds in tfvars) and removes the corresponding `secrets` block entries and IAM `Resource` grants. Confirms the fallback path is reversible via a normal `terraform apply`, with the caveat noted in the LLD that this is a config-time rollback requiring a new task definition revision and rolling deployment, not an instantaneous runtime toggle.

**Cleanup:**
```bash
aws secretsmanager delete-secret --secret-id "${TEST_SECRET_PREFIX}-registry-api-token" --force-delete-without-recovery --region "$AWS_REGION"
aws secretsmanager delete-secret --secret-id "${TEST_SECRET_PREFIX}-federation-static-token" --force-delete-without-recovery --region "$AWS_REGION"
aws secretsmanager delete-secret --secret-id "${TEST_SECRET_PREFIX}-grafana-admin-password" --force-delete-without-recovery --region "$AWS_REGION"
```

## 5. End-to-End API Tests

### 5.1 Admin Config Panel reflects the migrated value correctly
1. Deploy with `registry_api_token_secret_arn` set (per 4.2.4).
2. Log into the registry admin UI as an administrator.
3. Navigate to the Configuration/Settings panel.
4. Confirm the "Registry API Token" field displays as masked (e.g. `****`) exactly as it did before migration - the panel reads the resolved env var by field name and has no awareness of whether the value came from Secrets Manager or a plaintext variable.

**Expected:** No visible difference in the admin UI before and after migration; this is the concrete verification of the Frontend reviewer's (Pixel) recommendation in `review.md` to confirm the Config Panel is unaffected.

### 5.2 Federation flow exercises the migrated token end-to-end
1. Deploy registry A with `federation_static_token_secret_arn` set to a test secret's ARN, `federation_static_token_auth_enabled = true`.
2. Deploy (or reuse) registry B configured to call registry A's federation API using the same token value (as plaintext, since B is not part of this migration's scope) as its `Authorization: Bearer <token>` header.
3. Trigger a federation sync call from B to A.

**Expected:** B's request succeeds (HTTP 200) against A, confirming A's `auth_server`/`registry` process received the correct token value via Secrets Manager resolution and validated it identically to the plaintext path - i.e., the migration is transparent to a real inter-service authenticated call, not just to `env | grep`.

## 6. Test Execution Checklist
- [ ] Section 1 (Functional) - Not Applicable, confirmed no new endpoints/CLI commands introduced.
- [ ] Section 2 (Backwards Compatibility) - `terraform validate`/`plan` pass with unmodified tfvars; existing `environment` entries unchanged.
- [ ] Section 3 (UX) - Not Applicable beyond the Config Panel smoke check folded into 5.1.
- [ ] Section 4 (Deployment) - test secrets created; `plan`/`apply` show correct `secrets`/`environment` split; IAM policy diff confirms scoped access; running task confirmed to receive the resolved value via ECS Exec; rollback verified.
- [ ] Section 5 (E2E) - Config Panel smoke-checked; federation token flow exercised end-to-end against a real second registry.
- [ ] No unit tests added under `tests/unit/` - this change has no Python code to unit-test (confirmed zero application code changes in the LLD).
- [ ] No integration tests added under `tests/integration/` - covered instead by the Terraform-plan and live-ECS verification steps above, since this repo has no existing Terraform test harness (e.g. Terratest) to extend.
- [ ] `uv run pytest tests/` passes with no regressions (sanity check only - expected to be a no-op given zero Python changes, but run it to catch any accidental drift).
