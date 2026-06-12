# Expert Review: Remove FAISS from Codebase

*Created: 2026-06-12*
*Author: Claude (minimax-m2.5 benchmark)*

---

## Reviewer: Pixel (Frontend Engineer)

### Strengths
- Clear separation between keyword search and vector search
- File-based JSON storage is simple and human-readable
- Preserves the existing SearchRepositoryBase interface

### Concerns
1. **Search Quality**: Keyword-only search may return less relevant results compared to vector/semantic search. The relevance scoring is basic (token matching).

### New Libraries / Infra Dependencies Required
- None

### Better Alternatives Considered
- Keep FAISS for vector search, add simple keyword fallback for when FAISS fails
- **Why Rejected**: Goal is to remove FAISS entirely, not add more complexity

### Recommendations
1. Consider adding TF-IDF based ranking for more sophisticated keyword search
2. Document that this change reduces search quality for semantic similarity

### Questions for Author
1. How will this affect users who rely on semantic/vector search?
2. Is there a plan to add vector search back with a simpler implementation?

### Verdict: APPROVED WITH CHANGES

---

## Reviewer: Byte (Backend Engineer)

### Strengths
- Clean implementation following existing patterns
- Good use of JSON file storage for persistence
- Proper async implementation throughout
- Score calculation includes multiple matching signals (name, description, tags, path)

### Concerns
1. **Memory**: Loading all entities into memory (`self._entities` dict) could be problematic for large registries. Consider pagination or streaming for very large datasets.
2. **Race Conditions**: No file locking on `_save_metadata()` - concurrent writes could corrupt the JSON file.
3. **Search Scoring**: Linear token matching is basic. Terms must appear in documents but order/position doesn't matter.

### New Libraries / Infra Dependencies Required
- None

### Better Alternatives Considered
- Use SQLite with FTS (Full-Text Search) for better keyword search
- Use ElasticSearch/OpenSearch for production-grade search
- **Why Rejected**: Adding new dependencies is counter to the goal

### Recommendations
1. Add file locking or use atomic writes for metadata persistence
2. Consider lazy-loading entities instead of loading all at startup
3. Add integration tests for concurrent index operations

### Questions for Author
1. What is the expected scale of entities to be indexed?
2. Has the performance been benchmarked for large datasets?

### Verdict: APPROVED WITH CHANGES

---

## Reviewer: Circuit (SRE/DevOps Engineer)

### Strengths
- Eliminates `faiss-cpu` native dependency - simplifies container builds
- Removes need for ML model downloads at startup - faster boot times
- JSON file storage is simple to debug and backup
- No external services required (unlike DocumentDB)

### Concerns
1. **Storage**: JSON file will grow unbounded. Need cleanup strategy for old/deleted entities.
2. **Backup**: Large JSON files in `/servers/` directory need to be included in backups.
3. **Monitoring**: No metrics for search operations (query latency, result count).

### New Libraries / Infra Dependencies Required
- None

### Better Alternatives Considered
- Keep search in DocumentDB for all backends (not just MongoDB backends)
- Use S3 for search metadata storage
- **Why Rejected**: Complexity and cost

### Recommendations
1. Add a max file size or entity count limit with warning logs
2. Document the backup requirements for the JSON metadata file
3. Add basic search metrics (query count, latency histogram)

### Questions for Author
1. How is the JSON file backed up in production?
2. What happens if the file grows too large?

### Verdict: APPROVED WITH CHANGES

---

## Reviewer: Cipher (Security Engineer)

### Strengths
- Removes external native library (`faiss-cpu`) reducing attack surface
- No new dependencies means fewer CVEs to track
- JSON storage is transparent and auditable

### Concerns
1. **File Permissions**: JSON metadata file in `/servers/` should have restrictive permissions
2. **Path Traversal**: Entity paths are used directly as dict keys - ensure no injection possible
3. **Sensitive Data**: Entity descriptions may contain sensitive info - ensure proper access control

### New Libraries / Infra Dependencies Required
- None

### Better Alternatives Considered
- Encrypt search metadata at rest
- **Why Rejected**: Performance overhead

### Recommendations
1. Add file permission checks on startup
2. Validate entity paths don't contain `..` or absolute paths
3. Document data classification for search metadata

### Questions for Author
1. Are entity paths validated before storage?
2. Is there any PII in the search metadata that needs protection?

### Verdict: APPROVED

---

## Reviewer: Sage (SMTS - Overall Architecture)

### Strengths
- Clear elimination of unnecessary dependency
- Follows YAGNI principle - implements only what's needed
- Maintains interface contract - no API changes
- Good negative impact analysis (what's removed vs. added)

### Concerns
1. **Search Feature Parity**: Removing vector search reduces functionality. DocumentDB search already exists for vector use cases - users should migrate to that.
2. **Interface Consistency**: The `rebuild_index()` method is a no-op. This could be confusing - consider either implementing it or removing from interface.
3. **Testing Coverage**: Need to ensure existing search API tests pass with new implementation.

### New Libraries / Infra Dependencies Required
- None

### Better Alternatives Considered
- Deprecate FAISS gradually with warning logs instead of immediate removal
- Document the migration path for users
- **Why Rejected**: Goal is complete removal as per issue

### Recommendations
1. Add a `rebuild_index()` that actually rebuilds from source (load from servers/agents/skills directories)
2. Update SearchRepositoryBase docstring to clarify "file-based" replaces "FAISS"
3. Consider adding a feature flag for search backend selection

### Questions for Author
1. Has impact on users been communicated?
2. What's the migration path for users needing vector search?

### Verdict: APPROVED WITH CHANGES

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Pixel (Frontend) | APPROVED WITH CHANGES | 0 | Consider TF-IDF ranking |
| Byte (Backend) | APPROVED WITH CHANGES | 0 | Add file locking, lazy loading |
| Circuit (SRE) | APPROVED WITH CHANGES | 0 | Add file size limits, metrics |
| Cipher (Security) | APPROVED | 0 | Path validation |
| Sage (SMTS) | APPROVED WITH CHANGES | 0 | Implement rebuild_index() properly |

### High-Priority Items

1. **File Locking** (Byte, Circuit): Add file locking for concurrent access to JSON metadata
2. **Path Validation** (Cipher): Validate entity paths don't contain directory traversal
3. **rebuild_index** (Sage): Implement actual rebuild logic instead of no-op

### Medium-Priority Items

1. **Search Quality** (Pixel): Consider TF-IDF or better keyword matching
2. **Lazy Loading** (Byte): Don't load all entities into memory on initialize
3. **File Size Limits** (Circuit): Add warnings for large metadata files

### Low-Priority Items

1. Metrics for search operations
2. Feature flag for search backend

### Conclusion

The design is sound and achieves the goal of removing FAISS with minimal disruption. Several improvements should be made before implementation, particularly around file safety and scalability. The reviewers agreed that this change simplifies the codebase significantly while maintaining API compatibility.