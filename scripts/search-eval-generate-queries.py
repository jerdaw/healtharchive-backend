#!/usr/bin/env python
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ha_backend.db import get_session  # noqa: E402
from ha_backend.models import Snapshot  # noqa: E402


_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.IGNORECASE)


def _strip_diacritics(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _normalize_title(title: str) -> str:
    t = title.strip()
    t = _strip_diacritics(t)
    t = t.replace("\u00a0", " ")
    t = t.lower()

    # Remove extremely common site suffixes that drown out term stats.
    for suffix in (
        " - canada.ca",
        " | canada.ca",
        " - cihr",
        " | cihr",
        " - travel.gc.ca",
        " | travel.gc.ca",
        " - ontario.ca",
        " | ontario.ca",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
            break

    return t


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def build_stopwords() -> set[str]:
    # Small, pragmatic set for search eval query generation.
    # (Not intended to be linguistically perfect.)
    common = {
        # English
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "from",
        "by",
        "at",
        "as",
        "is",
        "are",
        "be",
        "this",
        "that",
        "your",
        "our",
        "about",
        "what",
        "how",
        "when",
        "where",
        "who",
        "why",
        # French
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "de",
        "du",
        "au",
        "aux",
        "et",
        "ou",
        "en",
        "sur",
        "pour",
        "avec",
        "dans",
        "par",
        "ce",
        "cet",
        "cette",
        "ces",
        "que",
        "qui",
        "quoi",
        "comment",
        "quand",
        "ou",
        "nous",
        "vous",
        # Site/noise tokens
        "canada",
        "canadian",
        "gouvernement",
        "government",
        "public",
        "health",
        "services",
        "service",
        "information",
        "resources",
        "resource",
        "program",
        "programs",
        "programme",
        "programmes",
        "report",
        "reports",
        "publication",
        "publications",
        "page",
        "site",
        "news",
        "update",
        "updates",
        "guidance",
        "guidelines",
        "guide",
        "overview",
        "statement",
        "summary",
        "archived",
    }
    return common


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a corpus-derived search eval query list from Snapshot titles.",
    )
    p.add_argument(
        "--out",
        required=True,
        help="Output queries file (one per line; comments allowed).",
    )
    p.add_argument(
        "--max-titles",
        type=int,
        default=50000,
        help="Maximum number of Snapshot titles to process.",
    )
    p.add_argument(
        "--min-token-len",
        type=int,
        default=4,
        help="Minimum token length to include (after normalization).",
    )
    p.add_argument(
        "--top-unigrams",
        type=int,
        default=40,
        help="Number of unigram queries to emit.",
    )
    p.add_argument(
        "--top-bigrams",
        type=int,
        default=30,
        help="Number of bigram queries to emit.",
    )
    p.add_argument(
        "--min-count",
        type=int,
        default=8,
        help="Minimum token/bigram count to consider.",
    )
    p.add_argument(
        "--where-title-ilike",
        default=None,
        help="Optional case-insensitive substring filter to scope generation (debugging).",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    stopwords = build_stopwords()

    unigram_counts: Counter[str] = Counter()
    bigram_counts: Counter[tuple[str, str]] = Counter()

    processed = 0
    with get_session() as session:
        q = session.query(Snapshot.title).filter(Snapshot.title.isnot(None))
        if args.where_title_ilike:
            q = q.filter(Snapshot.title.ilike(f"%{args.where_title_ilike}%"))
        q = q.yield_per(2000)

        for (title,) in q:
            if not title:
                continue
            t = _normalize_title(title)
            tokens = [
                tok
                for tok in _tokenize(t)
                if len(tok) >= args.min_token_len and tok not in stopwords
            ]
            if not tokens:
                continue

            unigram_counts.update(tokens)
            bigram_counts.update(zip(tokens, tokens[1:]))

            processed += 1
            if processed >= args.max_titles:
                break

    def eligible_unigrams() -> list[str]:
        out: list[str] = []
        for tok, count in unigram_counts.most_common():
            if count < args.min_count:
                break
            if tok in stopwords:
                continue
            out.append(tok)
            if len(out) >= args.top_unigrams:
                break
        return out

    def eligible_bigrams() -> list[str]:
        out: list[str] = []
        for (a, b), count in bigram_counts.most_common():
            if count < args.min_count:
                break
            if a in stopwords or b in stopwords:
                continue
            phrase = f"{a} {b}"
            out.append(phrase)
            if len(out) >= args.top_bigrams:
                break
        return out

    unigrams = eligible_unigrams()
    bigrams = eligible_bigrams()

    out_path = args.out
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Auto-generated corpus-derived queries (Snapshot.title)\n")
        f.write("# Regenerate with:\n")
        f.write(
            "#   python scripts/search-eval-generate-queries.py --out <file>\n"
        )
        f.write(f"# processed_titles={processed}\n")
        f.write(f"# min_token_len={args.min_token_len} min_count={args.min_count}\n")
        f.write("\n")
        f.write("# Unigrams\n")
        for tok in unigrams:
            f.write(tok + "\n")
        f.write("\n")
        f.write("# Bigrams\n")
        for phrase in bigrams:
            f.write(phrase + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
