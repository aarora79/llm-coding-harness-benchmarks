# Testing Plan: Migrate remaining sensitive ECS env vars to AWS Secrets Manager

*Created: 2026-06-25*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This change is infrastructure-only (Terraform/ECS). Testing verifies that (a) the migrated secret names no longer appear in any container `environment` block, (b) they appear in the `secrets` block with valid `valueFrom` ARNs, (c) new Secrets Manager resources and IAM grants are created/updated correctly, (d) `terraform validate`/`plan` succeed under both default and all-features-enabled configurations, and (e) services still start and authenticate after apply. There is no application code change, so the test focus is plan-diff assertions, rendered-task-definition inspection, and a deploy-and-verify smoke pass.

Per the SWE skill constraints, the commands below are the executable plan a future implementer runs. This skill does not execute them against the cloned `repo/`.

### Prerequisites
- [ ] Terraform >= the version pinned in `terraform/aws-ecs` initialized (`terraform init`).
- [ ] AWS credentials for a non-prod account/stack for the deploy-and-verify section.
- [ ] `jq` and `grep` available for plan/JSON assertions.
- [ ] `helm` with the `unittest` plugin (for the reserved-env-name check).
- [ ] The cloned repo at tag `1.24.4`.

### Shared Variables
```bash
export TF_DIR="terraform/aws-ecs"
export MODULE_DIR="$TF_DIR/modules/mcp-gateway"

# The secret env-var names being migrated (single source of truth for the asserts below)
export MIGRATED_NAMES="REGISTRY_API_TOKEN REGISTRY_API_KEYS FEDERATION_STATIC_TOKEN FEDERATION_ENCRYPTION_KEY ANS_API_KEY ANS_API_SECRET REGISTRATION_WEBHOOK_AUTH_TOKEN REGISTRATION_GATE_AUTH_CREDENTIAL REGISTRATION_GATE_OAUTH2_CLIENT_SECRET GITHUB_PAT GITHUB_APP_PRIVATE_KEY GF_SECURITY_ADMIN_PASSWORD"

# For deploy-and-verify (set to your stack)
export AWS_REGION="us-east-1"
export CLUSTER="<your-ecs-cluster-name>"
```

---

## 1. Functional Tests

### 1.1 curl / HTTP Tests

**Not Applicable** - this change adds no HTTP endpoints and modifies none. The services' HTTP surface is unchanged; only the injection mechanism for existing env vars changes. (Service-level smoke checks live in Section 5.)

### 1.2 CLI Tests

The "CLI" under test is Terraform. These are the core assertions.

#### 1.2.1 Static validation

```bash
cd "$TF_DIR"
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
```
Expected: `Success! The configuration is valid.` and no fmt diffs.

#### 1.2.2 Plan succeeds (default configuration)

```bash
terraform plan -out=tfplan.default
terraform show -json tfplan.default > plan.default.json
```
Expected: exit 0; plan shows new `aws_secretsmanager_secret(_version)` resources to be created and `aws_iam_policy.ecs_secrets_access` updated in place.

#### 1.2.3 Plan succeeds (all optional features enabled)

Create a throwaway tfvars enabling every gated feature so conditional code paths are exercised:

```bash
cat > all-features.auto.tfvars <<'EOF'
entra_enabled        = true
okta_enabled         = true
auth0_enabled        = true
enable_observability = true
ans_integration_enabled = true
registration_gate_enabled = true
# ... plus required non-secret companions (domains, client_ids) per variables.tf
EOF

terraform plan -out=tfplan.all -var-file=all-features.auto.tfvars
terraform show -json tfplan.all > plan.all.json
rm all-features.auto.tfvars
```
Expected: exit 0; no `index out of range` / count errors; Grafana secret + IAM grant present.

#### 1.2.4 ASSERTION: no migrated secret appears in any `environment` block

This is the central correctness gate. Inspect the planned task definitions in the JSON plan and confirm every migrated name appears only under `secrets`, never under `environment`.

```bash
for PLAN in plan.default.json plan.all.json; do
  echo "=== $PLAN ==="
  # Extract every container definition's environment + secrets from planned task defs
  jq -r '
    [.. | objects | select(.containerDefinitions? != null) | .containerDefinitions[]] as $cds
    | $cds[]
    | {name: .name,
       env: ([.environment[]?.name]),
       sec: ([.secrets[]?.name])}
  ' "$PLAN" > /tmp/cds.$PLAN.json 2>/dev/null || true

  for NAME in $MIGRATED_NAMES; do
    if grep -q "\"$NAME\"" /tmp/cds.$PLAN.json; then
      # Must be under sec, never under env
      if jq -e --arg n "$NAME" 'select(.env | index($n))' /tmp/cds.$PLAN.json >/dev/null; then
        echo "FAIL: $NAME still present in an environment block ($PLAN)"
      else
        echo "OK:   $NAME only in secrets ($PLAN)"
      fi
    fi
  done
done
```
Expected: every line is `OK:`; zero `FAIL:` lines.

> Note: planned `containerDefinitions` may render as a JSON string inside the plan (the ECS module uses `jsonencode`). If so, pipe through `fromjson`:
> `jq -r '.. | .container_definitions? // empty' plan.default.json | jq '.[] | {name, env:[.environment[]?.name], sec:[.secrets[]?.name]}'`
> Adjust the extraction to whichever form `terraform show -json` emits for this module version.

#### 1.2.5 ASSERTION: source grep gate (defense in depth)

Independent of the plan, assert the HCL no longer wires any migrated name as plaintext `value =`:

```bash
cd "$MODULE_DIR"
FAIL=0
for NAME in $MIGRATED_NAMES; do
  # find a 'name = "NAME"' immediately followed by a 'value =' (plaintext) within 2 lines
  if grep -A2 "name *= *\"$NAME\"" ecs-services.tf observability.tf 2>/dev/null | grep -q 'value *='; then
    echo "FAIL: $NAME appears to still use plaintext value ="
    FAIL=1
  fi
done
[ "$FAIL" -eq 0 ] && echo "OK: no migrated name uses plaintext value ="
```
Expected: `OK: no migrated name uses plaintext value =`.

#### 1.2.6 ASSERTION: every migrated secret has a definition and an IAM grant

```bash
cd "$MODULE_DIR"
for SLUG in registry_api_token registry_api_keys federation_static_token federation_encryption_key \
            ans_api_key ans_api_secret registration_webhook_auth_token registration_gate_auth_credential \
            registration_gate_oauth2_client_secret github_pat github_app_private_key grafana_admin_password; do
  grep -q "aws_secretsmanager_secret\" \"$SLUG\"" secrets.tf \
    && echo "OK def: $SLUG" || echo "FAIL def missing: $SLUG"
  grep -q "$SLUG" iam.tf \
    && echo "OK iam: $SLUG" || echo "FAIL iam grant missing: $SLUG"
done
```
Expected: every secret has both `OK def:` and `OK iam:`. (This guards the "added a secret, forgot the IAM grant" bug Sage flagged.)

---

## 2. Backwards Compatibility Tests

The contract: containers receive the same env var **names** with the same **values** at runtime. Only the delivery channel changes.

### 2.1 Env var name set is unchanged

```bash
# Compare the union of env+secret names before and after the change for each container.
# On the pre-change checkout:
git stash   # or check out the base revision in a worktree
terraform show -json tfplan.base > plan.base.json   # generated from base
# Extract names:
jq -r '.. | .container_definitions? // empty' plan.base.json 2>/dev/null \
  | jq -r '.[] | "\(.name): \([.environment[]?.name,.secrets[]?.name]|sort|join(","))"' \
  | sort > /tmp/names.base.txt
# Repeat on the post-change plan -> /tmp/names.head.txt
diff /tmp/names.base.txt /tmp/names.head.txt
```
Expected: **no diff**. The set of delivered env var names per container is identical; only `environment` vs `secrets` membership shifts. (If a name moved containers or was dropped, this fails.)

### 2.2 Deployments that leave optional secrets empty still apply

```bash
# Default plan (Section 1.2.2) already exercises "all optional secrets empty".
# Assert no conditional secret with count=0 is referenced with [0]:
terraform plan -var-file=/dev/null   # all gated features off
```
Expected: exit 0, no `Invalid index` errors. Confirms `count`/index guards are correct.

### 2.3 `mongodb_connection_string` plaintext path

- Deployment using `mongodb_connection_string_secret_arn` (the already-supported SM path): **unchanged** - assert the registry/auth-server `secrets` still reference the supplied ARN.
- Deployment using the plaintext `var.mongodb_connection_string` (Option A): assert the new managed secret is created and referenced, and that `MONGODB_CONNECTION_STRING` no longer appears under `environment`.

```bash
echo 'mongodb_connection_string = "mongodb://user:pass@host:27017/db"' > mongo.auto.tfvars
terraform plan -out=tfplan.mongo -var-file=mongo.auto.tfvars
terraform show -json tfplan.mongo | grep -c "MONGODB_CONNECTION_STRING"   # appears in secrets, not environment
rm mongo.auto.tfvars
```
Expected: `MONGODB_CONNECTION_STRING` present only in `secrets`; new managed secret planned.

### 2.4 `*_extra_env` collision behavior unchanged

```bash
# Supplying a migrated name via extra_env must still be rejected by the existing validation.
echo 'registry_extra_env = [{ name = "REGISTRY_API_TOKEN", value = "x" }]' > collide.auto.tfvars
terraform plan -var-file=collide.auto.tfvars || echo "OK: collision rejected as expected"
rm collide.auto.tfvars
```
Expected: plan fails with the reserved-name validation error (proves the migrated name is still reserved).

---

## 3. UX Tests

The only user-visible surface is the Grafana admin login; everything else is operator-facing docs.

### 3.1 Grafana login (post-deploy, observability enabled)
- After apply and after setting the real admin password (Section 4.5), browse to `https://<domain>/grafana/` and log in as `admin` with the value stored in the `*-grafana-admin-password-*` secret.
- Expected: login succeeds; the AMP datasource and dashboard provisioned by the `grafana-config` sidecar are present (proves the sidecar's `$${GF_SECURITY_ADMIN_PASSWORD}` reference still resolves via the injected secret).
- Negative: logging in with the old plaintext value (if different) fails - confirms the secret value is the live credential.

### 3.2 Operator documentation clarity
- Verify `OPERATIONS.md` lists each new secret name and gives a copy-paste `aws secretsmanager put-secret-value` command.
- Expected: an operator can set every real secret value without reading the Terraform source.

---

## 4. Deployment Surface Tests

### 4.1 Docker wiring
**Not Applicable** - this change is scoped to `terraform/aws-ecs`. Docker Compose secret handling (`extra_env/`, `.env`) is unchanged and out of scope for issue #1134.

### 4.2 Terraform / ECS wiring

#### 4.2.1 Secrets Manager resources created
```bash
aws secretsmanager list-secrets --region "$AWS_REGION" \
  --query "SecretList[?contains(Name, '-registry-api-token-') || contains(Name,'-federation-') || contains(Name,'-github-') || contains(Name,'-ans-') || contains(Name,'-grafana-admin-password-') || contains(Name,'-registration-')].Name" \
  --output table
```
Expected: all migrated secrets listed with the `${name_prefix}-<slug>-<suffix>` naming.

#### 4.2.2 IAM execution-role grant
```bash
# Identify the execution role and its inline/attached secrets policy, then confirm the new ARNs are present.
aws iam list-attached-role-policies --role-name "${STACK_NAME}-task-execution" --output table
# Inspect the ecs_secrets_access policy document and grep for the new secret ARNs / GetSecretValue.
```
Expected: `secretsmanager:GetSecretValue` granted on each new ARN; KMS `Decrypt` on the secrets key already present (unchanged).

#### 4.2.3 Rendered task definition has no plaintext secrets
```bash
for SVC in auth-server registry grafana; do
  TD=$(aws ecs describe-services --cluster "$CLUSTER" --services "$SVC" \
        --query 'services[0].taskDefinition' --output text --region "$AWS_REGION")
  echo "=== $SVC ($TD) ==="
  aws ecs describe-task-definition --task-definition "$TD" --region "$AWS_REGION" \
    --query 'taskDefinition.containerDefinitions[].{name:name, env:environment[].name, secrets:secrets[].name}'
done
```
Expected: every migrated name appears under `secrets`, none under `env`. This is the live-environment equivalent of the plan assertion in 1.2.4.

### 4.3 Helm / EKS wiring

#### 4.3.1 Reserved-env-name consistency
```bash
helm unittest charts/registry charts/auth-server charts/mcpgw charts/mcp-gateway-registry-stack
```
Expected: suites pass. Confirms the migrated names are present in `charts/*/reserved-env-names.txt` and that no positional/reserved assertion drifted. (Helm itself does not use ECS Secrets Manager, but the reserved-name lists are a cross-surface source of truth per CLAUDE.md.)

### 4.4 Deploy and verify
```bash
cd "$TF_DIR"
terraform apply tfplan.default   # or tfplan.all in a feature-complete non-prod stack
```
Expected: apply succeeds; new secrets created; services roll to new task-definition revisions and reach steady state:
```bash
aws ecs wait services-stable --cluster "$CLUSTER" --services auth-server registry grafana --region "$AWS_REGION"
```

### 4.5 Set real secret values (post-apply runbook step)
Because versions use `ignore_changes = [secret_string]`, set the real values out-of-band, then force a new deployment:
```bash
aws secretsmanager put-secret-value --secret-id "<grafana-admin-password-arn>" \
  --secret-string "$REAL_GRAFANA_PASSWORD" --region "$AWS_REGION"
# repeat for each secret that needs a real value
aws ecs update-service --cluster "$CLUSTER" --service grafana --force-new-deployment --region "$AWS_REGION"
```
Expected: tasks restart and pick up the real values; subsequent `terraform plan` shows **no drift** (proves `ignore_changes` works).

### 4.6 Rollback verification
```bash
# Roll a service back to its previous task-definition revision (no Terraform needed for fast rollback):
aws ecs update-service --cluster "$CLUSTER" --service registry \
  --task-definition "<previous-revision-arn>" --region "$AWS_REGION"
```
Expected: service returns to the prior revision and reaches steady state. Also verify a full `terraform destroy`-free revert: `git revert` the change, `terraform apply`, services roll back, secrets optionally remain (harmless).

---

## 5. End-to-End API Tests

These confirm runtime behavior is unbroken - the services actually use the secrets that are now injected via Secrets Manager.

### 5.1 Registry static-token auth (if `registry_static_token_auth_enabled`)
```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer $REAL_REGISTRY_API_TOKEN" \
  "https://<domain>/api/<an-authenticated-endpoint>"
```
Expected: `200` with the correct token (proves `REGISTRY_API_TOKEN` injected from Secrets Manager is the live value); `401` with a wrong token.

### 5.2 Federation handshake (if federation enabled)
- Trigger a federation pull/health-check between two registries that share `FEDERATION_STATIC_TOKEN`.
- Expected: peer authenticates; stored federation tokens decrypt with `FEDERATION_ENCRYPTION_KEY`. A mismatch (e.g. wrong/placeholder key) surfaces as a decryption failure - which also validates that the real key (not the `"not-configured"` sentinel) is in effect.

### 5.3 GitHub skill fetch (if `github_pat`/GitHub App configured)
- Register/refresh a server whose `SKILL.md` lives in a private repo.
- Expected: fetch succeeds, proving `GITHUB_PAT` / `GITHUB_APP_PRIVATE_KEY` from Secrets Manager work.

### 5.4 Grafana datasource provisioning
- Already covered in 3.1: the `grafana-config` sidecar creating the AMP datasource is itself an E2E proof that `GF_SECURITY_ADMIN_PASSWORD` injected as a secret is usable by the sidecar's shell command.

---

## 6. Test Execution Checklist

- [ ] Section 1 (Functional / Terraform): `fmt`, `validate`, `plan` (default + all-features) pass; assertions 1.2.4-1.2.6 all `OK`, zero `FAIL`.
- [ ] Section 2 (Backwards Compat): env-var name set unchanged (2.1 no diff); empty-optional plan applies (2.2); Mongo paths correct (2.3); extra_env collision still rejected (2.4).
- [ ] Section 3 (UX): Grafana login works post-apply; OPERATIONS.md runbook is copy-paste complete.
- [ ] Section 4 (Deployment): SM resources + IAM grants present; rendered task defs have zero plaintext secrets (4.2.3); helm unittests pass (4.3.1); apply reaches steady state; real values set with no resulting drift (4.5); rollback verified (4.6).
- [ ] Section 5 (E2E): registry token auth, federation, GitHub fetch, Grafana provisioning all functional with secrets injected from Secrets Manager.
- [ ] No new unit/integration tests are required in `tests/` (no application code changed); if the implementer adds the suggested CI grep gate, place it under `.github/workflows/` or `scripts/`.
- [ ] `terraform plan` after apply shows no drift (confirms `ignore_changes` on secret versions).
