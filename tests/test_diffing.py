"""
Tests for ha_backend.diffing module.

Verifies:
- HTML normalization (whitespace, stripped tags, attribute sorting)
- Noise filtering (banners, navigation)
- Diff algorithm correctness
- Bilingual content handling
"""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup, Tag

from ha_backend.diffing import (
    DiffDocument,
    _extract_sections,
    _is_noise_tag,
    _normalize_whitespace,
    _strip_noise,
    compute_diff,
    normalize_html_for_diff,
    normalize_html_for_diff_full_page,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "html"


def test_normalize_whitespace_collapses_multiple_spaces():
    assert _normalize_whitespace("  hello   world  ") == "hello world"
    assert _normalize_whitespace("line\nbreak") == "line break"
    assert _normalize_whitespace("\t\ttabbed\t\t") == "tabbed"


def test_normalize_html_for_diff_attribute_sorting():
    html_a = '<p class="b" id="a">Text</p>'
    html_b = '<p id="a" class="b">Text</p>'
    doc_a = normalize_html_for_diff(html_a)
    doc_b = normalize_html_for_diff(html_b)
    # The BeautifulSoup parser and our normalization should yield identical text/lines
    assert doc_a.text == doc_b.text
    assert doc_a.lines == doc_b.lines


def test_normalize_html_for_diff_case_normalization():
    html_a = "<DIV><P>Text</P></DIV>"
    html_b = "<div><p>Text</p></div>"
    doc_a = normalize_html_for_diff(html_a)
    doc_b = normalize_html_for_diff(html_b)
    assert doc_a.text == doc_b.text
    assert doc_a.lines == doc_b.lines


def test_normalize_whitespace_preserves_newlines_in_pre():
    html = "<main><pre>line1\n  line2</pre></main>"
    doc = normalize_html_for_diff(html)
    assert "line1" in doc.text
    assert "  line2" in doc.text
    assert len(doc.lines) >= 2
    assert "line1" in doc.lines[0]
    assert "  line2" in doc.lines[1]


def test_is_noise_tag_detects_keywords():
    soup = BeautifulSoup('<div class="cookie-banner"></div>', "html.parser")
    tag = soup.find("div")
    assert isinstance(tag, Tag)
    assert _is_noise_tag(tag) is True

    soup = BeautifulSoup('<div id="subscribe-popup"></div>', "html.parser")
    tag = soup.find("div")
    assert isinstance(tag, Tag)
    assert _is_noise_tag(tag) is True

    soup = BeautifulSoup('<div class="content"></div>', "html.parser")
    tag = soup.find("div")
    assert isinstance(tag, Tag)
    assert _is_noise_tag(tag) is False


def test_strip_noise_removes_chrome_and_noise():
    html = """
    <html>
        <header>Header</header>
        <nav>Nav</nav>
        <div class="cookie-notice">Cookies</div>
        <main>
            <p>Content</p>
            <script>console.log('noise')</script>
        </main>
        <footer>Footer</footer>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    _strip_noise(soup, strip_chrome=True)

    assert soup.find("header") is None
    assert soup.find("nav") is None
    assert soup.find("footer") is None
    assert soup.find("script") is None
    assert soup.select_one(".cookie-notice") is None

    main = soup.find("main")
    assert main is not None
    assert main.get_text(strip=True) == "Content"


def test_strip_noise_preserves_chrome_when_flag_false():
    html = "<html><header>Header</header><main>Content</main></html>"
    soup = BeautifulSoup(html, "html.parser")
    _strip_noise(soup, strip_chrome=False)

    assert soup.find("header") is not None
    assert soup.find("main") is not None


def test_extract_sections_identifies_headings():
    html = """
    <main>
        <h1>Main Title</h1>
        <p>Intro text.</p>
        <h2>Section 1</h2>
        <p>Details 1.</p>
        <h3>Subsection 1.1</h3>
        <p>More details.</p>
        <h2>Section 2</h2>
        <div>Just div text</div>
    </main>
    """
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main")
    assert isinstance(main, Tag)

    sections = _extract_sections(main)

    assert len(sections) == 4
    assert sections[0] == ("Main Title", "Intro text.")
    assert sections[1] == ("Section 1", "Details 1.")
    assert sections[2] == ("Subsection 1.1", "More details.")
    assert sections[3] == ("Section 2", "Just div text")


def test_extract_sections_fallback_to_content():
    html = "<main><p>Just text, no headings.</p></main>"
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main")
    assert isinstance(main, Tag)

    sections = _extract_sections(main)
    assert len(sections) == 1
    assert sections[0] == ("Content", "Just text, no headings.")


def test_normalize_html_for_diff_simple_page():
    fixture_path = FIXTURES_DIR / "simple_page.html"
    html = fixture_path.read_text()

    doc = normalize_html_for_diff(html)
    assert isinstance(doc, DiffDocument)
    assert "Main Heading" in doc.text
    assert "Item 1" in doc.text
    # Should contain standardized whitespace
    assert "Item 1" in doc.lines
    assert len(doc.sections) >= 2


def test_normalize_html_for_diff_canada_ca():
    fixture_path = FIXTURES_DIR / "canada_ca_page.html"
    html = fixture_path.read_text()

    doc = normalize_html_for_diff(html)

    # Verify noise stripping (header/footer removal in standard diff)
    assert "Government of Canada" not in doc.text
    assert "About this site" not in doc.text
    assert "Public Health Agency" in doc.text
    assert "Important health information" in doc.text


def test_normalize_html_for_diff_full_page_preserves_chrome():
    fixture_path = FIXTURES_DIR / "canada_ca_page.html"
    html = fixture_path.read_text()

    doc = normalize_html_for_diff_full_page(html)

    # Should KEEP chrome
    assert "Government of Canada" in doc.text
    assert "About this site" in doc.text
    assert "Public Health Agency" in doc.text


def test_compute_diff_identical_documents():
    doc = DiffDocument(text="abc", lines=["a", "b", "c"], sections=[("H", "abc")])
    res = compute_diff(doc, doc)

    assert res.change_ratio == 0.0
    assert res.added_lines == 0
    assert res.removed_lines == 0
    assert res.diff_truncated is False
    assert res.diff_html == ""


def test_compute_diff_completely_different():
    doc_a = DiffDocument(text="abc", lines=["a", "b", "c"], sections=[])
    doc_b = DiffDocument(text="def", lines=["d", "e", "f"], sections=[])

    res = compute_diff(doc_a, doc_b)

    # Ratio might not be exactly 1.0 depending on difflib internals for very short seqs,
    # but for distinct chars it should be high.
    assert res.change_ratio > 0.9
    assert res.added_lines == 3
    assert res.removed_lines == 3
    assert "ha-diff-del" in res.diff_html
    assert "ha-diff-add" in res.diff_html


def test_compute_diff_small_change():
    doc_a = DiffDocument(text="a b c", lines=["line1", "line2", "line3"], sections=[])
    doc_b = DiffDocument(text="a b c mod", lines=["line1", "line2 modified", "line3"], sections=[])

    res = compute_diff(doc_a, doc_b)

    assert 0 < res.change_ratio < 1.0
    assert res.added_lines == 1
    assert res.removed_lines == 1
    assert "line2" in res.diff_html


def test_diff_truncation_logic():
    # Create large docs
    lines = [f"line {i}" for i in range(1000)]
    lines_b = [f"line {i} mod" for i in range(1000)]

    doc_a = DiffDocument(text="", lines=lines, sections=[])
    doc_b = DiffDocument(text="", lines=lines_b, sections=[])

    res = compute_diff(doc_a, doc_b)
    assert res.diff_truncated is True
    # Implementation defines MAX_DIFF_LINES = 400
    # The html generation joins the lines, so we can't count lines easily in string,
    # but we can rely on flag.


def test_diff_bilingual_content(html_factory):
    # Testing that UTF-8/accents don't crash anything
    html_en = html_factory(content="<p>Hello world</p>")
    html_fr = html_factory(content="<p>Bonjour le monde</p>")

    doc_en = normalize_html_for_diff(html_en)
    doc_fr = normalize_html_for_diff(html_fr)

    res = compute_diff(doc_en, doc_fr)
    assert res.change_ratio > 0
    assert "Bonjour le monde" in res.diff_html


def test_render_diff_line_formatting():
    from ha_backend.diffing import _render_diff_line

    # Context
    line = "  context"
    rendered = _render_diff_line(line)
    assert "ha-diff-context" in rendered
    assert "<code>  context</code>" in rendered

    # Addition
    line = "+added"
    rendered = _render_diff_line(line)
    assert "ha-diff-add" in rendered
    assert "<code>+added</code>" in rendered

    # Deletion
    line = "-removed"
    rendered = _render_diff_line(line)
    assert "ha-diff-del" in rendered
    assert "<code>-removed</code>" in rendered

    # Hunk
    line = "@@ -1,1 +1,1 @@"
    rendered = _render_diff_line(line)
    assert "ha-diff-hunk" in rendered

    # Scaping
    line = "+<script>"
    rendered = _render_diff_line(line)
    assert "&lt;script&gt;" in rendered


def test_normalize_html_no_main_element():
    html = "<body><p>Direct body content</p></body>"
    doc = normalize_html_for_diff(html)
    assert doc.text == "Direct body content"
    assert doc.lines == ["Direct body content"]


def test_normalize_html_empty_input():
    # Very minimal or empty HTML
    assert normalize_html_for_diff("").text == ""
    assert normalize_html_for_diff("<html></html>").text == ""
    assert normalize_html_for_diff("   ").text == ""


def test_banner_stripping_from_fixtures():
    # English banner
    fixture_en = (FIXTURES_DIR / "archived_page_en.html").read_text()
    doc_en = normalize_html_for_diff(fixture_en)
    assert "This page has been archived" not in doc_en.text
    assert "Archived Content" in doc_en.text

    # French banner
    fixture_fr = (FIXTURES_DIR / "archived_page_fr.html").read_text()
    doc_fr = normalize_html_for_diff(fixture_fr)
    assert "Cette page a été archivée" not in doc_fr.text
    assert "Contenu Archivé" in doc_fr.text


def test_extract_sections_nested_headings():
    html = """
    <main>
        <h1>Level 1</h1>
        <div>
            <h2>Level 2</h2>
            <p>Content 2</p>
        </div>
        <p>Content 1</p>
    </main>
    """
    soup = BeautifulSoup(html, "html.parser")
    sections = _extract_sections(soup.find("main"))
    # Currently _extract_sections uses descendants, so it should find nested ones.
    # Level 1 content will be "Content 1" because Level 2 starts a new section.
    # Actually "Content 1" comes AFTER Level 2 in the DOM traversal?
    # Yes, descendants are depth-first.

    # h1 Level 1 -> title="Level 1"
    # h2 Level 2 -> ends Level 1, title="Level 2"
    # Content 2 -> Level 2 content
    # Content 1 -> Level 2 content (since it's after h2)

    assert len(sections) == 2
    assert sections[0][0] == "Level 1"
    assert sections[1][0] == "Level 2"
    assert "Content 2" in sections[1][1]
    assert "Content 1" in sections[1][1]
