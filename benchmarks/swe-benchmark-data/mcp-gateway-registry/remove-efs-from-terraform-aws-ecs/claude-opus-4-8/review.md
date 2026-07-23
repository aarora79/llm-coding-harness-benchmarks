# Expert Review: Remove Amazon EFS from the Terraform AWS ECS deployment

*Created: 2026-06-15*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

Five reviewer personas evaluated the design. Reviews are intentionally critical and
focus on real risks, not praise.

---

## Frontend Engineer - Pixel

**Focus:** UI/UX, components, state, API integration.

### Strengths
- The change has no frontend surface. The registry UI and Gradio target groups
  (`ecs-services.tf:1422-1429`) are untouched.

### Concerns
- **C1 (low):** If the auth-server's scopes load fails after repointing
  `SCOPES_CONFIG_PATH`, the user-visible symptom is authorization failures in the
  web UI (login works, but actions are denied). The failure would surface far from
  its cause. The design should ensure the post-deploy health gate catches this
  before declaring success.

### New libraries / infra dependencies required
- None.

### Better alternatives considered
- None applicable to the frontend.

### Recommendations
- Add a post-deploy UX smoke step: log in and confirm a scoped action succeeds, so a
  broken scopes path is caught as a red deploy rather than a silent permission bug.

### Questions for author
- Does any frontend config read an EFS-derived output (it should not)? Confirmed
  none in the analysis.

### Verdict: APPROVED

---

## Backend Engineer - Byte

**Focus:** API design, data models, business logic, performance.

### Strengths
- Strong reuse of the existing registry precedent. Making auth-server and mcpgw
  structurally identical to registry (`volume = {}`, no EFS mounts) is the
  lowest-risk path and keeps the three services consistent.
- Net deletion of ~230 lines reduces surface area and matches the project direction
  (default `storage_backend = documentdb`).

### Concerns
- **C2 (high):** The single biggest risk is `scopes.yml` provenance (Open Question
  1). Repointing `SCOPES_CONFIG_PATH` to `/app/auth_server/scopes.yml` only works if
  the auth-server image ships that file or loads scopes from DocumentDB. The design
  correctly flags this but treats it as a dependency. This MUST be verified before
  apply, not after, or auth-server starts with no scopes.
- **C3 (medium):** `mcpgw_data` (`/app/data`) durability is unverified (Open Question
  2). If mcpgw writes durable state there that is not in DocumentDB, removing the
  mount causes silent data loss on task replacement. "Ephemeral by analogy to
  registry" is an assumption, not a confirmation.
- **C4 (low):** The auth-server previously wrote logs to `/app/logs` on EFS. The app
  must tolerate a non-shared, ephemeral `/app/logs`. Almost certainly fine (it is a
  writable path either way), but worth an explicit check that nothing reads logs back
  across tasks. (Grounded: app logs default to `APP_LOG_DIR` = `/var/log/containers/ai-registry`,
  not `/app/logs`; audit logs are DocumentDB-backed with only 1-hour local retention,
  so the shared `/app/logs` copy was already ephemeral-by-policy.)
- **C2b (high) - path decision needs resolving (verified against the Dockerfiles):**
  The design repoints auth `SCOPES_CONFIG_PATH` to `/app/auth_server/scopes.yml`, but
  `docker/Dockerfile.auth` does `WORKDIR /app` + `COPY auth_server/ /app/`, so in the
  AUTH image the file actually lands at `/app/scopes.yml`. `/app/auth_server/scopes.yml`
  is the REGISTRY image's path (`Dockerfile.registry` copies to `/app/auth_server/`).
  Both confirmed by reading the two Dockerfiles. The issue's Out-of-Scope section already
  tracks "ship scopes.yml at `/app/auth_server/scopes.yml`" as a packaging dependency -
  but that requires an auth-image Dockerfile change. The LOWER-RISK alternative is to
  point auth at its existing baked path `/app/scopes.yml` and make NO image change.
  Additionally, in `file` mode `reload_scopes_config()` also invokes
  `FileScopeRepository.load_all()`, which hardcodes `/app/auth_server/scopes.yml`
  (+ an `auth_config/` fallback); on the auth image that hardcoded path does not exist,
  so `get_ui_scopes()` returns empty regardless of `SCOPES_CONFIG_PATH`. Implementer MUST
  pick one path, and for the `file` backend also reconcile the loader's hardcoded path.
  In the shipped `documentdb` backend this is a no-op (scopes load from the DB), so it is
  not a blocker for the recommended config - but it is the one item the artifacts leave
  under-specified.
- **C2c (medium):** `run-documentdb-init.sh` seeds only the admin scope
  (`registry-admins.json`); its `load-scopes.py` step is commented out. So a fresh
  DocumentDB cluster gets the admin bootstrap only - non-admin group definitions in
  `scopes.yml` are NOT propagated automatically. Confirm this is the intended seed, or
  re-enable a scopes-load step, else fresh clusters silently lack non-admin groups.

### New libraries / infra dependencies required
- None. Confirms removal of `terraform-aws-modules/efs/aws`.

### Better alternatives considered
- For `mcpgw_data`, if durability is required, ECS-managed EBS (LLD Alternative 3) is
  preferable to resurrecting EFS, but only if Open Question 2 proves a hard
  requirement.

### Recommendations
- Block the change on resolving Open Questions 1 and 2 with concrete evidence (grep
  the auth-server Dockerfile for `scopes.yml`; ask the mcpgw owner what `/app/data`
  holds). Until then this is not safe to apply to production.

### Questions for author
- Where is the authoritative copy of `scopes.yml` after this change?
- Is `/app/data` reconstructable from DocumentDB on a fresh task?

### Verdict: APPROVED WITH CHANGES (resolve C2 and C3 before apply)

---

## SRE/DevOps Engineer - Circuit

**Focus:** Deployment, monitoring, scaling, infrastructure.

### Strengths
- Removing EFS eliminates mount targets, an NFS security group, and a throughput-mode
  decision, and removes a documented class of slow/failed ECS task starts. Startup
  reliability should improve.
- The rollout plan correctly identifies that apply DESTROYS a stateful resource and
  requires a pre-apply snapshot/export. This is the most important operational point
  and the design gets it right.
- Collapsing two bootstrap scripts into one (DocumentDB only) reduces deploy
  complexity.

### Concerns
- **C5 (high):** State destruction is irreversible. The `terraform plan` will show
  the EFS file system and access points being destroyed. If an operator applies
  without exporting data, scopes/`mcpgw_data` history is lost. The plan mentions this
  but it deserves a hard gate (manual approval, documented runbook step), not just a
  bullet.
- **C6 (medium):** The post-deploy script previously fell back to EFS when no
  DocumentDB endpoint was present. After the change, environments configured with
  `storage_backend = "file"` (no DocumentDB provisioned) lose their scopes bootstrap
  entirely. The design says to fail loudly, which is correct, but the `file` backend
  scenario needs an explicit answer: how do `file`-backend deployments get scopes
  now? This may be an additional out-of-scope dependency.
- **C7 (low):** Orphaned external runbooks/CI calling `run-scopes-init-task.sh` or
  reading `mcp_gateway_efs_*` outputs will break (Open Question 3). Need a repo-wide
  and org-wide grep before deletion.
- **C7b (high):** Destroy-ordering race in a single apply. The ECS service uses
  `terraform-aws-modules/ecs/aws//modules/service ~> 6.0` with no
  `wait_for_steady_state` (module default is `false`), so `UpdateService` returns
  before new tasks are RUNNING or old tasks drain. Once the `volume`/`mountPoints`
  blocks are removed, the service resource no longer references `module.efs.*`, so
  Terraform's graph loses the edge that would force the service update to finish
  before the EFS destroy. Terraform can therefore delete mount targets (and the file
  system) while old task-def revisions are still draining with NFS mounted, and a task
  that scales/restarts against the old revision in that window fails to launch against
  a destroyed EFS id. `terraform validate`/`plan` stay green - the break surfaces only
  at apply/runtime. This is the strongest argument for the phased rollout below.

### New libraries / infra dependencies required
- None.

### Better alternatives considered
- A two-step rollout: first stop mounting EFS (apply), confirm services healthy for a
  bake period, then delete the EFS resources in a follow-up apply. This de-risks the
  destroy by separating "stop using" from "delete." Worth considering for production.

### Recommendations
- Split the rollout into "detach" then "destroy" applies for production.
- Add an explicit decision for the `file` storage-backend path (C6).
- Require manual plan review + data export as a documented gate (C5).

### Verdict: APPROVED WITH CHANGES (address C5/C6 in the rollout/runbook)

---

## Security Engineer - Cipher

**Focus:** AuthN/AuthZ, validation, OWASP, data protection.

### Strengths
- Removing EFS removes the NFS (port 2049) ingress security group and the broad
  all-outbound egress rule (`storage.tf:169-182`) - a net reduction in network
  attack surface.
- EFS encryption-at-rest and transit encryption are no longer needed; DocumentDB and
  CloudWatch have their own encryption controls already in place.
- Removing `elasticfilesystem:*` from the example IAM policy in README tightens the
  least-privilege guidance.

### Concerns
- **C8 (medium):** `scopes.yml` defines authorization scopes. Moving its source of
  truth (from EFS to in-image or DocumentDB) changes who can modify authorization
  policy and how. An in-image `scopes.yml` means scope changes require an image
  rebuild/redeploy (more controlled, arguably better); a DocumentDB-sourced one means
  DB write access can alter authz. The design should state which model is in effect
  so the threat model is clear.
- **C9 (low):** Ensure no EFS data being destroyed contains secrets that were only
  ever stored there. The pre-apply export step should include a check that nothing
  sensitive is uniquely on EFS. (Grounded: no AWS Backup plans or EFS snapshots exist
  in the module, so disposal is clean - but make it an explicit teardown checklist item.)
- **C9b (medium) - stale-authz tradeoff in `file` mode:** Baking `scopes.yml` into the
  image means it can no longer be hot-patched. In `file` mode an urgent authz
  correction (revoking a compromised group, closing an over-broad mapping) now needs an
  image rebuild + redeploy instead of an EFS edit + `/reload`. A lagging image = stale
  mappings, which is either an escalation (revocation not yet applied) or a lockout.
  Document this operational-security tradeoff; the `documentdb` backend (with a live
  `/reload` path) avoids it entirely and should be the recommended production backend.
- **C9c (low) - runtime-writable authz file:** The auth container does not set
  `readonlyRootFilesystem` and `/app` is owned by `appuser`, so a process compromise in
  `file` mode could rewrite `/app/scopes.yml` and self-escalate. Not a regression (the
  EFS mount was `readOnly = false` too), but since the config is now immutable-by-intent,
  this is the moment to harden it (read-only root FS + tmpfs scratch, or read-only mount).

### New libraries / infra dependencies required
- None.

### Better alternatives considered
- None; the change reduces surface area.

### Recommendations
- Document the post-change authorization-policy source of truth and its write-access
  model (ties to Backend C2 and Open Question 1).
- Confirm DocumentDB access is least-privilege now that it is the sole shared
  persistence tier.

### Verdict: APPROVED WITH CHANGES (document authz source-of-truth)

---

## SMTS (Overall) - Sage

**Focus:** Architecture, code quality, maintainability.

### Strengths
- The design is a disciplined "follow the precedent" refactor. It does not invent new
  mechanisms; it removes a legacy one and aligns three services on one pattern. This
  is exactly the right altitude for the stated task.
- Clear, file-and-line-precise implementation steps make this implementable by an
  entry-level engineer, satisfying the LLD bar.
- Honest Open Questions section surfaces the two real risks (scopes provenance,
  mcpgw_data durability) rather than papering over them.

### Concerns
- **C10 (high):** The design's correctness hinges entirely on two unverified
  assumptions (scopes provenance, mcpgw_data durability). These should be promoted
  from "Open Questions" to "Pre-conditions that block implementation." As written, an
  eager implementer could apply and break auth.
- **C11 (low):** `data.tf`'s `aws_vpc` removal is correctly gated on a grep, but the
  design should remind the implementer to also `terraform fmt` and re-run
  `validate` after each deletion to catch dangling references early.
- **C12 (low):** Consider whether `storage.tf` should be deleted vs emptied. Deleting
  is cleaner; the design recommends deletion, which is correct. Just ensure no
  `*.tf` include/glob assumptions break (none expected).
- **C13 (medium) - missed dangling references:** Beyond `terraform/README.md` and
  `post-deployment-setup.sh`, two live consumers of the retired script are not in the
  design's change set: (1) `docs/deployment-modes.md` (lines ~211, ~218) instructs
  users to run `./scripts/run-scopes-init-task.sh --skip-build` and mentions scopes
  "initialized on EFS"; (2) `terraform/aws-ecs/README.md` (~line 402) "Running scopes
  initialization". Deleting the script without fixing these leaves broken instructions.
  Add both to scope.
- **C14 (low) - orphaned build target:** `codebuild.tf` (lines ~33, ~219, ~270) still
  builds/pushes the `mcp-gateway-scopes-init` image, and `docker/Dockerfile.scopes-init`
  copies `scopes.yml` to an EFS mount (`/mnt`). Once `run-scopes-init-task.sh` is gone
  this image has no consumer. `codebuild.tf` is in `terraform/aws-ecs/` (in scope);
  either remove the scopes-init build target or add an explicit LLD note that it is
  knowingly retained as dead with a follow-up ticket. (`Dockerfile.scopes-init` sits
  outside `terraform/aws-ecs/`, so deferring that file is defensible.)
- **C15 (low) - required_outputs list:** `post-deployment-setup.sh` lists
  `mcp_gateway_efs_id` in its `required_outputs` validation array (~line 218). Removing
  only the fallback branch is insufficient - the array entry must also go, or
  post-deployment validation hard-fails on a now-nonexistent output.

### Better alternatives considered
- The phased "detach then destroy" rollout (raised by Circuit) is the main
  architectural refinement worth adopting for production safety.

### Recommendations
- Reclassify Open Questions 1 and 2 as blocking pre-conditions.
- Adopt the two-phase rollout for production.
- Otherwise proceed; the approach is sound and maintainable.

### Verdict: APPROVED WITH CHANGES (gate on pre-conditions; adopt phased rollout)

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED | 0 | Add login + scoped-action post-deploy smoke test |
| Backend (Byte) | APPROVED WITH CHANGES | 3 | Fix auth scopes path to `/app/scopes.yml` (C2b); verify scopes provenance (C2) and mcpgw_data durability (C3) before apply |
| SRE (Circuit) | APPROVED WITH CHANGES | 3 | Two-phase (or `wait_for_steady_state`) apply to avoid the destroy-ordering race (C7b); phased detach-then-destroy; gate on data export |
| Security (Cipher) | APPROVED WITH CHANGES | 0 (3 to document) | Document authz source-of-truth, stale-authz tradeoff, and runtime-writable-file hardening |
| SMTS (Sage) | APPROVED WITH CHANGES | 1 | Promote Open Questions 1-2 to blocking pre-conditions; add missed doc refs (C13) and codebuild target (C14) to scope; phased rollout |

**Consensus:** The approach is correct and low-complexity (net deletion, follows the
registry precedent), but it is NOT safe to apply as written. Blocking items, in order
of severity:

1. **Auth scopes path needs resolving (C2b, verified).** The artifacts repoint auth to
   `/app/auth_server/scopes.yml`, but the auth image bakes the file at `/app/scopes.yml`
   (`/app/auth_server/scopes.yml` is the registry image's path). Either point auth at
   `/app/scopes.yml` (no image change, lower risk) or add the tracked Dockerfile
   packaging change; for the `file` backend also reconcile `FileScopeRepository`'s
   hardcoded path. No-op under the shipped `documentdb` backend, but resolve it before
   implementation.
2. **Pre-conditions must be confirmed:** (a) `scopes.yml` has a non-EFS source of truth
   for auth-server (C2), and (b) `mcpgw_data` holds no durable state absent from
   DocumentDB (C3).
3. **Destroy-ordering race (C7b).** No `wait_for_steady_state` on the ECS service module
   means a single apply can destroy mount targets under still-draining tasks. Adopt a
   two-phase detach-then-destroy rollout (or set `wait_for_steady_state = true` for the
   detach apply).
4. **Scope expansion:** the change set must also cover the missed doc references
   (`docs/deployment-modes.md`, `terraform/aws-ecs/README.md` - C13), the
   `required_outputs` array entry (C15), and a decision on the orphaned
   `mcp-gateway-scopes-init` codebuild target (C14).

`documentdb` (or another `MONGODB_BACKENDS` value) is the only durable ECS backend after
this change; `storage_backend = "file"` on ECS becomes a data-loss configuration and
should be documented as unsupported (ideally fail-fast).

## Next Steps

1. Fix the auth `SCOPES_CONFIG_PATH` repoint to `/app/scopes.yml` (the auth image's
   actual baked location) and reconcile `FileScopeRepository`'s hardcoded
   `/app/auth_server/scopes.yml` (C2b). This is a concrete correctness fix, not just a
   pre-condition.
2. Resolve blocking pre-conditions C2/C3 (scopes provenance, mcpgw_data durability)
   with concrete evidence from the auth-server Dockerfile and mcpgw owner.
3. Adopt the phased detach-then-destroy rollout (or `wait_for_steady_state = true` on
   the detach apply) to eliminate the destroy-ordering race (C7b / Circuit / Sage).
4. Expand the change set: `docs/deployment-modes.md` and `terraform/aws-ecs/README.md`
   run-scopes-init references (C13), the `mcp_gateway_efs_id` entry in
   `post-deployment-setup.sh` `required_outputs` (C15), and a decision on the orphaned
   `mcp-gateway-scopes-init` codebuild target (C14).
5. Decide and document the scopes bootstrap story for the `file` storage backend (C6),
   and confirm `run-documentdb-init.sh` seeds only the admin scope by intent (C2c).
6. Document the post-change authorization-policy source of truth, the stale-authz
   image-rebuild tradeoff, and runtime-writable-file hardening (Cipher C8/C9b/C9c).
7. Repo-wide and org-wide grep for `run-scopes-init-task` and `mcp_gateway_efs_*`
   consumers before deletion (C7 / Open Question 3).
8. Proceed to implementation only after 1-2 are confirmed; then run the validation
   steps in `testing.md`.
