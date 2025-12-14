from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session

from .models import Source


def seed_sources(session: Session) -> int:
    """
    Insert initial Source rows (hc, phac, cihr) if they do not already exist.

    Returns the number of sources created.
    """
    initial_sources: Iterable[tuple[str, str, str, str]] = [
        (
            "hc",
            "Health Canada",
            "https://www.canada.ca/en/health-canada.html",
            "Federal department responsible for helping Canadians maintain and improve their health.",
        ),
        (
            "phac",
            "Public Health Agency of Canada",
            "https://www.canada.ca/en/public-health.html",
            "Agency focused on public health, disease prevention, and health promotion in Canada.",
        ),
        (
            "cihr",
            "Canadian Institutes of Health Research",
            "https://cihr-irsc.gc.ca/",
            "Canada’s federal agency for health research funding, supporting the creation and translation of health knowledge.",
        ),
    ]

    # Ensure any pending Source objects are flushed so we have a complete
    # view of existing codes within this session.
    session.flush()

    existing_codes = {code for (code,) in session.query(Source.code).all()}

    created = 0
    for code, name, base_url, description in initial_sources:
        if code in existing_codes:
            continue
        source = Source(
            code=code,
            name=name,
            base_url=base_url,
            description=description,
            enabled=True,
        )
        session.add(source)
        created += 1

    return created


__all__ = ["seed_sources"]
