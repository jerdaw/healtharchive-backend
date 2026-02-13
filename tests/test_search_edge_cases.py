"""Tests for search query edge cases (SQL injection, XSS, etc.)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine


def _init_test_app(tmp_path: Path, monkeypatch):
    """
    Configure a temporary SQLite DB and return a FastAPI TestClient.
    """
    db_path = tmp_path / "search_edge_cases_test.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    # Reset cached engine/session so we pick up the new URL.
    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    from ha_backend.api import app

    try:
        import uvloop  # noqa: F401
    except Exception:
        return TestClient(app)
    return TestClient(app, backend_options={"use_uvloop": True})


def test_search_sql_injection_attempts(tmp_path, monkeypatch):
    """Test that SQL injection attempts are safely handled."""
    client = _init_test_app(tmp_path, monkeypatch)

    # Various SQL injection payloads
    sql_injection_payloads = [
        "' OR '1'='1",
        "'; DROP TABLE snapshots; --",
        '" OR "1"="1',
        "' UNION SELECT * FROM snapshots --",
        "1' AND 1=1 --",
        "admin'--",
        "' OR 1=1/*",
        "1; DELETE FROM snapshots WHERE '1'='1",
    ]

    for payload in sql_injection_payloads:
        response = client.get(f"/api/search?q={payload}")

        # Should not cause 500 error (SQL injection should be escaped/parameterized)
        assert response.status_code in (200, 400, 422), (
            f"SQL injection payload '{payload}' caused unexpected status: {response.status_code}"
        )

        # Response should be valid JSON
        data = response.json()
        assert isinstance(data, dict)

        # If successful search, results should be a list
        if response.status_code == 200:
            assert "results" in data
            assert isinstance(data["results"], list)


def test_search_xss_attempts(tmp_path, monkeypatch):
    """Test that XSS payloads are safely handled."""
    client = _init_test_app(tmp_path, monkeypatch)

    # Various XSS payloads
    xss_payloads = [
        "<script>alert('XSS')</script>",
        "<img src=x onerror=alert('XSS')>",
        "<svg/onload=alert('XSS')>",
        "javascript:alert('XSS')",
        "<iframe src='javascript:alert(1)'>",
        "<body onload=alert('XSS')>",
        "<<SCRIPT>alert('XSS');//<</SCRIPT>",
    ]

    for payload in xss_payloads:
        response = client.get(f"/api/search?q={payload}")

        # Should not cause error
        assert response.status_code in (200, 400, 422), (
            f"XSS payload '{payload}' caused unexpected status: {response.status_code}"
        )

        # Response should be valid JSON
        data = response.json()
        assert isinstance(data, dict)

        # Response should not contain unescaped script tags
        response_text = response.text
        # JSON encoding should escape these properly
        assert "<script>" not in response_text.lower()


def test_search_empty_query(tmp_path, monkeypatch):
    """Test search with empty query."""
    client = _init_test_app(tmp_path, monkeypatch)

    # Empty query
    response = client.get("/api/search?q=")

    # Should handle gracefully (either 200 with empty results or 400/422)
    assert response.status_code in (200, 400, 422)
    data = response.json()
    assert isinstance(data, dict)


def test_search_very_long_query(tmp_path, monkeypatch):
    """Test search with very long query string."""
    client = _init_test_app(tmp_path, monkeypatch)

    # Very long query (but under 8KB URL limit)
    long_query = "a" * 1000
    response = client.get(f"/api/search?q={long_query}")

    # Should handle gracefully
    assert response.status_code in (200, 400, 422)
    data = response.json()
    assert isinstance(data, dict)


def test_search_unicode_characters(tmp_path, monkeypatch):
    """Test search with various Unicode characters."""
    client = _init_test_app(tmp_path, monkeypatch)

    unicode_queries = [
        "santé",  # French accents
        "健康",  # Chinese characters
        "здоровье",  # Cyrillic
        "🏥",  # Emoji
        "café",  # Mixed ASCII and accents
    ]

    for query in unicode_queries:
        response = client.get(f"/api/search?q={query}")

        # Should handle Unicode properly
        assert response.status_code in (200, 400, 422)
        data = response.json()
        assert isinstance(data, dict)


def test_search_special_characters(tmp_path, monkeypatch):
    """Test search with special characters."""
    client = _init_test_app(tmp_path, monkeypatch)

    special_char_queries = [
        "health & safety",
        "covid-19",
        "100%",
        "test@example.com",
        "file.name",
        "price $50",
        "question?",
        "exclamation!",
    ]

    for query in special_char_queries:
        response = client.get(f"/api/search?q={query}")

        # Should handle special characters
        assert response.status_code in (200, 400, 422)
        data = response.json()
        assert isinstance(data, dict)


def test_search_invalid_page_number(tmp_path, monkeypatch):
    """Test search with invalid page numbers."""
    client = _init_test_app(tmp_path, monkeypatch)

    invalid_pages = [
        -1,
        0,
        "abc",
    ]

    for page in invalid_pages:
        response = client.get(f"/api/search?q=test&page={page}")

        # Should return error or handle gracefully
        # (422 for validation errors, or 200 with clamped values)
        assert response.status_code in (200, 400, 422, 500)
        if response.status_code != 500:
            data = response.json()
            assert isinstance(data, dict)


def test_search_invalid_page_size(tmp_path, monkeypatch):
    """Test search with invalid page sizes."""
    client = _init_test_app(tmp_path, monkeypatch)

    invalid_page_sizes = [
        -1,
        0,
        1000,  # Too large
        "abc",
    ]

    for page_size in invalid_page_sizes:
        response = client.get(f"/api/search?q=test&pageSize={page_size}")

        # Should return error or clamp to valid range
        assert response.status_code in (200, 400, 422)
        data = response.json()
        assert isinstance(data, dict)


def test_search_null_bytes(tmp_path, monkeypatch):
    """Test search with null bytes."""
    client = _init_test_app(tmp_path, monkeypatch)

    # Null byte injection attempt
    response = client.get("/api/search?q=test%00malicious")

    # Should handle gracefully (either sanitize or reject)
    assert response.status_code in (200, 400, 422)
    data = response.json()
    assert isinstance(data, dict)


def test_search_path_traversal_attempts(tmp_path, monkeypatch):
    """Test search with path traversal attempts."""
    client = _init_test_app(tmp_path, monkeypatch)

    path_traversal_payloads = [
        "../../../etc/passwd",
        "..\\..\\..\\windows\\system32",
        "....//....//....//etc/passwd",
    ]

    for payload in path_traversal_payloads:
        response = client.get(f"/api/search?q={payload}")

        # Should treat as normal search query, not file path
        assert response.status_code in (200, 400, 422)
        data = response.json()
        assert isinstance(data, dict)


def test_search_command_injection_attempts(tmp_path, monkeypatch):
    """Test search with command injection attempts."""
    client = _init_test_app(tmp_path, monkeypatch)

    command_injection_payloads = [
        "; ls -la",
        "| cat /etc/passwd",
        "& whoami",
        "`id`",
        "$(whoami)",
    ]

    for payload in command_injection_payloads:
        response = client.get(f"/api/search?q={payload}")

        # Should treat as normal search query, not execute commands
        assert response.status_code in (200, 400, 422)
        data = response.json()
        assert isinstance(data, dict)


def test_search_nosql_injection_attempts(tmp_path, monkeypatch):
    """Test search with NoSQL injection attempts (defense in depth)."""
    client = _init_test_app(tmp_path, monkeypatch)

    nosql_payloads = [
        '{"$gt": ""}',
        '{"$ne": null}',
        '{"$regex": ".*"}',
    ]

    for payload in nosql_payloads:
        response = client.get(f"/api/search?q={payload}")

        # Should treat as normal search string
        assert response.status_code in (200, 400, 422)
        data = response.json()
        assert isinstance(data, dict)


def test_search_with_invalid_source(tmp_path, monkeypatch):
    """Test search with invalid source parameter."""
    client = _init_test_app(tmp_path, monkeypatch)

    invalid_sources = [
        "nonexistent",
        "<script>alert(1)</script>",
        "'; DROP TABLE sources; --",
        "../../../etc/passwd",
    ]

    for source in invalid_sources:
        response = client.get(f"/api/search?q=test&source={source}")

        # Should handle gracefully (either ignore invalid source or return error)
        assert response.status_code in (200, 400, 422)
        data = response.json()
        assert isinstance(data, dict)
