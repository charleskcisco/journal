#!/usr/bin/env python3
"""
Tests for manuscripts data models and citation formatting.
"""

import json
import os
import tempfile
import zipfile
from pathlib import Path

# Add source paths for imports
import sys
sys.path.insert(0, str(Path(__file__).parent))

from manuscripts import (
    Source, Project, Storage, fuzzy_filter,
    parse_yaml_frontmatter, resolve_reference_doc,
    detect_pandoc, detect_libreoffice,
    _generate_lua_filter, _lua_basic_filter,
    _lua_coverpage_filter, _lua_header_filter,
    _postprocess_docx, _REFS_DIR,
)


def test_chicago_book_footnote():
    """Book footnotes must have comma after author name."""
    book = Source(
        id="1", source_type="book",
        author="Fitzgerald, F. Scott",
        title="The Great Gatsby",
        year="1925", publisher="Scribner", city="New York",
    )
    fn = book.to_chicago_footnote()
    assert fn == "F. Scott Fitzgerald, *The Great Gatsby* (Scribner, 1925)."
    print(f"  Book footnote: {fn}")

    fn_page = book.to_chicago_footnote("42")
    assert ", 42." in fn_page
    print(f"  Book footnote w/ page: {fn_page}")


def test_chicago_article_footnote():
    article = Source(
        id="2", source_type="article",
        author="Smith, John",
        title="The Symbolism of the Green Light",
        year="2020", journal="American Literature Quarterly",
        volume="45", issue="2", pages="112-134",
    )
    fn = article.to_chicago_footnote()
    assert fn.startswith("John Smith, ")
    assert '"The Symbolism of the Green Light,"' in fn
    assert "45, no. 2" in fn
    print(f"  Article footnote: {fn}")


def test_chicago_website_footnote():
    web = Source(
        id="3", source_type="website",
        author="Johnson, Mary",
        title="Understanding Gatsby's America",
        year="2023", site_name="Literary Analysis Hub",
        url="https://example.com/gatsby",
        access_date="January 15, 2024",
    )
    fn = web.to_chicago_footnote()
    assert fn.startswith("Mary Johnson, ")
    assert "accessed January 15, 2024" in fn
    print(f"  Website footnote: {fn}")


def test_chicago_bibliography():
    book = Source(
        id="1", source_type="book",
        author="Fitzgerald, F. Scott",
        title="The Great Gatsby",
        year="1925", publisher="Scribner", city="New York",
    )
    bib = book.to_chicago_bibliography()
    assert bib.startswith("Fitzgerald, F. Scott.")
    print(f"  Book bibliography: {bib}")


def test_citekey():
    book = Source(id="1", source_type="book",
                  author="Fitzgerald, F. Scott", title="X", year="1925")
    assert book.to_citekey() == "fitzgerald1925"

    article = Source(id="2", source_type="article",
                     author="Smith, John", title="X", year="2020")
    assert article.to_citekey() == "smith2020"
    print("  Citekeys OK")


def test_author_formatting():
    s = Source(id="1", source_type="book", author="Fitzgerald, F. Scott",
              title="X", year="2000")
    assert s._author_first() == "F. Scott Fitzgerald"
    assert s._author_last() == "Fitzgerald, F. Scott"

    s2 = Source(id="2", source_type="book", author="John Smith",
               title="X", year="2000")
    assert s2._author_first() == "John Smith"
    assert s2._author_last() == "Smith, John"

    s3 = Source(id="3", source_type="book", author="", title="X", year="2000")
    assert s3._author_first() == ""
    assert s3._author_last() == ""
    print("  Author formatting OK")


def test_storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = Storage(Path(tmpdir))

        project = storage.create_project("Test Essay")
        assert project.name == "Test Essay"
        assert project.id

        project.content = "# My Essay\n\nThis is a test."
        source = Source(
            id="test1", source_type="book",
            author="Test, Author", title="Test Book", year="2024",
        )
        project.add_source(source)
        storage.save_project(project)

        loaded = storage.load_project(project.id)
        assert loaded is not None
        assert loaded.name == "Test Essay"
        assert loaded.content == "# My Essay\n\nThis is a test."
        assert len(loaded.sources) == 1
        assert loaded.sources[0]["author"] == "Test, Author"

        projects = storage.list_projects()
        assert len(projects) == 1

        storage.delete_project(project.id)
        assert len(storage.list_projects()) == 0

    print("  Storage OK")


def test_project_source_management():
    p = Project(id="1", name="Test", created="", modified="")
    s1 = Source(id="s1", source_type="book", author="A", title="B", year="2000")
    s2 = Source(id="s2", source_type="article", author="C", title="D", year="2001")

    p.add_source(s1)
    p.add_source(s2)
    assert len(p.sources) == 2

    sources = p.get_sources()
    assert len(sources) == 2
    assert sources[0].author == "A"

    p.remove_source("s1")
    assert len(p.sources) == 1
    assert p.sources[0]["id"] == "s2"
    print("  Project source management OK")


def test_fuzzy_filter():
    sources = [
        Source(id="1", source_type="book", author="Fitzgerald, F. Scott",
               title="The Great Gatsby", year="1925"),
        Source(id="2", source_type="article", author="Smith, John",
               title="Green Light Symbolism", year="2020"),
        Source(id="3", source_type="book", author="Hemingway, Ernest",
               title="The Old Man and the Sea", year="1952"),
    ]

    results = fuzzy_filter(sources, "fitzgerald")
    assert len(results) >= 1
    assert results[0].author == "Fitzgerald, F. Scott"

    results = fuzzy_filter(sources, "gatsby")
    assert len(results) >= 1
    assert "Gatsby" in results[0].title

    results = fuzzy_filter(sources, "1925")
    assert len(results) >= 1

    results = fuzzy_filter(sources, "")
    assert len(results) == 3

    print("  Fuzzy filter OK")


def test_parse_yaml_frontmatter():
    # Basic extraction
    content = "---\ntitle: My Essay\nauthor: John Smith\ndate: 2025-03-07\n---\n\nBody text."
    yaml = parse_yaml_frontmatter(content)
    assert yaml["title"] == "My Essay"
    assert yaml["author"] == "John Smith"
    assert yaml["date"] == "2025-03-07"
    print("  Basic frontmatter OK")

    # Quoted values
    content2 = '---\ntitle: "My Quoted Title"\nauthor: \'Jane Doe\'\n---\n\nBody.'
    yaml2 = parse_yaml_frontmatter(content2)
    assert yaml2["title"] == "My Quoted Title"
    assert yaml2["author"] == "Jane Doe"
    print("  Quoted values OK")

    # No frontmatter
    yaml3 = parse_yaml_frontmatter("Just some text without frontmatter.")
    assert yaml3 == {}
    print("  No frontmatter OK")

    # Empty frontmatter
    yaml4 = parse_yaml_frontmatter("---\n\n---\n\nBody.")
    assert yaml4 == {}
    print("  Empty frontmatter OK")


def test_resolve_reference_doc():
    with tempfile.TemporaryDirectory() as tmpdir:
        import manuscripts
        orig_refs = manuscripts._REFS_DIR

        # Create a fake refs dir
        fake_refs = Path(tmpdir) / "refs"
        fake_refs.mkdir()
        manuscripts._REFS_DIR = fake_refs

        try:
            # No docs at all
            assert resolve_reference_doc({}) is None
            print("  Missing refs dir OK")

            # Create default
            (fake_refs / "double.docx").write_bytes(b"fake")
            result = resolve_reference_doc({})
            assert result is not None
            assert result.name == "double.docx"
            print("  Default fallback OK")

            # Explicit ref
            (fake_refs / "single.docx").write_bytes(b"fake")
            result = resolve_reference_doc({"spacing": "single"})
            assert result is not None
            assert result.name == "single.docx"
            print("  Explicit spacing OK")

            # Explicit spacing that doesn't exist falls back to default
            result = resolve_reference_doc({"spacing": "nonexistent"})
            assert result is not None
            assert result.name == "double.docx"
            print("  Missing explicit spacing fallback OK")
        finally:
            manuscripts._REFS_DIR = orig_refs


def test_lua_filter_generation():
    # Basic filter
    basic = _lua_basic_filter()
    assert "function Pandoc" in basic
    assert "pageBreakBefore" in basic
    assert "Bibliography" in basic
    assert "w:hanging" in basic
    print("  Basic filter OK")

    # Coverpage filter
    yaml = {"title": "Test", "author": "Smith", "style": "chicago"}
    cover = _lua_coverpage_filter(yaml)
    assert "function Meta" in cover
    assert "function Pandoc" in cover
    assert "pageBreakBefore" in cover
    assert "w:hanging" in cover
    assert '"Test"' in cover or "Test" in cover
    print("  Coverpage filter OK")

    # Header filter
    yaml2 = {"title": "Essay", "author": "Doe", "style": "mla"}
    header = _lua_header_filter(yaml2)
    assert "function Meta" in header
    assert "function Pandoc" in header
    assert "w:hanging" in header
    assert "MLA" in header
    print("  Header filter OK")

    # Dispatcher
    assert _generate_lua_filter({"style": "chicago"}) == _lua_coverpage_filter({})
    assert _generate_lua_filter({"style": "mla"}) == _lua_header_filter({})
    assert _generate_lua_filter({}) == _lua_basic_filter()
    print("  Dispatcher OK")


def test_postprocess_docx():
    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = os.path.join(tmpdir, "test.docx")

        # Create a minimal DOCX zip with a header containing {{LASTNAME}}
        header_xml = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p><w:r><w:t>{{LASTNAME}} </w:t></w:r></w:p>
</w:hdr>"""
        footer_xml = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p><w:r><w:t>Page footer</w:t></w:r></w:p>
</w:ftr>"""
        with zipfile.ZipFile(docx_path, "w") as zf:
            zf.writestr("word/header1.xml", header_xml)
            zf.writestr("word/footer1.xml", footer_xml)
            zf.writestr("word/document.xml", b"<w:document/>")

        # Test coverpage format: strips headers, keeps footers, replaces lastname
        _postprocess_docx(docx_path, {"author": "John Smith", "style": "chicago"})
        with zipfile.ZipFile(docx_path, "r") as zf:
            header = zf.read("word/header1.xml").decode("utf-8")
            footer = zf.read("word/footer1.xml").decode("utf-8")
            # Header should be stripped (empty)
            assert "{{LASTNAME}}" not in header
            assert "Smith" not in header  # stripped, not replaced
            assert "Header" in header  # has the empty header style
            # Footer should be preserved
            assert "Page footer" in footer
        print("  Coverpage postprocess OK")

        # Rebuild for header format test
        with zipfile.ZipFile(docx_path, "w") as zf:
            zf.writestr("word/header1.xml", header_xml)
            zf.writestr("word/footer1.xml", footer_xml)
            zf.writestr("word/document.xml", b"<w:document/>")

        # Test header format: keeps headers (with replacement), strips footers
        _postprocess_docx(docx_path, {"author": "Jane Doe", "style": "mla"})
        with zipfile.ZipFile(docx_path, "r") as zf:
            header = zf.read("word/header1.xml").decode("utf-8")
            footer = zf.read("word/footer1.xml").decode("utf-8")
            # Header should have lastname replaced
            assert "Doe " in header
            assert "{{LASTNAME}}" not in header
            # Footer should be stripped
            assert "Page footer" not in footer
            assert "Footer" in footer  # has the empty footer style
        print("  Header postprocess OK")

        # Rebuild for no-author test
        with zipfile.ZipFile(docx_path, "w") as zf:
            zf.writestr("word/header1.xml", header_xml)
            zf.writestr("word/document.xml", b"<w:document/>")

        _postprocess_docx(docx_path, {"style": "mla"})
        with zipfile.ZipFile(docx_path, "r") as zf:
            header = zf.read("word/header1.xml").decode("utf-8")
            # No author: placeholder removed, not replaced
            assert "{{LASTNAME}}" not in header
        print("  No-author postprocess OK")


def test_detect_tools():
    # These should return str or None, never raise
    pandoc = detect_pandoc()
    assert pandoc is None or isinstance(pandoc, str)
    print(f"  detect_pandoc: {pandoc or '(not found)'}")

    lo = detect_libreoffice()
    assert lo is None or isinstance(lo, str)
    print(f"  detect_libreoffice: {lo or '(not found)'}")


if __name__ == "__main__":
    print("Testing citation formatting...")
    test_chicago_book_footnote()
    test_chicago_article_footnote()
    test_chicago_website_footnote()
    test_chicago_bibliography()
    test_citekey()
    test_author_formatting()
    print("  ✓ All citation tests passed\n")

    print("Testing storage...")
    test_storage()
    print("  ✓ Storage tests passed\n")

    print("Testing project source management...")
    test_project_source_management()
    print("  ✓ Source management tests passed\n")

    print("Testing fuzzy filter...")
    test_fuzzy_filter()
    print("  ✓ Fuzzy filter tests passed\n")

    print("Testing YAML frontmatter parsing...")
    test_parse_yaml_frontmatter()
    print("  ✓ YAML frontmatter tests passed\n")

    print("Testing reference doc resolution...")
    test_resolve_reference_doc()
    print("  ✓ Reference doc tests passed\n")

    print("Testing Lua filter generation...")
    test_lua_filter_generation()
    print("  ✓ Lua filter tests passed\n")

    print("Testing DOCX post-processing...")
    test_postprocess_docx()
    print("  ✓ DOCX post-processing tests passed\n")

    print("Testing tool detection...")
    test_detect_tools()
    print("  ✓ Tool detection tests passed\n")

    print("=" * 50)
    print("All tests passed!")
    print("=" * 50)
