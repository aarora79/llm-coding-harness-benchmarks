# Testing Plan: Remove Amazon EFS from the Terraform AWS ECS deployment

*Created: 2026-06-15*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This is an infrastructure-as-code change in `terraform/aws-ecs/`. Testing verifies
that (a) no functional EFS references remain in Terraform, scripts, or docs, (b) the
configuration still validates and plans cleanly with EFS resources destroyed and
nothing else unexpectedly changed, and (c) after deploy, scopes initialize via the
DocumentDB path and all three ECS services reach a healthy state without EFS mounts.

There is no Python unit-test surface for this change (it touches Terraform and shell,
not application code), so testing centers on static assertions, `terraform validate`
/ `plan`, shell syntax checks, and a post-deploy smoke test.

### Prerequisites
- [ ] Terraform CLI installed (matching the version pinned in `versions.tf`).
- [ ] AWS credentials for a non-production account configured (for `plan`/`apply`).
- [ ] The `terraform/aws-ecs/repo` working tree at tag `1.24.4` with the change applied.
- [ ] `jq`, `grep`, `bash` available locally.
- [ ] For the post-deploy smoke test: a deployed stack with `storage_backend = "documentdb"`.

### Shared Variables
```bash
export TF_DIR="terraform/aws-ecs"
export MODULE_DIR="$TF_DIR/modules/mcp-gateway"
# After deploy, terraform outputs are saved here by save-terraform-outputs.sh:
export OUTPUTS_FILE="$TF_DIR/terraform-outputs.json"
```

---

## 1. Functional Tests

### 1.1 curl / HTTP Tests

**Not Applicable** - This change adds or modifies no HTTP endpoint. The auth-server,
registry, and mcpgw HTTP interfaces are unchanged; only their underlying storage
wiring changes. End-to-end HTTP behavior is covered indirectly by the smoke test in
Section 5.

### 1.2 CLI Tests

The only CLI surface affected is the post-deployment script. Verify its interface is
intact and the EFS bootstrap script is gone.

#### 1.2.1 Static: no functional EFS references remain in Terraform
```bash
# Expect: no matches in *.tf files (documentation history is checked separately).
grep -rn -i 'efs\|elasticfilesystem\|access_point\|mount_target\|efs_volume_configuration' \
  "$TF_DIR" --include='*.tf'
echo "exit=$?"   # grep exit 1 (no matches) is the PASS condition
```
**Expected:** No output; grep exits nonzero (no matches).
**Assertion:** Zero `*.tf` lines reference EFS.

#### 1.2.2 Static: storage.tf and the scopes-init script are deleted
```bash
test ! -f "$MODULE_DIR/storage.tf" && echo "PASS: storage.tf removed" || echo "FAIL: storage.tf still present"
test ! -f "$TF_DIR/scripts/run-scopes-init-task.sh" && echo "PASS: scopes-init script removed" || echo "FAIL: still present"
```
**Expected:** Both PASS lines.

#### 1.2.3 Static: EFS variables and outputs are gone
```bash
grep -rn 'efs_throughput_mode\|efs_provisioned_throughput' "$TF_DIR"   ; echo "vars exit=$?"
grep -rn 'efs_id\|efs_arn\|efs_access_points\|mcp_gateway_efs' "$TF_DIR" --include='*.tf' ; echo "outputs exit=$?"
```
**Expected:** No matches; both greps exit nonzero.

#### 1.2.4 Static: auth-server scopes path repointed
```bash
grep -n 'SCOPES_CONFIG_PATH' "$MODULE_DIR/ecs-services.tf"
```
**Expected:** Every `SCOPES_CONFIG_PATH` value is `/app/auth_server/scopes.yml`. No
occurrence of `/efs/...` remains.

#### 1.2.5 Static: services match the registry EFS-free pattern
```bash
# All three services should declare empty volume blocks and no EFS mountPoints.
grep -n 'volume = {}' "$MODULE_DIR/ecs-services.tf"        # expect 3 occurrences (auth, registry, mcpgw)
grep -n 'efs_volume_configuration' "$MODULE_DIR/ecs-services.tf" ; echo "efs vol exit=$?"  # expect none
```
**Expected:** Three `volume = {}` matches; zero `efs_volume_configuration` matches.

#### 1.2.6 terraform fmt and validate
```bash
cd "$TF_DIR"
terraform fmt -recursive -check    # expect: clean, exit 0
terraform init -backend=false      # for validate without remote state
terraform validate                 # expect: "Success! The configuration is valid."
```
**Expected:** `fmt -check` reports no changes; `validate` succeeds.

#### 1.2.7 Shell syntax check for the modified post-deployment script
```bash
bash -n "$TF_DIR/scripts/post-deployment-setup.sh" && echo "PASS: syntax ok"
# And confirm the EFS branch is gone:
grep -n 'run-scopes-init-task\|Using EFS storage backend\|initialized on EFS' \
  "$TF_DIR/scripts/post-deployment-setup.sh" ; echo "efs-branch exit=$?"
```
**Expected:** `PASS: syntax ok`; the grep finds nothing (exit nonzero).

**Negative case:** Re-introduce an `efs_volume_configuration` block temporarily and
re-run 1.2.1 - it must FAIL (grep finds the match). This confirms the assertion is
real and not vacuously passing. Revert afterward.

---

## 2. Backwards Compatibility Tests

This change touches existing task definitions, variables, and outputs, so
backwards-compatibility verification is required.

### 2.1 Removed outputs do not break the outputs save step
```bash
# save-terraform-outputs.sh writes terraform-outputs.json. Confirm it no longer
# expects the EFS outputs.
grep -n 'efs' "$TF_DIR/scripts/save-terraform-outputs.sh" ; echo "exit=$?"
```
**Expected:** No EFS references (exit nonzero), or the script reads outputs generically
and is unaffected. If it hardcoded `mcp_gateway_efs_*`, that must be removed.

### 2.2 post-deployment-setup outputs-validation list no longer requires EFS
```bash
grep -n 'mcp_gateway_efs_id' "$TF_DIR/scripts/post-deployment-setup.sh" ; echo "exit=$?"
```
**Expected:** No match (the `mcp_gateway_efs_id` entry at the old line ~218 is removed
from the required-outputs validation list).

### 2.3 Registry service is unchanged
```bash
# The registry task definition should be byte-identical to pre-change except for
# unrelated edits. Diff the registry block region against the base ref.
git -C "$TF_DIR/.." diff 1.24.4 -- "$MODULE_DIR/ecs-services.tf" \
  | grep -A3 -B3 'registry' | head -40
```
**Expected:** No semantic changes inside the registry container/volume blocks (it was
already EFS-free). Only auth-server and mcpgw blocks change.

### 2.4 plan against an existing EFS-backed state: destroy-only, no collateral
```bash
cd "$TF_DIR"
terraform plan -out=remove-efs.tfplan
terraform show -no-color remove-efs.tfplan | grep -E '^( +# |Plan:)'
```
**Expected:**
- The plan summary (`Plan: X to add, Y to change, Z to destroy`) shows the EFS file
  system, access points, mount targets, NFS security group, and egress rule under
  destroy.
- No unrelated resource is destroyed or replaced. Changes to auth-server/mcpgw task
  definitions are expected (volume/mount removal); registry is unchanged.
- Specifically confirm `module.mcp_gateway.module.efs.*` and
  `aws_vpc_security_group_egress_rule.efs_all_outbound` appear only as destroy.

**Assertion:** No `aws_db_*`, `aws_docdb_*`, ALB, or networking resource appears in
the destroy/replace set due to this change.

### 2.5 storage_backend defaults preserved
```bash
grep -n -A2 'variable "storage_backend"' "$TF_DIR/variables.tf"        # root default documentdb
grep -n -A2 'variable "storage_backend"' "$MODULE_DIR/variables.tf"    # module default file
```
**Expected:** Defaults are unchanged by this EFS-removal work (root `documentdb`,
module `file`). EFS removal must not alter storage backend selection.

---

## 3. UX Tests

### 3.1 CLI output clarity (post-deployment script)
After the edit, run a dry-run and confirm the messaging is coherent (single
DocumentDB path, no dangling EFS language):
```bash
cd "$TF_DIR"
./scripts/post-deployment-setup.sh --dry-run --skip-keycloak 2>&1 | grep -i -E 'scope|efs|documentdb'
```
**Expected:** Output references DocumentDB scopes initialization only. No "EFS" text.
If the DocumentDB endpoint is missing in a dry-run context, the message is an
actionable error, not a silent skip.

### 3.2 Web UI authorization still works
Covered by the end-to-end smoke test (Section 5.2): after deploy, log in to the
registry UI and confirm a scope-gated action succeeds, proving `scopes.yml` loaded
from the new path.

---

## 4. Deployment Surface Tests

### 4.1 Docker wiring

**Not Applicable** - This change is scoped to `terraform/aws-ecs/`. Docker Compose /
Podman surfaces (`docker-compose*.yml`) are out of scope and must not be modified.
Confirm no Compose file was touched:
```bash
git -C "$TF_DIR/.." diff --name-only 1.24.4 | grep -i 'docker-compose\|podman' ; echo "exit=$?"
```
**Expected:** No matches (exit nonzero).

### 4.2 Terraform / ECS wiring
The core of this change. Anchored on concrete files:
- `terraform/aws-ecs/modules/mcp-gateway/storage.tf` - deleted.
- `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` - auth-server/mcpgw
  `volume = {}`, no EFS `mountPoints`.
- `terraform/aws-ecs/modules/mcp-gateway/variables.tf`, `outputs.tf`,
  `terraform/aws-ecs/outputs.tf` - EFS vars/outputs removed.

```bash
cd "$TF_DIR"
terraform validate && echo "PASS: validate"
terraform plan | tail -5     # review Plan: line for destroy-only of EFS
```
**Expected:** Validate passes; plan destroys EFS resources only (see 2.4).

### 4.3 Helm / EKS wiring

**Not Applicable** - The Helm charts under `charts/` are a separate deployment surface
and are out of scope for this Terraform-only change. Confirm untouched:
```bash
git -C "$TF_DIR/.." diff --name-only 1.24.4 | grep '^charts/' ; echo "exit=$?"
```
**Expected:** No matches (exit nonzero).

### 4.4 Deploy and verify
In a non-production account:
```bash
cd "$TF_DIR"
terraform apply remove-efs.tfplan
./scripts/save-terraform-outputs.sh
# Confirm no EFS outputs are produced:
jq 'keys[]' "$OUTPUTS_FILE" | grep -i efs ; echo "exit=$?"   # expect no matches
```
**Expected:** Apply succeeds, EFS resources destroyed, and `terraform-outputs.json`
contains no `*efs*` keys.

Confirm ECS services start without EFS mounts:
```bash
CLUSTER=$(jq -r '.ecs_cluster_name.value // empty' "$OUTPUTS_FILE")
for svc in auth-server registry mcpgw; do
  aws ecs describe-services --cluster "$CLUSTER" \
    --services "$(jq -r ".${svc}_service_name.value // empty" "$OUTPUTS_FILE")" \
    --query 'services[0].{running:runningCount,desired:desiredCount}' --output text
done
```
**Expected:** Each service reaches `running == desired` with no task-start failures
referencing EFS mount errors (check `aws ecs describe-tasks` stoppedReason if any
task fails).

### 4.5 Rollback verification
```bash
# Revert the commit, then:
cd "$TF_DIR"
terraform plan   # expect EFS resources to be re-created (add), services re-mount
```
**Expected:** Plan shows EFS resources being added back. Note (documented in LLD): a
recreated EFS is empty; scopes must be re-initialized. Rollback restores
infrastructure shape, not EFS data.

---

## 5. End-to-End API Tests

### 5.1 Scopes initialize via DocumentDB (not EFS)
```bash
cd "$TF_DIR"
./scripts/post-deployment-setup.sh --skip-keycloak 2>&1 | tee /tmp/postdeploy.log
grep -i 'documentdb' /tmp/postdeploy.log     # expect scopes init via DocumentDB
grep -i 'efs' /tmp/postdeploy.log ; echo "exit=$?"   # expect no EFS lines
```
**Expected:** Log shows DocumentDB scopes initialization succeeding; no EFS lines.

### 5.2 Authorization end-to-end (proves scopes.yml loaded from new path)
After deploy, exercise a scope-gated workflow against the live registry:
```bash
export REGISTRY_URL=$(jq -r '.cloudfront_url.value // .registry_url.value' "$OUTPUTS_FILE")
# 1. Obtain an access token via the configured auth flow (Keycloak/Auth0 per env).
# 2. Call a scope-protected endpoint and confirm 200, not 403.
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$REGISTRY_URL/api/health"        # health is open; replace with a scoped route
# Then a genuinely scope-gated route:
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$REGISTRY_URL/api/<scope-protected-route>"
```
**Expected:** The scope-protected route returns 200 (or the correct authorized
status), confirming the auth-server loaded scopes from `/app/auth_server/scopes.yml`.
A 403 due to "no scopes loaded" is a FAIL and indicates the scopes-provenance
pre-condition (review C2) was not satisfied.

### 5.3 mcpgw service functions without its EFS data mount
```bash
# Exercise an mcpgw operation that previously relied on /app/data, confirm success
# and that data persists as expected per the resolved durability decision (review C3).
```
**Expected:** mcpgw serves requests; any data previously on `/app/data` is either
reconstructed, sourced from DocumentDB, or confirmed ephemeral per Open Question 2.

---

## 6. Test Execution Checklist
- [ ] Section 1 (Functional/static + validate) passes - no EFS in `*.tf`, files
      deleted, `terraform validate` succeeds, shell syntax ok
- [ ] Section 2 (Backwards Compat) verified - destroy-only plan, registry unchanged,
      storage_backend defaults preserved
- [ ] Section 3 (UX) verified - post-deploy messaging coherent; authz UI works
- [ ] Section 4 (Deployment) verified - apply destroys EFS only; services healthy;
      Docker/Helm surfaces untouched; rollback plan re-creates EFS
- [ ] Section 5 (E2E) verified - scopes init via DocumentDB; scope-gated route 200;
      mcpgw functional
- [ ] No EFS references remain: `grep -rn -i 'efs\|elasticfilesystem' terraform/ --include='*.tf'`
      returns nothing
- [ ] `terraform fmt -recursive -check` clean
- [ ] `terraform validate` passes
- [ ] `bash -n scripts/post-deployment-setup.sh` passes
- [ ] Pre-conditions from `review.md` (scopes provenance C2, mcpgw_data durability C3)
      confirmed before production apply
