#!/usr/bin/env python3
"""
Tests for journal data models, .bib parsing, and export helpers.
"""

import os
import tempfile
import zipfile
from pathlib import Path

# Add source paths for imports
import sys
sys.path.insert(0, str(Path(__file__).parent))

from journal import (
    Entry, BibEntry, VaultStorage, fuzzy_filter, fuzzy_filter_entries,
    parse_bib_lightweight, _find_bib_file, _load_bib_entries,
    parse_yaml_frontmatter, resolve_reference_doc,
    detect_pandoc, detect_libreoffice,
    _generate_lua_filter, _lua_basic_filter,
    _lua_coverpage_filter, _lua_header_filter,
    _postprocess_docx, _REFS_DIR,
)


def test_vault_storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = VaultStorage(Path(tmpdir))

        # Directories created
        assert (Path(tmpdir) / "pdf").is_dir()
        assert (Path(tmpdir) / "docx").is_dir()

        # Create entry
        entry = storage.create_entry("Test Note")
        assert entry.name == "Test Note"
        assert entry.path.exists()
        assert entry.path.suffix == ".md"

        # Save and read
        storage.save_entry(entry, "# Hello\n\nWorld.")
        content = storage.read_entry(entry)
        assert content == "# Hello\n\nWorld."

        # List entries
        entries = storage.list_entries()
        assert len(entries) == 1
        assert entries[0].name == "Test Note"

        # Rename
        renamed = storage.rename_entry(entry, "Renamed Note")
        assert renamed.name == "Renamed Note"
        assert renamed.path.exists()
        assert not entry.path.exists()

        # Read renamed
        content = storage.read_entry(renamed)
        assert content == "# Hello\n\nWorld."

        # Delete
        storage.delete_entry(renamed)
        assert len(storage.list_entries()) == 0

    print("  VaultStorage OK")


def test_entry_dataclass():
    p = Path("/tmp/test.md")
    e = Entry(path=p, name="test", modified=1234567890.0)
    assert e.path == p
    assert e.name == "test"
    assert e.modified == 1234567890.0
    print("  Entry dataclass OK")


def test_bib_entry_dataclass():
    b = BibEntry(citekey="smith2020", author="Smith, John", title="A Study")
    assert b.citekey == "smith2020"
    assert b.author == "Smith, John"
    assert b.title == "A Study"
    print("  BibEntry dataclass OK")


def test_parse_bib_lightweight():
    bib_text = """
@book{fitzgerald1925,
  author = {Fitzgerald, F. Scott},
  title = {The Great Gatsby},
  year = {1925},
  publisher = {Scribner},
}

@article{smith2020,
  author = {Smith, John},
  title = {The Symbolism of the Green Light},
  journal = {American Literature Quarterly},
  year = {2020},
  volume = {45},
}

@misc{web2023,
  title = {Understanding Gatsby},
  year = {2023},
}
"""
    entries = parse_bib_lightweight(bib_text)
    assert len(entries) == 3

    assert entries[0].citekey == "fitzgerald1925"
    assert entries[0].author == "Fitzgerald, F. Scott"
    assert entries[0].title == "The Great Gatsby"

    assert entries[1].citekey == "smith2020"
    assert entries[1].author == "Smith, John"

    # misc with no author
    assert entries[2].citekey == "web2023"
    assert entries[2].author == ""
    assert entries[2].title == "Understanding Gatsby"

    print("  parse_bib_lightweight OK")


def test_find_bib_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = Path(tmpdir)

        # No sources dir
        assert _find_bib_file(vault) is None

        # Empty sources dir
        (vault / "sources").mkdir()
        assert _find_bib_file(vault) is None

        # With a .bib file
        bib = vault / "sources" / "refs.bib"
        bib.write_text("@book{test, author={A}, title={B}}")
        result = _find_bib_file(vault)
        assert result is not None
        assert result.name == "refs.bib"

    print("  _find_bib_file OK")


def test_load_bib_entries():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = Path(tmpdir)
        (vault / "sources").mkdir()
        bib = vault / "sources" / "library.bib"
        bib.write_text('@book{doe2021, author={Doe, Jane}, title={A Book}}')

        entries, path, mtime = _load_bib_entries(vault)
        assert len(entries) == 1
        assert entries[0].citekey == "doe2021"
        assert path is not None
        assert mtime > 0

    print("  _load_bib_entries OK")


def test_fuzzy_filter():
    entries = [
        BibEntry(citekey="fitzgerald1925", author="Fitzgerald, F. Scott",
                 title="The Great Gatsby"),
        BibEntry(citekey="smith2020", author="Smith, John",
                 title="Green Light Symbolism"),
        BibEntry(citekey="hemingway1952", author="Hemingway, Ernest",
                 title="The Old Man and the Sea"),
    ]

    results = fuzzy_filter(entries, "fitzgerald")
    assert len(results) >= 1
    assert results[0].author == "Fitzgerald, F. Scott"

    results = fuzzy_filter(entries, "gatsby")
    assert len(results) >= 1
    assert "Gatsby" in results[0].title

    results = fuzzy_filter(entries, "")
    assert len(results) == 3

    # Search by citekey
    results = fuzzy_filter(entries, "smith2020")
    assert len(results) >= 1
    assert results[0].citekey == "smith2020"

    print("  Fuzzy filter OK")


def test_fuzzy_filter_entries():
    entries = [
        Entry(path=Path("/tmp/essay.md"), name="essay", modified=100.0),
        Entry(path=Path("/tmp/notes.md"), name="notes", modified=200.0),
        Entry(path=Path("/tmp/draft.md"), name="draft", modified=300.0),
    ]

    results = fuzzy_filter_entries(entries, "essay")
    assert len(results) >= 1
    assert results[0].name == "essay"

    results = fuzzy_filter_entries(entries, "")
    assert len(results) == 3

    print("  Fuzzy filter entries OK")


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
        import journal
        orig_refs = journal._REFS_DIR

        # Create a fake refs dir
        fake_refs = Path(tmpdir) / "refs"
        fake_refs.mkdir()
        journal._REFS_DIR = fake_refs

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
            journal._REFS_DIR = orig_refs


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
    print("Testing data models...")
    test_entry_dataclass()
    test_bib_entry_dataclass()
    print("  \u2713 Data model tests passed\n")

    print("Testing vault storage...")
    test_vault_storage()
    print("  \u2713 Storage tests passed\n")

    print("Testing .bib parsing...")
    test_parse_bib_lightweight()
    test_find_bib_file()
    test_load_bib_entries()
    print("  \u2713 Bib parsing tests passed\n")

    print("Testing fuzzy filter...")
    test_fuzzy_filter()
    test_fuzzy_filter_entries()
    print("  \u2713 Fuzzy filter tests passed\n")

    print("Testing YAML frontmatter parsing...")
    test_parse_yaml_frontmatter()
    print("  \u2713 YAML frontmatter tests passed\n")

    print("Testing reference doc resolution...")
    test_resolve_reference_doc()
    print("  \u2713 Reference doc tests passed\n")

    print("Testing Lua filter generation...")
    test_lua_filter_generation()
    print("  \u2713 Lua filter tests passed\n")

    print("Testing DOCX post-processing...")
    test_postprocess_docx()
    print("  \u2713 DOCX post-processing tests passed\n")

    print("Testing tool detection...")
    test_detect_tools()
    print("  \u2713 Tool detection tests passed\n")

    print("=" * 50)
    print("All tests passed!")
    print("=" * 50)
