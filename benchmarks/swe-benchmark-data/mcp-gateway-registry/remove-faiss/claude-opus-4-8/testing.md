# Testing Plan: Remove FAISS and Consolidate Search

*Created: 2026-06-15*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
Verify that FAISS is fully removed (code, dependency, config, Terraform, Docker, CLI, docs) and that
search still works end-to-end on the default `STORAGE_BACKEND=file` backend with an unchanged API
contract, while the MongoDB-compatible backends are behaviorally unchanged.

### Prerequisites
- [ ] Repo checked out at the target ref with the change applied.
- [ ] `uv` installed; `uv sync` succeeds without `faiss-cpu`.
- [ ] For functional API tests: registry running locally with `STORAGE_BACKEND=file`.
- [ ] For backend-parity tests: a MongoDB instance (`docker ps | grep mongo`) and a second run with
      `STORAGE_BACKEND=mongodb-ce`.
- [ ] An embeddings model available locally (baked into the image or downloaded once) so the file
      backend can embed.
- [ ] A valid access token for authenticated endpoints.

### Shared Variables
```bash
export REGISTRY_URL="http://localhost:7860"        # adjust to local registry port
export ACCESS_TOKEN=$(jq -r '.access_token' .oauth-tokens/ingress.json 2>/dev/null || echo "REPLACE_ME")
export REPO_ROOT="$(git rev-parse --show-toplevel)/benchmarks/swe-benchmark-data/mcp-gateway-registry/repo"
```

---

## 0. Removal Verification (grep gates)

These are the primary acceptance gates. All must return no production hits.

```bash
cd "$REPO_ROOT"

# 0.1 No faiss-cpu dependency anywhere
grep -rni "faiss-cpu" pyproject.toml uv.lock && echo "FAIL: faiss-cpu still declared" || echo "PASS"

# 0.2 No python import of faiss
grep -rni "import faiss" --include="*.py" registry/ api/ cli/ scripts/ metrics-service/ \
  && echo "FAIL: import faiss remains" || echo "PASS"

# 0.3 No reference to the FaissService singleton or module
grep -rni "from .*search.service import faiss_service\|registry.search.service\|faiss_service\b" \
  --include="*.py" registry/ \
  && echo "FAIL: faiss_service usage remains" || echo "PASS"

# 0.4 FaissService / FaissMetadata / FaissSearchRepository classes are gone
grep -rni "class FaissService\|class FaissMetadata\|class FaissSearchRepository" --include="*.py" . \
  && echo "FAIL: FAISS class remains" || echo "PASS"

# 0.5 No .faiss index files referenced in code/config/scripts
grep -rni "service_index.faiss\|service_index_metadata.json\|faiss_index_path\|faiss_metadata_path" \
  registry/ build_and_run.sh cli/ terraform/ scripts/ \
  && echo "FAIL: faiss index path remains" || echo "PASS"

# 0.6 No verify_faiss_metadata function in shell/terraform scripts
grep -rni "verify_faiss_metadata\|FAISS_FILES" build_and_run.sh cli/ terraform/ \
  && echo "FAIL: faiss shell helper remains" || echo "PASS"

# 0.7 Whole-repo sweep (manual review): only test files that intentionally assert
#     "no faiss" may remain. Everything else must be clean.
grep -rnil "faiss" --exclude-dir=.git . | sort
# EXPECTED after change: at most a deliberate assertion in a test, e.g. tests verifying
# sys.modules has no "faiss". Docs, deps, config, terraform, cli, and registry/ code: zero hits.

# 0.8 The deleted files no longer exist
test ! -f registry/search/service.py && echo "PASS: service.py deleted" || echo "FAIL"
test ! -f tests/fixtures/mocks/mock_faiss.py && echo "PASS: mock_faiss.py deleted" || echo "FAIL"
test ! -f tests/unit/search/test_faiss_service.py && echo "PASS: faiss test deleted" || echo "FAIL"
```

```bash
# 0.9 Dependencies resolve and lock is clean of faiss
cd "$REPO_ROOT"
uv lock --check 2>&1 | tail -5      # or `uv sync` in a clean venv
grep -ni "faiss" uv.lock && echo "FAIL: faiss in lock" || echo "PASS: lock clean"
```

---

## 1. Functional Tests

### 1.1 curl / HTTP Tests (STORAGE_BACKEND=file)

Run the registry with `STORAGE_BACKEND=file` (the default) before these.

#### 1.1.1 Semantic search returns the unchanged schema
```bash
curl -sS -X POST "$REGISTRY_URL/api/search" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "current time and timezone", "entity_types": ["mcp_server","tool"], "max_results": 5}' \
  | jq '{servers: (.servers|length), tools: (.tools|length), first_server: .servers[0]}'
```
- **Expected status:** 200
- **Assertions:**
  - Top-level keys include `servers`, `tools`, `agents` (and `skills`, `virtual_servers` where
    applicable) - same shape as before the change.
  - Each server entry has `path`, `server_name`, `description`, `tags`, `relevance_score`
    (0.0-1.0), `match_context`, `matching_tools`.
  - `relevance_score` is a float in [0, 1].
- **Negative case:**
```bash
curl -sS -o /dev/null -w "%{http_code}\n" -X POST "$REGISTRY_URL/api/search" \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "", "max_results": 5}'
# Expected: 400 (no query and no tags)
```

#### 1.1.2 Tag-only search (exact match path)
```bash
curl -sS -X POST "$REGISTRY_URL/api/search" \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "#timeserver", "entity_types": ["mcp_server"], "max_results": 10}' \
  | jq '.servers | map(.server_name)'
```
- **Expected status:** 200
- **Assertions:** results are servers carrying the requested tag; `relevance_score` present.
  Confirms `FileSearchRepository.search_by_tags` is implemented (not the lossy base fallback).

#### 1.1.3 All tags endpoint
```bash
curl -sS "$REGISTRY_URL/api/tags" -H "Authorization: Bearer $ACCESS_TOKEN" | jq 'length, .[0:5]'
```
- **Expected status:** 200
- **Assertions:** sorted unique tag list; non-empty when servers/agents have tags. Confirms
  `get_all_tags()` over the in-memory corpus.

#### 1.1.4 Write path re-indexes through the repository (register -> search)
```bash
# Register a server (use the project's standard register payload/endpoint), then search for it.
# After registration, the new server must be findable WITHOUT any .faiss file being written.
curl -sS -X POST "$REGISTRY_URL/api/search" \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "<distinctive words from the just-registered server description>", "max_results": 5}' \
  | jq '.servers | map(.server_name)'
# Expected: the newly registered server appears.

# Confirm no FAISS artifacts were created at runtime:
find "$REPO_ROOT/registry/servers" -name "*.faiss" -o -name "service_index_metadata.json" \
  | tee /dev/stderr | wc -l
# Expected: 0
```

#### 1.1.5 Toggle re-indexes enabled state
```bash
# Toggle a server off, then search with include_disabled=false (default) and confirm it is excluded;
# toggle on and confirm it returns. Verifies the toggle path now updates the repo index only.
curl -sS -X POST "$REGISTRY_URL/api/search" \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "<server words>", "include_disabled": false, "max_results": 5}' | jq '.servers|length'
```

### 1.2 CLI Tests
```bash
# Service management script must run without the FAISS metadata verification step.
bash -n "$REPO_ROOT/cli/service_mgmt.sh" && echo "PASS: cli/service_mgmt.sh syntax ok"
bash -n "$REPO_ROOT/build_and_run.sh" && echo "PASS: build_and_run.sh syntax ok"
bash -n "$REPO_ROOT/terraform/aws-ecs/scripts/service_mgmt.sh" && echo "PASS: tf service_mgmt syntax ok"

# agent_mgmt help text no longer claims FAISS:
grep -ni "faiss" "$REPO_ROOT/cli/agent_mgmt.py" && echo "FAIL" || echo "PASS: cli help clean"
```

---

## 2. Backwards Compatibility Tests

### 2.1 API response shape is preserved (file backend)
- Capture a `/api/search` response on the pre-change build and on the post-change build for the same
  query and corpus; diff the JSON **keys** (not score values). Keys must be identical.
```bash
# Post-change capture (compare against a saved pre-change baseline):
curl -sS -X POST "$REGISTRY_URL/api/search" -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" -d '{"query":"weather","max_results":5}' \
  | jq -S 'paths | join(".")' | sort -u > /tmp/post_keys.txt
# diff /tmp/pre_keys.txt /tmp/post_keys.txt   # expected: no differences in key paths
```

### 2.2 MongoDB backend unchanged
- Run the registry with `STORAGE_BACKEND=mongodb-ce` and repeat 1.1.1-1.1.3. Behavior and schema must
  be identical to before this change (the DocumentDB repo is untouched except for the optional
  helper-extraction import).
```bash
# With STORAGE_BACKEND=mongodb-ce running:
curl -sS -X POST "$REGISTRY_URL/api/search" -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" -d '{"query":"current time","max_results":5}' | jq '.servers|length'
# Expected: same nonzero results as pre-change.
```

### 2.3 Default backend unchanged
- With no `STORAGE_BACKEND` set, `config.py` must still default to `file` and the registry must boot
  and serve search. Confirms we did not change the default or break the file path.

### 2.4 Telemetry value change is intentional
```bash
# The search_backend telemetry tag for the file backend is now "file" (was "faiss").
grep -n 'search_backend' "$REPO_ROOT/registry/core/telemetry.py"
# Expected: value resolves to "documentdb" for Mongo backends and "file" (not "faiss") otherwise.
```
This is a deliberate, documented change (release notes), not a regression.

---

## 3. UX Tests

### 3.1 Search UI renders file-backend results
- With `STORAGE_BACKEND=file`, open the registry web UI, run a known query, and confirm:
  - Result cards render with names, descriptions, tags, score badges, and matching-tool sub-lists.
  - Ordering is sensible (most relevant first).
  - Compare ordering for a fixed query against the prior FAISS ordering; note expected differences
    in absolute score values (see Pixel's review). Ordering of clearly-relevant results should be
    stable.

### 3.2 Error message clarity
- Trigger the 503 path (e.g. make the embedding model unavailable) and confirm the user-facing
  message is the generic "Semantic search is temporarily unavailable" - and that server logs say
  "Search service unavailable" (no longer "FAISS search service unavailable").

---

## 4. Deployment Surface Tests

### 4.1 Docker wiring
- **Not Applicable** for FAISS volume mounts - confirmed there are no `.faiss` mounts in any
  `docker-compose*.yml`. Still verify the image builds without `faiss-cpu`:
```bash
cd "$REPO_ROOT"
docker build -f Dockerfile -t mcp-registry-test . 2>&1 | tail -20
# Expected: build succeeds; no faiss-cpu wheel fetched.
```

### 4.2 Terraform / ECS wiring
```bash
cd "$REPO_ROOT/terraform/aws-ecs"
terraform validate 2>&1 | tail -10        # expected: success
grep -rni "faiss" . && echo "FAIL: faiss in terraform" || echo "PASS"
bash -n scripts/service_mgmt.sh && echo "PASS: tf service_mgmt syntax ok"
# Confirm add/remove service still has a coherent post-op success check (no dangling empty function).
```

### 4.3 Helm / EKS wiring
- **Not Applicable** - no FAISS references found under `charts/`. Confirm with:
```bash
grep -rni "faiss" "$REPO_ROOT/charts/" && echo "FAIL" || echo "PASS: charts clean"
```

### 4.4 Deploy and verify
- Deploy the file-backend stack (`./build_and_run.sh`) and confirm startup logs no longer reference
  FAISS index file creation, and that the registry serves `/api/search`.
```bash
# After ./build_and_run.sh, the startup log for the file backend should say it built the in-memory
# index (no "FAISS index files created" / no .faiss path lines).
docker logs <registry-container> 2>&1 | grep -i "faiss" && echo "FAIL: faiss in logs" || echo "PASS"
```

### 4.5 Rollback verification
- Rolling back to the previous image restores `faiss-cpu` and the FAISS index files; the file
  backend re-indexes from source on boot either way (no persisted FAISS state is required by the new
  build, and the old build rebuilds its own index), so rollback is safe with no data migration.

---

## 5. End-to-End API Tests

### 5.1 Full lifecycle on the file backend (register -> search -> toggle -> delete)
1. Register a new MCP server with a distinctive description.
2. `POST /api/search` for the distinctive words -> server appears.
3. `POST /toggle/<path>` to disable -> search (include_disabled=false) excludes it.
4. Toggle to enable -> search includes it again.
5. Delete the server -> search no longer returns it; `remove_entity` removed it from the in-memory
   index.
6. Throughout, assert no `.faiss` / `service_index_metadata.json` file is created under
   `registry/servers/`.

### 5.2 Agent batch path
1. Submit an agent batch job (`POST /api/agents/batch`) that creates and later removes agents.
2. After processing, `POST /api/search` with `entity_types=["a2a_agent"]` reflects the created
   agents, and removed agents disappear - confirming `agent_batch_item_processor.py` now indexes via
   the repository (and that `index_agent` accepts an `AgentCard`).

---

## 6. Test Execution Checklist
- [ ] Section 0 (Removal grep gates) all PASS - this is the primary acceptance gate.
- [ ] Section 1 (Functional: file backend search, tags, write-path re-index, no .faiss files) passes.
- [ ] Section 2 (Backwards Compat: schema preserved; Mongo backend unchanged; default unchanged;
      telemetry value change intentional) verified.
- [ ] Section 3 (UX) verified.
- [ ] Section 4 (Deployment: image builds without faiss-cpu; terraform validates; charts clean) verified.
- [ ] Section 5 (E2E lifecycle + agent batch) verified.
- [ ] `tests/unit/search/test_faiss_service.py` removed; `tests/fixtures/mocks/mock_faiss.py` removed.
- [ ] New `tests/unit/search/test_file_search_repository.py` added (index, remove, cosine ranking,
      keyword-only fallback when embeddings unavailable, `search_by_tags`, `get_all_tags`, empty
      corpus, `index_agent` accepts `AgentCard`, pathological/long query bounded tokenization).
- [ ] `tests/conftest.py` no longer mocks `sys.modules["faiss"]`; `tests/unit/conftest.py`,
      `tests/unit/core/test_config.py`, `tests/test_infrastructure.py`,
      `tests/unit/test_safe_eval_arithmetic.py` updated to drop FAISS references.
- [ ] `uv run bandit -r registry/` shows no new findings and no orphaned `# nosec` comments.
- [ ] `uv run ruff check . && uv run ruff format --check .` clean.
- [ ] `uv run pytest tests/ -n 8` passes with no regressions; coverage stays at or above the
      configured minimum (~35%).
