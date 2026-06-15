# Low-Level Design: Remove Amazon EFS from the Terraform AWS ECS deployment

*Created: 2026-06-15*
*Author: Claude (claude-opus-4-8)*
*Status: Draft*

## Table of Contents
1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Data Models](#data-models)
5. [API / CLI Design](#api--cli-design)
6. [Configuration Parameters](#configuration-parameters)
7. [New Dependencies](#new-dependencies)
8. [Implementation Details](#implementation-details)
9. [Observability](#observability)
10. [Scaling Considerations](#scaling-considerations)
11. [File Changes](#file-changes)
12. [Testing Strategy](#testing-strategy)
13. [Alternatives Considered](#alternatives-considered)
14. [Rollout Plan](#rollout-plan)
15. [Open Questions](#open-questions)
16. [References](#references)

## Overview

### Problem Statement

The Terraform AWS ECS deployment (`terraform/aws-ecs/`) provisions an Amazon EFS
file system with six access points, an NFS security group, a manual all-outbound
egress rule, and one mount target per private subnet. Only two of the three
application services still mount EFS:

- `auth-server` mounts `logs` (to `/app/logs`) and `auth_config` (to
  `/efs/auth_config`), and reads `SCOPES_CONFIG_PATH=/efs/auth_config/auth_config/scopes.yml`.
- `mcpgw` mounts `mcpgw_data` (to `/app/data`).

The `registry` service was already migrated off EFS to ephemeral storage plus
Amazon DocumentDB (`ecs-services.tf:1367`, `:1419`). Three access points
(`servers`, `models`, `agents`) are provisioned but mounted by nothing. This design
removes EFS entirely from the Terraform AWS ECS surface, following the registry
precedent.

### Goals

- Eliminate all EFS Terraform resources, variables, and outputs in
  `terraform/aws-ecs/`.
- Bring `auth-server` and `mcpgw` to the same EFS-free shape as `registry`
  (`volume = {}`, no EFS `mountPoints`, logs to CloudWatch, persistence via
  DocumentDB).
- Repoint auth-server scopes to the in-image path the registry already uses and
  bootstrap scopes through the existing DocumentDB init path.
- Remove the EFS-only `run-scopes-init-task.sh` bootstrap and its branch in
  `post-deployment-setup.sh`.
- Update documentation and the example IAM policy.
- Leave `terraform validate` clean and produce a `terraform plan` that destroys EFS
  resources without collateral changes.

### Non-Goals

- No changes to Python application code in `registry/`, `auth_server/`, or `mcpgw/`.
- No changes to Docker Compose, Podman, or Helm/EKS deployment surfaces.
- No migration of existing EFS data (operator responsibility; see Rollout Plan).
- No removal of the `file` storage backend from the Python allowlist.

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/modules/mcp-gateway/storage.tf` | Declares the entire EFS file system, 6 access points, NFS SG, egress rule | Deleted in full |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | All 3 ECS service/task definitions | `auth-server` and `mcpgw` EFS volumes/mounts removed; scopes env repointed |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | Module input variables | Remove `efs_throughput_mode`, `efs_provisioned_throughput` (lines 260-274) |
| `terraform/aws-ecs/modules/mcp-gateway/outputs.tf` | Module outputs | Remove `efs_id`, `efs_arn`, `efs_access_points` (lines 47-69) |
| `terraform/aws-ecs/modules/mcp-gateway/data.tf` | `data "aws_vpc" "vpc"` used by storage.tf | Remove the data source only if no other consumer remains |
| `terraform/aws-ecs/outputs.tf` | Root outputs | Remove `mcp_gateway_efs_id/_arn/_access_points` (lines 67-81) |
| `terraform/aws-ecs/variables.tf` | Root variables | No EFS variable present; `storage_backend` default is `documentdb` (line 399) |
| `terraform/aws-ecs/main.tf` | Wires `module "mcp_gateway"` | No EFS vars wired; no change needed |
| `terraform/aws-ecs/scripts/run-scopes-init-task.sh` | One-off ECS task that writes scopes.yml to the auth_config EFS access point | Removed |
| `terraform/aws-ecs/scripts/post-deployment-setup.sh` | Post-deploy orchestration; branches DocumentDB vs EFS (lines 517-568) | EFS branch removed |
| `terraform/aws-ecs/README.md` | Deployment docs incl. example IAM policy with `elasticfilesystem:*` | EFS references removed |
| `terraform/README.md` | Top-level Terraform docs | EFS mention removed |

### Existing Patterns Identified

1. **EFS-free service definition (the target pattern).** The `registry` service
   already shows exactly what an EFS-free service looks like:
   - Files: `ecs-services.tf:1367-1369` (`mountPoints = []`), `:1419-1420`
     (`volume = {}`).
   - How a future implementer should follow this: make `auth-server` and `mcpgw`
     structurally identical (empty `volume`, no EFS `mountPoints`,
     `enable_cloudwatch_logging = true`).

2. **In-image scopes path.** The registry already sets
   `SCOPES_CONFIG_PATH = "/app/auth_server/scopes.yml"` (`ecs-services.tf:821-822`),
   demonstrating the non-EFS scopes location. Auth-server should adopt the same
   value (currently `/efs/auth_config/auth_config/scopes.yml` at `:220-221`).

3. **Storage backend selection.** `storage_backend` (root default `documentdb`,
   `variables.tf:399`; module default `file`, module `variables.tf:454`) already
   drives persistence. DocumentDB env vars are wired into services
   (`DOCUMENTDB_HOST`, `DOCUMENTDB_DATABASE`, etc., `ecs-services.tf:297-319`).
   EFS is orthogonal to this selection and is pure legacy.

4. **DocumentDB bootstrap path.** `post-deployment-setup.sh:524-547` already detects
   a DocumentDB endpoint and runs `run-documentdb-init.sh` to initialize indexes and
   scopes. The EFS branch (`:549-568`) is the fallback to remove.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `registry` service | Pattern source | Provides the exact EFS-free task-definition shape to copy |
| DocumentDB module | Persistence target | `mcpgw_data` and scopes should rely on DocumentDB; env already wired |
| CloudWatch logging | Log sink | Already enabled on all services; replaces the EFS `logs` access point |
| `data.aws_vpc.vpc` | Removed dependency | Only used by storage.tf NFS ingress CIDR; safe to remove if unused elsewhere |
| `post-deployment-setup.sh` | Orchestration | Must always take the DocumentDB scopes path after change |

### Constraints and Limitations Discovered

- **scopes.yml provenance.** Auth-server currently obtains `scopes.yml` from the EFS
  `auth_config` access point, populated by `run-scopes-init-task.sh`. After removal,
  scopes must come from (a) the in-image file at `/app/auth_server/scopes.yml`, or
  (b) DocumentDB via `run-documentdb-init.sh`. This LLD assumes the registry's
  in-image path is valid for auth-server as well; confirm at implementation time
  (see Open Questions).
- **`mcpgw_data` semantics.** The `/app/data` mount holds runtime data whose
  durability requirements are not documented in Terraform. Removing it means data is
  ephemeral per task. Confirm with the mcpgw application owner that `/app/data` does
  not require cross-restart durability, or that DocumentDB covers it.
- **`efs_access_points` output gap.** The module output maps only `servers`,
  `models`, `logs`, `auth_config` (not `mcpgw_data`). Irrelevant after removal but
  noted for completeness.
- **State destruction.** On existing deployments, applying this change destroys the
  EFS file system and all its data. This is irreversible; operators must snapshot or
  confirm data is reproducible first.
- **Module-internal IAM.** EFS access is granted automatically by the
  `terraform-aws-modules/ecs/aws` service submodule when an `efs_volume_configuration`
  is present; there is no explicit `elasticfilesystem:*` statement in `iam.tf` to
  remove. Removing the volume removes the grant.

## Architecture

### System Context Diagram (before vs after)

```
BEFORE
                         +---------------------------+
                         |  Amazon EFS file system   |
                         |  access points:           |
                         |   servers (unused)        |
                         |   models  (unused)        |
                         |   agents  (unused)        |
                         |   logs        <-----------+----+ auth-server  /app/logs
                         |   auth_config <-----------+----+ auth-server  /efs/auth_config
                         |   mcpgw_data  <-----------+----+ mcpgw        /app/data
                         +------------+--------------+
                                      | NFS 2049 (SG ingress from VPC CIDR)
                         +------------+--------------+
                         |  EFS security group       |
                         +---------------------------+

   registry  --> (no EFS)  ephemeral storage + DocumentDB + CloudWatch logs

AFTER
   auth-server --> ephemeral storage + DocumentDB + CloudWatch logs
                   SCOPES_CONFIG_PATH = /app/auth_server/scopes.yml
   mcpgw       --> ephemeral storage + DocumentDB + CloudWatch logs
   registry    --> ephemeral storage + DocumentDB + CloudWatch logs   (unchanged)

   (no EFS file system, no access points, no NFS security group, no mount targets)
```

### Sequence Diagram: scopes bootstrap (after)

```
operator               post-deployment-setup.sh        run-documentdb-init.sh      DocumentDB
   |  run script                |                              |                       |
   |--------------------------->|  read terraform outputs      |                       |
   |                            |  documentdb endpoint present?|                       |
   |                            |---- yes -------------------->|  connect + init       |
   |                            |                              |--- write scopes ----->|
   |                            |<----- success ---------------|                       |
   |<--- scopes initialized ----|                              |                       |
   |                            |  (EFS branch deleted)        |                       |
```

### Component Diagram (task definition shape, after)

```
auth-server task definition
  containers:
    auth-server         env: SCOPES_CONFIG_PATH=/app/auth_server/scopes.yml, DOCUMENTDB_*
                        mountPoints: []           <-- was [mcp-logs, auth-config]
                        enable_cloudwatch_logging: true
    adot-collector      (unchanged, observability sidecar)
  volume: {}            <-- was {mcp-logs, auth-config} EFS volumes

mcpgw task definition
  containers:
    mcpgw-server        mountPoints: []           <-- was [mcpgw-data]
                        enable_cloudwatch_logging: true
    adot-collector      (unchanged)
  volume: {}            <-- was {mcpgw-data} EFS volume
```

## Data Models

This is an infrastructure change with no Python data models. The Terraform "data
model" affected is the ECS task-definition `volume` and `mountPoints` structures and
the module variable/output surface. No Pydantic models are added or changed.

## API / CLI Design

### Changed CLI / Scripts

**`scripts/post-deployment-setup.sh`** - the scopes initialization step
(`_init_scopes`, lines ~517-569) collapses from two branches to one.

**Invocation (unchanged interface):**
```bash
cd terraform/aws-ecs
./scripts/post-deployment-setup.sh           # full run
./scripts/post-deployment-setup.sh --dry-run # preview
./scripts/post-deployment-setup.sh --skip-scopes
```

**Expected behavior after change:** the script always initializes scopes through
`run-documentdb-init.sh`. The "Using EFS storage backend" / "Running scopes
initialization task on EFS" log lines are gone. If the DocumentDB endpoint is not
present in terraform outputs, the step logs a clear error rather than silently
falling back to EFS.

**Removed CLI:** `scripts/run-scopes-init-task.sh` is deleted. Any operator runbook
that calls it directly must switch to `run-documentdb-init.sh`.

**Error Cases:**
- Nonzero exit if `terraform-outputs.json` lacks `documentdb_cluster_endpoint`
  while scopes init is requested (was previously masked by the EFS fallback).

## Configuration Parameters

### Removed Variables

| Variable Name | Type | Old Default | File | Notes |
|---------------|------|-------------|------|-------|
| `efs_throughput_mode` | string | `bursting` | `modules/mcp-gateway/variables.tf:260` | No replacement |
| `efs_provisioned_throughput` | number | `100` | `modules/mcp-gateway/variables.tf:270` | No replacement |

No new variables are introduced.

### Removed Outputs

| Output | Scope | File |
|--------|-------|------|
| `efs_id`, `efs_arn`, `efs_access_points` | module | `modules/mcp-gateway/outputs.tf:48-69` |
| `mcp_gateway_efs_id`, `mcp_gateway_efs_arn`, `mcp_gateway_efs_access_points` | root | `outputs.tf:68-81` |

### Deployment Surface Checklist

Surfaces to verify carry no EFS references after the change:

- [ ] `terraform/aws-ecs/modules/mcp-gateway/storage.tf` (file deleted or emptied)
- [ ] `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` (auth-server, mcpgw)
- [ ] `terraform/aws-ecs/modules/mcp-gateway/variables.tf`
- [ ] `terraform/aws-ecs/modules/mcp-gateway/outputs.tf`
- [ ] `terraform/aws-ecs/modules/mcp-gateway/data.tf` (`aws_vpc` data source)
- [ ] `terraform/aws-ecs/outputs.tf`
- [ ] `terraform/aws-ecs/terraform.tfvars.example` (no `efs_*` lines present today)
- [ ] `terraform/aws-ecs/scripts/post-deployment-setup.sh`
- [ ] `terraform/aws-ecs/scripts/run-scopes-init-task.sh` (deleted)
- [ ] `terraform/aws-ecs/README.md` and `terraform/README.md`

## New Dependencies

This change uses only existing dependencies. It removes the dependency on the
`terraform-aws-modules/efs/aws` module (`storage.tf:5`, `version = "~> 2.0"`). No new
Terraform providers, modules, or Python packages are introduced.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Delete the EFS storage definition
**File:** `terraform/aws-ecs/modules/mcp-gateway/storage.tf`
**Lines:** entire file (1-183)

Delete the entire file, which contains only `module "efs"` (lines 4-163) and
`resource "aws_vpc_security_group_egress_rule" "efs_all_outbound"` (lines 169-182).
If the repository convention prefers keeping the filename, leave it with only a
header comment; otherwise remove the file. Deleting the file is cleaner.

#### Step 2: Remove EFS volumes and mounts from auth-server
**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`
**Lines:** ~482-493 (mountPoints), ~542-557 (volume)

Replace the auth-server container `mountPoints` block:
```hcl
# BEFORE
mountPoints = [
  { sourceVolume = "mcp-logs",    containerPath = "/app/logs",        readOnly = false },
  { sourceVolume = "auth-config", containerPath = "/efs/auth_config", readOnly = false }
]
# AFTER
# EFS volumes removed - auth-server uses ephemeral storage and DocumentDB for persistence.
# Logs go to CloudWatch only (enable_cloudwatch_logging = true below).
mountPoints = []
```

Replace the auth-server service `volume` block:
```hcl
# BEFORE
volume = {
  mcp-logs    = { efs_volume_configuration = { file_system_id = module.efs.id, access_point_id = module.efs.access_points["logs"].id,        transit_encryption = "ENABLED" } }
  auth-config = { efs_volume_configuration = { file_system_id = module.efs.id, access_point_id = module.efs.access_points["auth_config"].id, transit_encryption = "ENABLED" } }
}
# AFTER
# EFS volumes removed - auth-server uses ephemeral storage and DocumentDB for persistence
volume = {}
```

#### Step 3: Repoint the auth-server scopes path
**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`
**Lines:** ~219-222

```hcl
# BEFORE
{ name = "SCOPES_CONFIG_PATH", value = "/efs/auth_config/auth_config/scopes.yml" },
# AFTER
{ name = "SCOPES_CONFIG_PATH", value = "/app/auth_server/scopes.yml" },
```
This matches the value the registry service already uses (`ecs-services.tf:821-822`).
Confirm the auth-server image ships `scopes.yml` at this path, or that scopes are
loaded from DocumentDB at runtime (see Open Questions).

#### Step 4: Remove EFS volume and mount from mcpgw
**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`
**Lines:** ~1803-1809 (mountPoints), ~1859-1867 (volume)

```hcl
# BEFORE
mountPoints = [
  { sourceVolume = "mcpgw-data", containerPath = "/app/data", readOnly = false }
]
# AFTER
# EFS volumes removed - mcpgw uses ephemeral storage and DocumentDB for persistence
mountPoints = []

# BEFORE
volume = {
  mcpgw-data = { efs_volume_configuration = { file_system_id = module.efs.id, access_point_id = module.efs.access_points["mcpgw_data"].id, transit_encryption = "ENABLED" } }
}
# AFTER
volume = {}
```

#### Step 5: Remove EFS module variables
**File:** `terraform/aws-ecs/modules/mcp-gateway/variables.tf`
**Lines:** 259-274 (the `# EFS Configuration` comment block plus both variables)

Delete `variable "efs_throughput_mode"` and `variable "efs_provisioned_throughput"`
and the preceding section comment.

#### Step 6: Remove EFS module outputs
**File:** `terraform/aws-ecs/modules/mcp-gateway/outputs.tf`
**Lines:** 47-69 (the `# EFS outputs` comment plus `efs_id`, `efs_arn`,
`efs_access_points`)

#### Step 7: Remove EFS root outputs
**File:** `terraform/aws-ecs/outputs.tf`
**Lines:** 67-81 (`# EFS Outputs` comment plus `mcp_gateway_efs_id`,
`mcp_gateway_efs_arn`, `mcp_gateway_efs_access_points`)

#### Step 8: Remove the now-orphaned VPC data source (conditional)
**File:** `terraform/aws-ecs/modules/mcp-gateway/data.tf`
**Lines:** the `data "aws_vpc" "vpc"` block

Grep for `data.aws_vpc.vpc` across the module first. It is referenced only by
`storage.tf:37` today. If no other reference remains after Step 1, delete the data
source to avoid an unused-declaration. If any other file uses it, leave it.

#### Step 9: Delete the EFS scopes-init script
**File:** `terraform/aws-ecs/scripts/run-scopes-init-task.sh`

Delete the file. Grep the repo for references (`run-scopes-init-task`) and update or
remove them (notably Step 10 and any README/runbook).

#### Step 10: Collapse the post-deployment scopes branch
**File:** `terraform/aws-ecs/scripts/post-deployment-setup.sh`
**Lines:** ~517-569 (the `_init_scopes` function)

Remove the `else  # EFS mode (default)` branch (lines ~548-568) that calls
`run-scopes-init-task.sh`. Keep the DocumentDB branch (lines ~524-547). After the
edit, if the DocumentDB endpoint is missing, log a clear error and return nonzero
(do not silently skip). Also remove the EFS-flavored log lines and the
`mcp_gateway_efs_id` reference at line ~218 in the outputs-validation list.

#### Step 11: Update documentation
**Files:** `terraform/aws-ecs/README.md`, `terraform/README.md`

- Remove `"elasticfilesystem:*"` from the example IAM policy (README.md ~line 1056).
- Remove any architecture text describing EFS access points / NFS as a storage
  backend. Replace with the DocumentDB + ephemeral + CloudWatch description already
  used for the registry service.
- Update the `terraform/README.md` EFS mention.

#### Step 12: Validate
Run (manually, outside this skill's scope):
```bash
cd terraform/aws-ecs
terraform fmt -recursive
terraform validate
terraform plan   # confirm EFS resources are destroyed, nothing else unexpected
bash -n scripts/post-deployment-setup.sh
```

### Error Handling

- The post-deployment scopes step must fail loudly (nonzero exit, explicit log) when
  the DocumentDB endpoint is absent, rather than reverting to the deleted EFS path.
- `terraform plan` must be reviewed by an operator before apply because the change
  destroys a stateful resource (the EFS file system).

### Logging

- Preserve the existing `log_info` / `log_success` / `log_error` helpers in
  `post-deployment-setup.sh`. Remove only the EFS-specific messages. Keep the
  DocumentDB success/failure messages.
- All three services already set `enable_cloudwatch_logging = true`; no logging
  regressions are expected since the EFS `logs` access point was never the primary
  log sink.

## Observability

### Tracing / Metrics / Logging Points

- No tracing changes. The ADOT collector sidecars (`enable_observability`) are
  untouched on all services.
- **Logs:** auth-server previously also wrote to `/app/logs` on EFS. After removal,
  logs go solely to the existing CloudWatch log group
  `/ecs/${name_prefix}-auth-server`. Verify the auth-server image does not assume a
  writable `/app/logs` backed by a network volume; ephemeral local disk still
  satisfies a writable path, so this is expected to be a no-op for the app.
- **Metrics to watch post-deploy:** ECS task start success rate (EFS mount failures
  are eliminated, so this should improve), DocumentDB connection metrics, and
  CloudWatch log ingestion for all three services.

## Scaling Considerations

- **Positive impact on task startup.** Removing EFS mounts eliminates the NFS mount
  step during task placement, which removes a class of slow/failed starts when mount
  targets or the NFS security group are misconfigured.
- **No shared-state coupling.** With EFS gone, services scale horizontally without a
  shared file system as a contention or throughput-mode (bursting vs provisioned)
  concern. DocumentDB becomes the single shared persistence tier, which already
  scales independently.
- **Ephemeral storage sizing.** Tasks rely on the ECS task ephemeral storage default
  (21 GB) for any local writes. Confirm `mcpgw` `/app/data` working-set fits; if it
  ever needs more, raise `ephemeralStorage` on the task definition (out of scope
  unless a problem is observed).

## File Changes

### New Files

None.

### Deleted Files

| File Path | Reason |
|-----------|--------|
| `terraform/aws-ecs/modules/mcp-gateway/storage.tf` | Contains only EFS resources |
| `terraform/aws-ecs/scripts/run-scopes-init-task.sh` | EFS-only scopes bootstrap |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `modules/mcp-gateway/ecs-services.tf` | ~219-222, ~482-493, ~542-557, ~1803-1809, ~1859-1867 | Remove EFS volumes/mounts from auth-server and mcpgw; repoint SCOPES_CONFIG_PATH |
| `modules/mcp-gateway/variables.tf` | 259-274 | Remove two EFS variables |
| `modules/mcp-gateway/outputs.tf` | 47-69 | Remove three EFS outputs |
| `modules/mcp-gateway/data.tf` | aws_vpc block | Remove if no longer referenced |
| `outputs.tf` | 67-81 | Remove three root EFS outputs |
| `scripts/post-deployment-setup.sh` | ~218, ~517-569 | Remove EFS scopes branch and EFS output reference |
| `README.md` | ~1056 + architecture text | Remove `elasticfilesystem:*` and EFS description |
| `terraform/README.md` | EFS mention | Remove |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~10 (replacement comments / empty `volume = {}` blocks) |
| New tests | ~0 (validation via `terraform validate` + `plan`; no unit-test framework for TF here) |
| Deleted code | ~280 (storage.tf ~183 + run-scopes-init-task.sh ~80 + EFS blocks/outputs/vars) |
| Modified code | ~40 |
| **Net total** | **~ -230 (net deletion)** |

## Testing Strategy

See `./testing.md` for the full plan. Summary:
- Grep-based assertions that no functional EFS references remain in `terraform/`.
- `terraform validate` and `terraform plan` review (destroy-only of EFS resources).
- Backwards-compatibility checks for the `auth-server`/`mcpgw` task definitions vs
  the registry pattern.
- Shell syntax check (`bash -n`) for the modified post-deployment script.
- Post-deploy smoke test that scopes load from the DocumentDB path and all three
  services reach a healthy state.

## Alternatives Considered

### Alternative 1: Keep EFS but gate it behind a feature flag
**Description:** Add `enable_efs` and conditionally create the file system and
mounts.
**Pros:** Reversible; supports operators still relying on EFS.
**Cons:** Keeps all EFS code paths, the second bootstrap script, and the divergent
service shapes. Adds conditional complexity rather than removing it.
**Why Rejected:** The task is to remove EFS, and the registry already proves the
EFS-free path works. A flag would entrench the legacy path.

### Alternative 2: Replace EFS with an S3-backed config + sidecar sync
**Description:** Store `scopes.yml` in S3 and sync into the container on startup.
**Pros:** Durable, cheap, no NFS.
**Cons:** Introduces a new mechanism and IAM surface when DocumentDB and the in-image
path already cover the need; larger change than required.
**Why Rejected:** Out of scope and unnecessary; the codebase already standardized on
DocumentDB + in-image scopes.

### Alternative 3: Migrate `mcpgw_data` to a new EBS volume per task
**Description:** Use ECS-managed EBS volumes for `/app/data`.
**Pros:** Block storage durability without NFS.
**Cons:** EBS volumes are per-task, not shared; adds new resources; presumes
`/app/data` needs durability, which is unconfirmed.
**Why Rejected:** Premature. The registry precedent treats such data as ephemeral or
DocumentDB-backed. Pursue only if Open Question on `mcpgw_data` reveals a hard
durability requirement.

### Comparison Matrix

| Criteria | Chosen (remove EFS) | Alt 1 (flag) | Alt 2 (S3) | Alt 3 (EBS) |
|----------|---------------------|--------------|------------|-------------|
| Complexity | Low (net deletion) | Med | Med-High | Med |
| Matches existing pattern | Yes (registry) | No | No | No |
| New AWS surface | None | None | S3 + IAM | EBS |
| Reversibility | Via VCS revert | Runtime flag | n/a | n/a |
| Effort | Low | Med | High | Med |

## Rollout Plan

- **Phase 0 (pre-req, operator):** For any live EFS-backed environment, snapshot or
  export EFS contents and confirm scopes exist in DocumentDB (or in the auth-server
  image). This is mandatory because apply destroys the file system.
- **Phase 1 (implementation, out of scope for this skill):** Apply the file changes
  in Steps 1-11.
- **Phase 2 (validation):** `terraform fmt`, `terraform validate`, `terraform plan`
  review (destroy-only of EFS), `bash -n` on the modified script. Deploy to a
  non-production environment first.
- **Phase 3 (deployment):** `terraform apply`, then run
  `./scripts/post-deployment-setup.sh` and confirm scopes initialize via DocumentDB
  and all three services become healthy.
- **Rollback:** Revert the commit and `terraform apply` to recreate EFS. Note that
  recreated EFS will be empty; scopes must be re-initialized (DocumentDB path is
  unaffected by rollback).

## Open Questions

1. **scopes.yml provenance for auth-server.** Does the auth-server container image
   ship `scopes.yml` at `/app/auth_server/scopes.yml` (like registry), or must
   scopes be read from DocumentDB? If neither, packaging the file into the image is a
   prerequisite (tracked as a dependency, not done here).
2. **`mcpgw_data` durability.** What is written to `/app/data` in mcpgw, and does it
   require persistence across task restarts? If yes, the data must be confirmed to
   live in DocumentDB before removing the mount; otherwise Alternative 3 (EBS) may be
   needed.
3. **External runbooks.** Are there operator runbooks or CI jobs outside the repo
   that call `run-scopes-init-task.sh` or read the `mcp_gateway_efs_*` outputs? Those
   must be updated in lockstep.

## References

- Registry EFS-free precedent: `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf:1367-1369`, `:1419-1420`, `:821-822`.
- EFS module: `terraform/aws-ecs/modules/mcp-gateway/storage.tf:4-6` (`terraform-aws-modules/efs/aws ~> 2.0`).
- Storage backend variable: `terraform/aws-ecs/variables.tf:380-409` (default `documentdb`).
- DocumentDB scopes bootstrap: `terraform/aws-ecs/scripts/post-deployment-setup.sh:524-547`.
- Project standards: repository `CLAUDE.md` (logging, modularity, no emojis, "Amazon Bedrock" naming).
