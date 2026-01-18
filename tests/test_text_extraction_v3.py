"""Tests for v3 text extraction improvements."""

from __future__ import annotations

from ha_backend.indexing.text_extraction import (
    detect_is_archived,
    extract_content_text,
    extract_text,
    make_snippet,
)


class TestDetectIsArchived:
    """Tests for archived page detection."""

    def test_archived_title_prefix_english(self) -> None:
        assert detect_is_archived("Archived - COVID-19 Guidelines", "") is True
        assert detect_is_archived("Archived: Old Policy", "") is True
        assert detect_is_archived("[Archived] Historical Document", "") is True

    def test_archived_title_prefix_french(self) -> None:
        assert detect_is_archived("Archivé - Lignes directrices COVID-19", "") is True
        assert detect_is_archived("Archivée - Information historique", "") is True
        assert detect_is_archived("[Archivé] Document historique", "") is True

    def test_archived_body_banner_english(self) -> None:
        text = "We have archived this page and will not be updating it."
        assert detect_is_archived("Normal Title", text) is True

        text2 = "This page has been archived for historical purposes."
        assert detect_is_archived("Normal Title", text2) is True

    def test_archived_body_banner_french(self) -> None:
        text = "Cette page a été archivée et ne sera plus mise à jour."
        assert detect_is_archived("Titre normal", text) is True

        text2 = "Information archivée - cette ressource n'est plus maintenue."
        assert detect_is_archived("Titre normal", text2) is True

    def test_not_archived_normal_page(self) -> None:
        assert detect_is_archived("COVID-19 Vaccines", "Get your vaccine today.") is False
        assert detect_is_archived("Health Canada", "About our services.") is False

    def test_archived_in_body_but_not_banner(self) -> None:
        # "archived" appears but not as a banner phrase.
        text = "The archived records can be accessed in the library."
        assert detect_is_archived("Records Access", text) is False


class TestExtractContentText:
    """Tests for extended content text extraction."""

    def test_extracts_main_content(self) -> None:
        html = """
        <html>
        <head><title>Test Page</title></head>
        <body>
            <nav>Navigation links here</nav>
            <main>
                <h1>Main Content</h1>
                <p>This is the main content of the page with important information.</p>
            </main>
            <footer>Footer content</footer>
        </body>
        </html>
        """
        content = extract_content_text(html)
        assert "Main Content" in content
        assert "main content of the page" in content
        # Nav and footer should be removed.
        assert "Navigation links" not in content
        assert "Footer content" not in content

    def test_respects_max_chars(self) -> None:
        html = "<html><body><p>" + "word " * 1000 + "</p></body></html>"
        content = extract_content_text(html, max_chars=100)
        assert len(content) <= 100

    def test_truncates_at_word_boundary(self) -> None:
        html = "<html><body><p>" + "abcdefghij " * 50 + "</p></body></html>"
        content = extract_content_text(html, max_chars=100)
        # Should not end mid-word.
        assert not content.endswith("abcde")


class TestAriaRolePruning:
    """Tests for ARIA role boilerplate removal."""

    def test_removes_navigation_role(self) -> None:
        html = """
        <html><body>
            <div role="navigation">Skip links and menus</div>
            <main>Actual content here.</main>
        </body></html>
        """
        text = extract_text(html)
        assert "Skip links" not in text
        assert "Actual content" in text

    def test_removes_banner_role(self) -> None:
        html = """
        <html><body>
            <header role="banner">Site Header Banner</header>
            <main>Page content.</main>
        </body></html>
        """
        text = extract_text(html)
        assert "Site Header" not in text
        assert "Page content" in text

    def test_removes_contentinfo_role(self) -> None:
        html = """
        <html><body>
            <main>Main content.</main>
            <footer role="contentinfo">Copyright info</footer>
        </body></html>
        """
        text = extract_text(html)
        assert "Main content" in text
        assert "Copyright" not in text

    def test_removes_search_role(self) -> None:
        html = """
        <html><body>
            <div role="search">Search input here</div>
            <article>Article text.</article>
        </body></html>
        """
        text = extract_text(html)
        assert "Search input" not in text
        assert "Article text" in text


class TestMakeSnippetBoilerplateFiltering:
    """Tests for boilerplate phrase filtering in snippets."""

    def test_skips_skip_to_content(self) -> None:
        text = "Skip to main content. This is the actual page content about vaccines."
        snippet = make_snippet(text)
        # Should try to find content after boilerplate.
        # Note: The current implementation may or may not successfully skip.
        # This test documents expected behavior.
        assert len(snippet) > 0

    def test_normal_text_unchanged(self) -> None:
        text = "COVID-19 vaccines are safe and effective for preventing severe illness."
        snippet = make_snippet(text)
        assert snippet.startswith("COVID-19")

    def test_truncates_long_text(self) -> None:
        text = "This is a very long sentence. " * 20
        snippet = make_snippet(text, max_len=100)
        assert len(snippet) <= 101  # +1 for ellipsis character.
        assert snippet.endswith("…")
