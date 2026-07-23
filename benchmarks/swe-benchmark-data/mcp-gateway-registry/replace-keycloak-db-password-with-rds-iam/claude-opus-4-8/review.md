# Expert Review: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-07-23*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

This document captures multi-persona review of the RDS IAM authentication design for Keycloak's Aurora MySQL connection. Each reviewer assessed the design strictly from their domain.

---

### Backend Engineer Review (Byte)

**Strengths**
- The core architectural call is correct. Keycloak (Quarkus/Agroal) authenticates only at physical connection open time, and the AWS Advanced JDBC Wrapper's `iam` plugin regenerates/caches the 15-minute token per new physical connection. Existing pooled connections never expire mid-flight; only new connections pay for token minting. Rejecting Alternatives 1 and 3 (boot-time `KC_DB_PASSWORD` / file-based token) is well reasoned.
- Keeping the master user on password auth is sound: it is the break-glass/bootstrap identity, required to run `CREATE USER ... AWSAuthenticationPlugin`, and keeps rollback trivial.
- Scoping `rds-db:connect` to `dbuser:<cluster_resource_id>/<db-user>` (not cluster name) is the correct ARN form, and placing it on the task role rather than the execution role is right.
- Feature-flag design matches repo conventions; default-`false` byte-for-byte fallback is a genuine no-op for existing deployments.

**Concerns**
- **`KC_DB_DRIVER` is a Keycloak build-time option and the LLD never bakes it into the image.** The Dockerfile runs `kc.sh build` and `start --optimized`, but does not set `KC_DB_DRIVER` before the build; meanwhile the task definition overrides with `command = ["start"]` (non-optimized). The design silently relies on the non-optimized runtime rebuild to pick up the driver from env, which contradicts the "requires the custom optimized image" claim. Biggest correctness risk, unaddressed.
- **JDBC driver classpath is unverified.** Whether KC 25 accepts a `jdbc:aws-wrapper:mysql://` URL against `KC_DB=mysql` and resolves `software.amazon.jdbc.Driver` from `providers/` is asserted, not proven.
- **`KC_DB_USERNAME` handling is ambiguous and the default path is a foot-gun.** Today Keycloak logs in as the master user (secret `username` = `keycloak`). The IAM path needs `keycloak_iam` to match the ARN. The LLD's "keep from secret OR set from local" leaves a branch where username stays `keycloak` while the ARN authorizes `keycloak_iam` and every connection is denied. Must be prescriptive.
- **The issue.md STS-egress dependency is wrong.** `GenerateDBAuthToken` is offline SigV4 signing; credentials come from the ECS metadata endpoint (`169.254.170.2`), not STS. The stated "egress to STS/RDS token-signing path" is misleading.
- Bootstrap `GRANT` under-specifies whether `keycloak_iam` covers the Liquibase DDL privileges Keycloak runs at startup.
- `iam_database_authentication_enabled` toggle behavior on a live cluster (apply-immediately vs maintenance window) is not called out.

**New libraries / infra dependencies required**
- `aws-advanced-jdbc-wrapper` (`software.amazon.jdbc`) ~2.5.x JAR bundled into the Keycloak image; only new runtime dependency, self-contained. `mysql` client for the bootstrap script.

**Better alternatives considered**
- The three alternatives are correctly rejected. One nuance: RDS Proxy with `iam_auth=REQUIRED` does not strictly require the client wrapper (proxy can accept a plain token), though the 15-min client-leg expiry still argues for it. The LLD slightly overstates the equivalence but the defer-to-follow-up conclusion is fine.

**Recommendations**
- Set `KC_DB_DRIVER` before `kc.sh build` and decide deliberately whether the IAM task runs `--optimized` or keeps `command=["start"]`.
- Make `KC_DB_USERNAME` unambiguously `local.keycloak_iam_db_user` via a plain env entry when the flag is on; drop it from secrets; remove the "or" option.
- Add the `keycloak_image_uri` precondition.
- Prove the full boot path on a real Aurora cluster before merge; capture logs showing the iam plugin active.
- Fix the issue.md STS-egress claim.

**Questions for author**
- Has `jdbc:aws-wrapper:mysql://...wrapperPlugins=iam&sslMode=VERIFY_IDENTITY` actually been booted against Aurora MySQL 8.0 + Keycloak 25?
- Is `KC_DB_DRIVER` applied at build time or via the non-optimized rebuild? What is the intended entrypoint for the IAM image?
- Does switching from the master user to `keycloak_iam` require reconciling any table/definer privileges?
- Does enabling `iam_database_authentication_enabled` apply immediately with any connection interruption?

**Verdict:** APPROVED WITH CHANGES

---

### SRE/DevOps Engineer Review (Circuit)

**Strengths**
- Feature-flagged with `false` default and password path preserved byte-for-byte: the safest shape for a credential migration.
- Correctly identifies the task-role-vs-exec-role distinction and the `cluster_resource_id` ARN form.
- Correctly rejects the boot-time-token and sidecar patterns (both die at 15-min expiry).
- Keeps master user on password auth for break-glass; phased rollout with a rollback path.

**Concerns**
- **Custom image + `command = ["start"]` is broken as written (blocker).** ECS `command` overrides `CMD`, not `ENTRYPOINT`. The custom image's entrypoint is `["kc.sh","start","--optimized"]`, so the existing task def yields `kc.sh start --optimized start` — malformed args, task fails to start. The LLD never makes `command` conditional. This crashes independent of IAM.
- **Flag and image URI are decoupled; the foot-gun is only "recommended" mitigated.** Flag `true` + stock image → `ClassNotFoundException` crash-loop. The precondition must be mandatory, not an Open Question.
- **Bootstrap ordering is a deadlock.** A single apply drops `KC_DB_PASSWORD` and switches to `keycloak_iam` before the `CREATE USER` SQL runs → crash-loop. Worse, wiring bootstrap into `post-deployment-setup.sh` places it behind that script's ECS/Keycloak health gate — which can never pass until the IAM user exists. Bootstrap must run out-of-band against the DB before the task switch.
- **No deployment circuit breaker / auto-rollback on the ECS service.** Given the #1026 crash-loop history, shipping without `deployment_circuit_breaker { enable = true, rollback = true }` is a gap.
- **Monitoring is eyeball-only.** No metric filter/alarm on DB-connect failures or crash-loops during the riskiest window.
- **Rollback can re-introduce #1026 drift.** If rotation ever changed the cluster password before IAM was enabled, re-applying `aws_secretsmanager_secret_version` from tfvars may disagree with Aurora.
- **Rotation-stack gating described too loosely.** The `rotation_lambda` role/policy and lambda SG are shared with DocumentDB rotation; gating them by `count` would break DocumentDB. `aws_lambda_permission` and SG rules must be gated in lockstep, references guarded with `[0]`/`try()`, and `count` toggling shows destroys/recreates in the plan.
- **`KC_DB_USERNAME` secret-vs-env contradiction** between Data Models and Step 5 would produce a duplicate-key task-def validation error if both are applied.
- **`ADD <github-url>` for the JAR is a supply-chain/build-reliability liability** — no checksum, no digest pin.
- **`iam_database_authentication_enabled` reboot/downtime question unanswered** (it is a no-reboot modification for Aurora MySQL, but the LLD never says so or sets `apply_immediately`).

**New libraries / infra dependencies required**
- `aws-advanced-jdbc-wrapper` 2.5.x JAR; hard shift from stock to custom image when flag on; new `rds-db:connect` task-role policy; bootstrap script + in-VPC `mysql` client.

**Better alternatives considered**
- Route IAM through the existing RDS Proxy (`iam_auth=REQUIRED`) deserves more than a footnote for the autoscale case. Prefer a pinned-digest/checksum JAR download or an internal mirror. Create `keycloak_iam` in Phase 1 regardless of the flag (idempotent) so the Phase 2 switch has no manual ordering dependency.

**Recommendations**
- Make `command` conditional so the custom image runs `--optimized` correctly.
- Promote the image precondition to a required `precondition`/`validation`.
- Move bootstrap out-of-band and before the task switch; make it idempotent.
- Add `deployment_circuit_breaker` + CloudWatch alarms (Access denied, driver ClassNotFound, deployment-failure, RDS DatabaseConnections).
- Resolve the `KC_DB_USERNAME` contradiction into one gated path.
- Verify the JAR by SHA-256 or mirror it; pin the image by digest.
- Clarify rotation gating: keep shared role/policy/SG; gate only the RDS function, its permission, schedule, and RDS-specific SG rules.
- State and test that enabling IAM auth on Aurora MySQL is a no-reboot modification.

**Questions for author**
- Does the wrapper's embedded RDS CA satisfy `VERIFY_IDENTITY` against the cluster writer endpoint CNAME in every region, with no truststore step?
- Does `keycloak_iam` need the identical schema grants the master user relied on?
- On rollback, does re-applying the secret version actually re-sync Aurora or re-create #1026 drift?
- Where does `bootstrap-iam-db-user.sh` run (laptop / CI / ECS exec) given the private-subnet DB and needed 3306 reach?
- Is the destroy churn of the rotation Lambda/log group/SG rules on flag flip acceptable in every environment?

**Verdict:** NEEDS REVISION

---

### Security Engineer Review (Cipher)

**Strengths**
- Correct direction: app connection moves from a long-lived password to a short-lived (15-min), auto-refreshed IAM token minted per connection.
- The `rds-db:connect` grant is genuinely least-privilege on the resource axis (`dbuser:<cluster_resource_id>/<db-user>`, single action, task role).
- Meaningful privilege reduction the design under-sells: today Keycloak logs in as the Aurora master user; switching to a scoped `keycloak_iam` user drops the app off the master credential.
- TLS posture on the app path is correct (`VERIFY_IDENTITY` enforces encryption + hostname; RDS rejects non-TLS IAM connections).
- Feature-flagged with a clean rollback.

**Concerns**
- **Standing-credential risk is only partially reduced; the highest-value credential is untouched.** `master_password` remains set, the secret still stores it, rotation is retained, so the plaintext password still lives in `terraform.tfvars` and Terraform state. The issue's stated goal (eliminate the standing credential and its plaintext presence) is NOT met — it is deferred to an out-of-scope Phase 4.
- **The old password login path is not revoked.** Because the app previously used the master user and it is retained, a valid high-privilege password credential still works after cutover. IAM auth stops using the password; it does not close the door. A leaked tfvars/state password still grants full DB access.
- **CKV_AWS_162 handling is logically broken.** A `#checkov:skip` is static text and cannot be conditional. In the default (`false`) shipped state IAM auth is genuinely off and the check should fire, but the proposed replacement comment suppresses it unconditionally — hiding a legitimate finding in the default config.
- **Token-in-logs risk.** Recommending transient `KEYCLOAK_LOGLEVEL=DEBUG` during rollout can write a live 15-min-valid token (the JDBC password) into CloudWatch. Must forbid driver credential logging.
- **Bootstrap script TLS unspecified.** It fetches the master password and runs `mysql` "over TLS" but no `--ssl-mode=VERIFY_IDENTITY`/CA is specified — the most sensitive connection in the flow.
- **`GRANT ALL PRIVILEGES ON keycloak.*` is broader than needed** (includes DROP; escalation surface). Enumerate the needed privileges.
- Retained-but-idle rotation Lambda keeps privileged DB/network/secretsmanager access as attack surface.
- Pre-existing: the RDS Proxy has `require_tls = false` and `iam_auth = "DISABLED"` — off the new path, but remains a non-TLS password entry to the same cluster.

**New libraries / infra dependencies required**
- `aws-advanced-jdbc-wrapper` 2.5.x via unpinned `ADD` from GitHub releases (no checksum/signature) — supply-chain risk. Relies on the wrapper's embedded RDS CA bundle for `VERIFY_IDENTITY`, coupling TLS trust to the JAR version.

**Better alternatives considered**
- To actually meet the security goal: after cutover, revoke/rotate the app password path and move the master password out of tfvars/state (`manage_master_user_password = true`, or a generated password stored only in Secrets Manager).
- For CKV_AWS_162: flip the default to IAM once proven, or accept the finding as a documented deviation rather than a silent skip.

**Recommendations**
- Do not claim standing-credential elimination; reframe as privilege reduction and track master-password removal as a near-term follow-up.
- Add an explicit teardown that disables/rotates the old password login after cutover.
- Fix the CKV_AWS_162 story honestly.
- Harden the bootstrap script (`VERIFY_IDENTITY` + CA, `set -euo pipefail`, never echo the password, `CREATE USER IF NOT EXISTS`).
- Replace `GRANT ALL PRIVILEGES` with an enumerated grant list.
- Forbid driver/credential debug logging; scrub or short-retain any rollout debug window.
- Pin the JDBC wrapper JAR by SHA-256 or verify its signature.
- Keep the image precondition as a hard guardrail.

**Questions for author**
- After cutover, what disables the old password login for the master user?
- How do you reconcile a static `#checkov:skip=CKV_AWS_162` with honestly reporting the password-default state?
- Does the bootstrap `mysql` invocation enforce `VERIFY_IDENTITY` with a pinned CA?
- What is the plan and owner for moving `master_password` out of tfvars/state?
- Is the JAR verified at build, and how is the embedded RDS CA kept current across rotations?
- During DEBUG rollout, is the token confirmed not logged as the connection password?

**Verdict:** NEEDS REVISION

---

### SMTS Review (Sage)

**Strengths**
- The feature-flag choice is idiomatic; `enable_cloudfront`/`entra_enabled` establish the exact pattern proposed, so the new flag will read as native.
- The codebase analysis is accurate and load-bearing: it correctly pre-empts the checkov skip, the task-role-vs-exec-role distinction, and the `cluster_resource_id` ARN — the mistakes a naive implementer would make.
- Rejecting the entrypoint-token and sidecar alternatives is well reasoned; the RDS-Proxy-as-follow-up call is right given the proxy is `iam_auth = "DISABLED"`.
- Composing env/secrets via `concat` of conditional locals matches existing structure and keeps the flag-off path byte-identical.

**Concerns**
- **Image-selection coupling is the weakest point and under-enforced.** Flag-on + default image is a guaranteed crash-loop found only at health-check time; the precondition is only "recommended." It is also unclear whether a `providers/` JAR is picked up without a rebuild in the consuming path.
- **Bootstrap connectivity is unspecified and likely impossible as written.** The cluster is in private subnets with 3306 ingress only from the ECS task SG and rotation Lambda SG — no operator ingress. An operator workstation cannot reach the endpoint. Needs a concrete mechanism (ECS Exec, a one-shot in-VPC task, or SSM) and possibly an SG change. Biggest execution gap.
- **`KC_DB_USERNAME` ambiguity is the exact class of bug that caused #1026.** Step 5's either/or plus the Data Models example (username from the secret `keycloak`, not `keycloak_iam`) reproduces the drift pattern. Pick one source of truth consistent with the ARN and bootstrapped user.
- **The fallback is genuine dual-path debt with no bound.** Keeping master password, secret, and the full rotation stack means every future DB-connectivity change must be reasoned about in two modes indefinitely; "decommission is a follow-up" with no owner/trigger is how permanent dual paths are born.
- Gating the shared `rotation_lambda` role/SG must confirm the DocumentDB rotation path stays coherent.
- The `VERIFY_IDENTITY` no-truststore claim should be verified against the pinned wrapper version and the exact endpoint SANs.

**New libraries / infra dependencies required**
- `aws-advanced-jdbc-wrapper` ~2.5.x (image-scoped, inert when off — clean containment). A private ECR repo + CI path to build/push the custom image (implicit today's default needs neither). No new Terraform providers.

**Better alternatives considered**
- Question whether the flag should be permanent versus a time-boxed migration (flag for one or two releases, then delete the password path) to avoid perpetual dual-path maintenance.
- For bootstrap, a short-lived in-VPC ECS one-off task using the task role is cleaner than an operator-run `mysql` client and sidesteps the private-subnet problem; it should be the primary option.

**Recommendations**
- Promote the image precondition from Open Question to a required `precondition`/`validation`.
- Fully specify the bootstrap execution mechanism and network path; prefer an in-VPC one-off task.
- Resolve `KC_DB_USERNAME` to a single source of truth equal to `local.keycloak_iam_db_user` and assert it matches the ARN dbuser.
- Add a concrete decommission trigger for the rotation stack.
- Add plan assertions: flag-off produces zero diff; flag-on drops `KC_DB_PASSWORD` and adds the policy.
- Verify the `VERIFY_IDENTITY` truststore/hostname claim against the pinned version.

**Questions for author**
- How does the bootstrap script reach the private-subnet Aurora endpoint, and does it need a new DB-SG ingress rule?
- Does a JAR in `/opt/keycloak/providers/` load under `start --optimized` without a rebuild you have not shown?
- Which single value backs `KC_DB_USERNAME` on the IAM path?
- What is the trigger/owner for decommissioning the rotation stack, and does gating only the Keycloak rotation leave the shared role coherent?
- Does toggling `iam_database_authentication_enabled` force a disruptive modification/reboot on apply?

**Verdict:** APPROVED WITH CHANGES

---

### Frontend Engineer Review (Pixel)

**Strengths**
- No web/UI surface is touched; the Keycloak login page, themes, realm config, and client flows are untouched. Zero risk to the rendered login experience by design.
- Flag defaults to `false` with a byte-for-byte-unchanged path, so non-opting deployments see no availability change.
- The clean rollback path is a good safety net for user-facing availability.
- Operator UX of the flag (single bool, documented, precondition guard) is the right instinct.

**Concerns**
- **Login-availability during cutover is understated.** Phase 2 redeploys at `desired_count = 1`; if ECS does a rolling replace with no surplus capacity, there is a window where no Keycloak instance serves and logins / in-flight OIDC redirects error. Min-healthy percent is never stated.
- Failure modes surface to end users as opaque 500/blank login pages, not graceful degradation. Docs should frame each misconfig as "logins are unavailable until fixed."
- The operator guardrail for "flag on + stock image" is soft ("optionally a Terraform validation") — should be a hard `precondition`.
- Bootstrap ordering is a subtle operator trap: applying everything in one shot without bootstrapping first causes a login outage. Sequencing must be unmissable in docs.

**New libraries / infra dependencies required**
- None in the frontend area. Only the Java JDBC wrapper JAR, outside scope.

**Better alternatives considered**
- Out of area; the client-side token-refresh tradeoffs look sound to a non-specialist.

**Recommendations**
- State the cutover availability posture explicitly: bring up a healthy new task before draining the old one (min-healthy >= 100%); document a maintenance window if a brief outage is unavoidable.
- Upgrade the image check from optional to a hard `precondition`.
- Add a per-failure-mode "user-facing impact" note in README/OPERATIONS.
- Make the Phase 1 -> Phase 2 bootstrap-before-switch ordering unmissable.

**Questions for author**
- During Phase 2 redeploy at `desired_count = 1`, is there a zero-healthy-task window? What are the deployment min/max healthy percentages?
- Are in-flight OIDC authorization-code redirects drained gracefully on task replacement?
- Is there a customer-visible status/health surface that should reflect a Keycloak-down state during a botched cutover?

**Verdict:** APPROVED WITH CHANGES

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED WITH CHANGES | 0 | Nail down cutover availability (min-healthy >= 100%); hard precondition; docs frame misconfig as login outage |
| Backend (Byte) | APPROVED WITH CHANGES | 0 | Fix `KC_DB_DRIVER` build-vs-runtime; make `KC_DB_USERNAME` prescriptive; prove real boot; fix STS-egress claim |
| SRE (Circuit) | NEEDS REVISION | 3 | Fix `command`/entrypoint clash; out-of-band idempotent bootstrap; deployment circuit breaker + alarms; correct rotation gating |
| Security (Cipher) | NEEDS REVISION | 2 | Revoke old password path + move master password out of state; fix CKV_AWS_162; harden bootstrap TLS; pin JAR |
| SMTS (Sage) | APPROVED WITH CHANGES | 0 | Mandatory precondition; concrete in-VPC bootstrap mechanism; single-source `KC_DB_USERNAME`; bound the dual-path debt |

### Cross-Cutting Themes (raised by 3+ reviewers)

1. **Mandatory image precondition (all 5).** Flag-on + stock image is a guaranteed crash-loop; the guard must be a hard Terraform `precondition`, not an Open Question.
2. **`KC_DB_USERNAME` single source of truth (Byte, Circuit, Sage).** The either/or in Step 5 vs the Data Models example reproduces the #1026 drift class; must be one gated value equal to `keycloak_iam`.
3. **Bootstrap execution mechanism and ordering (Circuit, Cipher, Sage, Pixel).** The private-subnet DB is unreachable from an operator laptop, the bootstrap cannot sit behind the health gate, and it must complete before the task switch. Prefer an in-VPC one-off task.
4. **`command`/entrypoint reconciliation for the custom image (Byte, Circuit).** ECS `command=["start"]` + `--optimized` entrypoint yields malformed args; `command` must be made conditional.
5. **Security goal not fully met (Cipher, echoed by Sage).** The master password stays in tfvars/state and the old password path is not revoked; the issue must not over-claim "standing-credential elimination."

### Next Steps

- Address the two NEEDS REVISION reviews before implementation: (a) resolve the deployment mechanics (conditional `command`, mandatory precondition, out-of-band idempotent bootstrap, circuit breaker + alarms, correct rotation gating); (b) resolve the security gaps (revoke/rotate the old password path or explicitly re-scope the issue's goal, fix CKV_AWS_162, harden bootstrap TLS, pin the JAR by digest).
- Update the LLD to make `KC_DB_USERNAME` prescriptive, bake or deliberately choose the driver/entrypoint strategy, and specify the in-VPC bootstrap task.
- Validate the full boot path on a real Aurora MySQL 8.0 + Keycloak 25 environment and capture logs before merging.
