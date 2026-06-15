# GitHub Issue: Remove Amazon EFS from the Terraform AWS ECS deployment

## Title
Remove Amazon EFS from the Terraform AWS ECS deployment (auth-server and mcpgw services)

## Labels
- enhancement
- infra
- terraform
- refactor

## Description

### Problem Statement

The Terraform AWS ECS deployment under `terraform/aws-ecs/` still provisions an
Amazon Elastic File System (EFS) file system, six access points, an NFS security
group, and a manual egress rule, even though the registry service was already
migrated off EFS to ephemeral storage plus Amazon DocumentDB (see the comment at
`terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf:1367`,
`# EFS volumes removed - registry now uses ephemeral storage and DocumentDB for persistence`).

The remaining EFS coupling causes ongoing problems:

- **Inconsistent architecture.** Only the `auth-server` and `mcpgw` services still
  mount EFS. The `registry` service already runs without it. The `servers`,
  `models`, and `agents` access points are provisioned but mounted by no service.
- **Cost and operational overhead.** EFS file systems, mount targets (one per
  private subnet), and the NFS security group are billed and must be monitored,
  patched, and reasoned about during incident response.
- **Slower, more fragile deployments.** EFS mount targets must be created in every
  private subnet before tasks can start, and the `scopes.yml` bootstrap depends on
  a separate one-off ECS task (`scripts/run-scopes-init-task.sh`) that mounts the
  `auth_config` access point. This is an extra moving part that the DocumentDB
  path (`scripts/run-documentdb-init.sh`) does not need.
- **Drift from the intended design.** The default `storage_backend` at the root
  module is already `documentdb` (`terraform/aws-ecs/variables.tf:399`). EFS is a
  legacy persistence layer the project is actively moving away from.

### Proposed Solution

Remove EFS from the Terraform AWS ECS deployment entirely, following the precedent
the `registry` service already set:

1. Delete the EFS file system, access points, NFS security group, and egress rule
   in `modules/mcp-gateway/storage.tf`.
2. Remove the EFS `volume {}` blocks and EFS `mountPoints` from the `auth-server`
   and `mcpgw` task definitions; route persistence through DocumentDB and route
   logs to CloudWatch only (as `registry` already does).
3. Repoint the auth-server `SCOPES_CONFIG_PATH` from the EFS path
   (`/efs/auth_config/auth_config/scopes.yml`) to the in-image path the registry
   already uses (`/app/auth_server/scopes.yml`), and bootstrap scopes through the
   existing DocumentDB initialization path rather than the EFS scopes-init task.
4. Remove the `efs_throughput_mode` and `efs_provisioned_throughput` variables, the
   `efs_*` module outputs, and the `mcp_gateway_efs_*` root outputs.
5. Retire `scripts/run-scopes-init-task.sh` and the EFS branch in
   `scripts/post-deployment-setup.sh` so post-deployment always uses the DocumentDB
   scopes initialization.
6. Update documentation (`terraform/aws-ecs/README.md`, `terraform/README.md`) to
   drop EFS from the architecture description and the example IAM policy.

### User Stories

- As a **platform operator**, I want the ECS deployment to provision no EFS
  resources so that I have fewer billable, patchable, monitorable components.
- As a **developer deploying the stack**, I want a single, consistent persistence
  story (DocumentDB plus ephemeral local storage) across all services so that I do
  not have to reason about which service mounts a network file system.
- As a **release engineer**, I want post-deployment bootstrap to use one code path
  (DocumentDB scopes init) so that deployments are faster and have fewer failure
  modes.

### Acceptance Criteria

- [ ] `terraform/aws-ecs/modules/mcp-gateway/storage.tf` no longer declares any EFS
      file system, access point, mount target, NFS security group, or egress rule.
- [ ] The `auth-server` and `mcpgw` task definitions declare `volume = {}` and
      contain no EFS `mountPoints` (matching the `registry` service pattern).
- [ ] Auth-server `SCOPES_CONFIG_PATH` no longer references any `/efs/...` path.
- [ ] `efs_throughput_mode` and `efs_provisioned_throughput` variables are removed
      from both `variables.tf` files; no references remain.
- [ ] `efs_id`, `efs_arn`, `efs_access_points` module outputs and
      `mcp_gateway_efs_id`, `mcp_gateway_efs_arn`, `mcp_gateway_efs_access_points`
      root outputs are removed.
- [ ] `scripts/run-scopes-init-task.sh` is removed and
      `scripts/post-deployment-setup.sh` no longer has an EFS branch.
- [ ] `grep -ri 'efs\|elasticfilesystem\|access_point\|mount_target' terraform/`
      returns no functional Terraform references (documentation history excepted as
      noted in scope).
- [ ] `terraform validate` succeeds for `terraform/aws-ecs/`.
- [ ] `terraform plan` against an existing EFS-backed state shows the EFS resources
      being destroyed and no unintended changes to unrelated resources.
- [ ] README and architecture docs no longer describe EFS as a storage backend and
      the example IAM policy no longer grants `elasticfilesystem:*`.

### Out of Scope

- Changing the registry service (already EFS-free).
- Modifying the Python application code in `registry/`, `auth_server/`, or `mcpgw/`
  (the `SCOPES_CONFIG_PATH` is read by existing code; only the Terraform-provided
  value changes). If the auth-server image does not already ship `scopes.yml` at
  `/app/auth_server/scopes.yml`, that packaging change is tracked as a dependency,
  not done here.
- Docker Compose, Podman, and Helm/EKS deployment surfaces. This issue is limited
  to `terraform/aws-ecs/`.
- Any data migration of existing EFS contents. Operators must confirm DocumentDB
  holds the authoritative scopes before applying (see Dependencies).
- Removing the `file` storage backend option from the Python layer.

### Dependencies

- The auth-server container image must provide `scopes.yml` at
  `/app/auth_server/scopes.yml` (the path the registry already uses), or scopes must
  be initialized into DocumentDB by `scripts/run-documentdb-init.sh`. Confirm the
  current image/bootstrap before merging.
- Whatever was persisted to the `mcpgw_data` EFS access point (`/app/data`) must be
  confirmed as either reconstructable, ephemeral, or already stored in DocumentDB
  before the mount is removed.
- Existing deployments with live EFS-backed state require a one-time `terraform
  apply` that destroys the EFS resources; operators should snapshot/export any
  needed EFS data first.

### Related Issues

- Storage backend allowlist work (referenced in `variables.tf` as issue #954).
- Registry EFS removal (precedent; comment at `ecs-services.tf:1367`).
