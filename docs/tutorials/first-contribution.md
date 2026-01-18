# Your First Contribution

This tutorial walks you through making your first code contribution to HealthArchive, from setup to merged pull request.

**Time**: 30-45 minutes
**Prerequisites**:
- Git installed
- Python 3.11+ installed
- Basic command line knowledge
- GitHub account

---

## Step 1: Fork and Clone

1. **Fork the repository** on GitHub:
   - Visit [github.com/jerdaw/healtharchive-backend](https://github.com/jerdaw/healtharchive-backend)
   - Click "Fork" in the top-right corner

2. **Clone your fork**:
   ```bash
   git clone https://github.com/YOUR-USERNAME/healtharchive-backend.git
   cd healtharchive-backend
   ```

3. **Add upstream remote** (to sync with the main repo later):
   ```bash
   git remote add upstream https://github.com/jerdaw/healtharchive-backend.git
   ```

4. **Verify remotes**:
   ```bash
   git remote -v
   # Should show:
   # origin    https://github.com/YOUR-USERNAME/healtharchive-backend.git (fetch)
   # origin    https://github.com/YOUR-USERNAME/healtharchive-backend.git (push)
   # upstream  https://github.com/jerdaw/healtharchive-backend.git (fetch)
   # upstream  https://github.com/jerdaw/healtharchive-backend.git (push)
   ```

---

## Step 2: Set Up Your Development Environment

1. **Create a virtual environment and install dependencies**:
   ```bash
   make venv
   ```

   This command:
   - Creates `.venv/` directory
   - Installs all Python dependencies
   - Installs development tools (pytest, ruff, mypy, pre-commit)

2. **Activate the virtual environment**:
   ```bash
   source .venv/bin/activate
   ```

3. **Copy the example environment file**:
   ```bash
   cp .env.example .env
   ```

4. **Edit `.env`** with your local paths (optional, defaults work for most cases):
   ```bash
   # Example local settings
   HEALTHARCHIVE_DATABASE_URL=sqlite:///$(pwd)/.dev-healtharchive.db
   HEALTHARCHIVE_ARCHIVE_ROOT=$(pwd)/.dev-archive-root
   HEALTHARCHIVE_LOG_LEVEL=INFO
   ```

5. **Source the environment**:
   ```bash
   source .env
   ```

6. **Run database migrations**:
   ```bash
   alembic upgrade head
   ```

7. **Seed initial data**:
   ```bash
   ha-backend seed-sources
   ```

---

## Step 3: Verify Your Setup

Run the fast CI checks to ensure everything works:

```bash
make ci
```

This runs:
- `ruff check` (linting)
- `mypy` (type checking)
- `pytest` (tests)

**Expected output**: All checks should pass ✅

If anything fails, review the error messages and check:
- Python version is 3.11+
- Virtual environment is activated
- Dependencies installed correctly

---

## Step 4: Find a Good First Issue

1. **Browse issues labeled `good first issue`**:
   - Visit [github.com/jerdaw/healtharchive-backend/issues?q=is:issue+is:open+label:"good+first+issue"](https://github.com/jerdaw/healtharchive-backend/issues?q=is:issue+is:open+label:%22good+first+issue%22)

2. **Pick an issue that interests you**:
   - Look for clear descriptions
   - Check if anyone is already working on it
   - Comment on the issue to claim it

3. **No good first issues available?** Try:
   - Fix a typo in documentation
   - Add a test for an existing function
   - Improve error messages or log output

**For this tutorial, we'll add a simple CLI command as an example.**

---

## Step 5: Create a Feature Branch

1. **Sync with upstream** (ensure you have latest changes):
   ```bash
   git checkout main
   git pull upstream main
   ```

2. **Create a new branch** (use a descriptive name):
   ```bash
   git checkout -b add-version-command
   ```

   Branch naming conventions:
   - `add-*` for new features
   - `fix-*` for bug fixes
   - `docs-*` for documentation changes
   - `refactor-*` for code refactoring

---

## Step 6: Make Your Change

Let's add a simple `--version` command to the CLI.

1. **Open `src/ha_backend/cli/main.py`** in your editor

2. **Add a version command** (example change):
   ```python
   @cli.command()
   def version():
       """Display version information."""
       import ha_backend
       from ha_backend.logging_config import setup_logging

       setup_logging()
       logger = logging.getLogger(__name__)

       # You would import from a version module in a real implementation
       version_string = "0.1.0"  # Placeholder
       logger.info(f"HealthArchive Backend version {version_string}")
       print(f"HealthArchive Backend v{version_string}")
   ```

3. **Test your change manually**:
   ```bash
   ha-backend version
   # Should output: HealthArchive Backend v0.1.0
   ```

---

## Step 7: Write Tests

Every code change needs tests. Let's write a test for our new command.

1. **Create or update test file**: `tests/test_cli_version.py`

   ```python
   """Tests for version CLI command."""
   import subprocess


   def test_version_command():
       """Test that version command runs and outputs version info."""
       result = subprocess.run(
           ["ha-backend", "version"],
           capture_output=True,
           text=True,
       )

       assert result.returncode == 0
       assert "HealthArchive Backend" in result.stdout
       assert "0.1.0" in result.stdout
   ```

2. **Run the test**:
   ```bash
   pytest tests/test_cli_version.py -v
   ```

3. **Verify it passes**:
   ```
   tests/test_cli_version.py::test_version_command PASSED
   ```

---

## Step 8: Run All Checks

Before submitting, ensure all checks pass:

```bash
make ci
```

This runs:
1. **Format check**: `ruff format --check .`
2. **Lint**: `ruff check .`
3. **Type check**: `mypy .`
4. **Tests**: `pytest`

**Fix any failures** before proceeding.

Common fixes:
- **Formatting issues**: Run `make format` to auto-fix
- **Linting issues**: Fix manually or run `ruff check --fix .`
- **Type errors**: Add type hints or fix type mismatches
- **Test failures**: Fix the code or update tests

---

## Step 9: Commit Your Changes

1. **Stage your changes**:
   ```bash
   git add src/ha_backend/cli/main.py tests/test_cli_version.py
   ```

2. **Commit with a clear message**:
   ```bash
   git commit -m "feat: add version command to CLI

   - Add 'ha-backend version' command
   - Display version information
   - Add test coverage for version command

   Closes #123"
   ```

   **Commit message conventions**:
   - First line: `type: short description` (50 chars or less)
   - Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`
   - Body: Explain what and why (not how)
   - Footer: Reference issues with `Closes #123`

---

## Step 10: Push and Create Pull Request

1. **Push your branch** to your fork:
   ```bash
   git push origin add-version-command
   ```

2. **Create a pull request** on GitHub:
   - Visit your fork on GitHub
   - Click "Compare & pull request" button
   - Fill in the PR template:
     - **Title**: Clear, concise description
     - **Description**: What changed and why
     - **Testing**: How you verified it works
     - **Checklist**: Complete all items

3. **Example PR description**:
   ```markdown
   ## Summary
   Adds a `--version` command to display backend version information.

   ## Changes
   - Added `version` command to CLI
   - Added test coverage in `tests/test_cli_version.py`

   ## Testing
   - ✅ Manual testing: `ha-backend version` outputs correct version
   - ✅ All CI checks pass
   - ✅ New tests added and passing

   ## Related Issues
   Closes #123
   ```

4. **Wait for CI checks** to run on your PR (takes ~5 minutes)

5. **Address review feedback** if requested

---

## Step 11: Respond to Review Feedback

When maintainers review your PR, they may request changes:

1. **Make the requested changes**:
   ```bash
   # Still on your feature branch
   vim src/ha_backend/cli/main.py
   ```

2. **Commit the changes**:
   ```bash
   git add .
   git commit -m "fix: address review feedback

   - Improve version output formatting
   - Add docstring details"
   ```

3. **Push updates**:
   ```bash
   git push origin add-version-command
   ```

   **The PR will update automatically!**

4. **Reply to review comments** to acknowledge feedback

---

## Step 12: Merge and Celebrate! 🎉

Once approved:

1. A maintainer will merge your PR
2. Your contribution is now part of HealthArchive!
3. Your GitHub profile will show the contribution

**Optional: Clean up your local branches**:
```bash
git checkout main
git pull upstream main
git branch -d add-version-command
```

---

## Tips for Success

### Code Quality
- ✅ Follow existing code style and patterns
- ✅ Add type hints to all functions
- ✅ Write clear docstrings
- ✅ Keep changes focused and small

### Testing
- ✅ Write tests for all new code
- ✅ Ensure tests are deterministic (no flaky tests)
- ✅ Use fixtures for test data
- ✅ Test edge cases and error conditions

### Communication
- ✅ Ask questions if unclear
- ✅ Keep PR descriptions detailed
- ✅ Respond to feedback promptly
- ✅ Be respectful and collaborative

### Common Pitfalls to Avoid
- ❌ Don't commit directly to `main`
- ❌ Don't include unrelated changes in one PR
- ❌ Don't skip writing tests
- ❌ Don't ignore CI failures

---

## Next Steps

After your first contribution:

1. **Tackle more issues**: Graduate to `help wanted` issues
2. **Learn the architecture**: Read the [Architecture Walkthrough](architecture-walkthrough.md)
3. **Improve the docs**: Documentation PRs are always welcome
4. **Review others' PRs**: Great way to learn and help the community

---

## Getting Help

- **Questions about the code?** Ask in the issue thread
- **CI failures?** Check the [Testing Guidelines](../development/testing-guidelines.md)
- **Architecture questions?** Read the [Architecture Guide](../architecture.md)
- **Still stuck?** Open a discussion on GitHub

---

## Resources

- [Development Setup](../development/dev-environment-setup.md)
- [Testing Guidelines](../development/testing-guidelines.md)
- [Architecture Guide](../architecture.md)
- [Documentation Guidelines](../documentation-guidelines.md)

Welcome to the HealthArchive community! 🚀
