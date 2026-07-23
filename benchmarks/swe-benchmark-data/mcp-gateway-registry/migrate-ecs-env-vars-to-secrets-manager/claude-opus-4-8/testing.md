# Testing Plan: Migrate remaining sensitive ECS env vars to AWS Secrets Manager

*Created: 2026-07-23*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This change is Terraform infrastructure that moves 13 sensitive values from the ECS `environment` block to the `secrets` block (Secrets Manager `valueFrom`) on the `auth-server`, `registry`, and `grafana` services, adds a `use_secrets_manager_for_env` fallback flag, and extends the IAM secrets-read policy. Testing verifies: (1) Terraform validates and plans cleanly with the flag on and off; (2) each sensitive variable appears in exactly one of `environment`/`secrets` per the flag; (3) the IAM policy grants read on every referenced secret ARN; (4) the running application boots and reads each value identically to the plaintext path; (5) empty optional secrets are absent (not injected as a truthy placeholder); (6) the fallback path is byte-for-byte identical to pre-change behavior.

There are no new HTTP endpoints or CLI commands. The dominant test surface is Terraform plan inspection, IAM coverage, and a live task-start smoke test.

### Prerequisites
- [ ] Terraform >= the version pinned in `versions.tf`, AWS provider configured.
- [ ] A representative `terraform.tfvars` (copy `terraform.tfvars.example`), including at least one non-empty value for each of `registry_api_token`, `github_pat`, `github_app_private_key`, `federation_encryption_key`, and (for observability) `enable_observability = true`.
- [ ] AWS credentials with permission to plan/apply the stack in a non-production account.
- [ ] `jq` installed for JSON assertions.
- [ ] For the live smoke test: the stack applied and ECS services reaching steady state.

### Shared Variables
```bash
export TF_DIR="terraform/aws-ecs"
export AWS_REGION="us-east-1"
export CLUSTER="mcp-gateway"            # adjust to your name_prefix
export REGISTRY_URL="https://<your-registry-domain>"
```

---

## 1. Functional Tests

### 1.1 curl / HTTP Tests

**Not Applicable** - This change adds no HTTP endpoints and modifies no request/response contract. The application reads the same environment variable names in both the plaintext and Secrets Manager paths, so existing endpoints are unchanged. The one endpoint whose auth depends on a migrated variable (`/admin/federation-token`, which checks `REGISTRY_API_TOKEN`) is covered as a negative security test in Section 5.

### 1.2 CLI Tests

**Not Applicable** - No CLI command is added or modified. The operator-facing surface is the Terraform workflow, covered in Section 4.

---

## 2. Backwards Compatibility Tests

The migration must be transparent to the application and, with the flag off, byte-for-byte identical to today.

### 2.1 Fallback flag off produces no net change

```bash
cd "$TF_DIR"
# Baseline: capture the pre-change plan (run on the pre-migration commit, or against the current live task defs)
terraform plan -var-file=terraform.tfvars -var 'use_secrets_manager_for_env=false' -out=tf-fallback.plan
terraform show -json tf-fallback.plan > tf-fallback.json

# Assert: with the flag OFF, no migrated var moves to a secrets block, and each still appears
# as a plaintext environment entry on auth-server and registry.
jq -r '
  .resource_changes[]
  | select(.type=="aws_ecs_task_definition")
  | .change.after.container_definitions
' tf-fallback.json > /dev/null && echo "task defs present"
```

**Expected:** With `use_secrets_manager_for_env=false`, `terraform plan` shows the 13 variables still injected as `environment` entries (values unchanged), and the `secrets` blocks contain only the previously-migrated first-tier secrets. No new secret is referenced by any task definition.

### 2.2 Application reads the same env var names in both modes

The app reads every migrated value from a process env var by name (Pydantic `BaseSettings`, `case_sensitive=False`, `registry/core/config.py:56-60`; direct `os.environ.get(...)` in `auth_server/server.py`). ECS injects `secrets` as ordinary env vars under the same name, so no code path changes.

```bash
# On a running task (Secrets Manager path), confirm the app sees the expected env var names.
# Use ECS Exec into the registry container:
aws ecs execute-command --cluster "$CLUSTER" \
  --task <registry-task-id> --container registry --interactive \
  --command "/bin/sh -c 'env | grep -E \"^(REGISTRY_API_TOKEN|GITHUB_PAT|FEDERATION_ENCRYPTION_KEY)=\" | sed \"s/=.*/=<redacted>/\"'"
```

**Expected:** Each configured variable is present in the container environment with a non-empty value. The names match exactly what the app reads; the app boots without `RuntimeError` on required values (`SECRET_KEY` guard at `config.py:924-931` still satisfied - `SECRET_KEY` is unchanged first-tier).

### 2.3 Empty optional var yields an ABSENT env var, not a placeholder (regression guard)

This is the critical backwards-compat assertion from the backend and security reviews: an empty optional value must NOT be injected as a truthy placeholder (e.g. `not-configured`), because the app gates on truthiness (`if REGISTRY_API_TOKEN:`, `if settings.github_pat:`, `hmac.compare_digest`).

```bash
cd "$TF_DIR"
# Plan with github_pat intentionally empty and the flag ON.
terraform plan -var-file=terraform.tfvars -var 'github_pat=' -var 'use_secrets_manager_for_env=true' -out=tf-empty.plan
terraform show -json tf-empty.plan > tf-empty.json

# Assert: no secret resource is created for github_pat, and GITHUB_PAT is NOT present in the
# registry container's secrets block.
jq -r '[.resource_changes[] | select(.address | test("aws_secretsmanager_secret.github_pat"))] | length' tf-empty.json
# Expected: 0

jq -r '
  .planned_values.root_module | ..
  | .container_definitions? // empty
' tf-empty.json | grep -c '"GITHUB_PAT"' || echo "GITHUB_PAT absent (expected)"
# Expected: GITHUB_PAT absent from both environment and secrets
```

**Expected:** With `github_pat` empty and the flag on, no `aws_secretsmanager_secret.github_pat` is created and `GITHUB_PAT` appears in neither block. At runtime the app's Pydantic field defaults to `""` (falsy) - identical to today's empty behavior. There is NO `not-configured` value anywhere.

### 2.4 No variable appears in both environment and secrets (ECS duplicate-name guard)

```bash
# Extract each service's container definition and assert no name is in both blocks.
terraform plan -var-file=terraform.tfvars -var 'use_secrets_manager_for_env=true' -out=tf-on.plan
terraform show -json tf-on.plan > tf-on.json

python3 - <<'PY'
import json
data = json.load(open("tf-on.json"))
def walk(o):
    if isinstance(o, dict):
        if "container_definitions" in o:
            yield o["container_definitions"]
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)
dupes = []
for cds in walk(data):
    cds = cds if isinstance(cds, list) else json.loads(cds)
    for c in cds:
        env = {e["name"] for e in c.get("environment", [])}
        sec = {s["name"] for s in c.get("secrets", [])}
        overlap = env & sec
        if overlap:
            dupes.append((c.get("name"), sorted(overlap)))
print("DUPLICATES:", dupes)
assert not dupes, "A variable appears in both environment and secrets"
print("OK: no duplicate env/secret names")
PY
```

**Expected:** `OK: no duplicate env/secret names`. ECS rejects task definitions with a name in both blocks; this guard catches the toggle-logic bug before apply.

---

## 3. UX Tests

### 3.1 Grafana admin login still works

The only user-facing surface affected is the Grafana admin login. After migration, the admin password is sourced from Secrets Manager (operator-supplied or generated).

```bash
# If the password was auto-generated because grafana_admin_password was empty, retrieve it:
aws secretsmanager get-secret-value \
  --secret-id "$(aws secretsmanager list-secrets \
      --query "SecretList[?starts_with(Name, '${CLUSTER}-grafana-admin-password')].Name | [0]" \
      --output text)" \
  --query SecretString --output text
```

**Expected:** The Grafana UI at the grafana endpoint accepts login with `admin` / the retrieved password. Datasource and dashboard provisioning (run by the grafana-config sidecar, which reads `GF_SECURITY_ADMIN_PASSWORD` from its env) completed - dashboards are present.

### 3.2 Error message clarity on misconfiguration

```bash
# Simulate a missing IAM grant by inspecting a failed task's stoppedReason (if a deploy fails).
aws ecs describe-tasks --cluster "$CLUSTER" --tasks <failed-task-id> \
  --query 'tasks[0].stoppedReason' --output text
```

**Expected:** If the execution role lacks a grant, the message is the standard `ResourceInitializationError: unable to pull secrets or registry auth ... AccessDeniedException`, which points the operator at the IAM policy. This is the expected failure mode documented in the LLD.

---

## 4. Deployment Surface Tests

### 4.1 Docker wiring

**Not Applicable** - The Docker Compose surface (`docker-compose.yml`, `.env.example`) is unchanged. Local/Compose deployments continue to pass these values via `.env`; Secrets Manager is an ECS-only concern per the requirements.

### 4.2 Terraform / ECS wiring

Anchor on the concrete files from the LLD.

```bash
cd "$TF_DIR"
terraform init -backend=false
terraform validate
```

**Expected:** `Success! The configuration is valid.`

```bash
# Plan with the flag ON and observability ON.
terraform plan -var-file=terraform.tfvars \
  -var 'use_secrets_manager_for_env=true' -var 'enable_observability=true' -out=tf-on.plan
terraform show -json tf-on.plan > tf-on.json

# 4.2.a - New secrets exist for configured vars (spot-check three):
for s in registry-api-token github-pat grafana-admin-password; do
  n=$(jq -r --arg s "$s" '[.resource_changes[] | select(.address | test("aws_secretsmanager_secret\\..*" + ($s|gsub("-";"_"))))] | length' tf-on.json)
  echo "$s secret resources planned: $n"
done
```

**Expected:** A secret + version pair is planned for each configured variable; `grafana-admin-password` is planned only because `enable_observability=true`.

```bash
# 4.2.b - IAM coverage: every secret ARN referenced by a task def is granted in ecs_secrets_access.
python3 - <<'PY'
import json
data = json.load(open("tf-on.json"))
def walk(o):
    if isinstance(o, dict):
        if "container_definitions" in o: yield o["container_definitions"]
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)
referenced = set()
for cds in walk(data):
    cds = cds if isinstance(cds, list) else json.loads(cds)
    for c in cds:
        for s in c.get("secrets", []):
            arn = s["valueFrom"].split(":")[:7]  # strip :jsonkey:: suffix
            referenced.add(":".join(arn))
# Collect the ecs_secrets_access policy Resource list from the plan
granted = set()
for rc in data["resource_changes"]:
    if rc["type"]=="aws_iam_policy" and "ecs_secrets" in rc["address"]:
        pol = json.loads(rc["change"]["after"]["policy"])
        for stmt in pol["Statement"]:
            if "secretsmanager:GetSecretValue" in (stmt["Action"] if isinstance(stmt["Action"],list) else [stmt["Action"]]):
                res = stmt["Resource"]; granted |= set(res if isinstance(res,list) else [res])
granted_prefixes = {g.split(":")[:7] and ":".join(g.split(":")[:7]) for g in granted}
missing = {r for r in referenced if r not in granted_prefixes}
print("REFERENCED:", len(referenced), "GRANTED:", len(granted))
print("MISSING GRANTS:", missing)
assert not missing, "A referenced secret ARN is not granted in ecs_secrets_access"
print("OK: all referenced secret ARNs are granted")
PY
```

**Expected:** `OK: all referenced secret ARNs are granted`. Note: plan-time ARNs may be unknown (computed); if so, run this assertion post-apply against `terraform state show` / the live task definition and the applied policy.

```bash
# 4.2.c - Grafana execution role attaches ecs_secrets_access.
grep -n "SecretsManagerAccess" modules/mcp-gateway/observability.tf
```

**Expected:** The grafana `module "ecs_service_grafana"` `task_exec_iam_role_policies` map now includes `SecretsManagerAccess = aws_iam_policy.ecs_secrets_access.arn` (LLD Step 7).

### 4.3 Helm / EKS wiring

**Not Applicable** - Helm/EKS (`charts/`) is explicitly out of scope per the requirements; this migration targets the Terraform ECS stack only.

### 4.4 Deploy and verify

```bash
cd "$TF_DIR"
terraform apply -var-file=terraform.tfvars -var 'use_secrets_manager_for_env=true'

# Confirm services reach steady state.
for svc in auth-server registry grafana; do
  aws ecs describe-services --cluster "$CLUSTER" --services "$svc" \
    --query 'services[0].deployments[0].rolloutState' --output text
done
```

**Expected:** Each service prints `COMPLETED`. No `ResourceInitializationError` in task events.

```bash
# Verify CloudTrail recorded GetSecretValue by the execution role (audit trail is the goal).
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=GetSecretValue \
  --max-results 5 --query 'Events[].CloudTrailEvent' --output text | jq -r '.userIdentity.arn' 2>/dev/null
```

**Expected:** The caller ARN matches a `*-task-exec-*` execution role, confirming secrets are pulled at task start and audited.

### 4.5 Rollback verification

```bash
cd "$TF_DIR"
# Break-glass: flip the flag off and re-apply.
terraform apply -var-file=terraform.tfvars -var 'use_secrets_manager_for_env=false'
```

**Expected:** New task-def revisions inject the values as plaintext `environment` again; services return to steady state. Note (documented behavior): the secret resources remain provisioned (not gated by the flag), and the plaintext values are re-written into the task-def JSON and state. This is the intended break-glass behavior, not a state-clean revert.

---

## 5. End-to-End API Tests

The migration spans three services but adds no new multi-service workflow. The meaningful E2E checks are (a) the app functions identically after migration, and (b) the security regression guard on the federation-token endpoint.

### 5.1 Post-migration functional smoke

```bash
# Registry health and a representative authenticated call still work with Secrets Manager-sourced creds.
curl -s -o /dev/null -w "%{http_code}\n" "$REGISTRY_URL/health"
```

**Expected:** `200`. The registry booted with `SECRET_KEY` and DB creds (first-tier, unchanged) plus the newly-migrated values, and serves requests.

### 5.2 GitHub App JWT signing (PEM fidelity)

```bash
# If GitHub App auth is configured, exercise a code path that signs a GitHub App JWT
# (e.g., a repository verification / installation-token fetch) and confirm no signing error.
# Inspect registry logs for a successful GitHub App token exchange vs. a PEM parse error.
aws logs tail "/ecs/${CLUSTER}/registry" --since 10m --format short | grep -iE "github.*(jwt|installation|token)" | tail -20
```

**Expected:** GitHub App JWT signing succeeds (no `ValueError`/PEM parse error). Confirms `GITHUB_APP_PRIVATE_KEY` survived the `tfvars -> secret_string -> ECS injection -> os.environ -> .replace("\\n","\n")` chain intact (`registry/services/github_auth.py:129-130`).

### 5.3 Negative: `not-configured` must never be a valid token (security regression guard)

```bash
# Ensure no truthy placeholder was injected: the literal must be rejected.
curl -s -o /dev/null -w "%{http_code}\n" \
  -X POST "$REGISTRY_URL/admin/federation-token" \
  -H "Authorization: Bearer not-configured"
```

**Expected:** `401` (or `403`), never `200`. This guards against a truthy sentinel being injected for an empty `REGISTRY_API_TOKEN`/`FEDERATION_STATIC_TOKEN` (`auth_server/server.py:2592`). If this returns `200`, a sentinel regression was introduced and must be fixed by count-gating.

### 5.4 Unit tests for the static-token map

```bash
# Extend the existing auth-server unit tests to assert an empty REGISTRY_API_TOKEN does not
# populate _STATIC_TOKEN_MAP with a placeholder admin credential.
uv run pytest tests/auth_server/unit/test_server.py -k "static_token" -v
```

**Expected:** Tests pass, including a new assertion that with `REGISTRY_API_TOKEN=""` the legacy static token is not registered and `Bearer not-configured` is not accepted.

---

## 6. Test Execution Checklist

- [ ] Section 1 (Functional) - marked Not Applicable (no new endpoints/CLI)
- [ ] Section 2 (Backwards Compat) - flag-off no-change, same env names, empty-var absent (no placeholder), no duplicate env/secret names
- [ ] Section 3 (UX) - Grafana admin login and provisioning verified; error-message clarity confirmed
- [ ] Section 4 (Deployment) - `validate` + `plan` (flag on/off), secret creation, IAM coverage, grafana exec-role grant, apply reaches steady state, CloudTrail audit, rollback
- [ ] Section 5 (E2E) - post-migration smoke, GitHub App JWT signing, `not-configured` rejection, static-token unit test
- [ ] `terraform validate` passes and `terraform plan` is clean for the representative tfvars (flag on and off)
- [ ] Unit tests added/updated under `tests/auth_server/unit/` for the static-token regression guard
- [ ] `uv run pytest tests/` passes with no regressions (application code is unchanged, so this should be a no-op baseline)
