from __future__ import annotations

from sqlalchemy import func, literal_column
from sqlalchemy.sql.elements import ColumnElement

# For now we use the 'simple' text search config to avoid incorrect stemming for
# bilingual content. We can later switch based on Snapshot.language ('english'
# vs 'french') once we have a robust approach.
TS_CONFIG = literal_column("'simple'")
WEIGHT_A = literal_column("'A'")
WEIGHT_B = literal_column("'B'")
WEIGHT_C = literal_column("'C'")


def build_search_vector(title: ColumnElement | str | None,
                        snippet: ColumnElement | str | None,
                        url: ColumnElement | str | None) -> ColumnElement:
    """
    Return a weighted Postgres tsvector expression suitable for Snapshot.search_vector.

    This function is safe to call even when the inputs are plain Python strings;
    it returns a SQLAlchemy expression that Postgres can evaluate.
    """
    vector_title = func.setweight(
        func.to_tsvector(TS_CONFIG, func.coalesce(title, "")),
        WEIGHT_A,
    )
    vector_snippet = func.setweight(
        func.to_tsvector(TS_CONFIG, func.coalesce(snippet, "")),
        WEIGHT_B,
    )
    vector_url = func.setweight(
        func.to_tsvector(TS_CONFIG, func.coalesce(url, "")),
        WEIGHT_C,
    )
    return vector_title.op("||")(vector_snippet.op("||")(vector_url))


__all__ = ["TS_CONFIG", "build_search_vector"]
