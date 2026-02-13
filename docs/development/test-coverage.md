# Test Coverage Requirements

This document defines test coverage requirements and quality gates for the HealthArchive backend.

## Coverage Targets

### Critical Modules

The following modules are considered **critical** for system reliability and must maintain minimum test coverage:

| Module | Current | Target | Priority |
|--------|---------|--------|----------|
| `ha_backend/api` | 95.81% / 77.29% | 80% | High |
| `ha_backend/worker` | 81.76% | 80% | High |
| `ha_backend/indexing` | Mixed | 75% → 80% | Medium |

**Overall Critical Modules**: 76.96% (target: 75% enforced, 80% goal)

### Running Coverage Checks

```bash
# Full coverage report (all modules)
make coverage

# Critical modules only (enforced in CI check-full)
make coverage-critical

# View coverage reports
make coverage-report
```

## CI Enforcement

### Current Enforcement (check-full)

The `make check-full` target includes `coverage-critical` which enforces:
- **75% minimum** coverage on critical modules (API, worker, indexing)
- Fails CI if coverage drops below threshold
- Prevents coverage regressions

### Not Enforced in Daily CI (check/ci)

Coverage is **not checked** in the daily `make ci` target to keep PR checks fast. Coverage is only enforced in:
- `make check-full` (pre-deploy checks)
- Manual coverage audits

## Coverage Configuration

Coverage settings are in `pyproject.toml`:

```toml
[tool.coverage.run]
source = ["src"]
omit = ["*/tests/*", "*/test_*.py"]

[tool.coverage.report]
precision = 2
show_missing = true
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
    # ... more exclusions
]
```

## Path to 80% Coverage

**Current bottleneck**: `indexing/pipeline.py` at 22.55%

To reach 80% overall:
1. Add integration tests for indexing pipeline
2. Test error paths in WARC processing
3. Test edge cases in text extraction

**Why 75% now, 80% later?**
- 75% is realistic given current test suite
- Enforcing 75% prevents regressions
- Provides concrete baseline for portfolio
- 80% is achievable with ~100 more lines of tests

## Coverage Best Practices

### What to Test

✅ **High priority**:
- API endpoints (request/response validation)
- Business logic (search, ranking, deduplication)
- Error handling (4xx/5xx responses)
- Security middleware (auth, rate limiting, CSP)

✅ **Medium priority**:
- Worker job lifecycle
- WARC indexing pipeline
- Database queries (critical paths)

⚠️ **Low priority**:
- CLI argument parsing
- Logging statements
- Configuration getters
- Development-only utilities

### Coverage Exclusions

Use `# pragma: no cover` sparingly for:
- Abstract methods that must be overridden
- Defensive assertions that should never trigger
- Platform-specific code paths
- `if __name__ == "__main__"` blocks

**Never exclude**:
- Error handling
- Business logic
- API endpoints
- Security code

## Viewing Coverage Reports

After running `make coverage` or `make coverage-critical`:

```bash
# Full report
open htmlcov/index.html

# Critical modules only
open htmlcov-critical/index.html
```

Reports show:
- Line-by-line coverage
- Missing lines highlighted in red
- Partially covered branches
- Excluded lines

## Coverage in CI

Coverage enforcement in CI workflow:

```yaml
# .github/workflows/backend-ci.yml
- name: Run full checks (includes coverage)
  run: make check-full
```

**When coverage fails**:
1. Check which module dropped below threshold
2. Review the diff - did you add untested code?
3. Add tests to cover new functionality
4. Re-run `make coverage-critical`

## FAQ

**Q: Why not 100% coverage?**
A: Diminishing returns. 75-80% covers critical paths. Higher coverage often tests trivial code.

**Q: Why different thresholds per module?**
A: API and worker are user-facing and easier to test. Indexing has complex file I/O harder to mock.

**Q: Can I temporarily disable coverage checks?**
A: No. Use `# pragma: no cover` for specific lines only, with justification in code comments.

**Q: How do I find what's not covered?**
A: Run `make coverage-critical` and open `htmlcov-critical/index.html` in a browser.

---

**Related docs**:
- [Testing Guide](testing-guide.md) (if it exists)
- [CI/CD Pipeline](../deployment/ci-cd.md) (if it exists)
- [Contributing](../../CONTRIBUTING.md) (if it exists)
