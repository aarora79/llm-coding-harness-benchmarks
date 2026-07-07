# Expert Review: Remove FAISS from the codebase

*Created: 2026-07-06*
*Author: Claude (qwen3.6-35b)*
*Status: Draft*

---

## Frontend Engineer — Pixel

### Strengths
- No UI changes are involved, so no frontend impact.

### Concerns
- **N/A** — This is a backend/cleanup task.

### Recommendations
- None for frontend.

### Questions for Author
- None.

### Verdict
**APPROVED**

---

## Backend Engineer — Byte

### Strengths
- Clear mapping of every file that references FAISS, with approximate line counts.
- Step-by-step implementation plan ordered by dependency (delete core files first, then remove calls, then update configs, then clean tests and docs).
- Correctly identifies that the factory pattern should be simplified, not patched with a stub.
- Notes that `faiss_service` is imported lazily in route handlers (inside function bodies), so removing calls does not affect import order or startup.

### Concerns
1. **test_safe_eval_arithmetic.py faiss __spec__ patching**: This test patches `faiss.__spec__` because transformers internally calls `importlib.util.find_spec("faiss")`. After removing faiss-cpu, this patch will no longer be needed — but the test file needs to be checked carefully to ensure the patch removal does not break the test itself. The patch is inside a conditional block (`if "faiss" in sys.modules`), so it should be safe to remove.
2. **test_factory_aliases.py**: The test checks that `get_search_repository()` returns a class starting with "File" or named "FaissSearchRepository". Removing FaissSearchRepository means this test needs to be updated to expect "DocumentDB" instead. The LLD mentions this but does not show the exact code change.
3. **migrate-file-to-mongodb.py**: This script exists in the repo. Should it be deleted along with FAISS? The LLD flags it as an open question but does not make a recommendation. If FAISS indices exist in production, this script may still be useful for one-time migration. However, the issue states that DocumentDB is the active backend — suggesting no migration is needed.

### Recommendations
- Resolve the migrate-file-to-mongodb.py question explicitly. If no FAISS indices exist in production, delete the script. If they do, keep it but update its description to say "migration from removed FAISS backend — use with caution."
- Add explicit validation in config.py: if storage_backend is not in MONGODB_BACKENDS, raise ValueError. This prevents accidental reverts to a non-DocumentDB config.
- The LLD should list the exact line numbers for each edit in server_routes.py. There are ~14 import locations — a simple `grep` + manual verification is needed.

### Questions for Author
- What happens if someone sets storage_backend to an empty string or a non-DocumentDB value after this change? Is there validation?
- Are there any production FAISS index files (service_index.faiss, service_metadata.json) that need cleanup in deployed servers?

### New Libraries / Infra Dependencies
- None.

### Better Alternatives Considered
- None. The chosen approach (full removal) is the simplest and most correct.

### Verdict
**APPROVED WITH CHANGES**
- Blocker: Add config validation for storage_backend.
- Blocker: Decide on migrate-file-to-mongodb.py fate.

---

## SRE/DevOps Engineer — Circuit

### Strengths
- Correctly identifies Docker, docker-compose, build-config.yaml, and Terraform as affected surfaces.
- Notes that removing faiss-cpu will reduce container image size by eliminating the native FAISS library pull.

### Concerns
1. **Dockerfile**: The LLD does not mention the Dockerfile. There may be `RUN pip install` or `pip install -e .` lines that install faiss-cpu as a transitive dependency. After removing it from pyproject.toml, pip will no longer install it, but the Dockerfile itself may have explicit references that should be cleaned up.
2. **Helm charts**: The `charts/` directory was not explicitly mentioned in the file changes table. Helm values files may reference storage_backend or have FAISS-related comments.
3. **Container image layers**: If the Dockerfile caches pip install layers, removing faiss-cpu may not reduce image size on the first deploy (cached layers still exist). Operators may need to rebuild with --no-cache or use a fresh build cache.
4. **ECS task definitions**: The Terraform files reference FAISS in comments but the actual ECS task definition may have environment variables for storage_backend. Need to verify.

### Recommendations
- Check the Dockerfile for any explicit FAISS references (RUN commands, ENV vars, COPY of FAISS-specific assets).
- Check charts/ directory for FAISS references in Helm values, templates, or comments.
- Document the expected Docker image size reduction in release notes.
- Add a rollback verification test in testing.md: verify that a previous version of the image (with FAISS) can still run while the new version deploys.

### Questions for Author
- What is the current Docker image size? What is the expected reduction?
- Are there any CI/CD pipelines that build Docker images? Do they need updating (e.g., build args for FAISS)?

### New Libraries / Infra Dependencies
- None.

### Verdict
**APPROVED WITH CHANGES**
- Blocker: Check Dockerfile for FAISS references.
- Blocker: Check charts/ for FAISS/Helm references.
- Recommendation: Document expected image size reduction.

---

## Security Engineer — Cipher

### Strengths
- Removing faiss-cpu reduces the attack surface by one fewer native library to audit.
- No new dependencies are introduced, which is a security positive.

### Concerns
1. **No auth/AuthZ changes**: This is a cleanup task, so there are no new authentication or authorization concerns.
2. **Telemetry data**: The telemetry.py change removes the "faiss" fallback string. This is purely cosmetic and does not affect telemetry data collection security.
3. **Migration script**: If `scripts/migrate-file-to-mongodb.py` is kept, it may read FAISS index files from disk. These files could contain sensitive server metadata. The script should be reviewed for data leakage risks if kept.

### Recommendations
- If migrate-file-to-mongodb.py is kept, add a note that it handles potentially sensitive server metadata and should not be run on untrusted data.
- No other security concerns identified.

### Questions for Author
- Does the telemetry collector schema regex change (`"^(faiss|documentdb)$"` to `"^documentdb$"`) affect any running telemetry collectors?

### Verdict
**APPROVED**

---

## SMTS (Overall) — Sage

### Strengths
- Thorough codebase analysis with accurate file counts and line estimates.
- Implementation plan is well-ordered: core files first, then callers, then configs, then tests, then docs.
- Correctly identifies the factory pattern simplification as the architectural improvement.
- No new dependencies are introduced — a clean reduction.
- Estimated net code reduction of ~400 lines is realistic given the 328 non-test FAISS references found.

### Concerns
1. **Documentation coverage is incomplete**: The LLD lists 40+ documentation files but only a few have specific line counts. Many are listed as "Multiple" without detail. The implementer will need to carefully read each file to determine what to change vs. what to leave (historical content in release notes, for example, should not be altered).
2. **Historical content risk**: The LLD mentions updating `release-notes/v1.0.17.md` but historical release notes should not be altered — they document what was true at that release time. The implementer should add a NEW release note (e.g., v1.25.0 or next version) documenting FAISS removal, not retroactively edit old notes.
3. **tests/README.md**: The LLD lists this as "Modified" but test documentation may be valuable to keep as historical reference for contributors who encounter old test fixtures. Consider moving old test fixtures to a `tests/_legacy/` directory instead of deleting immediately.
4. **Factory pattern simplification**: Removing the else-branch entirely is correct, but the `MONGODB_BACKENDS` import in factory.py should be checked. If other factory functions still use it, the import stays. If not, it should be removed from that file's imports.
5. **Edge case: empty storage_backend**: The LLD flags adding config validation but does not show the exact code. If storage_backend defaults to a DocumentDB value, existing deployments are unaffected. But if it defaults to empty or a non-DocumentDB value, the app will crash. Need to verify the default.

### Recommendations
- Add a release note for the next version documenting FAISS removal and expected image size reduction.
- Do NOT edit historical release notes — only add new ones.
- Show the exact config validation code in the LLD (not just a pseudocode comment).
- Verify the default value of storage_backend to ensure no regression.
- Consider adding a TODO or issue for migrating `scripts/migrate-file-to-mongodb.py` if it should be kept.

### Questions for Author
- What is the default value of `storage_backend`? Is it guaranteed to be a DocumentDB backend?
- Are there any integration tests that explicitly test the FAISS code path (via `storage_backend` not in MONGODB_BACKENDS)? These tests will need to be deleted or rewritten.
- Should the `registry/repositories/file/` directory be deleted entirely (if it contains ONLY search_repository.py), or does it have other file-based repositories?

### Verdict
**APPROVED WITH CHANGES**
- Blocker: Do NOT edit historical release notes.
- Blocker: Show exact config validation code in LLD.
- Blocker: Verify default storage_backend value.
- Recommendation: Check if `registry/repositories/file/` directory should be deleted.
- Recommendation: Add new release note documenting FAISS removal.

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED | 0 | None |
| Backend (Byte) | APPROVED WITH CHANGES | 2 | Add config validation; decide on migrate-file-to-mongodb.py |
| SRE (Circuit) | APPROVED WITH CHANGES | 2 | Check Dockerfile; check charts/ for FAISS references |
| Security (Cipher) | APPROVED | 0 | No security concerns |
| SMTS (Sage) | APPROVED WITH CHANGES | 3 | Do not edit historical release notes; show exact validation code; verify default storage_backend |

**Overall: APPROVED WITH CHANGES** (5 blockers across 3 reviewers, 0 blockers from 2 reviewers)

## Next Steps
1. Add config validation for storage_backend (Backend, SMTS consensus).
2. Check Dockerfile and charts/ directory for additional FAISS references (SRE).
3. Decide on migrate-file-to-mongodb.py fate (Backend).
4. Do NOT edit historical release notes; add a new release note for this change (SMTS).
5. Verify default storage_backend value is a DocumentDB backend (SMTS).
6. Check if `registry/repositories/file/` directory can be fully deleted (SMTS).
7. Add exact config validation code snippet to the LLD implementation steps.