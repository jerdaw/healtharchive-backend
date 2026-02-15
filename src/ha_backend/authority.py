from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, inspect
from sqlalchemy.orm import Session

from ha_backend.models import PageSignal, Snapshot, SnapshotOutlink

logger = logging.getLogger("healtharchive.authority")


@dataclass(frozen=True)
class _GraphSignals:
    inlink_count: dict[str, int]
    outlink_count: dict[str, int]
    pagerank_scaled: dict[str, float]


def _compute_pagerank_scaled(
    *,
    nodes: list[str],
    adjacency: dict[int, set[int]],
    damping: float = 0.85,
    max_iter: int = 40,
    tol: float = 1e-8,
) -> dict[str, float]:
    """
    Compute a scaled PageRank where mean(rank) ~= 1.0 across all nodes.
    """
    n = len(nodes)
    if n == 0:
        return {}

    out_adj: list[list[int]] = [[] for _ in range(n)]
    out_deg: list[int] = [0 for _ in range(n)]

    for from_idx, to_idxs in adjacency.items():
        if not (0 <= from_idx < n):
            continue
        tos = [t for t in to_idxs if 0 <= t < n and t != from_idx]
        tos = sorted(set(tos))
        out_adj[from_idx] = tos
        out_deg[from_idx] = len(tos)

    rank = [1.0 / n for _ in range(n)]
    base = (1.0 - damping) / n

    for _ in range(max_iter):
        new_rank = [base for _ in range(n)]

        dangling_sum = 0.0
        for i in range(n):
            if out_deg[i] == 0:
                dangling_sum += rank[i]

        dangling_contrib = damping * dangling_sum / n
        if dangling_contrib:
            for j in range(n):
                new_rank[j] += dangling_contrib

        for i in range(n):
            deg = out_deg[i]
            if deg <= 0:
                continue
            share = damping * rank[i] / deg
            for j in out_adj[i]:
                new_rank[j] += share

        diff = 0.0
        for i in range(n):
            diff += abs(new_rank[i] - rank[i])
        rank = new_rank
        if diff < tol:
            break

    return {nodes[i]: rank[i] * n for i in range(n)}


def _compute_graph_signals(session: Session) -> _GraphSignals:
    """
    Build a page-group link graph (distinct edges) and compute:
    - inlink_count: number of distinct linking groups per target
    - outlink_count: number of distinct targets per group
    - pagerank (scaled)
    """
    from_group = func.coalesce(Snapshot.normalized_url_group, Snapshot.url)
    edge_query = (
        session.query(
            from_group.label("from_group"),
            SnapshotOutlink.to_normalized_url_group.label("to_group"),
        )
        .join(Snapshot, Snapshot.id == SnapshotOutlink.snapshot_id)
        .filter(SnapshotOutlink.to_normalized_url_group != from_group)
        .distinct()
    )

    node_index: dict[str, int] = {}
    nodes_list: list[str] = []
    adjacency: dict[int, set[int]] = {}

    def get_idx(node: str) -> int:
        existing = node_index.get(node)
        if existing is not None:
            return existing
        idx = len(nodes_list)
        node_index[node] = idx
        nodes_list.append(node)
        return idx

    for from_g, to_g in edge_query.yield_per(50000):
        if not from_g or not to_g:
            continue
        from_s = str(from_g)
        to_s = str(to_g)
        if from_s == to_s:
            continue
        from_idx = get_idx(from_s)
        to_idx = get_idx(to_s)
        adjacency.setdefault(from_idx, set()).add(to_idx)

    pagerank_scaled = _compute_pagerank_scaled(nodes=nodes_list, adjacency=adjacency)

    inlink_arr: list[int] = [0 for _ in range(len(nodes_list))]
    outlink_arr: list[int] = [0 for _ in range(len(nodes_list))]
    for from_idx, tos in adjacency.items():
        outlink_arr[from_idx] = len(tos)
        for to_idx in tos:
            if 0 <= to_idx < len(inlink_arr):
                inlink_arr[to_idx] += 1

    inlink_count: dict[str, int] = {nodes_list[i]: inlink_arr[i] for i in range(len(nodes_list))}
    outlink_count: dict[str, int] = {nodes_list[i]: outlink_arr[i] for i in range(len(nodes_list))}

    return _GraphSignals(
        inlink_count=inlink_count,
        outlink_count=outlink_count,
        pagerank_scaled=pagerank_scaled,
    )


def recompute_page_signals(
    session: Session,
    *,
    groups: Sequence[str] | None = None,
) -> int:
    """
    Recompute PageSignal link signals.

    Fields:
    - inlink_count: distinct linking page groups
    - outlink_count: distinct outgoing page groups (if present)
    - pagerank: scaled PageRank (mean ~= 1) (if present)

    If groups is None, rebuilds the entire table (deletes existing rows and
    recomputes pagerank).
    If groups is provided, only updates those groups (and removes rows that no
    longer have any inlinks; also considers outlinks if outlink_count exists).

    Returns the number of PageSignal rows inserted/updated/deleted.
    """
    inspector = inspect(session.get_bind())
    try:
        ps_cols = {c.get("name") for c in inspector.get_columns("page_signals")}
    except Exception:
        ps_cols = {"normalized_url_group", "inlink_count"}

    has_outlink_count = "outlink_count" in ps_cols
    has_pagerank = "pagerank" in ps_cols

    normalized_groups = None
    if groups is not None:
        normalized_groups = sorted({g for g in groups if g})
        if not normalized_groups:
            return 0

    if normalized_groups is None:
        signals = _compute_graph_signals(session)

        deleted = session.query(PageSignal).delete(synchronize_session=False) or 0
        inserted = 0

        rows: list[dict[str, object]] = []
        for group, pr in signals.pagerank_scaled.items():
            mapping: dict[str, object] = {
                "normalized_url_group": group,
                "inlink_count": int(signals.inlink_count.get(group, 0) or 0),
            }
            if has_outlink_count:
                mapping["outlink_count"] = int(signals.outlink_count.get(group, 0) or 0)
            if has_pagerank:
                mapping["pagerank"] = float(pr or 0.0)
            rows.append(mapping)

        if rows:
            session.bulk_insert_mappings(PageSignal.__mapper__, rows)
            inserted = len(rows)

        return int(deleted) + inserted

    from_group = func.coalesce(Snapshot.normalized_url_group, Snapshot.url)

    inlink_query = (
        session.query(
            SnapshotOutlink.to_normalized_url_group.label("group"),
            func.count(func.distinct(from_group)).label("inlinks"),
        )
        .join(Snapshot, Snapshot.id == SnapshotOutlink.snapshot_id)
        .filter(SnapshotOutlink.to_normalized_url_group != from_group)
        .filter(SnapshotOutlink.to_normalized_url_group.in_(normalized_groups))
        .group_by(SnapshotOutlink.to_normalized_url_group)
    )
    inlink_counts = {group: int(inlinks or 0) for group, inlinks in inlink_query.all()}

    outlink_counts: dict[str, int] = {}
    if has_outlink_count:
        outlink_query = (
            session.query(
                from_group.label("group"),
                func.count(func.distinct(SnapshotOutlink.to_normalized_url_group)).label(
                    "outlinks"
                ),
            )
            .join(Snapshot, Snapshot.id == SnapshotOutlink.snapshot_id)
            .filter(SnapshotOutlink.to_normalized_url_group != from_group)
            .filter(from_group.in_(normalized_groups))
            .group_by(from_group)
        )
        outlink_counts = {group: int(outlinks or 0) for group, outlinks in outlink_query.all()}

    existing = (
        session.query(PageSignal)
        .filter(PageSignal.normalized_url_group.in_(normalized_groups))
        .all()
    )
    existing_by_group = {row.normalized_url_group: row for row in existing}

    touched = 0
    for group in normalized_groups:
        inlinks = inlink_counts.get(group, 0)
        outlinks = outlink_counts.get(group, 0) if has_outlink_count else 0
        existing_row = existing_by_group.get(group)

        should_delete = inlinks <= 0 if not has_outlink_count else (inlinks <= 0 and outlinks <= 0)
        if should_delete:
            if existing_row is not None:
                session.delete(existing_row)
                touched += 1
            continue

        if existing_row is None:
            page_signal = PageSignal(
                normalized_url_group=group,
                inlink_count=inlinks,
            )
            if has_outlink_count:
                page_signal.outlink_count = outlinks
            if has_pagerank:
                page_signal.pagerank = 1.0
            session.add(page_signal)
            touched += 1
            continue

        changed = False
        if existing_row.inlink_count != inlinks:
            existing_row.inlink_count = inlinks
            changed = True
        if has_outlink_count and getattr(existing_row, "outlink_count", None) != outlinks:
            existing_row.outlink_count = outlinks
            changed = True
        if changed:
            touched += 1

    return touched


__all__ = ["recompute_page_signals"]
