from __future__ import annotations

from typing import Any

from sqlalchemy import func, literal_column
from sqlalchemy.sql.elements import ColumnElement

# For now we use the 'simple' text search config to avoid incorrect stemming for
# bilingual content. We can later switch based on Snapshot.language ('english'
# vs 'french') once we have a robust approach.
TS_CONFIG: ColumnElement[str] = literal_column("'simple'")
WEIGHT_A: ColumnElement[str] = literal_column("'A'")
WEIGHT_B: ColumnElement[str] = literal_column("'B'")
WEIGHT_C: ColumnElement[str] = literal_column("'C'")


def build_search_vector(
    title: Any,
    snippet: Any,
    url: Any,
    *,
    content_text: Any = None,
) -> ColumnElement[Any]:
    """
    Return a weighted Postgres tsvector expression suitable for Snapshot.search_vector.

    This function is safe to call even when the inputs are plain Python strings;
    it returns a SQLAlchemy expression that Postgres can evaluate.

    Args:
        title: Page title (weight A - highest).
        snippet: Short snippet for display (used if content_text is None).
        url: Page URL (weight C - lowest).
        content_text: Optional extended content text (~4KB) for FTS (weight B).
                      If provided, this is used instead of snippet for better recall.
    """
    vector_title = func.setweight(
        func.to_tsvector(TS_CONFIG, func.coalesce(title, "")),
        WEIGHT_A,
    )

    # Use content_text for FTS if provided (v3), otherwise fall back to snippet.
    body_text = content_text if content_text is not None else snippet
    vector_body = func.setweight(
        func.to_tsvector(TS_CONFIG, func.coalesce(body_text, "")),
        WEIGHT_B,
    )

    vector_url = func.setweight(
        func.to_tsvector(TS_CONFIG, func.coalesce(url, "")),
        WEIGHT_C,
    )
    return vector_title.op("||")(vector_body.op("||")(vector_url))


__all__ = ["TS_CONFIG", "build_search_vector"]

