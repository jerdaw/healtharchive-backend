#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

MODEL_FILE = "src/ha_backend/models.py"
MIGRATION_PREFIX = "alembic/versions/"
DEFAULT_EXCEPTIONS_FILE = ".github/migration-guard-exceptions.txt"
MAX_EXCEPTION_DAYS = 30
_COLUMN_CALL_NAMES = {"mapped_column", "Column"}
_SCHEMA_DDL_PATTERNS = (
    re.compile(r"\\bALTER\\s+TABLE\\b", re.IGNORECASE),
    re.compile(r"\\bCREATE\\s+TABLE\\b", re.IGNORECASE),
    re.compile(r"\\bDROP\\s+TABLE\\b", re.IGNORECASE),
    re.compile(r"\\bADD\\s+COLUMN\\b", re.IGNORECASE),
    re.compile(r"\\bDROP\\s+COLUMN\\b", re.IGNORECASE),
    re.compile(r"\\bCREATE\\s+INDEX\\b", re.IGNORECASE),
    re.compile(r"\\bDROP\\s+INDEX\\b", re.IGNORECASE),
    re.compile(r"\\bop\\.(add_column|drop_column|alter_column|create_table|drop_table)\\b"),
    re.compile(r"\\bop\\.(create_index|drop_index|create_unique_constraint)\\b"),
)


@dataclass(frozen=True)
class GuardDecision:
    ok: bool
    schema_signals: tuple[str, ...]
    migration_files: tuple[str, ...]
    matched_exception_rules: tuple[str, ...]
    expired_exception_rules: tuple[str, ...]


@dataclass(frozen=True)
class ExceptionRule:
    path_glob: str
    signal_pattern_raw: str
    signal_pattern: re.Pattern[str]
    expires_on: date
    reason: str
    source: str


def _run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(  # nosec: B603 - trusted local git invocation
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr or 'unknown git error'}")
    return result.stdout


def _changed_files(repo_root: Path, *, base_ref: str, head_ref: str) -> list[str]:
    out = _run_git(repo_root, "diff", "--name-only", f"{base_ref}...{head_ref}")
    return [line.strip() for line in out.splitlines() if line.strip()]


def _read_file_at_ref(repo_root: Path, *, ref: str, path: str) -> str | None:
    result = subprocess.run(  # nosec: B603 - trusted local git invocation
        ["git", "show", f"{ref}:{path}"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _is_column_call(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _COLUMN_CALL_NAMES
    if isinstance(func, ast.Attribute):
        return func.attr in _COLUMN_CALL_NAMES
    return False


def _model_columns_from_source(source: str | None) -> dict[str, set[str]]:
    if source is None:
        return {}
    try:
        module = ast.parse(source)
    except SyntaxError:
        return {}

    model_columns: dict[str, set[str]] = {}
    for node in module.body:
        if not isinstance(node, ast.ClassDef):
            continue

        columns: set[str] = set()
        for stmt in node.body:
            target_name: str | None = None
            value_node: ast.AST | None = None

            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                target_name = stmt.target.id
                value_node = stmt.value
            elif (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                target_name = stmt.targets[0].id
                value_node = stmt.value

            if target_name and _is_column_call(value_node):
                columns.add(target_name)

        if columns:
            model_columns[node.name] = columns

    return model_columns


def _model_schema_signals(base_source: str | None, head_source: str | None) -> list[str]:
    if base_source is None or head_source is None:
        return [f"{MODEL_FILE} changed and model column comparison could not be completed"]

    base_columns = _model_columns_from_source(base_source)
    head_columns = _model_columns_from_source(head_source)

    # If parsing fails entirely, keep the guard conservative.
    if not base_columns and not head_columns:
        return [f"{MODEL_FILE} changed and model parsing failed for both refs"]

    signals: list[str] = []
    all_model_names = sorted(set(base_columns) | set(head_columns))
    for model_name in all_model_names:
        base = base_columns.get(model_name, set())
        head = head_columns.get(model_name, set())
        added = sorted(head - base)
        removed = sorted(base - head)
        if not added and not removed:
            continue

        parts: list[str] = []
        if added:
            parts.append(f"added={added}")
        if removed:
            parts.append(f"removed={removed}")
        signals.append(f"{model_name}: {'; '.join(parts)}")

    return signals


def _added_lines(repo_root: Path, *, base_ref: str, head_ref: str, pathspec: str) -> list[str]:
    patch = _run_git(repo_root, "diff", "--unified=0", f"{base_ref}...{head_ref}", "--", pathspec)
    lines: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        content = line[1:].strip()
        if content:
            lines.append(content)
    return lines


def _schema_ddl_hits(added_lines: Sequence[str]) -> list[str]:
    hits: list[str] = []
    seen: set[str] = set()
    for line in added_lines:
        for pattern in _SCHEMA_DDL_PATTERNS:
            if pattern.search(line):
                if line not in seen:
                    hits.append(line)
                    seen.add(line)
                break
    return hits


def _load_exception_rules(
    repo_root: Path,
    *,
    exceptions_file: str,
    today: date,
) -> tuple[list[ExceptionRule], list[str]]:
    path = (repo_root / exceptions_file).resolve()
    if not path.exists():
        return ([], [])

    active_rules: list[ExceptionRule] = []
    expired_rules: list[str] = []
    max_allowed_expiry = today + timedelta(days=MAX_EXCEPTION_DAYS)

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        parts = [part.strip() for part in stripped.split("|", maxsplit=3)]
        if len(parts) != 4:
            raise RuntimeError(
                f"{exceptions_file}:{lineno}: expected 4 pipe-delimited fields "
                "(path_glob|signal_regex|expires_yyyy-mm-dd|reason)"
            )
        path_glob, signal_regex_raw, expires_raw, reason = parts
        if not path_glob or not signal_regex_raw or not expires_raw or not reason:
            raise RuntimeError(
                f"{exceptions_file}:{lineno}: fields must be non-empty "
                "(path_glob|signal_regex|expires_yyyy-mm-dd|reason)"
            )

        try:
            signal_pattern = re.compile(signal_regex_raw)
        except re.error as exc:
            raise RuntimeError(
                f"{exceptions_file}:{lineno}: invalid signal regex {signal_regex_raw!r}: {exc}"
            ) from exc

        try:
            expires_on = date.fromisoformat(expires_raw)
        except ValueError as exc:
            raise RuntimeError(
                f"{exceptions_file}:{lineno}: invalid expires date {expires_raw!r}; "
                "expected YYYY-MM-DD"
            ) from exc

        if expires_on > max_allowed_expiry:
            raise RuntimeError(
                f"{exceptions_file}:{lineno}: expires date {expires_on.isoformat()} is too far in "
                f"the future (max {max_allowed_expiry.isoformat()}); keep exceptions temporary"
            )

        source = f"{exceptions_file}:{lineno}"
        if expires_on < today:
            expired_rules.append(
                f"{source} ({path_glob} | {signal_regex_raw}) expired on {expires_on.isoformat()}"
            )
            continue

        active_rules.append(
            ExceptionRule(
                path_glob=path_glob,
                signal_pattern_raw=signal_regex_raw,
                signal_pattern=signal_pattern,
                expires_on=expires_on,
                reason=reason,
                source=source,
            )
        )

    return (active_rules, expired_rules)


def _match_exception_rule(
    *,
    signal: str,
    changed_files: Sequence[str],
    exception_rules: Sequence[ExceptionRule],
) -> ExceptionRule | None:
    for rule in exception_rules:
        if not rule.signal_pattern.search(signal):
            continue
        if not any(fnmatch.fnmatch(path, rule.path_glob) for path in changed_files):
            continue
        return rule
    return None


def evaluate_changed_content(
    changed_files: Sequence[str],
    *,
    base_models_source: str | None,
    head_models_source: str | None,
    schema_ddl_hits: Sequence[str],
    exception_rules: Sequence[ExceptionRule] = (),
    expired_exception_rules: Sequence[str] = (),
) -> GuardDecision:
    migration_files = sorted(
        f for f in changed_files if f.startswith(MIGRATION_PREFIX) and f.endswith(".py")
    )

    schema_signals: list[str] = []
    if MODEL_FILE in changed_files:
        schema_signals.extend(_model_schema_signals(base_models_source, head_models_source))

    for hit in schema_ddl_hits:
        schema_signals.append(f"app DDL detected: {hit}")

    if not schema_signals:
        return GuardDecision(
            ok=True,
            schema_signals=(),
            migration_files=tuple(migration_files),
            matched_exception_rules=(),
            expired_exception_rules=tuple(expired_exception_rules),
        )

    if migration_files:
        return GuardDecision(
            ok=True,
            schema_signals=tuple(schema_signals),
            migration_files=tuple(migration_files),
            matched_exception_rules=(),
            expired_exception_rules=tuple(expired_exception_rules),
        )

    unmatched_signals: list[str] = []
    matched_rule_summaries: list[str] = []
    for signal in schema_signals:
        matched = _match_exception_rule(
            signal=signal,
            changed_files=changed_files,
            exception_rules=exception_rules,
        )
        if matched is None:
            unmatched_signals.append(signal)
            continue
        matched_rule_summaries.append(
            f"{matched.source} path={matched.path_glob!r} "
            f"signal={matched.signal_pattern_raw!r} "
            f"expires={matched.expires_on.isoformat()} reason={matched.reason}"
        )

    if not unmatched_signals:
        return GuardDecision(
            ok=True,
            schema_signals=tuple(schema_signals),
            migration_files=(),
            matched_exception_rules=tuple(sorted(set(matched_rule_summaries))),
            expired_exception_rules=tuple(expired_exception_rules),
        )

    return GuardDecision(
        ok=False,
        schema_signals=tuple(unmatched_signals),
        migration_files=(),
        matched_exception_rules=tuple(sorted(set(matched_rule_summaries))),
        expired_exception_rules=tuple(expired_exception_rules),
    )


def run_guard(
    repo_root: Path,
    *,
    base_ref: str,
    head_ref: str,
    exceptions_file: str,
) -> GuardDecision:
    changed_files = _changed_files(repo_root, base_ref=base_ref, head_ref=head_ref)

    base_models_source: str | None = None
    head_models_source: str | None = None
    if MODEL_FILE in changed_files:
        base_models_source = _read_file_at_ref(repo_root, ref=base_ref, path=MODEL_FILE)
        head_models_source = _read_file_at_ref(repo_root, ref=head_ref, path=MODEL_FILE)

    added_app_lines = _added_lines(repo_root, base_ref=base_ref, head_ref=head_ref, pathspec="src")
    ddl_hits = _schema_ddl_hits(added_app_lines)
    today = date.today()
    exception_rules, expired_exception_rules = _load_exception_rules(
        repo_root,
        exceptions_file=exceptions_file,
        today=today,
    )

    return evaluate_changed_content(
        changed_files,
        base_models_source=base_models_source,
        head_models_source=head_models_source,
        schema_ddl_hits=ddl_hits,
        exception_rules=exception_rules,
        expired_exception_rules=expired_exception_rules,
    )


def _print_decision(decision: GuardDecision, *, base_ref: str, head_ref: str) -> None:
    if decision.ok:
        if decision.schema_signals and decision.migration_files:
            print("OK: schema-sensitive changes detected with Alembic migration files present.")
            print(f"Compared refs: {base_ref}...{head_ref}")
            print("Migrations:")
            for path in decision.migration_files:
                print(f"  - {path}")
            return

        if decision.matched_exception_rules:
            print(
                "OK: schema-sensitive changes matched temporary migration-guard exception rule(s)."
            )
            print(f"Compared refs: {base_ref}...{head_ref}")
            print("Matched exception rules:")
            for rule in decision.matched_exception_rules:
                print(f"  - {rule}")
            if decision.expired_exception_rules:
                print("Expired exception rules ignored:")
                for expired in decision.expired_exception_rules:
                    print(f"  - {expired}")
            return

        print("OK: no migration-required schema changes detected.")
        print(f"Compared refs: {base_ref}...{head_ref}")
        if decision.expired_exception_rules:
            print("Expired exception rules ignored:")
            for expired in decision.expired_exception_rules:
                print(f"  - {expired}")
        return

    print("FAIL: schema-sensitive changes detected without an Alembic migration.", file=sys.stderr)
    print(f"Compared refs: {base_ref}...{head_ref}", file=sys.stderr)
    if decision.matched_exception_rules:
        print(
            "Some signals matched exception rules, but at least one unmatched signal remains.",
            file=sys.stderr,
        )
        print("Matched exception rules:", file=sys.stderr)
        for rule in decision.matched_exception_rules:
            print(f"  - {rule}", file=sys.stderr)
    if decision.expired_exception_rules:
        print("Expired exception rules ignored:", file=sys.stderr)
        for expired in decision.expired_exception_rules:
            print(f"  - {expired}", file=sys.stderr)
    print("Signals:", file=sys.stderr)
    for signal in decision.schema_signals:
        print(f"  - {signal}", file=sys.stderr)
    print("Remediation:", file=sys.stderr)
    print("  1. Add a migration file under alembic/versions/.", file=sys.stderr)
    print(
        "  2. Validate locally: alembic upgrade head && pytest -q tests/test_ci_schema_parity.py",
        file=sys.stderr,
    )
    print(
        f"  3. If this is a verified false positive, add a temporary rule to "
        f"{DEFAULT_EXCEPTIONS_FILE} with a short expiry (<= {MAX_EXCEPTION_DAYS} days).",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Guardrail for PRs: if schema-sensitive backend changes are present, "
            "require an Alembic migration file."
        )
    )
    parser.add_argument(
        "--base-ref",
        default="origin/main",
        help="Base git ref for diff (default: origin/main).",
    )
    parser.add_argument(
        "--head-ref",
        default="HEAD",
        help="Head git ref for diff (default: HEAD).",
    )
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Path to repository root (default: inferred from script path).",
    )
    parser.add_argument(
        "--exceptions-file",
        default=DEFAULT_EXCEPTIONS_FILE,
        help=(
            "Repo-relative path to migration guard exception rules "
            f"(default: {DEFAULT_EXCEPTIONS_FILE})."
        ),
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()

    try:
        decision = run_guard(
            repo_root,
            base_ref=args.base_ref,
            head_ref=args.head_ref,
            exceptions_file=args.exceptions_file,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    _print_decision(decision, base_ref=args.base_ref, head_ref=args.head_ref)
    return 0 if decision.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
