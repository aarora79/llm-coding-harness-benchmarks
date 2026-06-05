# Expert Review: Remove FAISS from the codebase

*Created: 2026-06-05*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED | 0 | Update CLI docs for search command |
| Backend (Byte) | APPROVED | 0 | Test file backend thoroughly |
| SRE (Circuit) | APPROVED WITH CHANGES | 1 | Verify Docker builds work |
| Security (Cipher) | APPROVED | 0 | Run security scan after changes |
| SMTS (Sage) | APPROVED | 0 | Consider metrics-service in follow-up |

---

## Review Details

### Frontend Engineer (Pixel)

**Focus:** CLI, user-facing API, documentation

#### Strengths
- Search API contract remains unchanged - no breaking changes for clients
- Documentation updates scope is well-defined
- Repository factory provides clean abstraction for search backend

#### Concerns
1. **CLI documentation**: The `cli/agent_mgmt.py` docstring mentions "FAISS vector index" - this should be updated to "vector search" or "hybrid search"
2. **User-facing error messages**: If someone has `faiss-cpu` installed and tries to use file backend after removal, error messages should be helpful

#### New Libraries / Dependencies Required
None - removing a dependency is a net positive

#### Better Alternatives Considered
- Keeping FAISS deprecated:Rejected - adds complexity
- Rolling out deprecation warnings first: Rejected - spec calls for direct removal

#### Recommendations
1. Update CLI command help text to reference "vector search" generically
2. Add a startup log message about which search backend is being used
3. Consider adding a migration note in release notes

#### Questions for Author
1. Will the `搜索` command still work the same way after the change? *Yes - same API contract*

#### Verdict: APPROVED

---

### Backend Engineer (Byte)

**Focus:** API design, data models, business logic, performance

#### Strengths
- DocumentDB search implementation is well-structured with proper fallbacks
- Search repository interface provides clean separation of concerns
- Hybrid search (vector + keyword + RRF) is a robust implementation

#### Concerns
1. **File backend behavior**: After removing `FaissSearchRepository`, file backend uses `DocumentDBSearchRepository`. Need to verify this works correctly for local development
2. **Embedded server test isolation**: If tests assume FAISS exists, they may fail

#### New Libraries / Dependencies Required
None - removing `faiss-cpu` is beneficial

#### Better Alternatives Considered
- Implementing lightweight vector search for file backend: Rejected - too much work
- Keeping FAISS: Rejected - maintenance burden

#### Recommendations
1. Add integration test for file backend search functionality
2. Verify embeddings client works without FAISS loaded
3. Ensure test fixtures don't reference FAISS

#### Questions for Author
1. How should local development mode work without FAISS? *DocumentDB search can work in file mode by connecting to local MongoDB or using client-side search*

#### Verdict: APPROVED

---

### SRE/DevOps Engineer (Circuit)

**Focus:** Deployment, monitoring, scaling, infrastructure

#### Strengths
- Docker image build should be faster without FAISS (smaller image)
- No more native FAISS library to install in containers
- Simpler dependency graph

#### Concerns
1. **Docker build verification**: FAISS has native extensions that may affect Docker layers - need to verify builds work after removal
2. **MongoDB connection in file mode**: If file backend needs MongoDB, ensure `MONGO_URI` or similar is documented
3. **Terraform service descriptions**: The AWS ECS service management script has FAISS-related verification functions that should be removed

#### New Libraries / Dependencies Required
None - removing FAISS is good

#### Better Alternatives Considered
- None - this is a straightforward dependency removal

#### Recommendations
1. **Verify Docker image builds** without FAISS
2. Update `docker-compose*.yml` comments to remove "FAISS" mention
3. Remove Faiss-related functions from `terraform/aws-ecs/scripts/service_mgmt.sh`
4. Update `.env.example` if FAISS is mentioned there

#### Deployment Checklist
- [ ] Docker image builds successfully
- [ ] FAISS not present in image layers
- [ ] Search endpoints work after deployment
- [ ] File backend search works for local development

#### Questions for Author
1. Will the Docker image be smaller after removing FAISS? *Yes - FAISS has native dependencies that add significant size*

#### Verdict: APPROVED WITH CHANGES

**Blocker:** Verify Docker builds work before deployment

---

### Security Engineer (Cipher)

**Focus:** AuthN/AuthZ, validation, OWASP, data protection

#### Strengths
- Removing an external dependency reduces attack surface
- NoFAISS means fewer native library vulnerabilities to track
- DocumentDB search uses standard Python libraries

#### Concerns
1. **Dependency reduction**: Removing `faiss-cpu` reduces the number of dependencies to audit
2. **Embeddings security**: Ensure embedding API calls still go through proper auth paths

#### New Libraries / Dependencies Required
None

#### Better Alternatives Considered
- None - this is a security-positive change

#### Recommendations
1. Run `uv run bandit -r registry/` after changes
2. Verify no embeddings credentials are logged
3. Run `pip-audit` or similar to verify dependency tree

#### Compliance Considerations
- None - this change does not affect data handling or PII

#### Questions for Author
1. Does removing FAISS affect any data export/import functionality? *No - search is read-only*

#### Verdict: APPROVED

---

### SMTS (Sage)

**Focus:** Architecture, code quality, maintainability

#### Strengths
- Clean abstraction with `SearchRepositoryBase` interface
- DocumentDB search is production-ready with robust fallbacks
- Code removal follows principle of keeping only necessary code

#### Concerns
1. **Test coverage**: Need to ensure all search-related tests work without FAISS
2. **Migration path**: Users with existing FAISS indices need guidance

#### New Libraries / Dependencies Required
None

#### Better Alternatives Considered
- Alternative 1: Deprecate then remove - Rejected
- Alternative 2: New lightweight vector search - Rejected
- Alternative 3: Dual implementation - Rejected

#### Recommendations
1. Consider a follow-up issue to clean up metrics-service FAISS references
2. Add a migration section to release notes
3. Consider logging which search backend is active at startup

#### Code Quality Observations
- Repository factory pattern is well-designed for backend switching
- Search interface is clean and minimal
- DocumentDB search implementation follows existing patterns

#### Questions for Author
1. What happens to existing FAISS indices when upgrading? *They become unused - no data loss since DocumentDB maintains its own search index*

#### Verdict: APPROVED

---

## Cross-Cutting Concerns

### Testing Strategy
All reviewers agree that testing is critical:
1. Run full test suite
2. Test file backend specifically
3. Test documentdb backend
4. Test search API endpoints

### Deployment Checklist
- [ ] Remove `faiss-cpu` from `pyproject.toml`
- [ ] Delete FAISS-specific files
- [ ] Update factory.py
- [ ] Update tests
- [ ] Run `uv run bandit -r registry/`
- [ ] Build Docker image
- [ ] Test search endpoints

### Documentation Checklist
- [ ] Update CLI help text
- [ ] Update docker-compose comments
- [ ] Update README.md if needed
- [ ] Update release notes
- [ ] Update terraform documentation
