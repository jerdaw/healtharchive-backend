#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit, urlunsplit


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _iter_result_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results = payload.get("results", [])
    if not isinstance(results, list):
        return []
    out: list[dict[str, Any]] = []
    for item in results:
        if isinstance(item, dict):
            out.append(item)
    return out


_DROP_QUERY_KEYS_EXACT = {
    "wbdisable",
    "gclid",
    "fbclid",
}


def _canonicalize_url(url: str) -> str:
    """
    Make URLs comparable across captures.

    - Drops known tracking params and wbdisable.
    - Drops fragments.
    - Keeps other query params (sorted).
    """
    try:
        parts = urlsplit(url)
    except Exception:
        return url

    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept: list[tuple[str, str]] = []
    for k, v in query_pairs:
        lk = k.lower()
        if lk in _DROP_QUERY_KEYS_EXACT:
            continue
        if lk.startswith("utm_"):
            continue
        kept.append((k, v))
    kept.sort()
    new_query = "&".join([f"{k}={v}" for k, v in kept]) if kept else ""

    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, ""))


def _item_key(item: dict[str, Any], *, key_field: str) -> str:
    if key_field in item:
        val = item.get(key_field)
        if val is None:
            return ""
        return str(val)
    for fallback in ("originalUrl", "rawSnapshotUrl", "id"):
        if fallback in item:
            val = item.get(fallback)
            if val is None:
                continue
            return str(val)
    return ""


def _item_label(item: dict[str, Any]) -> str:
    title = item.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    url = item.get("originalUrl")
    if isinstance(url, str) and url.strip():
        return url.strip()
    return str(item.get("id", ""))


@dataclass(frozen=True)
class DiffRow:
    key: str
    label: str
    pos_a: int | None
    pos_b: int | None


def _pos_map(keys: Iterable[str]) -> dict[str, int]:
    return {k: i + 1 for i, k in enumerate(keys) if k}


def _render_section(title: str, rows: list[DiffRow], *, max_rows: int) -> str:
    if not rows:
        return f"{title}: (none)"
    out = [f"{title}:"]
    for row in rows[:max_rows]:
        a = "-" if row.pos_a is None else str(row.pos_a)
        b = "-" if row.pos_b is None else str(row.pos_b)
        out.append(f"  {a:>3} → {b:<3}  {row.label}")
    if len(rows) > max_rows:
        out.append(f"  … ({len(rows) - max_rows} more)")
    return "\n".join(out)


def _diff_lists(
    *,
    a_items: list[dict[str, Any]],
    b_items: list[dict[str, Any]],
    top_n: int,
    key_field: str,
    normalize_urls: bool,
) -> tuple[list[DiffRow], list[DiffRow], list[DiffRow]]:
    def build_keys(items: list[dict[str, Any]]) -> tuple[list[str], dict[str, str]]:
        keys: list[str] = []
        labels: dict[str, str] = {}
        for item in items[:top_n]:
            k = _item_key(item, key_field=key_field)
            if not k:
                continue
            if normalize_urls and key_field == "originalUrl":
                k = _canonicalize_url(k)
            keys.append(k)
            labels.setdefault(k, _item_label(item))
        return keys, labels

    a_keys, a_labels = build_keys(a_items)
    b_keys, b_labels = build_keys(b_items)

    pos_a = _pos_map(a_keys)
    pos_b = _pos_map(b_keys)
    all_labels = {**b_labels, **a_labels}

    overlap = set(pos_a).intersection(pos_b)
    moved = sorted(
        [
            DiffRow(
                key=k,
                label=all_labels.get(k, k),
                pos_a=pos_a.get(k),
                pos_b=pos_b.get(k),
            )
            for k in overlap
            if pos_a.get(k) != pos_b.get(k)
        ],
        key=lambda r: (abs((r.pos_a or 0) - (r.pos_b or 0)), r.pos_a or 0),
        reverse=True,
    )

    only_a = [
        DiffRow(
            key=k,
            label=all_labels.get(k, k),
            pos_a=pos_a.get(k),
            pos_b=None,
        )
        for k in a_keys
        if k not in pos_b
    ]
    only_b = [
        DiffRow(
            key=k,
            label=all_labels.get(k, k),
            pos_a=None,
            pos_b=pos_b.get(k),
        )
        for k in b_keys
        if k not in pos_a
    ]

    return moved, only_a, only_b


def _capture_files(dir_path: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in sorted(dir_path.glob("*.json")):
        if p.name.startswith("queries."):
            continue
        out[p.name] = p
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diff /api/search capture directories (top-N changes per query/view)."
    )
    parser.add_argument("--a", required=True, help="Path to capture dir A (e.g. v1).")
    parser.add_argument("--b", required=True, help="Path to capture dir B (e.g. v2).")
    parser.add_argument("--top", type=int, default=20, help="Top N results to compare.")
    parser.add_argument(
        "--key",
        default="originalUrl",
        choices=["originalUrl", "id", "rawSnapshotUrl"],
        help="Result field to compare across captures.",
    )
    parser.add_argument(
        "--no-normalize-urls",
        action="store_true",
        help="Disable URL canonicalization when --key=originalUrl.",
    )
    parser.add_argument(
        "--show",
        type=int,
        default=10,
        help="Max rows to print per section.",
    )
    args = parser.parse_args()

    dir_a = Path(os.path.expanduser(args.a)).resolve()
    dir_b = Path(os.path.expanduser(args.b)).resolve()
    if not dir_a.is_dir():
        raise SystemExit(f"Not a directory: {dir_a}")
    if not dir_b.is_dir():
        raise SystemExit(f"Not a directory: {dir_b}")

    files_a = _capture_files(dir_a)
    files_b = _capture_files(dir_b)
    names = sorted(set(files_a).union(files_b))
    if not names:
        print("No capture JSON files found.")
        return 2

    normalize_urls = not args.no_normalize_urls
    print(f"A: {dir_a}")
    print(f"B: {dir_b}")
    print(f"Comparing: top={args.top} key={args.key} normalize_urls={normalize_urls}")
    print()

    for name in names:
        path_a = files_a.get(name)
        path_b = files_b.get(name)
        if path_a is None or path_b is None:
            missing = "A" if path_a is None else "B"
            print(f"== {name} ==")
            print(f"Missing in {missing}; skipping.")
            print()
            continue

        payload_a = _load_json(path_a)
        payload_b = _load_json(path_b)
        items_a = _iter_result_items(payload_a)
        items_b = _iter_result_items(payload_b)
        count_a = len(items_a)
        count_b = len(items_b)
        top_a = min(args.top, count_a)
        top_b = min(args.top, count_b)
        top_common = min(top_a, top_b)
        moved, only_a, only_b = _diff_lists(
            a_items=items_a,
            b_items=items_b,
            top_n=args.top,
            key_field=args.key,
            normalize_urls=normalize_urls,
        )

        def make_key_set(items: list[dict[str, Any]]) -> set[str]:
            out: set[str] = set()
            for item in items[: args.top]:
                k = _item_key(item, key_field=args.key)
                if normalize_urls and args.key == "originalUrl":
                    k = _canonicalize_url(k)
                if k:
                    out.add(k)
            return out

        set_a = make_key_set(items_a)
        set_b = make_key_set(items_b)
        set_a.discard("")
        set_b.discard("")
        overlap_count = len(set_a.intersection(set_b))

        print(f"== {name} ==")
        print(f"Results: A={count_a} (top {top_a}), B={count_b} (top {top_b})")
        if top_common == 0:
            print("Overlap: (n/a; no results)")
        else:
            print(f"Overlap in top {top_common}: {overlap_count}/{top_common}")
        print(_render_section("Moved (within top-N)", moved, max_rows=args.show))
        print(_render_section("Only in A (dropped in B)", only_a, max_rows=args.show))
        print(_render_section("Only in B (new in B)", only_b, max_rows=args.show))
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
