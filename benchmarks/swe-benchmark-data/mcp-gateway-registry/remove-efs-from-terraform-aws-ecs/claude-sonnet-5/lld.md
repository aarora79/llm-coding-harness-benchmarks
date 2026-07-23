# Low-Level Design: Remove EFS from terraform/aws-ecs/

*Created: 2026-07-23*
*Author: Claude*
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

## Overview

### Problem Statement
`terraform/aws-ecs/` still provisions an Amazon EFS file system, mount targets, a dedicated security group, and six access points, even though the application now uses S3/DocumentDB for all persistent storage. Only two of the three ECS services still mount it (`auth-server`, `mcpgw-server`); `registry` was already migrated off EFS in a prior change. Of the two remaining consumers, `mcpgw-server`'s mount is unused dead weight (no code writes to `/app/data`), and `auth-server`'s mount is redundant because the file it depends on is already baked into the container image and the loader already has a fallback chain that finds it there.

### Goals
- Delete all EFS Terraform resources: the `module "efs"` block, its mount targets, security group, and access points, and the standalone egress-rule resource.
- Delete every ECS task-definition `volume`/`mountPoints` block that references an EFS access point.
- Delete every Terraform variable and output that exists solely to configure or expose EFS.
- Retire the `scopes-init` one-off ECS task and its Docker image/ECR repository, since their only purpose was to seed EFS.
- Confirm `terraform validate` and `terraform plan` succeed with only resource *removals* in the plan (no errors, no unrelated diffs).

### Non-Goals
- Do not change application source code (`registry/`, `auth_server/`, `servers/mcpgw/`). The scopes-loading fallback chain in `registry/common/scopes_loader.py` already works without `SCOPES_CONFIG_PATH` set.
- Do not migrate any data to S3/DocumentDB. Investigation confirmed no live data needs migrating (see Codebase Analysis).
- Do not touch the Helm/EKS deployment path (`charts/`) - it already has no EFS wiring.
- Do not modify `documentdb-elastic.tf.disabled` or any other already-disabled file.

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/modules/mcp-gateway/storage.tf` | Defines `module "efs"` (file system, mount targets, security group, 6 access points) and `aws_vpc_security_group_egress_rule.efs_all_outbound` | Delete entirely (182 lines) |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | Module input variables | Remove `efs_throughput_mode` (lines 260-268), `efs_provisioned_throughput` (lines 270-274) |
| `terraform/aws-ecs/modules/mcp-gateway/outputs.tf` | Module outputs | Remove `efs_id`, `efs_arn`, `efs_access_points` (lines 47-69) |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ECS service/task definitions for registry, auth-server, mcpgw-server, and others | Remove EFS `mountPoints`/`volume` blocks for auth-server and mcpgw-server; remove/repoint `SCOPES_CONFIG_PATH` |
| `terraform/aws-ecs/outputs.tf` | Root module outputs | Remove `mcp_gateway_efs_id`, `mcp_gateway_efs_arn`, `mcp_gateway_efs_access_points` (lines 67-81) |
| `terraform/aws-ecs/scripts/post-deployment-setup.sh` | Post-`apply` automation | Remove `mcp_gateway_efs_id` from `required_outputs` (line 218); remove the "EFS mode (default)" branch that calls `run-scopes-init-task.sh` (lines 548-566) |
| `terraform/aws-ecs/scripts/run-scopes-init-task.sh` | Builds/runs a one-off ECS task that writes `scopes.yml` onto the EFS `auth_config` access point | Delete entirely - only purpose was seeding EFS |
| `terraform/aws-ecs/codebuild.tf` | ECR repos + CodeBuild project for all service images | Remove `mcp-gateway-scopes-init` from `ecr_repositories` (line 33) and the `build_and_push mcp-gateway-scopes-init ...` line (line 270) and its mention in the docker-pull warm-up loop (line 219) |
| `docker/Dockerfile.scopes-init` | Busybox image that copies `scopes.yml` onto the EFS mount | Delete - no longer used once EFS is gone |
| `docker/Dockerfile.auth` (line 64) | Auth-server image build | No change - already `COPY auth_server/ /app/`, which lands `scopes.yml` at `/app/scopes.yml` |
| `registry/common/scopes_loader.py` (lines 125-164) | `load_scopes_from_yaml()` - reads `SCOPES_CONFIG_PATH`, or falls back to `Path(__file__).parent.parent.parent / "auth_server" / "scopes.yml"`, then to `.../auth_server/auth_config/scopes.yml`, else returns `{"group_mappings": {}}` with a warning (never raises) | Determines the exact path the auth-server env var must point at once EFS is gone - see correction below |
| `registry/audit/service.py` (lines 32-107) | `AuditLogger` - file-log parameters are explicitly deprecated; writes go to MongoDB/DocumentDB only | Confirms `/app/logs` EFS mount carries no live audit data |
| `registry/utils/logging_setup.py` (lines 69-145) | Always configures a console/stdout handler; also a `RotatingFileHandler` at `settings.log_dir`, which resolves to `/var/log/containers/ai-registry`, not `/app/logs` | Confirms application logs never depended on the `/app/logs` EFS mount |
| `servers/mcpgw/server.py` (entire file) | mcpgw FastMCP server - stateless, `mcp.run(..., stateless_http=True)` | Confirms nothing writes to `/app/data`; the mount is unused |
| `charts/mcpgw/templates/deployment.yaml` | Kubernetes deployment of the identical mcpgw container | Corroborates: no `volumeMounts`/`volumes` for `/app/data` on this path either |

### Existing Patterns Identified

1. **Registry service already shows the target end-state.** In `ecs-services.tf`, the `registry` container definition has:
   ```hcl
   # EFS volumes removed - registry now uses ephemeral storage and DocumentDB for persistence
   # Logs go to CloudWatch only
   mountPoints = []
   ```
   and at the task level:
   ```hcl
   # EFS volumes removed - registry uses ephemeral storage and DocumentDB for persistence
   volume = {}
   ```
   Files: `ecs-services.tf` (registry container block ~line 1367-1369, task `volume` ~line 1419-1420).
   How a future implementer should follow this: apply the exact same pattern (empty `mountPoints = []`, empty `volume = {}`, with a short comment) to the `auth-server` and `mcpgw-server` container/task blocks instead of deleting the `mountPoints`/`volume` keys outright, so the diff stays minimal and self-documenting and matches the precedent already in the file.

2. **CloudWatch logging is already wired for every service.** Every container block sets `enable_cloudwatch_logging = true` with a `cloudwatch_log_group_name` and retention. Files: `ecs-services.tf` (auth-server ~line 494-496, mcpgw-server ~line 1811-1813, registry ~line 1371-1373). No new observability wiring is needed - removing the `/app/logs` EFS mount does not remove any log path, since CloudWatch capture is independent of it.

3. **Module-external secrets/config are passed via environment variable blocks with a flat list of `{name, value}` objects.** File: `ecs-services.tf` auth-server container `environment` array, lines ~150-232. `SCOPES_CONFIG_PATH` (lines 219-222) is one entry in that list; removing it is a simple list-element deletion, following the same pattern used elsewhere in the file when a variable becomes unconditional (e.g. `AUTH0_ENABLED`).

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `modules/mcp-gateway/ecs-services.tf` auth-server task | Depends on | `module.efs.id`, `module.efs.access_points["logs"].id`, `module.efs.access_points["auth_config"].id` in the task-level `volume` block (lines 542-553) |
| `modules/mcp-gateway/ecs-services.tf` mcpgw-server task | Depends on | `module.efs.id`, `module.efs.access_points["mcpgw_data"].id` in the task-level `volume` block (lines 1859-1867) |
| `terraform/aws-ecs/outputs.tf` (root) | Depends on | `module.mcp_gateway.efs_id`, `module.mcp_gateway.efs_arn`, `module.mcp_gateway.efs_access_points` (lines 67-81) - unconditional (not `try()`/null-gated like the DocumentDB outputs), so they must be deleted, not just left dangling, or `terraform plan`/`apply` will error once the module outputs are removed |
| `scripts/post-deployment-setup.sh` | Depends on | `mcp_gateway_efs_id` in `required_outputs` array (line 218); calls `run-scopes-init-task.sh` in the non-DocumentDB branch (lines 548-566) |
| `scripts/run-scopes-init-task.sh` | Depends on | `jq -r '.mcp_gateway_efs_id.value'` and `.mcp_gateway_efs_access_points.value.auth_config` from `terraform-outputs.json`; builds an ECS task definition with an `efsVolumeConfiguration` block |
| `codebuild.tf` | Builds | `mcp-gateway-scopes-init` ECR repository and CodeBuild build step for `docker/Dockerfile.scopes-init` |
| `main.tf` (root) | Does NOT pass | No `efs_*` arguments are passed from root to `module.mcp_gateway` today - the child module's `efs_throughput_mode`/`efs_provisioned_throughput` variables always use their internal defaults. This means no root-level `main.tf` wiring change is needed beyond the outputs.tf deletion. |
| `variables.tf` (root), `terraform.tfvars.example` | N/A | Confirmed zero EFS references at the root level today. No change needed. |

### Constraints and Limitations Discovered
- **`private_subnet_ids` must NOT be removed.** It is a shared variable consumed by ECS service `subnet_ids` (7 call sites in `ecs-services.tf`), the ALB in `networking.tf:71`, and observability resources in `observability.tf:328,671`, in addition to the EFS `mount_targets` (`storage.tf:20`). Only the EFS-specific usage goes away; the variable itself stays untouched.
- **The `efs_access_points` output is already inconsistent with `storage.tf`.** It surfaces only 4 of the 6 access points (`servers`, `models`, `logs`, `auth_config` - missing `agents` and `mcpgw_data`). This is moot once the whole output is deleted, but is worth noting so the implementer does not try to "fix" the output instead of deleting it.
- **Auth-server's EFS dependency is behavioral, not just infrastructural, and the correct replacement value must be verified against the actual image layout, not assumed.** Unlike `mcpgw-server` (whose mount is provably unused - no code reads or writes `/app/data`), `auth-server` actively sets `SCOPES_CONFIG_PATH` to an EFS path. There is a second, pre-existing `SCOPES_CONFIG_PATH` occurrence in `ecs-services.tf` (in the `registry` service's environment block) with a *different* hardcoded value, `/app/auth_server/scopes.yml` - the two pre-change values already disagree with each other, which is itself a sign this configuration has drifted. Tracing the actual container layout: `docker/Dockerfile.auth` runs `COPY auth_server/ /app/` (flat copy - `auth_server/scopes.yml` lands at `/app/scopes.yml`), while `docker/Dockerfile.registry` runs `COPY auth_server/ /app/auth_server/` (nested copy - lands at `/app/auth_server/scopes.yml`). These are two different images with two different layouts. `scopes_loader.py`'s unset-path fallback computes `Path(__file__).parent.parent.parent / "auth_server" / "scopes.yml"`; since `scopes_loader.py` is installed at `/app/registry/common/scopes_loader.py` in both images, this resolves to `/app/auth_server/scopes.yml` in **both** cases - which matches the `registry` image's layout but does **not** match the `auth-server` image's flat-copy layout (`/app/scopes.yml`). Therefore, simply deleting the `auth-server` container's `SCOPES_CONFIG_PATH` entry and relying on the fallback, as an EFS-removal design might naively do, would silently regress `auth-server` to `{"group_mappings": {}}` (empty scopes, logged only as a warning, never a crash) for anyone running `storage_backend = "file"`. The correct fix is to **explicitly set** `auth-server`'s `SCOPES_CONFIG_PATH` to `/app/scopes.yml` (matching its own image's actual `COPY` destination) rather than deleting the variable outright - see Step 4a below. This also has the maintainability benefit of keeping the path visible and diffable in Terraform instead of depending on a Python fallback chain that does not itself distinguish between the two images' different layouts.
- **`storage_backend` defaults to `"documentdb"` for ECS (`terraform/aws-ecs/variables.tf:399`).** In the default deployment, scopes are already loaded from DocumentDB and the YAML/EFS path is never consulted at all (`scopes_loader.py:184-193`). The `file` backend (which does consult the YAML path) is a secondary, less-used configuration; the design must still support it correctly since it is not being removed as an option.
- **No IAM cleanup required.** `modules/mcp-gateway/iam.tf` has zero `elasticfilesystem:*` statements on either the task execution role or task role - Fargate's `efs_volume_configuration` with an access point does not need an explicit IAM grant in this module's IAM design. Nothing to remove there.
- **No security-group cross-references to clean up.** No service security group in `ecs-services.tf` references `module.efs.security_group_id`; the EFS module's own `create_security_group = true` output is only consumed inside `storage.tf` itself (the NFS ingress rule and the manual egress rule). Deleting `storage.tf` removes the security group and its rules atomically with no dangling references elsewhere.

## Architecture

### System Context Diagram (before)

```
                    +-------------------+
                    |   Private Subnets |
                    +-------------------+
                             |
        +--------------------+--------------------+
        |                    |                    |
  +-----v-----+       +------v------+      +-------v-------+
  | registry  |       | auth-server |      | mcpgw-server  |
  | (ephemeral| ------|  /app/logs  |------| /app/data     |
  |  + DocDB) |       |  /efs/auth_ |      | (unused mount)|
  +-----------+       |   config    |      +-------+-------+
                       +------+------+              |
                              |                      |
                       +------v----------------------v------+
                       |         Amazon EFS (module.efs)     |
                       | mount targets x N private subnets   |
                       | access points: servers, models,     |
                       |  logs, agents, auth_config,         |
                       |  mcpgw_data                         |
                       | security group: NFS 2049 from VPC   |
                       +--------------------------------------+
```

### System Context Diagram (after)

```
                    +-------------------+
                    |   Private Subnets |
                    +-------------------+
                             |
        +--------------------+--------------------+
        |                    |                    |
  +-----v-----+       +------v------+      +-------v-------+
  | registry  |       | auth-server |      | mcpgw-server  |
  | (ephemeral| ------| scopes.yml  |------| ephemeral     |
  |  + DocDB) |       | baked into  |      | container fs  |
  +-----------+       | image;      |      +---------------+
                       | logs to     |
                       | CloudWatch  |
                       +-------------+

  (No EFS. No mount targets. No EFS security group.)
```

### Sequence Diagram - auth-server scopes resolution (after change)

```
auth-server startup
  -> reload_scopes_config()
       -> storage_backend == "documentdb"?
            yes -> load_scopes_from_repository() [DocumentDB]  (default path, unaffected by this change)
            no  -> load_scopes_from_yaml(os.getenv("SCOPES_CONFIG_PATH"))  [now unset]
                     -> scopes_path falsy -> default to /app/scopes.yml (baked into image)
                     -> file exists -> parse and return group_mappings
```

### Component Diagram

```
terraform/aws-ecs/
  outputs.tf ------------------------------ [DELETE 3 EFS outputs]
  main.tf --------------------------------- [no change - no efs_* args passed]
  variables.tf ---------------------------- [no change - no EFS vars at root]
  terraform.tfvars.example ---------------- [no change - no EFS entries]
  codebuild.tf ----------------------------- [DELETE scopes-init ECR repo + build step]
  scripts/post-deployment-setup.sh -------- [DELETE efs output check + EFS branch]
  scripts/run-scopes-init-task.sh --------- [DELETE entire file]
  docker/Dockerfile.scopes-init ------------ [DELETE entire file]
  modules/mcp-gateway/
    storage.tf ---------------------------- [DELETE entire file]
    variables.tf --------------------------- [DELETE 2 EFS variables]
    outputs.tf ------------------------------ [DELETE 3 EFS outputs]
    ecs-services.tf -------------------------- [DELETE EFS volume/mountPoints for
                                                 auth-server + mcpgw-server;
                                                 DELETE SCOPES_CONFIG_PATH env entry]
```

## Data Models

No Pydantic models, dataclasses, or schemas are added, removed, or changed by this design. This is a pure infrastructure (Terraform HCL) change; the only "data model" touched is Terraform resource/variable/output declarations, covered in File Changes below.

## API / CLI Design

**Not Applicable** - this change removes infrastructure resources and shell-script branches; it does not add or modify any HTTP endpoint or CLI command signature. The only CLI-adjacent artifact removed is `scripts/run-scopes-init-task.sh`, which is deleted outright (not modified), and `scripts/post-deployment-setup.sh`'s existing `--skip-scopes` flag continues to work unchanged (it now simply skips a no-op EFS branch that no longer exists, or skips the DocumentDB init call, depending on backend).

## Configuration Parameters

### Removed Variables

| Variable Name | Type | Default | File | Reason for Removal |
|---------------|------|---------|------|---------------------|
| `efs_throughput_mode` | string | `"bursting"` | `modules/mcp-gateway/variables.tf:260-268` | Only consumed by `module.efs`, which is deleted |
| `efs_provisioned_throughput` | number | `100` | `modules/mcp-gateway/variables.tf:270-274` | Only consumed by `module.efs`, which is deleted |

No new environment variables or settings are introduced. No `.env.example`, Docker Compose, or Helm values changes are needed - EFS was never referenced there.

### Removed Outputs

| Output Name | File | Lines |
|-------------|------|-------|
| `efs_id`, `efs_arn`, `efs_access_points` | `modules/mcp-gateway/outputs.tf` | 47-69 |
| `mcp_gateway_efs_id`, `mcp_gateway_efs_arn`, `mcp_gateway_efs_access_points` | `outputs.tf` (root) | 67-81 |

### Deployment Surface Checklist
- [ ] Terraform module (`modules/mcp-gateway/`) - variables, outputs, storage.tf, ecs-services.tf (this is the primary surface for this change).
- [ ] Root Terraform (`terraform/aws-ecs/`) - outputs.tf.
- [ ] Shell automation (`scripts/post-deployment-setup.sh`, `scripts/run-scopes-init-task.sh`) - update/delete.
- [ ] CodeBuild/ECR wiring (`codebuild.tf`) - remove scopes-init repo and build step.
- [ ] Docker (`docker/Dockerfile.scopes-init`) - delete.
- [ ] `terraform.tfvars.example` - confirmed no entries to remove; no change needed.
- [ ] Helm/EKS (`charts/`) - confirmed no EFS wiring exists there; no change needed.

## New Dependencies

This change uses only existing dependencies. It removes a dependency on the `terraform-aws-modules/efs/aws` module (version `~> 2.0`), previously declared inline via the `source`/`version` arguments of `module "efs"` in `storage.tf`. No `required_providers` entry needs to change, since `versions.tf` only declares `aws` and `random` providers directly - the `efs` module's provider requirements are satisfied transitively by the same `aws` provider already required, so deleting the module introduces no provider-block change.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Delete the EFS storage module
**File:** `terraform/aws-ecs/modules/mcp-gateway/storage.tf`
**Action:** Delete the file entirely (182 lines: `module "efs"` at lines 4-163, and `resource "aws_vpc_security_group_egress_rule" "efs_all_outbound"` at lines 169-182).

```bash
git rm terraform/aws-ecs/modules/mcp-gateway/storage.tf
```

#### Step 2: Remove EFS variables
**File:** `terraform/aws-ecs/modules/mcp-gateway/variables.tf`
**Lines:** 259-274 (the `# EFS Configuration` comment plus both variable blocks)

Remove:
```hcl
# EFS Configuration
variable "efs_throughput_mode" {
  description = "Throughput mode for EFS (bursting or provisioned)"
  type        = string
  default     = "bursting"
  validation {
    condition     = contains(["bursting", "provisioned"], var.efs_throughput_mode)
    error_message = "EFS throughput mode must be either 'bursting' or 'provisioned'."
  }
}

variable "efs_provisioned_throughput" {
  description = "Provisioned throughput in MiB/s for EFS (only used if throughput_mode is provisioned)"
  type        = number
  default     = 100
}
```

#### Step 3: Remove EFS outputs from the module
**File:** `terraform/aws-ecs/modules/mcp-gateway/outputs.tf`
**Lines:** 47-69 (the `# EFS outputs` comment plus all three output blocks: `efs_id`, `efs_arn`, `efs_access_points`)

#### Step 4: Remove EFS wiring from auth-server task definition
**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`

4a. Update the `SCOPES_CONFIG_PATH` environment entry (lines 219-222) - do not delete it, repoint it:
```hcl
{
  name  = "SCOPES_CONFIG_PATH"
  value = "/efs/auth_config/auth_config/scopes.yml"
},
```
becomes:
```hcl
{
  name  = "SCOPES_CONFIG_PATH"
  value = "/app/scopes.yml"
},
```
This must be an explicit value, not a deleted entry. `docker/Dockerfile.auth` does `COPY auth_server/ /app/` (a flat copy), which places the image-baked `scopes.yml` at `/app/scopes.yml` inside the `auth-server` container - not at `/app/auth_server/scopes.yml`, which is where `scopes_loader.py`'s own unset-path fallback would look (that fallback path matches the `registry` image's nested `COPY auth_server/ /app/auth_server/` layout, not the `auth-server` image's flat layout - the two images copy `auth_server/` differently). Leaving the variable unset would silently degrade `auth-server` to empty scopes (`{"group_mappings": {}}`, logged only as a warning) for anyone running `storage_backend = "file"`. Setting it explicitly to `/app/scopes.yml` is both correct for this image's actual layout and keeps the effective path visible in Terraform rather than dependent on tracing Python fallback logic. Before shipping this change, verify the resolved path empirically by building the `auth-server` image and confirming `scopes.yml` exists at `/app/scopes.yml` inside the container (see `testing.md` section 4).

4b. Replace the auth-server container's `mountPoints` (lines 482-493):
```hcl
mountPoints = [
  {
    sourceVolume  = "mcp-logs"
    containerPath = "/app/logs"
    readOnly      = false
  },
  {
    sourceVolume  = "auth-config"
    containerPath = "/efs/auth_config"
    readOnly      = false
  }
]
```
with, following the `registry` service precedent, plus a comment that documents the `SCOPES_CONFIG_PATH` decision so a future reader does not need to trace `scopes_loader.py` to understand it:
```hcl
# EFS volumes removed - auth-server uses image-baked scopes.yml (see
# SCOPES_CONFIG_PATH=/app/scopes.yml above) and CloudWatch for logs
mountPoints = []
```

4c. Replace the auth-server task-level `volume` block (lines 542-553):
```hcl
volume = {
  mcp-logs = {
    efs_volume_configuration = {
      file_system_id     = module.efs.id
      access_point_id    = module.efs.access_points["logs"].id
      transit_encryption = "ENABLED"
    }
  }
  auth-config = {
    efs_volume_configuration = {
      file_system_id     = module.efs.id
      access_point_id    = module.efs.access_points["auth_config"].id
      transit_encryption = "ENABLED"
    }
  }
}
```
with:
```hcl
# EFS volumes removed - auth-server uses image-baked scopes.yml and CloudWatch logs
volume = {}
```

#### Step 5: Remove EFS wiring from mcpgw-server task definition
**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`

5a. Replace the mcpgw-server container's `mountPoints` (lines 1803-1809):
```hcl
mountPoints = [
  {
    sourceVolume  = "mcpgw-data"
    containerPath = "/app/data"
    readOnly      = false
  }
]
```
with:
```hcl
# EFS volume removed - mcpgw-server is stateless and does not persist data
mountPoints = []
```

5b. Replace the mcpgw-server task-level `volume` block (lines 1859-1867):
```hcl
volume = {
  mcpgw-data = {
    efs_volume_configuration = {
      file_system_id     = module.efs.id
      access_point_id    = module.efs.access_points["mcpgw_data"].id
      transit_encryption = "ENABLED"
    }
  }
}
```
with:
```hcl
# EFS volume removed - mcpgw-server is stateless and does not persist data
volume = {}
```

#### Step 6: Remove EFS outputs from the root module
**File:** `terraform/aws-ecs/outputs.tf`
**Lines:** 67-81 (the `# EFS Outputs` comment plus all three output blocks: `mcp_gateway_efs_id`, `mcp_gateway_efs_arn`, `mcp_gateway_efs_access_points`)

#### Step 7: Retire the scopes-init automation
**File:** `terraform/aws-ecs/scripts/post-deployment-setup.sh`

7a. Remove `"mcp_gateway_efs_id"` from the `required_outputs` array (line 218).

7b. Replace the `else` branch that runs `run-scopes-init-task.sh` (lines 548-566):
```hcl
    else
        # EFS mode (default)
        log_info "Using EFS storage backend"

        if [[ "$DRY_RUN" == "true" ]]; then
            log_info "[DRY RUN] Would run: $SCRIPT_DIR/run-scopes-init-task.sh --skip-build"
            STEPS_SKIPPED=$((STEPS_SKIPPED + 1))
            return 0
        fi

        log_info "Running scopes initialization task on EFS..."

        if "$SCRIPT_DIR/run-scopes-init-task.sh" --skip-build; then
            log_success "MCP scopes initialized on EFS!"
            STEPS_PASSED=$((STEPS_PASSED + 1))
        else
            log_error "Scopes initialization failed."
            STEPS_FAILED=$((STEPS_FAILED + 1))
            return 1
        fi
    fi
```
with a branch that reflects the image-baked fallback (no action required for the `file` backend, since `scopes.yml` ships in the auth-server image):
```hcl
    else
        # File-based storage backend - scopes.yml is already baked into the
        # auth-server image (docker/Dockerfile.auth); no initialization task needed.
        log_info "Using file storage backend - scopes.yml is bundled in the auth-server image"
        STEPS_SKIPPED=$((STEPS_SKIPPED + 1))
    fi
```

Also update the script's header comment (line 12: `# 6. Initializes MCP scopes on EFS`) to describe the DocumentDB-only initialization path, since the EFS path is being retired.

7c. Delete `terraform/aws-ecs/scripts/run-scopes-init-task.sh` entirely.

#### Step 8: Remove the scopes-init build artifacts
**File:** `terraform/aws-ecs/codebuild.tf`

8a. Remove `"mcp-gateway-scopes-init"` from the `ecr_repositories` set (line 33).

8b. Remove `mcp-gateway-scopes-init` from the docker-pull warm-up loop (line 219) and the `build_and_push mcp-gateway-scopes-init docker/Dockerfile.scopes-init . &` line (line 270).

**File:** `docker/Dockerfile.scopes-init` - delete entirely.

### Error Handling
No new error-handling code is introduced - this change is subtractive. The one behavioral edge that must be explicitly documented (not silently accepted): if an operator runs `storage_backend = "file"` and has been updating `auth_server/scopes.yml` live via the EFS mount (the pre-change workflow), that live-update capability goes away. Updating scopes for the `file` backend now requires rebuilding and redeploying the `auth-server` image. This is a real, not theoretical, operational capability regression - it must be called out in `variables.tf`'s `storage_backend` description and in the PR description, not left as an implicit side effect. See "`file` Backend Decision" below.

### `file` Backend Decision
`storage_backend = "file"` remains a supported, validated value after this change - it is not being deprecated. However, its scopes-update story changes from "write a file to a mount" to "rebuild and redeploy the `auth-server` image." This is judged acceptable because: (a) the ECS deployment's default and documented configuration is `storage_backend = "documentdb"` (`terraform/aws-ecs/variables.tf` default), for which this change is a complete behavioral no-op; (b) `file` backend on ECS was never advertised as supporting fast, rebuild-free scopes rotation as a feature - it inherited that capability incidentally from EFS being present for other reasons; (c) no new tooling is justified to preserve a capability that only exists as a side effect of infrastructure being removed for cost/complexity reasons. The implementer must update the `storage_backend` variable description in `variables.tf` to state this explicitly (see File Changes) so operators choosing `file` backend are not surprised. If fast, rebuild-free scopes rotation for `file` backend is later required, the LLD's Alternative 2 (S3-backed fetch) or an SSM Parameter Store-backed mount are the recommended follow-up designs - not a reason to block this change.

### AWS Backup Verification
Before applying this change, confirm no AWS Backup vault or plan targets this EFS file system: `aws backup list-protected-resources --query "Results[?ResourceType=='EFS']"`. A grep of this repository's Terraform for `aws_backup_vault`/`aws_backup_plan`/`aws_backup_selection` found zero matches, so no Terraform-managed backup plan exists for this file system - but a manually created, tag-based, or account-level default backup policy would not show up in this repo's source and must be checked against the live AWS account before `terraform apply` is run, since an active backup association can cause EFS file system deletion to fail or hang.

### Logging
No new logging is introduced. Existing `log_info`/`log_success`/`log_error` calls in `post-deployment-setup.sh` are updated to describe the new branch behavior (Step 7b) rather than reference EFS.

## Observability
No new tracing, metrics, or logging points are needed. Existing CloudWatch log groups (`/ecs/{name_prefix}-auth-server`, `/ecs/{name_prefix}-mcpgw`) already capture stdout from both services and are unaffected by this change.

## Scaling Considerations
Removing EFS mount targets and access points has no effect on ECS task scaling, since neither `auth-server` nor `mcpgw-server` used EFS for anything scaling-sensitive (session state, shared cache). auth-server's config is now purely image-baked (immutable per task, consistent across replicas by construction) and mcpgw-server was already stateless. This change slightly *simplifies* horizontal scaling reasoning, since there is no longer a shared NFS mount that all task replicas depend on.

## File Changes

### New Files
None.

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | 259-274 removed | Delete `efs_throughput_mode`, `efs_provisioned_throughput` |
| `terraform/aws-ecs/modules/mcp-gateway/outputs.tf` | 47-69 removed | Delete `efs_id`, `efs_arn`, `efs_access_points` |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ~219-222, 482-493, 542-553, 1803-1809, 1859-1867 removed/replaced | Remove `SCOPES_CONFIG_PATH` env entry; replace auth-server and mcpgw-server `mountPoints`/`volume` with empty equivalents |
| `terraform/aws-ecs/outputs.tf` | 67-81 removed | Delete `mcp_gateway_efs_id`, `mcp_gateway_efs_arn`, `mcp_gateway_efs_access_points` |
| `terraform/aws-ecs/scripts/post-deployment-setup.sh` | ~12, 218, 548-566 | Update header comment; remove `mcp_gateway_efs_id` from `required_outputs`; replace EFS branch with a no-op file-backend branch |
| `terraform/aws-ecs/codebuild.tf` | 33, 219, 270 | Remove `mcp-gateway-scopes-init` ECR repo entry and build step |

### Deleted Files

| File Path | Description |
|-----------|-------------|
| `terraform/aws-ecs/modules/mcp-gateway/storage.tf` | Entire EFS module and egress-rule resource |
| `terraform/aws-ecs/scripts/run-scopes-init-task.sh` | One-off ECS task automation for seeding EFS |
| `docker/Dockerfile.scopes-init` | Busybox image used only to seed EFS |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | 0 |
| New tests | ~40 (terraform validate/plan assertions, grep-based regression checks documented in testing.md) |
| Modified/removed code | ~330 (182 in storage.tf + ~55 in ecs-services.tf + ~19 in module outputs/variables + ~15 in root outputs.tf + ~25 in post-deployment-setup.sh + ~5 in codebuild.tf + ~150 in run-scopes-init-task.sh deleted + ~19 in Dockerfile.scopes-init deleted) |
| **Total** | **~330** |

## Testing Strategy
See `testing.md` for the full plan. In summary: `terraform validate` and `terraform plan` against an existing state must succeed and show only deletions; a grep-based regression check confirms no remaining case-insensitive `efs` match in `terraform/aws-ecs/**/*.tf` outside of explanatory comments; and a manual/staging verification confirms `auth-server` still serves scopes correctly (from the image-baked file) and `mcpgw-server` still starts and passes its health check with no mounted volume.

## Alternatives Considered

### Alternative 1: Keep EFS for auth-server, remove only for mcpgw-server and the three unused access points
**Description:** Since `auth-server` actively references an EFS-backed path via `SCOPES_CONFIG_PATH`, a more conservative change would keep its EFS mount and only remove the three dead access points (`servers`, `models`, `agents`) plus the unused `mcpgw-data` mount.
**Pros:** Smaller diff; avoids any behavior change for auth-server.
**Cons:** Leaves the EFS file system, its mount targets, and its security group provisioned (most of the cost and complexity the issue asks to eliminate), just for a single access point whose data is already redundant with the image-baked file.
**Why Rejected:** The task explicitly asks to delete the EFS file system entirely, and the codebase analysis confirms the image-baked `scopes.yml` plus the existing loader fallback chain make the EFS-backed path fully redundant, not just simplifiable.

### Alternative 2: Migrate scopes.yml to S3 and have auth-server fetch it at startup
**Description:** Instead of relying on the image-baked file, upload `scopes.yml` to an S3 bucket and have the auth-server download it on container start.
**Pros:** Allows updating scopes without rebuilding the auth-server image.
**Cons:** Requires new application code (an S3 client call in `scopes_loader.py` or an entrypoint script), a new IAM permission, and a new deployment surface (an S3 bucket + upload step) - all out of scope for a Terraform-only cleanup. It also does not match the stated problem framing ("the application uses S3/DocumentDB for all persistent storage" already, for the `documentdb` backend, which is the ECS default).
**Why Rejected:** Adds new application code and infrastructure to solve a problem (dynamic scopes updates for the `file` backend) that was not raised as a requirement. The `documentdb` backend, the ECS default, already provides this dynamism without EFS.

### Comparison Matrix

| Criteria | Chosen (full removal) | Alt 1 (partial removal) | Alt 2 (S3 migration) |
|----------|------------------------|--------------------------|------------------------|
| Complexity | Low | Medium | High |
| Meets stated goal (delete EFS entirely) | Yes | No | Yes, eventually |
| New app code required | No | No | Yes |
| New infra required | No | No | Yes (S3 bucket, IAM) |
| Cost reduction | Full (no EFS at all) | Partial | Full |

## Rollout Plan

**This change must be applied in two sequential `terraform apply` runs, not one.** Once the `mountPoints`/`volume` blocks are emptied, there is no remaining Terraform attribute reference from the `auth-server`/`mcpgw-server` ECS service resources to `module.efs` - the implicit dependency edge that exists today (via `efs_volume_configuration` attributes) disappears along with those attributes. Without an attribute-level dependency, Terraform's graph has no guarantee that the ECS service update (new task-definition revision, old tasks draining) completes before the EFS module's resources are destroyed in the same apply. Since EFS mount-target/access-point deletion can fail or hang if an old, still-draining task has an active NFS connection, and since this operation is irreversible (EFS data is unrecoverable once deleted), the safer procedure is:

- **Apply 1 - drain:** Change only `ecs-services.tf` (Steps 4-5: empty `mountPoints`/`volume`, repoint `SCOPES_CONFIG_PATH`). Run `terraform apply`, then confirm both services reach steady state with zero tasks on the old revision (`aws ecs wait services-stable`, or manually via `aws ecs describe-services`/`describe-tasks`). Confirm `auth-server` resolves scopes correctly (see `testing.md`) and `mcpgw-server` passes health checks with the new task definition.
- **Apply 2 - remove:** Delete `storage.tf`, the EFS variables and outputs (Steps 1-3, 6), and the scopes-init tooling (Steps 7-8). Run `terraform plan` and confirm the plan shows only removals with no unexpected diff, then apply.

Before Apply 2, run the AWS Backup check described above. Budget extra time for EFS mount-target ENI cleanup, which is asynchronous per subnet and can take several minutes; do not interrupt the apply if it appears to be waiting.

- Phase 1: Implementation (out of scope for this skill) - apply the file changes in Steps 1-8 above, split across the two applies described.
- Phase 2: Testing - run `terraform validate`, `terraform fmt -check`, and `terraform plan` against a real or sandbox state per `testing.md` for each apply; manually verify auth-server scopes resolution and mcpgw-server startup in a staging deployment before applying either stage to production.
- Phase 3: Deployment - apply Apply 1 during a low-traffic window and confirm steady state before proceeding to Apply 2. Confirm both services pass their health checks after each apply before considering rollout complete.

## Open Questions
None outstanding. The two items originally raised here (`file` backend's degraded scopes-update story, and destroy-ordering risk) have been resolved above as explicit decisions - see "`file` Backend Decision" and "Rollout Plan" - rather than left open, per review feedback.

## References
- `registry/common/scopes_loader.py` (fallback chain for `SCOPES_CONFIG_PATH`)
- `docker/Dockerfile.auth` (bakes `scopes.yml` into the image)
- `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` `registry` service (precedent for `mountPoints = []` / `volume = {}` pattern)
