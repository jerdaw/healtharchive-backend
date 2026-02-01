# Contributing to HealthArchive

Thank you for your interest in contributing to HealthArchive! This guide will help you get started.

---

## 🌟 Ways to Contribute

HealthArchive welcomes contributions of all kinds:

- 🐛 **Report bugs** - Found an issue? Let us know
- 💡 **Suggest features** - Have ideas for improvements?
- 📝 **Improve documentation** - Help make our docs better
- 🔧 **Submit code changes** - Fix bugs or implement features
- 🧪 **Add tests** - Increase test coverage
- 👀 **Review pull requests** - Help review others' contributions

---

## 🚀 Quick Start for New Contributors

**New to the project?** Start here:

1. **Read the Code of Conduct** (below)
2. **Complete the tutorial**: [Your First Contribution](docs/tutorials/first-contribution.md)
3. **Browse good first issues**: [Good First Issues](https://github.com/jerdaw/healtharchive-backend/issues?q=is:issue+is:open+label:%22good+first+issue%22)
4. **Ask questions**: Use [GitHub Discussions](https://github.com/jerdaw/healtharchive-backend/discussions)

---

## 📋 Development Workflow

### 1. Set Up Your Environment

```bash
# Fork and clone
git clone https://github.com/YOUR-USERNAME/healtharchive-backend.git
cd healtharchive-backend

# Create virtual environment and install dependencies
make venv
source .venv/bin/activate

# Copy environment file
cp .env.example .env
source .env

# Run database migrations
alembic upgrade head

# Seed initial data
ha-backend seed-sources

# Verify setup
make ci
```

**See**: [Development Environment Setup](docs/development/dev-environment-setup.md) for details.

### 2. Create a Feature Branch

```bash
# Sync with upstream
git checkout main
git pull upstream main

# Create a descriptive branch
git checkout -b add-feature-name
```

**Branch naming conventions**:
- `add-*` - New features
- `fix-*` - Bug fixes
- `docs-*` - Documentation changes
- `refactor-*` - Code refactoring
- `test-*` - Test additions/fixes

### 3. Make Your Changes

**Follow these guidelines**:

- ✅ Write clear, focused commits
- ✅ Add tests for new functionality
- ✅ Update documentation as needed
- ✅ Follow existing code style
- ✅ Keep changes small and focused

### 4. Run Quality Checks

Before submitting, ensure all checks pass:

```bash
# Fast checks (required)
make ci

# Full checks (recommended)
make check-full

# Auto-fix formatting
make format
```

**What these do**:
- `make ci`: Format check, lint, typecheck, tests (~2 min)
- `make check-full`: Adds pre-commit, security scan, docs check (~5 min)
- `make format`: Auto-format code with ruff

### 5. Commit Your Changes

```bash
git add .
git commit -m "feat: add user authentication

- Implement JWT token-based auth
- Add login and logout endpoints
- Include test coverage for auth flow

Closes #123"
```

**Commit message format**:
```
type: short description (50 chars or less)

- Longer explanation if needed
- Use bullet points for clarity
- Reference issues

Closes #issue-number
```

**Commit types**:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `test`: Test additions/fixes
- `refactor`: Code refactoring
- `chore`: Maintenance tasks
- `perf`: Performance improvements

### 6. Push and Create Pull Request

```bash
# Push to your fork
git push origin add-feature-name

# Open PR on GitHub
# Fill out the PR template completely
```

**PR Title Format**:
```
feat: Add user authentication system
fix: Resolve crawl timeout issue
docs: Update API consumer guide
```

---

## 📝 Code Standards

### Python Code Style

We use **ruff** for linting and formatting (configured in `pyproject.toml`):

```bash
# Check formatting
ruff format --check .

# Auto-format
ruff format .

# Check linting
ruff check .

# Auto-fix issues
ruff check --fix .
```

**Key style points**:
- Line length: 100 characters
- Use type hints on all functions
- Write docstrings for public functions
- Follow PEP 8 conventions

### Type Hints

All functions should have type hints:

```python
# ✅ Good
def process_snapshot(snapshot_id: int, include_metadata: bool = False) -> dict[str, Any]:
    """Process a snapshot and return metadata.

    Args:
        snapshot_id: The ID of the snapshot to process
        include_metadata: Whether to include full metadata

    Returns:
        Dictionary containing snapshot data

    Raises:
        ValueError: If snapshot_id is invalid
    """
    ...

# ❌ Bad
def process_snapshot(snapshot_id, include_metadata=False):
    ...
```

### Documentation

**Docstring format** (Google style):

```python
def search_snapshots(
    query: str,
    source: str | None = None,
    limit: int = 20
) -> list[Snapshot]:
    """Search for snapshots matching the query.

    This function searches the database for snapshots matching the provided
    query string, optionally filtered by source.

    Args:
        query: Search query string
        source: Optional source code filter (e.g., "hc", "phac")
        limit: Maximum number of results to return (default: 20)

    Returns:
        List of Snapshot objects matching the query

    Raises:
        ValueError: If limit is less than 1

    Example:
        >>> snapshots = search_snapshots("covid vaccines", source="hc", limit=10)
        >>> len(snapshots)
        10
    """
    ...
```

### Import Organization

Use ruff's isort integration (automatic):

```python
# 1. Standard library
import json
import logging
from datetime import datetime
from pathlib import Path

# 2. Third-party packages
from fastapi import FastAPI, HTTPException
from sqlalchemy.orm import Session

# 3. Local imports
from ha_backend.config import get_config
from ha_backend.models import ArchiveJob, Snapshot
```

---

## 🧪 Testing Guidelines

### Writing Tests

All new code requires tests. We use **pytest**.

```python
# tests/test_feature.py
import pytest
from ha_backend.feature import process_data


def test_process_data_success():
    """Test successful data processing."""
    result = process_data({"key": "value"})
    assert result["processed"] is True
    assert result["key"] == "value"


def test_process_data_invalid_input():
    """Test error handling for invalid input."""
    with pytest.raises(ValueError, match="Invalid input"):
        process_data(None)


@pytest.fixture
def sample_snapshot(db_session):
    """Fixture providing a sample snapshot for tests."""
    snapshot = Snapshot(
        url="https://example.com",
        title="Test Page",
        capture_timestamp=datetime.now()
    )
    db_session.add(snapshot)
    db_session.commit()
    return snapshot


def test_with_fixture(sample_snapshot):
    """Test using a fixture."""
    assert sample_snapshot.title == "Test Page"
```

### Running Tests

```bash
# Run all tests
pytest

# Run specific file
pytest tests/test_api.py

# Run specific test
pytest tests/test_api.py::test_health_endpoint

# Run with coverage
pytest --cov=ha_backend --cov-report=html

# Run verbose
pytest -v

# Run with output
pytest -s
```

### Test Organization

- `tests/` - All test files
- `tests/conftest.py` - Shared fixtures
- `tests/test_*.py` - Test modules (mirror source structure)

**See**: [Testing Guidelines](docs/development/testing-guidelines.md) for more details.

---

## 📚 Documentation Standards

### When to Update Docs

Update documentation when you:

- ✅ Add new features or APIs
- ✅ Change existing behavior
- ✅ Fix bugs that affect user-facing behavior
- ✅ Add new CLI commands
- ✅ Modify configuration options

### Documentation Types

Follow the [Diátaxis framework](https://diataxis.fr/):

| Type | When to Use | Location |
|------|-------------|----------|
| **Tutorial** | Teaching concepts step-by-step | `docs/tutorials/` |
| **How-To Guide** | Solving specific problems | `docs/operations/playbooks/` |
| **Reference** | Looking up technical details | `docs/reference/`, `docs/api.md` |
| **Explanation** | Understanding concepts | `docs/`, `docs/decisions/` |

### Documentation Checklist

- [ ] Update relevant documentation files
- [ ] Add code examples if applicable
- [ ] Update API documentation (OpenAPI)
- [ ] Check for broken links
- [ ] Preview docs locally: `make docs-serve`
- [ ] Run docs checks: `make docs-check`

**See**: [Documentation Guidelines](docs/documentation-guidelines.md) for detailed standards.

---

## 🔍 Code Review Process

### Submitting for Review

Your PR will be reviewed by maintainers. To help the review process:

1. **Fill out the PR template completely**
2. **Keep PRs focused** - One feature/fix per PR
3. **Add screenshots** for UI changes
4. **Link related issues**
5. **Mark as draft** if work-in-progress

### Review Criteria

Reviewers will check:

- ✅ Code quality and style
- ✅ Test coverage
- ✅ Documentation updates
- ✅ No breaking changes (or documented migration path)
- ✅ Security considerations
- ✅ Performance impact

### Responding to Feedback

1. **Be responsive** - Reply to comments promptly
2. **Ask questions** if feedback is unclear
3. **Make requested changes** and push to the same branch
4. **Mark conversations resolved** when addressed
5. **Be respectful** - We're all learning

---

## 🐛 Reporting Bugs

### Before Reporting

1. **Search existing issues** - Your bug might already be reported
2. **Verify it's reproducible** - Can you make it happen consistently?
3. **Check if it's fixed** - Try the latest `main` branch

### Bug Report Template

When creating a bug report, include:

```markdown
## Description
Clear description of the bug

## Steps to Reproduce
1. Run command X
2. Do action Y
3. Observe error Z

## Expected Behavior
What should happen

## Actual Behavior
What actually happens

## Environment
- OS: Ubuntu 22.04
- Python: 3.11.7
- HealthArchive version: main branch (commit abc123)

## Logs/Screenshots
[Paste error traceback or screenshot]

## Additional Context
Any other relevant information
```

---

## 💡 Suggesting Features

### Feature Request Template

```markdown
## Problem
What problem does this solve?

## Proposed Solution
How should it work?

## Alternatives Considered
What other approaches did you consider?

## Additional Context
Mockups, examples, references
```

### Feature Discussion

- **Use Discussions** for ideas and brainstorming
- **Create an Issue** when the feature is well-defined
- **Wait for feedback** before starting implementation
- **Break large features** into smaller, incremental PRs

---

## 🏗️ Architecture & Design

### Making Design Decisions

For significant architectural changes:

1. **Open a Discussion** - Get feedback early
2. **Write a decision record** - Document the choice (see `docs/decisions/`)
3. **Create a roadmap** - For multi-step implementations (see `docs/planning/`)
4. **Get consensus** - Especially for breaking changes

### Decision Record Template

See `docs/_templates/decision-template.md` for the format.

**Example decisions**:
- Choosing a database migration tool
- Changing API authentication method
- Adding a new dependency

---

## 🔐 Security

### Reporting Security Issues

**DO NOT** open public issues for security vulnerabilities.

Instead:
1. Email the maintainers (check repo for contact info)
2. Or use GitHub's private vulnerability reporting
3. Provide details and reproduction steps
4. Wait for acknowledgment before disclosing

### Security Best Practices

When contributing code:

- ✅ Never commit secrets (API keys, passwords, tokens)
- ✅ Use environment variables for configuration
- ✅ Validate all user input
- ✅ Use parameterized queries (prevent SQL injection)
- ✅ Escape HTML output (prevent XSS)
- ✅ Review dependencies for known vulnerabilities

We run security scans with:
- `bandit` for Python security issues
- `safety` for dependency vulnerabilities

---

## 📜 Code of Conduct

### Our Pledge

We are committed to providing a welcoming and inclusive environment for everyone, regardless of:

- Age, body size, disability, ethnicity, gender identity and expression
- Level of experience, education, socio-economic status
- Nationality, personal appearance, race, religion
- Sexual identity and orientation

### Our Standards

**Positive behavior**:
- ✅ Being respectful and inclusive
- ✅ Accepting constructive criticism gracefully
- ✅ Focusing on what's best for the community
- ✅ Showing empathy towards others

**Unacceptable behavior**:
- ❌ Harassment, insults, or derogatory comments
- ❌ Trolling or inflammatory comments
- ❌ Publishing others' private information
- ❌ Other conduct inappropriate in a professional setting

### Enforcement

Violations of the code of conduct may result in:
- Warning
- Temporary ban
- Permanent ban

Report issues to the project maintainers.

---

## 🎯 Issue Labels

We use labels to organize issues:

| Label | Meaning |
|-------|---------|
| `good first issue` | Good for newcomers |
| `help wanted` | We'd love contributions |
| `bug` | Something isn't working |
| `enhancement` | New feature or request |
| `documentation` | Docs improvements |
| `question` | Further information requested |
| `wontfix` | Will not be addressed |
| `duplicate` | Already reported |

---

## 🚢 Release Process

(For maintainers)

1. Update version in `pyproject.toml`
2. Update CHANGELOG.md
3. Create git tag: `git tag v0.2.0`
4. Push tag: `git push origin v0.2.0`
5. GitHub Actions builds and publishes release

---

## 🤝 Getting Help

### Where to Ask Questions

| Type | Where |
|------|-------|
| **General questions** | [GitHub Discussions](https://github.com/jerdaw/healtharchive-backend/discussions) |
| **Bug reports** | [GitHub Issues](https://github.com/jerdaw/healtharchive-backend/issues) |
| **Feature requests** | [GitHub Issues](https://github.com/jerdaw/healtharchive-backend/issues) |
| **Security issues** | Private vulnerability reporting |

### Resources

- **Documentation**: [docs.healtharchive.ca](https://docs.healtharchive.ca)
- **Architecture Guide**: [docs/architecture.md](https://docs.healtharchive.ca/architecture/)
- **First Contribution Tutorial**: [docs/tutorials/first-contribution.md](https://docs.healtharchive.ca/tutorials/first-contribution/)
- **API Reference**: [docs/api.md](https://docs.healtharchive.ca/api/)

---

## 📊 Project Structure

```
healtharchive-backend/
├── src/
│   ├── ha_backend/          # Main backend package
│   │   ├── api/             # FastAPI routes
│   │   ├── indexing/        # WARC parsing and indexing
│   │   ├── worker/          # Job processing
│   │   ├── models.py        # Database models
│   │   └── ...
│   └── archive_tool/        # Crawler subpackage
├── tests/                   # Test suite
├── docs/                    # Documentation
├── alembic/                 # Database migrations
├── scripts/                 # Utility scripts
├── Makefile                 # Build targets
├── pyproject.toml           # Dependencies and config
└── CONTRIBUTING.md          # This file
```

---

## 🙏 Thank You

Every contribution helps make HealthArchive better for researchers, journalists, and the public. Whether you're fixing a typo, reporting a bug, or implementing a feature, your effort is appreciated!

**Questions?** Don't hesitate to ask in [Discussions](https://github.com/jerdaw/healtharchive-backend/discussions).

Happy contributing! 🎉
