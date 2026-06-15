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
  across tasks.

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
  sensitive is uniquely on EFS.

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
| Backend (Byte) | APPROVED WITH CHANGES | 2 | Verify scopes provenance (C2) and mcpgw_data durability (C3) before apply |
| SRE (Circuit) | APPROVED WITH CHANGES | 2 | Phased detach-then-destroy rollout; answer `file`-backend scopes path; gate on data export |
| Security (Cipher) | APPROVED WITH CHANGES | 0 (1 to document) | Document authz source-of-truth and write-access model |
| SMTS (Sage) | APPROVED WITH CHANGES | 1 | Promote Open Questions 1-2 to blocking pre-conditions; phased rollout |

**Consensus:** The approach is correct and low-complexity (net deletion, follows the
registry precedent). It is NOT safe to apply until two pre-conditions are confirmed:
(1) `scopes.yml` has a non-EFS source of truth for auth-server, and (2) `mcpgw_data`
holds no durable state that is not already in DocumentDB. For production, adopt a
two-phase rollout (detach mounts, bake, then destroy EFS).

## Next Steps

1. Resolve blocking pre-conditions C2/C3 (scopes provenance, mcpgw_data durability)
   with concrete evidence from the auth-server Dockerfile and mcpgw owner.
2. Decide the scopes bootstrap story for the `file` storage backend (C6).
3. Adopt the phased detach-then-destroy rollout for production (Circuit/Sage).
4. Document the post-change authorization-policy source of truth (Cipher C8).
5. Repo-wide and org-wide grep for `run-scopes-init-task` and `mcp_gateway_efs_*`
   consumers before deletion (C7 / Open Question 3).
6. Proceed to implementation only after 1-2 are confirmed; then run the validation
   steps in `testing.md`.
