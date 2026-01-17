#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
_INLINE_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
_REF_DEF_RE = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)(?:\s+.*)?$")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


@dataclass(frozen=True)
class Finding:
    path: str


def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_mkdocs_config(repo_root: Path) -> dict[str, Any]:
    mkdocs_path = repo_root / "mkdocs.yml"
    if not mkdocs_path.exists():
        raise FileNotFoundError(f"Missing mkdocs.yml at {mkdocs_path}")

    try:
        import yaml  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "PyYAML is required to parse mkdocs.yml. Install dev dependencies (mkdocs)."
        ) from e

    class _IgnoreTagsLoader(yaml.SafeLoader):  # type: ignore[name-defined]
        pass

    def _construct_undefined(loader: Any, node: Any) -> Any:
        # MkDocs configs sometimes contain PyYAML-specific tags (e.g. `!!python/name:...`)
        # that are irrelevant for this script. Treat unknown-tag nodes as plain YAML.
        if hasattr(yaml, "ScalarNode") and isinstance(node, yaml.ScalarNode):  # type: ignore[attr-defined]
            return loader.construct_scalar(node)
        if hasattr(yaml, "SequenceNode") and isinstance(node, yaml.SequenceNode):  # type: ignore[attr-defined]
            return loader.construct_sequence(node)
        if hasattr(yaml, "MappingNode") and isinstance(node, yaml.MappingNode):  # type: ignore[attr-defined]
            return loader.construct_mapping(node)
        return None

    _IgnoreTagsLoader.add_constructor(None, _construct_undefined)  # type: ignore[arg-type]

    return yaml.load(mkdocs_path.read_text(), Loader=_IgnoreTagsLoader)


def _git_ls_files_md(repo_root: Path) -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "*.md", "*.mdx"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
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


def _iter_non_fenced_lines(text: str) -> Iterator[str]:
    in_fence = False
    fence_char = ""
    fence_len = 0

    for line in text.splitlines():
        match = _FENCE_RE.match(line)
        if not in_fence:
            if match:
                marker = match.group(1)
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
                continue
            yield line
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

    value = value.split()[0].strip().strip("\"'")
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


def _should_follow_code_token(token: str) -> bool:
    if (
        _is_external_or_anchor(token)
        or _is_workspace_reference(token)
        or token.startswith(("./", "../"))
    ):
        return True

    # Only treat likely doc paths as coverage edges (avoid `make`, `npm`, etc).
    if token.endswith((".md", ".mdx")):
        return True

    return False


def _resolve_doc_ref(*, docs_root: Path, current: Path, ref: str) -> Path | None:
    if _is_external_or_anchor(ref) or _is_workspace_reference(ref):
        return None

    ref = ref.strip()
    if not ref:
        return None

    candidates: list[Path] = []
    if ref.startswith("./"):
        candidates.append((docs_root / ref[2:]).resolve())
    elif ref.startswith("../"):
        candidates.append((current.parent / ref).resolve())
    else:
        candidates.append((current.parent / ref).resolve())
        candidates.append((docs_root / ref).resolve())

    resolved: list[Path] = []
    for cand in candidates:
        resolved.append(cand)
        if cand.suffix == "":
            resolved.append(cand.with_suffix(".md"))
            resolved.append(cand.with_suffix(".mdx"))
            resolved.append(cand / "README.md")
            resolved.append(cand / "index.md")

    for cand in resolved:
        try:
            cand_rel = cand.resolve().relative_to(docs_root)
        except Exception:
            continue
        candidate_path = docs_root / cand_rel
        if candidate_path.exists() and candidate_path.is_file():
            if candidate_path.suffix.lower() in (".md", ".mdx"):
                return candidate_path

    return None


def _extract_refs(*, docs_root: Path, current: Path, text: str) -> Iterator[Path]:
    for line in _iter_non_fenced_lines(text):
        for match in _INLINE_LINK_RE.finditer(line):
            target = _normalize_link_target(match.group(1))
            if not target:
                continue
            resolved = _resolve_doc_ref(docs_root=docs_root, current=current, ref=target)
            if resolved:
                yield resolved

        ref_def = _REF_DEF_RE.match(line)
        if ref_def:
            target = _normalize_link_target(ref_def.group(1))
            if target:
                resolved = _resolve_doc_ref(docs_root=docs_root, current=current, ref=target)
                if resolved:
                    yield resolved

        for match in _INLINE_CODE_RE.finditer(line):
            token = _normalize_code_token(match.group(1))
            if not token or not _should_follow_code_token(token):
                continue
            resolved = _resolve_doc_ref(docs_root=docs_root, current=current, ref=token)
            if resolved:
                yield resolved


def _iter_nav_paths(nav: Any) -> Iterator[str]:
    if nav is None:
        return
    if isinstance(nav, str):
        yield nav
        return
    if isinstance(nav, list):
        for item in nav:
            yield from _iter_nav_paths(item)
        return
    if isinstance(nav, dict):
        for value in nav.values():
            yield from _iter_nav_paths(value)
        return


def _compute_reachable(*, docs_root: Path, seed_rel_paths: Iterable[str]) -> set[Path]:
    reachable: set[Path] = set()
    queue: deque[Path] = deque()

    for rel in seed_rel_paths:
        rel = rel.strip()
        if not rel:
            continue
        seed = _resolve_doc_ref(docs_root=docs_root, current=docs_root / "README.md", ref=rel)
        if seed and seed not in reachable:
            reachable.add(seed)
            queue.append(seed)

    while queue:
        current = queue.popleft()
        try:
            text = current.read_text(encoding="utf-8")
        except Exception:
            continue
        for ref in _extract_refs(docs_root=docs_root, current=current, text=text):
            if ref not in reachable:
                reachable.add(ref)
                queue.append(ref)

    return reachable


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that markdown files under docs/ are reachable from the MkDocs nav via links/code references."
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Repository root (defaults to parent of scripts/).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any unreachable docs are found.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else _repo_root_from_script()
    cfg = _load_mkdocs_config(repo_root)
    docs_dir = cfg.get("docs_dir") or "docs"
    docs_root = (repo_root / docs_dir).resolve()

    all_md = [
        p
        for p in _git_ls_files_md(repo_root)
        if p.is_file()
        and p.suffix.lower() in (".md", ".mdx")
        and (docs_root in p.parents or p == docs_root)
    ]

    seed_rel_paths = list(_iter_nav_paths(cfg.get("nav")))
    reachable = _compute_reachable(docs_root=docs_root, seed_rel_paths=seed_rel_paths)

    all_docs_md = sorted({p.resolve() for p in all_md})
    unreachable = [p for p in all_docs_md if p not in reachable]

    if unreachable:
        rels = [str(p.relative_to(docs_root)) for p in unreachable]
        sys.stderr.write(
            "Unreachable docs (not in MkDocs nav and not linked from any reachable page):\n"
        )
        for rel in rels:
            sys.stderr.write(f"- {rel}\n")

    if args.strict and unreachable:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
