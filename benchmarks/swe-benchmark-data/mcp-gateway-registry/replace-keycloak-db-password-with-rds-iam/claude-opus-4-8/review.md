# Expert Review: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-06-25*
*Reviewing: `./lld.md` and `./github-issue.md`*
*Model under benchmark: claude-opus-4-8*

Five reviewer personas evaluate the design. Reviews are deliberately critical and
identify real issues, not just praise.

---

## 1. Frontend Engineer - "Pixel"
*Focus: UI/UX, components, state, API integration*

### Strengths
- The change is entirely infrastructure/backend. The Keycloak login UI, admin
  console, and the registry frontend that consumes Keycloak-issued tokens are
  unaffected; users see no difference.

### Concerns
- **Cold-start latency.** The entrypoint mints a token before `kc.sh start`. Combined
  with the existing `--optimized` start, this adds a small but real delay to task
  boot. If the ALB health check / target group draining is tight, a slower cold
  start during deploys could briefly surface 502s at the login page. The LLD's 60s
  `start_period` healthcheck likely absorbs this, but it should be confirmed.
- **Error opacity for end users.** If IAM auth misconfiguration causes Keycloak to
  crash-loop, the user-visible symptom is a generic login outage with no actionable
  message. That is acceptable for an infra failure but worth a runbook entry.

### New libraries / infra dependencies required
- None on the frontend.

### Better alternatives considered
- N/A for frontend.

### Recommendations
- Confirm the ALB health check `start_period` covers token-gen + optimized start.
- Add a one-line operator runbook: "login outage -> check Keycloak ECS task logs
  for `Access denied` / token-gen failure."

### Questions for author
- Does the registry frontend cache Keycloak's JWKS such that a brief Keycloak
  restart during cutover is invisible to logged-in users?

### Verdict: **APPROVED**

---

## 2. Backend Engineer - "Byte"
*Focus: API design, data models, business logic, performance*

### Strengths
- Correctly identifies the engine reality (Aurora MySQL, not PostgreSQL) and adapts
  the mechanism (`AWSAuthenticationPlugin AS 'RDS'` rather than `GRANT rds_iam`).
  This is the single most important catch in the design and avoids an implementer
  building the wrong thing from the issue text.
- Token is generated at runtime and never stored - the core security property is met.
- Least-privilege `rds-db:connect` scoped to a specific `dbuser` ARN built from
  `resource_id`, not `*`.

### Concerns
- **The token-expiry / connection-pool problem is the crux, and v1's mitigation is
  fragile.** "Fixed pool, never recycle, max-lifetime = 0" means the *initial*
  connections authenticated at boot live forever. But Aurora (and Serverless v2
  scaling events, failovers, and the ~idle/wait_timeout) WILL drop connections.
  When Agroal reopens a dropped connection, it reuses the boot-time
  `KC_DB_PASSWORD`, which is now an expired token -> `Access denied` ->
  connection-acquisition failures. The LLD acknowledges this but ships the weaker
  option as v1. I consider the **JDBC credentials provider (Alt 2)** or a
  **token-refresh sidecar that rewrites a file the driver reads** the only durable
  answers. v1 as written risks intermittent, hard-to-reproduce outages after ~15
  minutes under any connection churn.
- **MySQL driver TLS property names are unverified.** The JDBC URL uses
  `sslMode=VERIFY_CA&trustCertificateKeyStoreUrl=...`. Those are MySQL Connector/J
  property names; if Keycloak 25.0 bundles the MariaDB driver, the correct
  properties are `sslMode`/`serverSslCert` with different semantics. Getting this
  wrong fails TLS, which fails IAM auth. The LLD flags this in Open Questions but it
  is effectively a blocker until resolved.
- **`master_password` still required.** The cluster keeps `master_password = var...`,
  so the password is not fully gone from Terraform/state - only from the application
  login path. The issue's acceptance criterion "remove static DB credentials from
  Terraform" is only partially met. This should be stated plainly (it is, in Open
  Questions, but the issue framing oversells "remove").

### New libraries / infra dependencies required
- AWS CLI v2 in the image (justified; or a small SDK script).
- Agree the robust path likely needs a custom Quarkus credential-provider JAR.

### Better alternatives considered
- Endorse promoting Alt 2 (JDBC credentials provider) or a file-based token
  refresher to v1 rather than deferring.

### Recommendations
- **Resolve the driver/TLS property question before implementation**, not during.
- **Re-evaluate the pool strategy**: prove the fixed-pool approach survives an
  induced connection drop after 15 minutes, or adopt token refresh up front.
- Keep `master_password` but document it as break-glass and consider a separate,
  manually-rotated secret outside the app path.

### Questions for author
- What is the Aurora `wait_timeout`/idle behavior in this parameter group, and how
  does it interact with "never recycle"?
- On a Serverless v2 scale event or failover, do existing connections survive or get
  reset? (If reset, v1 breaks.)

### Verdict: **NEEDS REVISION** (pool/token-refresh strategy and driver TLS must be
nailed down before this is safe to implement)

---

## 3. SRE / DevOps Engineer - "Circuit"
*Focus: deployment, monitoring, scaling, infrastructure*

### Strengths
- Phased rollout (additive enable -> cutover -> cleanup) with the master password
  retained for rollback is the right shape and avoids a one-way door.
- Bypassing the RDS Proxy for the login path simplifies the auth reasoning and
  removes a stored-secret dependency.
- Observability section names the right leading indicators (task restart count,
  Agroal acquisition timeouts, RDS auth-failure metric).

### Concerns
- **Custom-image dependency raises the operational floor.** IAM auth now *requires*
  the CodeBuild-built ECR image; the public-image fallback (`var.keycloak_image_uri`
  default) silently cannot work. The LLD adds a precondition (good), but this couples
  every Keycloak deploy to the image pipeline. If CodeBuild breaks, there is no
  public-image escape hatch anymore.
- **Bootstrapping the `keycloak_iam` user is a manual, ordering-sensitive step.**
  The user must exist before the IAM-auth task starts, but creating it needs master
  access from inside the VPC. If the post-deploy script fails or runs late, the
  service crash-loops. This is a classic chicken-and-egg; needs a clear, idempotent,
  retried automation, not a hand-run SQL snippet.
- **CA bundle drift.** The RDS global CA bundle is baked into the image at build
  time. AWS rotates these CAs; a stale baked bundle eventually fails TLS. Needs a
  rebuild cadence or a runtime fetch.
- **Token generation depends on the container credential endpoint and egress.** If
  the task's egress to the STS/RDS signing path or the ECS credential endpoint is
  ever restricted (e.g. SG/NACL change), every task fails to start. Worth a
  monitored synthetic.

### New libraries / infra dependencies required
- CodeBuild pipeline becomes a hard dependency for Keycloak (already exists, but now
  load-bearing).

### Better alternatives considered
- For CA freshness, fetch the bundle at container start in the entrypoint (with a
  baked fallback) rather than only at build time.

### Recommendations
- Make IAM-user creation a first-class, idempotent, retried init container/task -
  not a manual script - and gate the main task on it.
- Add a deploy-time check that the resolved image is the ECR custom image.
- Define a CA-bundle refresh story.
- Alarm on Keycloak task restart rate and on RDS authentication failures.

### Questions for author
- How is ordering guaranteed between "create IAM user" and "first IAM-auth task
  start" on a fresh environment?
- What is the rebuild cadence for the image so the CA bundle stays current?

### Verdict: **APPROVED WITH CHANGES** (bootstrap automation + CA refresh + image
guard are required before production)

---

## 4. Security Engineer - "Cipher"
*Focus: AuthN/AuthZ, validation, OWASP, data protection*

### Strengths
- **This is a genuine security improvement.** Replacing a 30-day static password
  with 15-minute IAM tokens shrinks the credential-exposure window dramatically and
  makes access revocable via IAM policy.
- TLS is correctly made mandatory (IAM auth requires it), closing the current
  `require_tls = false` gap.
- `rds-db:connect` is scoped to one `dbuser` ARN, not wildcarded - least privilege.
- Token never logged; entrypoint avoids echoing the secret.

### Concerns
- **Residual master password.** The Aurora `master_password` remains in Terraform
  and in state. State files often live in S3 and are themselves sensitive; the
  "removed the password" claim is only true for the app path. Recommend documenting
  the master credential as break-glass and ensuring state backend encryption +
  access control.
- **`rds-db:connect` does not log per-connection in CloudTrail.** Detection of
  anomalous DB access via IAM is weaker than one might assume; compensate with RDS
  auth-failure metrics and DB-side audit logging.
- **Truststore password `changeit` in the JDBC URL.** It is a truststore (public
  CA), not a keystore, so confidentiality is not the concern - but a hardcoded
  password string in an SSM SecureString URL is a minor smell and should at least be
  consistent and documented as non-sensitive.
- **Token-signing username == DB user == ARN user must all match.** A subtle
  mismatch silently degrades to `Access denied`; not a vuln, but a foot-gun that
  invites operators to over-broaden the IAM resource to `*` to "fix" it. Guard
  against that temptation with a clear runbook.
- **Privilege of `keycloak_iam`.** The SQL grants `ALL PRIVILEGES ON keycloak.*`.
  Keycloak needs DML + DDL for migrations, so this is defensible, but call it out so
  it is a conscious decision, not a copy-paste default.

### New libraries / infra dependencies required
- None beyond the LLD's AWS CLI + CA bundle.

### Better alternatives considered
- Consider RDS-side audit logging (or Performance Insights) to recover some of the
  per-connection visibility CloudTrail does not provide.

### Recommendations
- Explicitly classify and protect the residual master credential; ensure TF state
  backend is encrypted and access-controlled.
- Add a runbook line: "never widen the `rds-db:connect` resource to `*` to fix
  Access denied; the username/ARN must match exactly."
- Confirm `keycloak_iam` privileges against Keycloak's actual migration needs.

### Questions for author
- Where does Terraform state live, and is it encrypted with restricted access given
  the master password remains?
- Will DB-side audit logging be enabled to compensate for CloudTrail's lack of
  per-connection `rds-db:connect` events?

### Verdict: **APPROVED WITH CHANGES** (residual-credential handling + detection
compensations should be documented)

---

## 5. SMTS / Overall - "Sage"
*Focus: architecture, code quality, maintainability*

### Strengths
- The design's standout quality is **honesty about the engine discrepancy**. Calling
  out that #1303 says PostgreSQL while production is Aurora MySQL, and adapting the
  mechanism accordingly, is exactly the senior judgment this kind of task demands.
  Many designs would have built a confident PostgreSQL solution that does not apply.
- Strong adherence to existing repo conventions: inline `jsonencode()` policies, the
  two-role ECS pattern, reusing existing `data` sources, and respecting the issue
  #1026 "single source of truth" lesson.
- Phased rollout with rollback and a clear Open Questions list signal appropriate
  humility about the unknowns.

### Concerns
- **The hardest part is deferred.** The token-expiry-vs-pool problem is *the*
  engineering challenge here, and v1 ships the weaker mitigation while naming the
  robust one as a follow-up. Byte is right: under realistic connection churn the
  fixed-pool approach is likely to produce intermittent post-15-minute failures.
  A design is judged by how it handles its hardest case; here that case is
  acknowledged but not solved.
- **Several load-bearing facts are unverified** (driver TLS properties, which
  `KC_DB_POOL_*` are first-class, proxy consumers, Serverless v2 connection behavior
  on scale/failover). These are correctly listed as Open Questions, but at least the
  driver/TLS and connection-survival questions are true blockers, not nice-to-haves.
- **"Remove static DB credentials from Terraform" is only partially achieved** while
  `master_password` persists. The design is honest about it but the issue's
  acceptance criteria should be reworded to match reality.

### Recommendations
- Before implementation: resolve (a) JDBC driver + TLS property names, (b) the
  definitive token-refresh strategy, (c) Serverless v2 connection survival on
  scale/failover. These three answers determine whether v1 is safe.
- Promote the IAM-user bootstrap to automated, idempotent, ordered infrastructure.
- Reword the issue acceptance criteria around the residual master credential.

### Questions for author
- Are you comfortable shipping v1 on the fixed-pool mitigation, or should the JDBC
  credentials provider be in-scope for the first release given the failure mode?

### Verdict: **NEEDS REVISION** (the central token/pool strategy and the unverified
driver/connection facts must be settled before this design is implementation-ready)

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED | 0 | Confirm health-check start_period; add login-outage runbook |
| Backend (Byte) | NEEDS REVISION | 2 | Settle driver/TLS props; fix token-refresh vs pool |
| SRE (Circuit) | APPROVED WITH CHANGES | 0 (3 required-before-prod) | Automate IAM-user bootstrap; CA refresh; image guard |
| Security (Cipher) | APPROVED WITH CHANGES | 0 | Protect residual master cred; detection compensations |
| SMTS (Sage) | NEEDS REVISION | 3 | Resolve driver/TLS, token strategy, SLv2 connection survival |

### Consolidated Blockers (must resolve before implementation)
1. **Token-expiry vs connection-pool strategy.** Prove the fixed-pool mitigation
   survives an induced connection drop after 15 minutes, or adopt the JDBC
   credentials provider / token-refresh approach for v1. (Byte, Sage)
2. **JDBC driver + TLS property names.** Confirm whether Keycloak 25.0 uses MySQL
   Connector/J or MariaDB driver and use the correct `sslMode`/truststore
   properties; wrong properties fail TLS and therefore IAM auth. (Byte, Sage)
3. **Serverless v2 connection behavior** on scale/failover - does it reset
   connections (which would break the "never recycle" mitigation)? (Sage)

### Required-Before-Production (not blocking design, but must precede go-live)
- Automated, idempotent, ordered `keycloak_iam` user bootstrap. (Circuit)
- RDS CA bundle refresh strategy. (Circuit)
- Deploy-time guard that the resolved image is the custom ECR image. (Circuit)
- Documented handling of the residual master credential and TF state protection. (Cipher)

### Next Steps
1. Author resolves the three consolidated blockers (spike the driver/TLS + a
   >15-minute connection-drop soak test).
2. Decide v1 token-refresh strategy (fixed-pool vs JDBC provider) based on the soak
   results.
3. Reword the issue's "remove static credentials" criterion to reflect the residual
   master password.
4. Re-review the revised LLD before any implementation.
