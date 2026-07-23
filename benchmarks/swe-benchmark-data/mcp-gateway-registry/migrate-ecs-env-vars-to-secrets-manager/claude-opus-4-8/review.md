# Expert Review: Migrate remaining sensitive ECS env vars to AWS Secrets Manager

*Related Issue: `./github-issue.md`*
*Related LLD: `./lld.md`*
*Reviewed: 2026-07-23*

Five reviewers examined the design against the live repo at `terraform/aws-ecs/`. Several reviewers surfaced genuine defects, two of which were factual errors in the initial LLD draft. The LLD has since been corrected; each reviewer section below notes where a fix was applied and where an item remains open. Verdicts reflect the design as reviewed (before those corrections) so the record is honest; the Review Summary and Next Steps reflect the post-correction state.

---

## Reviewer 1: Frontend Engineer (Pixel)

Focus: UI/UX, components, state, API integration.

### Strengths
- No frontend surface is touched. The React frontend (`frontend/`) authenticates via the auth-server and never reads these Terraform-level secrets, so the migration is invisible to the UI.
- The one indirect UI touchpoint - the Grafana admin login - is handled: the design ensures Grafana always has a valid admin password (operator-supplied or generated), so the Grafana UI login continues to work.

### Concerns
1. **[Low]** If the Grafana admin password is auto-generated (empty input), operators need a documented way to retrieve it to log in to the Grafana UI. Without that, the Grafana dashboard becomes unreachable via the admin account. The LLD Step 2 note now calls for documenting retrieval from Secrets Manager / a sensitive output.

### New libraries / infra dependencies
None.

### Better alternatives considered
None applicable to the frontend.

### Recommendations
- Ensure `OPERATIONS.md` documents retrieving a generated Grafana admin password.

### Questions for author
1. Is Grafana admin login the only user-facing surface affected? (Believed yes.)

### Verdict
**APPROVED** - No frontend impact; the single UX touchpoint (Grafana login) is addressed.

---

## Reviewer 2: Backend Engineer (Byte)

Focus: how the app reads config, fallback transparency, secret-flow edge cases.

### Strengths
- The timing/availability dimension of the fallback is genuinely transparent. ECS injects `secrets`-block values as ordinary env vars before the process starts, so the many import-time module-level reads (`auth_server/server.py:187,190`, `registry/utils/keycloak_manager.py:20`) and the `Settings()` singleton (`registry/core/config.py:1209`) all still see the values. Moving from `environment` to `secrets` does not change when a var is visible.
- PEM newline fidelity holds: `registry/services/github_auth.py:129` does `settings.github_app_private_key.replace("\\n", "\n")`, so both a real multi-line PEM and an escaped single-line PEM sign RS256 JWTs correctly.
- Per-secret resources (not one JSON blob) is the right IAM-granularity call.

### Concerns
1. **[Blocker -> resolved in LLD]** A `"not-configured"` truthy sentinel (a tempting way to satisfy the non-empty `secret_string` rule) would turn empty credentials into live values. `if REGISTRY_API_TOKEN:` (`server.py:380`) and the federation-token admin check (`server.py:2592`, `hmac.compare_digest`) would accept the literal `not-configured` as a valid admin bearer token - a direct privilege escalation. **The LLD now explicitly prohibits any truthy sentinel and mandates `count`-gating so an empty var yields an absent env var** (falsy Pydantic/`os.getenv` default preserved). Confirm the implementer follows this and does not reintroduce a sentinel to dodge the empty-string restriction.
2. **[High -> resolved in LLD]** Empty values flipping falsy->truthy would break `if settings.github_pat:` (`github_auth.py:110`, sends `Bearer not-configured` to GitHub), `FEDERATION_STATIC_TOKEN` peer auth, `REGISTRATION_WEBHOOK_AUTH_TOKEN`, and the ANS/registration-gate credentials. Same root cause as #1; same fix (count-gating). Now documented as a hard constraint in the LLD.
3. **[Medium -> resolved in LLD]** `FEDERATION_ENCRYPTION_KEY` sentinel would fail `Fernet("not-configured".encode())` on every federation call, converting a clean "unset -> None" path (`registry/utils/federation_encryption.py:34-46`) into recurring error-log spam. Count-gating keeps the var absent when federation is unused.
4. **[Medium -> resolved in LLD]** `GF_SECURITY_ADMIN_PASSWORD` had no empty-string handling; Secrets Manager rejects an empty `secret_string`, so `apply` would fail when observability is on but the password is blank. The LLD now generates a `random_password` when empty.
5. **[Low]** Pre-existing insecure `"development-secret-key"` fallback in `auth_server/providers/{okta,auth0,keycloak}.py`. Not activated by this migration (`SECRET_KEY` is a first-tier secret, always non-empty), but adjacent and worth closing. Captured as an Open Question.

### New libraries / infra dependencies
None. Reuses `aws_kms_key.secrets`, existing secret resources, `ecs_secrets_access`, and the ECS service module.

### Better alternatives considered
- Count-gating (absent env var when empty) over any sentinel - this is more faithful to current empty semantics because Pydantic/`os.getenv` defaults are falsy. Adopted as the primary design.

### Recommendations
- Add an explicit test that `Bearer not-configured` is rejected by `/admin/federation-token` and never appears in `_STATIC_TOKEN_MAP`.
- Smoke-test the empty-var semantic change in the secrets-on path for all 13 consumers.
- Verify the injected `GITHUB_APP_PRIVATE_KEY` signs a valid GitHub App JWT.

### Questions for author
1. Confirmed the implementer will use count-gating and never a truthy sentinel? (LLD now mandates this.)
2. Is the loss of the empty-string env var in the secrets-on path confirmed harmless for every Pydantic field with a non-empty default? (Believed yes; smoke-test required.)

### Verdict
**NEEDS REVISION** (as reviewed) - The sentinel-driven behavior changes were release-blocking. **The LLD has since been revised to prohibit sentinels and mandate count-gating, addressing Concerns 1-4;** re-review would move this to APPROVED WITH CHANGES pending the recommended tests.

---

## Reviewer 3: SRE / DevOps Engineer (Circuit)

Focus: deployment, operations, reliability.

### Strengths
- Count-gating keeps each optional secret's IAM ARN guarded by the same boolean that gates the secret resource, so Terraform builds a correct policy->secret dependency edge and never indexes `[0]` on a zero-count resource.
- The already-migrated `metrics-service` block (`observability.tf:158-278`) is a proven template attaching `ecs_secrets_access` to both roles and consuming KMS-encrypted secrets.
- KMS decrypt is granted two ways (identity policy `iam.tf:38-47` + resource policy `secrets.tf:23-42`).

### Concerns
1. **[Blocker -> resolved in LLD]** Grafana's execution role attaches ONLY `EcsExecTaskExecution` (`observability.tf:505-508`), not `ecs_secrets_access`. The ECS agent uses the execution role to resolve `valueFrom`, so moving `GF_SECURITY_ADMIN_PASSWORD` to `secrets` without this grant fails every grafana task launch with `ResourceInitializationError: unable to pull secrets`. **The LLD Step 7 is now imperative** and adds `SecretsManagerAccess` to the grafana execution role.
2. **[Blocker -> resolved in LLD]** No `aws_secretsmanager_secret` existed for the grafana admin password, and the value is consumed in two containers (grafana `:582-584` and the grafana-config sidecar `:645`, which interpolates `$${GF_SECURITY_ADMIN_PASSWORD}` in a shell command at `:630`). **The LLD now creates the secret and wires both containers' `secrets` blocks.**
3. **[High]** Moving env->secrets rewrites the container definitions of auth-server, registry, and grafana in one apply, forcing a simultaneous rolling replace of all three. The module sets no `deployment_circuit_breaker` / `wait_for_steady_state`. Recommend pinning the circuit breaker with rollback and staging the rollout per service. (Open - operational recommendation.)
4. **[High]** IAM eventual consistency: on first enable of a toggled feature, ECS may pull the new task-def revision before the `PutRolePolicy` propagates, causing transient `AccessDeniedException` on GetSecretValue until IAM converges. Usually self-heals via ECS retries; document "expect 1-2 failed placements on first enable." (Open - runbook note.)
5. **[Medium]** `recovery_window_in_days = 0` on all secrets means immediate, unrecoverable deletion. For externally-sourced, hard-to-reissue secrets (GitHub App PEM, OAuth client secrets) prefer `>= 7`. (Open - see security review Concern 7.)
6. **[Medium]** Rollback via `use_secrets_manager_for_env = false` restores service start but is not a clean revert: secrets remain provisioned (ungated by the flag) and plaintext is re-written into the task-def/state. Acceptable as break-glass; document it. (Documented in LLD Rollout/Open Questions.)

### New libraries / infra dependencies
- No new providers/modules. ~14 new `aws_secretsmanager_secret` + version pairs (13 + grafana). Each is a standing monthly cost plus KMS decrypt calls on every task start. Grafana task init now depends on Secrets Manager + KMS reachability (previously it had none), widening the init failure surface.

### Better alternatives considered
- Stage the rollout per service (registry, then auth, then grafana) to shrink blast radius.
- Reference exec-role ARNs explicitly in the KMS key policy rather than the `*task-exec*` wildcard.

### Recommendations
1. (Done) Add `SecretsManagerAccess` to the grafana execution role.
2. (Done) Add the grafana admin password secret and wire both grafana containers.
3. Pin `deployment_circuit_breaker = { enable = true, rollback = true }` and stage the migration.
4. Raise `recovery_window_in_days` for externally-sourced secrets.
5. Add a runbook note for transient first-enable GetSecretValue denials.

### Questions for author
1. Are the three services rolled in one apply or staged? What circuit-breaker defaults does the v6 module apply?
2. Confirmed the grafana exec-role name matches `*task-exec*` for KMS decrypt? (Consistent with the working metrics-service.)

### Verdict
**NEEDS REVISION** (as reviewed) - Two blockers (grafana exec-role grant; missing grafana secret) would break task startup the moment grafana env moved to `secrets`. **Both are now fixed in the LLD.** Remaining items (circuit breaker, recovery window, first-enable note) are APPROVED-WITH-CHANGES-level hardening.

---

## Reviewer 4: Security Engineer (Cipher)

Focus: secret handling, IAM least-privilege, encryption, audit.

### Strengths
- Correct direction and real risk reduction: moving OAuth secrets, the GitHub PAT/App PEM, the Fernet key, static tokens, and the Grafana password out of the cleartext task-def JSON (readable via `ecs:DescribeTaskDefinition` / `docker inspect`) into `valueFrom` is the right data-protection move.
- Reuses the CMK `aws_kms_key.secrets` (rotation enabled) - better than the AWS-managed default.
- Per-secret granularity preserves least-privilege on the IAM `Resource` list and gives per-secret CloudTrail auditing.
- Count-gated `secrets` fragments filter `if spec.arn != ""`, avoiding the `ResourceInitializationError` foot-gun.

### Concerns
1. **[High]** The `ecs_secrets_access` policy is attached to BOTH the execution role and the task role (`ecs-services.tf:51-64`). Only the execution role needs `GetSecretValue`/`kms:Decrypt`; the app reads injected env vars and never calls the Secrets Manager API. Granting the task role read on the whole catalog means an RCE/SSRF in a container can enumerate every secret via the task-role credentials on the ECS metadata endpoint - this widens blast radius versus today. **The LLD now attaches the policy to grafana's execution role ONLY**, and captures removing the auth/registry task-role attachment as a tracked follow-up (it changes an existing pattern).
2. **[High]** The KMS key policy grants `kms:Decrypt` to `Principal AWS "*"` gated on `aws:PrincipalArn StringLike role/*task-exec*` (`secrets.tf:26-41`). Any future in-account role whose name contains `task-exec` inherits decrypt. Account-scoping (`aws:PrincipalAccount`) caps this at High, not Blocker. Recommend binding to explicit execution-role ARNs. (Pre-existing; Open Question.)
3. **[Medium]** Plaintext fallback (`false`) re-introduces every secret into task-def JSON and state. Acceptable only as short-lived break-glass with a tracked removal date and a Terraform precondition/warning. (Captured in Open Questions.)
4. **[Medium - arguably top priority]** Secret values persist in Terraform state as `secret_string` even on the Secrets Manager path, and the root stack has no `backend` block (`main.tf`), so state defaults to a local unencrypted file. Without an S3 + SSE-KMS backend, the migration moves secrets from one cleartext location to another. **The LLD now flags encrypted remote state as a hard prerequisite** (Open Questions), though the backend change itself is out of scope for this issue.
5. **[Medium]** `"development-secret-key"` fallback in the auth providers is a JWT-forgery/auth-bypass primitive if `SECRET_KEY` is ever unset. Not activated by this migration but should be closed (fail-closed). Captured as an Open Question.
6. **[Low]** No rotation (`CKV2_AWS_57` skipped) is acceptable - these are externally-managed secrets; document a manual rotation runbook for the Fernet key and GitHub PAT.
7. **[Low]** `recovery_window_in_days = 0` - use `>= 7` for hard-to-reissue secrets (GitHub App PEM, OAuth client secrets).

### New libraries / infra dependencies
- None introduced. Implied missing dependency: an encrypted remote state backend (Concern 4).

### Better alternatives considered
- Split execution-role vs task-role IAM policies (addresses Concern 1) - strictly better least-privilege at trivial cost.
- Grant KMS decrypt via the execution-role IAM policy and drop the wildcard-principal key statement (addresses Concern 2).

### Recommendations
1. (Grafana done) Do not attach the read policy to task roles; execution role only. Track removing the auth/registry task-role attachment.
2. Tighten the KMS key policy to explicit execution-role ARNs.
3. Configure an encrypted S3 backend before shipping.
4. Remove the `"development-secret-key"` fallback (fail-closed).
5. File a dated removal issue for the plaintext fallback; add a precondition when `false`.
6. Use `recovery_window_in_days >= 7` for externally-sourced secrets.

### Questions for author
1. Does any runtime code path call `secretsmanager:GetSecretValue` directly? (No - so the task-role attachment has no functional justification.)
2. Where does Terraform state live today? If local/unencrypted, Concern 4 is a Blocker.
3. Can the `"development-secret-key"` removal be bundled here?

### Verdict
**NEEDS REVISION** (as reviewed) - The migration is directionally correct, but the task-role over-grant, the KMS wildcard, and the absent encrypted state backend must be addressed. **The LLD now scopes grafana to execution-role-only, flags the state backend as a prerequisite, and tracks the KMS and task-role items;** addressing the state backend and task-role split moves this to APPROVED WITH CHANGES.

---

## Reviewer 5: SMTS / Overall (Sage)

Focus: architecture, code quality, maintainability.

### Strengths
- Faithful reuse of the established secret+version and `secrets = concat(base, conditional...)` idioms - one pattern, not two.
- Correct raw-vs-JSON decision (single values as raw strings, bare ARN; `:key::` reserved for multi-field secrets). `REGISTRY_API_KEYS` is a JSON blob but still one env value, so raw storage is right.
- The variable grouping is verified-correct: the 7 "shared" vars appear in both auth-server and registry; the 5 "registry-only" vars appear only in registry.
- Real constraints surfaced (ECS duplicate-name prohibition, `ResourceInitializationError`, the grafana IAM gap).

### Concerns
1. **[High -> resolved in LLD]** The initial draft claimed `grafana_admin_password` has a non-empty default and created its secret unconditionally. `variables.tf:1175-1180` shows `default = ""`. Unconditional creation would orphan a secret (and fail `apply` on an empty `secret_string`) for every non-observability deployer. **The LLD now gates the grafana secret on `enable_observability` and generates a random password when empty.**
2. **[Medium -> partially resolved]** The non-empty predicate was triplicated (secret `count`, locals filter, IAM `Resource`). **The LLD now recommends driving the IAM `Resource` list from the `local.*_specs` maps** and offers a full `for_each` refactor (Alternative 4) as the single-source-of-truth option.
3. **[Medium -> resolved in LLD]** Byte-for-byte fidelity is only claimed for the `false` path; the default path drops the empty env var. **The LLD now documents this semantic change and requires a smoke test.**
4. **[Medium]** The fallback flag is a single global boolean with no committed removal plan; its "off" state re-exposes all 13 values. Recommend a dated follow-up and considering per-group flags. (Captured in Open Questions.)
5. **[Low]** 13 secrets across three services in one PR (~240 LOC) is reviewable but mechanical; consider splitting along the three groups.
6. **[Low -> resolved]** PEM newline was left as an open question; now resolved with the `.replace("\\n","\n")` finding and an explicit smoke-test requirement.

### New libraries / infra dependencies
None.

### Better alternatives considered
- `for_each` over a single secret-spec map (now Alternative 4) - the cleanest fix for triplication and reviewability.
- Per-group toggle rather than one global bool.

### Recommendations
1. (Done) Gate the grafana secret on `enable_observability`; handle empty password.
2. (Done) Make Step 7 imperative.
3. Drive IAM `Resource` from the specs map (ideally `for_each`).
4. File a dated removal issue for the fallback flag.
5. Consider splitting the PR along the three variable groups.

### Questions for author
1. Confirmed the grafana secret is gated on observability and never stores an empty string? (LLD now does both.)
2. Reuse the specs map for IAM, or keep 13 hand-written lines? (LLD recommends the comprehension.)

### Verdict
**APPROVED WITH CHANGES** - The architecture is sound and consistent with the repo's pattern. The grafana factual error and the imperative role attachment were the must-fixes and are now corrected; collapsing the triplication is the remaining maintainability ask.

---

## Review Summary

| Reviewer | Verdict (as reviewed) | Blockers | Key Recommendations |
|----------|-----------------------|----------|---------------------|
| Frontend (Pixel) | APPROVED | 0 | Document retrieval of a generated Grafana password |
| Backend (Byte) | NEEDS REVISION | 1 (resolved) | No truthy sentinels; count-gate; test `not-configured` rejection |
| SRE (Circuit) | NEEDS REVISION | 2 (resolved) | Grafana exec-role grant + secret (done); circuit breaker; recovery window |
| Security (Cipher) | NEEDS REVISION | 0 (2 High) | Execution-role-only grant; encrypted state backend; tighten KMS wildcard |
| SMTS (Sage) | APPROVED WITH CHANGES | 0 | Gate grafana on observability (done); collapse IAM triplication |

All items raised as Blockers or factual errors in the initial draft (the truthy-sentinel behavior changes, the grafana "non-empty default" mistake, the missing grafana secret, and the non-imperative role attachment) have been corrected in `lld.md`. The remaining High/Medium items are hardening and process work that do not change the approach:

- **Must resolve before implementation lands:** confirm an encrypted remote Terraform state backend exists (Cipher #4); attach the secrets-read policy to execution roles only and track removing the auth/registry task-role attachment (Cipher #1).
- **Should resolve during implementation:** pin `deployment_circuit_breaker`; raise `recovery_window_in_days >= 7` for externally-sourced secrets; drive IAM from the specs map; add the `Bearer not-configured` rejection test and the empty-var + PEM smoke tests.
- **Track as follow-ups:** tighten the KMS wildcard principal; remove the `"development-secret-key"` fallback; file a dated removal issue for the plaintext fallback flag; consider per-group toggles.

## Next Steps

1. Implementer applies count-gating for all optional secrets (no truthy sentinels), gates the grafana secret on `enable_observability` with a generated fallback password, and wires both grafana containers.
2. Add `SecretsManagerAccess` to the grafana execution role; keep the task role free of secrets read.
3. Confirm/provision an encrypted S3 state backend as a prerequisite.
4. Extend `terraform.tfvars.example`, `README.md`/`OPERATIONS.md` with the flag and the fallback/rollback procedure.
5. Execute `testing.md`: `terraform validate`/`plan` (flag on and off), IAM ARN coverage check, and the live task-start smoke test (including `not-configured` rejection and GitHub App JWT signing).
