#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    kind: str  # "link" | "code"
    raw: str
    resolved: str
    reason: str


_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
_INLINE_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
_REF_DEF_RE = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)(?:\s+.*)?$")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def _git_ls_files_md(repo_root: Path) -> list[Path]:
    try:
        proc = subprocess.run(
            ["git", "ls-files", "*.md", "*.mdx"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return sorted(
            p for p in repo_root.rglob("*.md") if ".git" not in p.parts and ".venv" not in p.parts
        )

    paths: list[Path] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        paths.append(repo_root / line)
    return paths


def _iter_non_fenced_lines(text: str) -> Iterator[tuple[int, str]]:
    in_fence = False
    fence_char = ""
    fence_len = 0

    for line_number, line in enumerate(text.splitlines(), start=1):
        match = _FENCE_RE.match(line)
        if not in_fence:
            if match:
                marker = match.group(1)
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
                continue
            yield line_number, line
            continue

        if match and match.group(1)[0] == fence_char and len(match.group(1)) >= fence_len:
            in_fence = False
            fence_char = ""
            fence_len = 0


def _normalize_link_target(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None

    if value.startswith("<") and ">" in value:
        value = value[1 : value.index(">")].strip()

    value = value.split()[0].strip()
    value = value.strip("\"'")
    if not value:
        return None

    value = value.split("#", 1)[0]
    value = value.split("?", 1)[0]
    return value.strip()


def _normalize_code_token(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None

    if " " in value or "\t" in value:
        return None

    # Keep leading `./` or `../` intact; only trim common wrapping punctuation.
    value = value.lstrip("([{\"'")
    value = value.rstrip(".,;:)]}\"'")
    if not value:
        return None

    value = value.split("#", 1)[0]
    value = value.split("?", 1)[0]
    value = value.split(":", 1)[0]  # tolerate `path/to/file.md:123`
    return value.strip()


def _is_external_or_anchor(target: str) -> bool:
    return (
        target.startswith("#")
        or target.startswith("/")
        or target.startswith("//")
        or _SCHEME_RE.match(target) is not None
    )


def _is_workspace_reference(target: str) -> bool:
    return target.startswith("healtharchive-") and "/" in target


def _looks_like_placeholder(target: str) -> bool:
    if "YYYY" in target:
        return True
    if "<" in target or ">" in target:
        return True
    if "*" in target:
        return True
    if "..." in target:
        return True
    return False


def _doc_root_prefixes(repo_root: Path) -> set[str]:
    docs_root = repo_root / "docs"
    if not docs_root.exists():
        return set()
    return {"deployment", "development", "operations", "roadmaps", "decisions"}


def _should_check_code_token(token: str, docs_root: Path | None) -> bool:
    if (
        _is_external_or_anchor(token)
        or _is_workspace_reference(token)
        or _looks_like_placeholder(token)
    ):
        return False

    if re.fullmatch(r"\.[a-z0-9]+", token):
        return False

    if token.startswith("./") or token.startswith("../"):
        return True

    if token.endswith((".md", ".mdx")):
        return True

    repo_prefixes = (
        "docs/",
        "scripts/",
        "src/",
        "tests/",
        ".github/",
        "alembic/",
    )
    if token.startswith(repo_prefixes):
        return True

    if docs_root and token.split("/", 1)[0] in _doc_root_prefixes(docs_root.parent):
        # Non-markdown files under docs-root should still be referenced via repo-root prefixes.
        return False

    return False


def _resolve_target_path(
    *,
    repo_root: Path,
    file_path: Path,
    token: str,
    kind: str,
) -> Path:
    if kind == "link":
        return (file_path.parent / token).resolve()

    if token.startswith("./"):
        return (repo_root / token[2:]).resolve()

    docs_root = repo_root / "docs"
    if token.startswith(("docs/", "scripts/", "src/", "tests/", ".github/", "alembic/")):
        return (repo_root / token).resolve()

    if docs_root.exists() and file_path.is_relative_to(docs_root):
        first = token.split("/", 1)[0]
        if first in _doc_root_prefixes(repo_root):
            return (docs_root / token).resolve()

    return (file_path.parent / token).resolve()


def _is_within_repo(repo_root: Path, path: Path) -> bool:
    try:
        return path.is_relative_to(repo_root)
    except AttributeError:
        # Python 3.8 fallback (not expected in CI)
        return str(path).startswith(str(repo_root))


def _iter_link_targets(line: str) -> Iterator[str]:
    for match in _INLINE_LINK_RE.finditer(line):
        target = _normalize_link_target(match.group(1))
        if target:
            yield target

    match = _REF_DEF_RE.match(line)
    if match:
        target = _normalize_link_target(match.group(1))
        if target:
            yield target


def _iter_code_tokens(line: str) -> Iterator[str]:
    for match in _INLINE_CODE_RE.finditer(line):
        token = _normalize_code_token(match.group(1))
        if token:
            yield token


def check_docs_references(repo_root: Path) -> list[Finding]:
    docs_root = repo_root / "docs"
    md_files = _git_ls_files_md(repo_root)

    findings: list[Finding] = []
    for file_path in md_files:
        if not file_path.exists():
            continue

        rel_file = str(file_path.relative_to(repo_root))
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = file_path.read_text(encoding="utf-8", errors="replace")

        for line_number, line in _iter_non_fenced_lines(text):
            for target in _iter_link_targets(line):
                if _is_external_or_anchor(target) or _is_workspace_reference(target):
                    continue

                resolved = _resolve_target_path(
                    repo_root=repo_root, file_path=file_path, token=target, kind="link"
                )
                if not _is_within_repo(repo_root, resolved):
                    continue
                if resolved.exists():
                    continue

                findings.append(
                    Finding(
                        file=rel_file,
                        line=line_number,
                        kind="link",
                        raw=target,
                        resolved=str(resolved.relative_to(repo_root)),
                        reason="missing path",
                    )
                )

            for token in _iter_code_tokens(line):
                if not _should_check_code_token(token, docs_root if docs_root.exists() else None):
                    continue

                resolved = _resolve_target_path(
                    repo_root=repo_root, file_path=file_path, token=token, kind="code"
                )
                if not _is_within_repo(repo_root, resolved):
                    continue
                if resolved.exists():
                    continue

                if "/" not in token:
                    repo_root_candidate = (repo_root / token).resolve()
                    if repo_root_candidate.exists():
                        continue

                findings.append(
                    Finding(
                        file=rel_file,
                        line=line_number,
                        kind="code",
                        raw=token,
                        resolved=str(resolved.relative_to(repo_root)),
                        reason="missing path",
                    )
                )

    return findings


def _render_findings(findings: Iterable[Finding]) -> str:
    lines: list[str] = []
    for finding in findings:
        lines.append(
            f"{finding.file}:{finding.line}: {finding.kind} reference to '{finding.raw}' -> '{finding.resolved}' ({finding.reason})"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check docs markdown links and file path references."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (defaults to parent of scripts/).",
    )
    args = parser.parse_args()

    repo_root = (args.repo_root or _repo_root_from_script()).resolve()
    findings = check_docs_references(repo_root)
    if findings:
        print(_render_findings(findings))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
