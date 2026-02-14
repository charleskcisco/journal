#!/usr/bin/env python3
"""Journal — A micro-journal companion for Obsidian vaults."""

from __future__ import annotations

import asyncio
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from prompt_toolkit import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import (
    ConditionalContainer, DynamicContainer, Float, FloatContainer,
    HSplit, VSplit, Window, WindowAlign,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.lexers import Lexer as PtLexer
from prompt_toolkit.styles import Style as PtStyle
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import Button, Dialog, Label, TextArea

# ════════════════════════════════════════════════════════════════════════
#  Data Models
# ════════════════════════════════════════════════════════════════════════


@dataclass
class Entry:
    """A markdown file in the vault."""
    path: Path          # Full path to the .md file
    name: str           # Filename without .md extension
    modified: float     # os.path.getmtime timestamp


@dataclass
class BibEntry:
    """Minimal .bib entry for search and insertion."""
    citekey: str


# ════════════════════════════════════════════════════════════════════════
#  Storage
# ════════════════════════════════════════════════════════════════════════


class VaultStorage:
    def __init__(self, vault_dir: Path):
        self.vault_dir = vault_dir
        self.pdf_dir = vault_dir / "pdf"
        self.docx_dir = vault_dir / "docx"
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.docx_dir.mkdir(parents=True, exist_ok=True)

    def list_entries(self) -> list[Entry]:
        """List top-level .md files, sorted by modified desc."""
        entries = []
        for p in self.vault_dir.glob("*.md"):
            entries.append(Entry(
                path=p, name=p.stem,
                modified=p.stat().st_mtime,
            ))
        return sorted(entries, key=lambda e: e.modified, reverse=True)

    def read_entry(self, entry: Entry) -> str:
        return entry.path.read_text(encoding="utf-8")

    def save_entry(self, entry: Entry, content: str) -> None:
        entry.path.write_text(content, encoding="utf-8")

    def create_entry(self, name: str) -> Entry:
        path = self.vault_dir / f"{name}.md"
        path.touch()
        return Entry(path=path, name=name, modified=path.stat().st_mtime)

    def rename_entry(self, entry: Entry, new_name: str) -> Entry:
        new_path = self.vault_dir / f"{new_name}.md"
        entry.path.rename(new_path)
        return Entry(path=new_path, name=new_name, modified=new_path.stat().st_mtime)

    def delete_entry(self, entry: Entry) -> None:
        entry.path.unlink()


# ════════════════════════════════════════════════════════════════════════
#  PDF Export Helpers
# ════════════════════════════════════════════════════════════════════════

_REFS_DIR = Path(__file__).resolve().parent / "refs"
_DEFAULT_SPACING = "double"


def parse_yaml_frontmatter(content: str) -> dict:
    """Extract key:value pairs from YAML frontmatter fenced by ---."""
    m = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not m:
        return {}
    yaml: dict[str, str] = {}
    for line in m.group(1).split("\n"):
        idx = line.find(":")
        if idx > 0:
            key = line[:idx].strip()
            val = line[idx + 1 :].strip()
            # Strip surrounding quotes
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            yaml[key] = val
    return yaml


def resolve_reference_doc(yaml: dict) -> Optional[Path]:
    """Return path to the reference .docx for pandoc, or None."""
    if not _REFS_DIR.is_dir():
        return None
    # Explicit spacing: field
    if yaml.get("spacing"):
        p = _REFS_DIR / (yaml["spacing"] + ".docx")
        if p.exists():
            return p
    # Default
    p = _REFS_DIR / (_DEFAULT_SPACING + ".docx")
    if p.exists():
        return p
    # Any .docx
    for p in sorted(_REFS_DIR.glob("*.docx")):
        return p
    return None


def detect_pandoc() -> Optional[str]:
    """Find the pandoc binary."""
    found = shutil.which("pandoc")
    if found:
        return found
    for p in [
        "/usr/local/bin/pandoc",
        "/opt/homebrew/bin/pandoc",
        "/usr/bin/pandoc",
        "/snap/bin/pandoc",
    ]:
        if os.path.isfile(p):
            return p
    return None


def detect_libreoffice() -> Optional[str]:
    """Find the LibreOffice/soffice binary."""
    if sys.platform == "darwin":
        candidates = [
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
            "/usr/local/bin/soffice",
        ]
    else:
        candidates = [
            "/usr/bin/libreoffice",
            "/usr/bin/soffice",
            "/usr/local/bin/libreoffice",
            "/snap/bin/libreoffice",
        ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return shutil.which("libreoffice") or shutil.which("soffice")


# ── Lua filter generators ─────────────────────────────────────────────


def _lua_bib_entry_xml() -> str:
    """Lua snippet: convert a Para block to a hanging-indent OpenXML raw block.

    Walks each inline element so that Emph (italic) and Strong (bold)
    formatting survive into the OpenXML output – fixing the bug where
    ``pandoc.utils.stringify`` stripped all markup from bibliography entries.
    """
    return """
local function escape_xml(s)
  s = s:gsub("&", "&amp;")
  s = s:gsub("<", "&lt;")
  s = s:gsub(">", "&gt;")
  return s
end

local function inlines_to_openxml(inlines)
  local runs = {}
  for _, inl in ipairs(inlines) do
    if inl.t == "Emph" then
      local txt = escape_xml(pandoc.utils.stringify(inl))
      table.insert(runs, string.format(
        '<w:r><w:rPr><w:i/><w:iCs/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r>', txt))
    elseif inl.t == "Strong" then
      local txt = escape_xml(pandoc.utils.stringify(inl))
      table.insert(runs, string.format(
        '<w:r><w:rPr><w:b/><w:bCs/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r>', txt))
    elseif inl.t == "Str" then
      table.insert(runs, string.format(
        '<w:r><w:t xml:space="preserve">%s</w:t></w:r>', escape_xml(inl.text)))
    elseif inl.t == "Space" then
      table.insert(runs, '<w:r><w:t xml:space="preserve"> </w:t></w:r>')
    elseif inl.t == "SoftBreak" or inl.t == "LineBreak" then
      table.insert(runs, '<w:r><w:t xml:space="preserve"> </w:t></w:r>')
    elseif inl.t == "Link" then
      local txt = escape_xml(pandoc.utils.stringify(inl))
      table.insert(runs, string.format(
        '<w:r><w:t xml:space="preserve">%s</w:t></w:r>', txt))
    else
      local txt = escape_xml(pandoc.utils.stringify(inl))
      if txt ~= "" then
        table.insert(runs, string.format(
          '<w:r><w:t xml:space="preserve">%s</w:t></w:r>', txt))
      end
    end
  end
  return table.concat(runs)
end

local function bib_entry_block(block)
  local runs_xml = inlines_to_openxml(block.content)
  return pandoc.RawBlock('openxml', string.format([[
<w:p>
  <w:pPr>
    <w:spacing w:after="0" w:line="480" w:lineRule="auto"/>
    <w:ind w:left="720" w:hanging="720"/>
  </w:pPr>
  %s
</w:p>]], runs_xml))
end

local function is_bib_heading(block)
  if block.t ~= "Header" then return false end
  local text = pandoc.utils.stringify(block)
  return text:match("Bibliography") or text:match("References") or text:match("Works Cited")
end
"""


def _lua_basic_filter() -> str:
    """Page break before Bibliography heading + hanging indent for entries."""
    return _lua_bib_entry_xml() + """
function Pandoc(doc)
  local new_blocks = {}
  local in_bib = false
  for i, block in ipairs(doc.blocks) do
    if is_bib_heading(block) then
      in_bib = true
      table.insert(new_blocks, pandoc.RawBlock('openxml', string.format([[
<w:p>
  <w:pPr>
    <w:pStyle w:val="Heading%d"/>
    <w:pageBreakBefore/>
  </w:pPr>
  <w:r>
    <w:t>%s</w:t>
  </w:r>
</w:p>]], block.level, pandoc.utils.stringify(block))))
    elseif in_bib and block.t == "Header" then
      in_bib = false
      table.insert(new_blocks, block)
    elseif in_bib and block.t == "Para" then
      table.insert(new_blocks, bib_entry_block(block))
    else
      table.insert(new_blocks, block)
    end
  end
  doc.blocks = new_blocks
  return doc
end"""


def _lua_coverpage_filter(yaml: dict) -> str:
    """Turabian-style cover page via OpenXML raw blocks."""
    title = yaml.get("title", "").replace('"', '\\"')
    author = yaml.get("author", "").replace('"', '\\"')
    course = yaml.get("course", "").replace('"', '\\"')
    instructor = yaml.get("instructor", "").replace('"', '\\"')
    date = yaml.get("date", "").replace('"', '\\"')

    return _lua_bib_entry_xml() + f"""-- Cover page format (Turabian style)
local meta_title = "{title}"
local meta_author = "{author}"
local meta_course = "{course}"
local meta_instructor = "{instructor}"
local meta_date = "{date}"

local function format_date(date_str)
  local months = {{
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
  }}
  local year, month, day = date_str:match("(%d+)-(%d+)-(%d+)")
  if year and month and day then
    local month_name = months[tonumber(month)]
    if month_name then
      return string.format("%s %d, %s", month_name, tonumber(day), year)
    end
  end
  return date_str
end

function Meta(meta)
  if meta.title and meta_title == "" then
    meta_title = pandoc.utils.stringify(meta.title)
  end
  if meta.author and meta_author == "" then
    meta_author = pandoc.utils.stringify(meta.author)
  end
  if meta.course and meta_course == "" then
    meta_course = pandoc.utils.stringify(meta.course)
  end
  if meta.instructor and meta_instructor == "" then
    meta_instructor = pandoc.utils.stringify(meta.instructor)
  end
  if meta.date and meta_date == "" then
    meta_date = pandoc.utils.stringify(meta.date)
  end
  meta.author = nil
  meta.date = nil
  meta.title = nil
  meta.course = nil
  meta.instructor = nil
  return meta
end

function Pandoc(doc)
  local new_blocks = {{}}

  if meta_title and meta_title ~= "" then
    table.insert(new_blocks, pandoc.RawBlock('openxml', string.format([[
<w:p>
  <w:pPr>
    <w:spacing w:before="2400" w:after="0" w:line="480" w:lineRule="auto"/>
    <w:jc w:val="center"/>
  </w:pPr>
  <w:r>
    <w:t>%s</w:t>
  </w:r>
</w:p>]], meta_title)))
  end

  local gap_before_author = 4320
  local first_info = true

  if meta_author and meta_author ~= "" then
    local spacing_before = first_info and gap_before_author or 0
    first_info = false
    table.insert(new_blocks, pandoc.RawBlock('openxml', string.format([[
<w:p>
  <w:pPr>
    <w:spacing w:before="%d" w:after="0" w:line="480" w:lineRule="auto"/>
    <w:jc w:val="center"/>
  </w:pPr>
  <w:r>
    <w:t>%s</w:t>
  </w:r>
</w:p>]], spacing_before, meta_author)))
  end

  if meta_course and meta_course ~= "" then
    local spacing_before = first_info and gap_before_author or 0
    first_info = false
    table.insert(new_blocks, pandoc.RawBlock('openxml', string.format([[
<w:p>
  <w:pPr>
    <w:spacing w:before="%d" w:after="0" w:line="480" w:lineRule="auto"/>
    <w:jc w:val="center"/>
  </w:pPr>
  <w:r>
    <w:t>%s</w:t>
  </w:r>
</w:p>]], spacing_before, meta_course)))
  end

  if meta_instructor and meta_instructor ~= "" then
    local spacing_before = first_info and gap_before_author or 0
    first_info = false
    table.insert(new_blocks, pandoc.RawBlock('openxml', string.format([[
<w:p>
  <w:pPr>
    <w:spacing w:before="%d" w:after="0" w:line="480" w:lineRule="auto"/>
    <w:jc w:val="center"/>
  </w:pPr>
  <w:r>
    <w:t>%s</w:t>
  </w:r>
</w:p>]], spacing_before, meta_instructor)))
  end

  if meta_date and meta_date ~= "" then
    local formatted_date = format_date(meta_date)
    local spacing_before = first_info and gap_before_author or 0
    first_info = false
    table.insert(new_blocks, pandoc.RawBlock('openxml', string.format([[
<w:p>
  <w:pPr>
    <w:spacing w:before="%d" w:after="0" w:line="480" w:lineRule="auto"/>
    <w:jc w:val="center"/>
  </w:pPr>
  <w:r>
    <w:t>%s</w:t>
  </w:r>
</w:p>]], spacing_before, formatted_date)))
  end

  local page_break_inserted = false
  local in_bib = false
  for i, block in ipairs(doc.blocks) do
    if is_bib_heading(block) then
      in_bib = true
      if not page_break_inserted then
        page_break_inserted = true
      end
      table.insert(new_blocks, pandoc.RawBlock('openxml', string.format([[
<w:p>
  <w:pPr>
    <w:pStyle w:val="Heading%d"/>
    <w:pageBreakBefore/>
  </w:pPr>
  <w:r>
    <w:t>%s</w:t>
  </w:r>
</w:p>]], block.level, pandoc.utils.stringify(block))))
    elseif in_bib and block.t == "Header" then
      in_bib = false
      table.insert(new_blocks, block)
    elseif in_bib and block.t == "Para" then
      table.insert(new_blocks, bib_entry_block(block))
    else
      if not page_break_inserted then
        if block.t == "Header" or
           (block.t == "Para" and #block.content > 0) or
           block.t == "CodeBlock" or
           block.t == "BulletList" or
           block.t == "OrderedList" or
           block.t == "Table" or
           block.t == "BlockQuote" or
           block.t == "RawBlock" then
          table.insert(new_blocks, pandoc.RawBlock('openxml', [[
<w:p>
  <w:pPr>
    <w:pageBreakBefore/>
  </w:pPr>
</w:p>]]))
          page_break_inserted = true
        end
      end
      table.insert(new_blocks, block)
    end
  end

  if not page_break_inserted then
    table.insert(new_blocks, pandoc.RawBlock('openxml', [[
<w:p>
  <w:pPr>
    <w:pageBreakBefore/>
  </w:pPr>
</w:p>]]))
  end

  doc.blocks = new_blocks
  return doc
end"""


def _lua_header_filter(yaml: dict) -> str:
    """MLA-style header block via OpenXML raw blocks."""
    title = yaml.get("title", "").replace('"', '\\"')
    author = yaml.get("author", "").replace('"', '\\"')
    course = yaml.get("course", "").replace('"', '\\"')
    instructor = yaml.get("instructor", "").replace('"', '\\"')
    date = yaml.get("date", "").replace('"', '\\"')

    return _lua_bib_entry_xml() + f"""-- MLA Header format
local meta_title = "{title}"
local meta_author = "{author}"
local meta_course = "{course}"
local meta_instructor = "{instructor}"
local meta_date = "{date}"

local function format_date(date_str)
  local months = {{
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
  }}
  local year, month, day = date_str:match("(%d+)-(%d+)-(%d+)")
  if year and month and day then
    local month_name = months[tonumber(month)]
    if month_name then
      return string.format("%d %s %s", tonumber(day), month_name, year)
    end
  end
  return date_str
end

function Meta(meta)
  if meta.title and meta_title == "" then
    meta_title = pandoc.utils.stringify(meta.title)
  end
  if meta.author and meta_author == "" then
    meta_author = pandoc.utils.stringify(meta.author)
  end
  if meta.course and meta_course == "" then
    meta_course = pandoc.utils.stringify(meta.course)
  end
  if meta.instructor and meta_instructor == "" then
    meta_instructor = pandoc.utils.stringify(meta.instructor)
  end
  if meta.date and meta_date == "" then
    meta_date = pandoc.utils.stringify(meta.date)
  end
  meta.author = nil
  meta.date = nil
  meta.title = nil
  meta.course = nil
  meta.instructor = nil
  return meta
end

function Pandoc(doc)
  local new_blocks = {{}}

  if meta_author and meta_author ~= "" then
    table.insert(new_blocks, pandoc.RawBlock('openxml', string.format([[
<w:p>
  <w:pPr>
    <w:spacing w:after="0" w:line="480" w:lineRule="auto"/>
  </w:pPr>
  <w:r>
    <w:t>%s</w:t>
  </w:r>
</w:p>]], meta_author)))
  end

  if meta_instructor and meta_instructor ~= "" then
    table.insert(new_blocks, pandoc.RawBlock('openxml', string.format([[
<w:p>
  <w:pPr>
    <w:spacing w:after="0" w:line="480" w:lineRule="auto"/>
  </w:pPr>
  <w:r>
    <w:t>%s</w:t>
  </w:r>
</w:p>]], meta_instructor)))
  end

  if meta_course and meta_course ~= "" then
    table.insert(new_blocks, pandoc.RawBlock('openxml', string.format([[
<w:p>
  <w:pPr>
    <w:spacing w:after="0" w:line="480" w:lineRule="auto"/>
  </w:pPr>
  <w:r>
    <w:t>%s</w:t>
  </w:r>
</w:p>]], meta_course)))
  end

  if meta_date and meta_date ~= "" then
    local formatted_date = format_date(meta_date)
    table.insert(new_blocks, pandoc.RawBlock('openxml', string.format([[
<w:p>
  <w:pPr>
    <w:spacing w:after="0" w:line="480" w:lineRule="auto"/>
  </w:pPr>
  <w:r>
    <w:t>%s</w:t>
  </w:r>
</w:p>]], formatted_date)))
  end

  if meta_title and meta_title ~= "" then
    table.insert(new_blocks, pandoc.RawBlock('openxml', string.format([[
<w:p>
  <w:pPr>
    <w:spacing w:after="0" w:line="480" w:lineRule="auto"/>
    <w:jc w:val="center"/>
  </w:pPr>
  <w:r>
    <w:t>%s</w:t>
  </w:r>
</w:p>]], meta_title)))
  end

  local in_bib = false
  for i, block in ipairs(doc.blocks) do
    if is_bib_heading(block) then
      in_bib = true
      table.insert(new_blocks, pandoc.RawBlock('openxml', string.format([[
<w:p>
  <w:pPr>
    <w:pStyle w:val="Heading%d"/>
    <w:pageBreakBefore/>
  </w:pPr>
  <w:r>
    <w:t>%s</w:t>
  </w:r>
</w:p>]], block.level, pandoc.utils.stringify(block))))
    elseif in_bib and block.t == "Header" then
      in_bib = false
      table.insert(new_blocks, block)
    elseif in_bib and block.t == "Para" then
      table.insert(new_blocks, bib_entry_block(block))
    else
      table.insert(new_blocks, block)
    end
  end

  doc.blocks = new_blocks
  return doc
end"""


def _generate_lua_filter(yaml: dict) -> str:
    """Dispatch to the right Lua filter based on style: field."""
    fmt = yaml.get("style", "")
    if fmt == "chicago":
        return _lua_coverpage_filter(yaml)
    if fmt == "mla":
        return _lua_header_filter(yaml)
    return _lua_basic_filter()


# ── DOCX post-processing ──────────────────────────────────────────────

_EMPTY_HEADER_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p>
    <w:pPr>
      <w:pStyle w:val="Header"/>
    </w:pPr>
  </w:p>
</w:hdr>"""

_EMPTY_FOOTER_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p>
    <w:pPr>
      <w:pStyle w:val="Footer"/>
    </w:pPr>
  </w:p>
</w:ftr>"""


def _postprocess_docx(docx_path: str, yaml: dict) -> None:
    """Strip headers/footers and replace {{LASTNAME}} in DOCX zip."""
    fmt = yaml.get("style", "")
    strip_headers = fmt != "mla"  # strip for chicago or blank
    strip_footers = fmt == "mla"  # strip only for mla format

    # Determine lastname replacement
    author = yaml.get("author", "")
    lastname = yaml.get("lastname", "")
    if not lastname and author:
        lastname = author.split()[-1] if author.split() else ""

    buf = io.BytesIO()
    with zipfile.ZipFile(docx_path, "r") as zin:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                is_header = re.match(r"word/header\d*\.xml", item.filename)
                is_footer = re.match(r"word/footer\d*\.xml", item.filename)

                if strip_headers and is_header:
                    data = _EMPTY_HEADER_XML
                elif strip_footers and is_footer:
                    data = _EMPTY_FOOTER_XML
                elif is_header or is_footer:
                    # Replace {{LASTNAME}} placeholder
                    text = data.decode("utf-8")
                    if lastname:
                        text = text.replace("{{LASTNAME}} ", lastname + " ")
                        text = text.replace("{{LASTNAME}}", lastname)
                    else:
                        text = text.replace("{{LASTNAME}} ", "")
                        text = text.replace("{{LASTNAME}}", "")
                    data = text.encode("utf-8")
                zout.writestr(item, data)

    with open(docx_path, "wb") as f:
        f.write(buf.getvalue())


# ════════════════════════════════════════════════════════════════════════
#  Helper: fuzzy filter
# ════════════════════════════════════════════════════════════════════════


def fuzzy_filter(bib_entries: list[BibEntry], query: str) -> list[BibEntry]:
    if not query:
        return list(bib_entries)
    q = query.lower()
    scored: list[tuple[float, BibEntry]] = []
    for e in bib_entries:
        hay = e.citekey.lower()
        if q in hay:
            scored.append((100.0, e))
        else:
            ratio = SequenceMatcher(None, q, hay).ratio() * 100
            if ratio > 30:
                scored.append((ratio, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored]


def fuzzy_filter_entries(entries: list[Entry], query: str) -> list[Entry]:
    if not query:
        return list(entries)
    q = query.lower()
    scored: list[tuple[float, Entry]] = []
    for e in entries:
        hay = e.name.lower()
        if q in hay:
            scored.append((100.0, e))
        else:
            ratio = SequenceMatcher(None, q, hay).ratio() * 100
            if ratio > 70:
                scored.append((ratio, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored]


# ════════════════════════════════════════════════════════════════════════
#  .bib parser (lightweight)
# ════════════════════════════════════════════════════════════════════════


def parse_bib_lightweight(text: str) -> list[BibEntry]:
    """Extract citekeys from .bib file."""
    entries = []
    for m in re.finditer(r"@\w+\s*\{([^,\s]+)", text):
        citekey = m.group(1).strip()
        if citekey:
            entries.append(BibEntry(citekey=citekey))
    return entries


def _find_bib_file(vault_dir: Path) -> Optional[Path]:
    """Find a .bib file, searching sources/ dirs then recursively."""
    def _valid(p: Path) -> bool:
        return not p.name.startswith("._") and ".Trash" not in p.parts

    # Check vault_dir/sources/ first
    sources_dir = vault_dir / "sources"
    if sources_dir.is_dir():
        for p in sorted(sources_dir.glob("*.bib")):
            if _valid(p):
                return p
    # Search recursively
    for p in sorted(vault_dir.rglob("*.bib")):
        if _valid(p):
            return p
    return None


def _load_bib_entries(vault_dir: Path) -> tuple[list[BibEntry], Optional[Path], float, str]:
    """Load bib entries, returning (entries, path, mtime, error)."""
    bib_path = _find_bib_file(vault_dir)
    if not bib_path:
        return [], None, 0.0, "no_file"
    if not bib_path.exists():
        return [], bib_path, 0.0, "not_exists"
    try:
        text = bib_path.read_text(encoding="utf-8")
        entries = parse_bib_lightweight(text)
        if not entries:
            return [], bib_path, 0.0, "no_entries"
        return entries, bib_path, bib_path.stat().st_mtime, ""
    except Exception as exc:
        return [], bib_path, 0.0, str(exc)


# ════════════════════════════════════════════════════════════════════════
#  Markdown Lexer (prompt_toolkit native — per-line regex, no Pygments)
# ════════════════════════════════════════════════════════════════════════


class MarkdownLexer(PtLexer):
    """Fast per-line markdown highlighter for the editor."""

    _HEADING_RE = re.compile(r'^(#{1,6}\s+)(.+)$')
    _PATTERNS = [
        (re.compile(r'\*\*[^*]+\*\*'), 'class:md.bold'),
        (re.compile(r'(?<!\*)\*(?!\*)[^*]+?(?<!\*)\*(?!\*)'), 'class:md.italic'),
        (re.compile(r'`[^`]+`'), 'class:md.code'),
        (re.compile(r'\^\[[^\]]*\]'), 'class:md.footnote'),
        (re.compile(r'\[[^\]]+\]\([^)]+\)'), 'class:md.link'),
    ]

    def lex_document(self, document):
        lines = document.lines

        def get_line(lineno):
            try:
                text = lines[lineno]
            except IndexError:
                return []
            if not text:
                return [('', '')]
            hm = MarkdownLexer._HEADING_RE.match(text)
            if hm:
                return [
                    ('class:md.heading-marker', hm.group(1)),
                    ('class:md.heading', hm.group(2)),
                ]
            matches = []
            for pattern, style in MarkdownLexer._PATTERNS:
                for m in pattern.finditer(text):
                    matches.append((m.start(), m.end(), style))
            if not matches:
                return [('', text)]
            matches.sort(key=lambda x: x[0])
            fragments = []
            pos = 0
            for start, end, style in matches:
                if start < pos:
                    continue
                if start > pos:
                    fragments.append(('', text[pos:start]))
                fragments.append((style, text[start:end]))
                pos = end
            if pos < len(text):
                fragments.append(('', text[pos:]))
            return fragments

        return get_line


# ════════════════════════════════════════════════════════════════════════
#  Word-Wrap Processor
# ════════════════════════════════════════════════════════════════════════


def _word_wrap_boundaries(text, width):
    """Return list of source-char indices where each visual line starts.

    For example, a line that wraps into 3 visual lines returns [0, s1, s2]
    where s1 and s2 are the source indices of the first char on lines 2 & 3.
    Also returns padding_inserts for the processor.
    """
    if not text or width <= 0 or len(text) <= width:
        return [0], []

    line_starts = [0]
    padding_inserts = []  # (source_index_of_space, pad_count)
    x = 0
    last_space_i = None
    last_space_x = 0

    for i, c in enumerate(text):
        cw = get_cwidth(c)
        if x + cw > width:
            if last_space_i is not None:
                pad = width - last_space_x - 1
                if pad > 0:
                    padding_inserts.append((last_space_i, pad))
                line_starts.append(last_space_i + 1)
                x = x - last_space_x - 1
                last_space_i = None
                last_space_x = 0
            else:
                line_starts.append(i)
                x = x % width if width else 0
        if c == ' ':
            last_space_i = i
            last_space_x = x
        x += cw

    return line_starts, padding_inserts


class WordWrapProcessor(Processor):
    """Insert padding at word boundaries so character-level wrap becomes word wrap."""

    def apply_transformation(self, ti):
        width = ti.width
        if not width or width <= 0:
            return Transformation(ti.fragments)

        text = ''.join(t for _, t, *__ in ti.fragments)
        if not text or len(text) <= width:
            return Transformation(ti.fragments)

        _, padding_inserts = _word_wrap_boundaries(text, width)

        if not padding_inserts:
            return Transformation(ti.fragments)

        # Insert padding spaces into the styled fragments.
        pad_dict = dict(padding_inserts)
        new_fragments = []
        source_pos = 0
        for style, frag_text, *rest in ti.fragments:
            start = 0
            for j, c in enumerate(frag_text):
                if source_pos + j in pad_dict:
                    new_fragments.append((style, frag_text[start:j + 1]))
                    new_fragments.append(('', ' ' * pad_dict[source_pos + j]))
                    start = j + 1
            if start < len(frag_text):
                new_fragments.append((style, frag_text[start:]))
            source_pos += len(frag_text)

        # Build cursor-position mappings.
        boundaries = []
        cum = 0
        for pos, pad in padding_inserts:
            cum += pad
            boundaries.append((pos + 1, cum, pad))

        def source_to_display(i):
            offset = 0
            for next_start, cum_pad, _ in boundaries:
                if i >= next_start:
                    offset = cum_pad
                else:
                    break
            return i + offset

        def display_to_source(i):
            prev_cum = 0
            for next_start, cum_pad, pad in boundaries:
                display_boundary = next_start + prev_cum
                if i >= display_boundary and i < display_boundary + pad:
                    return next_start
                elif i >= display_boundary + pad:
                    prev_cum = cum_pad
                else:
                    break
            return max(0, i - prev_cum)

        return Transformation(new_fragments, source_to_display, display_to_source)


# ════════════════════════════════════════════════════════════════════════
#  SelectableList Widget
# ════════════════════════════════════════════════════════════════════════


class SelectableList:
    """Navigable list widget. Items are (id, label) pairs."""

    def __init__(self, on_select=None):
        self.items = []
        self.selected_index = 0
        self.on_select = on_select
        self._kb = KeyBindings()
        sl = self

        @self._kb.add("up")
        def _up(event):
            if sl.selected_index > 0:
                sl.selected_index -= 1

        @self._kb.add("down")
        def _down(event):
            if sl.selected_index < len(sl.items) - 1:
                sl.selected_index += 1

        @self._kb.add("enter")
        def _enter(event):
            if sl.items and sl.on_select:
                sl.on_select(sl.items[sl.selected_index][0])

        @self._kb.add("home")
        def _home(event):
            sl.selected_index = 0

        @self._kb.add("end")
        def _end(event):
            if sl.items:
                sl.selected_index = len(sl.items) - 1

        self.control = FormattedTextControl(
            self._get_text, focusable=True, key_bindings=self._kb,
        )
        self.window = Window(
            content=self.control, style="class:select-list", wrap_lines=False,
        )

    def _get_text(self):
        if not self.items:
            return [("class:select-list.empty", "  (empty)\n")]
        result = []
        for i, (_, label) in enumerate(self.items):
            if i == self.selected_index:
                result.append(("[SetCursorPosition]", ""))
                result.append(("class:select-list.selected", f"  {label}\n"))
            else:
                result.append(("", f"  {label}\n"))
        return result

    def set_items(self, items):
        self.items = items
        if self.selected_index >= len(items):
            self.selected_index = max(0, len(items) - 1)

    def __pt_container__(self):
        return self.window


# ════════════════════════════════════════════════════════════════════════
#  Application State
# ════════════════════════════════════════════════════════════════════════


class AppState:
    """Mutable application state shared across the UI."""

    def __init__(self, storage):
        self.storage = storage
        self.entries = storage.list_entries()
        self.current_entry = None
        self.screen = "journal"
        self.notification = ""
        self.notification_task = None
        self.quit_pending = 0.0
        self.escape_pending = 0.0
        self.showing_exports = False
        self.show_keybindings = False
        self.editor_dirty = False
        self.root_container = None
        self.auto_save_task = None
        self.export_paths = []
        self.show_word_count = False
        self.last_find_query = ""
        self.show_find_panel = False
        self.find_panel = None
        self.shutdown_pending = 0.0
        # .bib cache
        self.bib_entries: list[BibEntry] = []
        self.bib_path: Optional[Path] = None
        self.bib_mtime: float = 0.0
        self.bib_error: str = ""


# ════════════════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════════════════


def show_notification(state, message, duration=3.0):
    """Show a notification in the status bar, auto-clearing after duration."""
    state.notification = message
    get_app().invalidate()
    if state.notification_task:
        state.notification_task.cancel()

    async def _clear():
        await asyncio.sleep(duration)
        if state.notification == message:
            state.notification = ""
            get_app().invalidate()

    state.notification_task = asyncio.ensure_future(_clear())


async def show_dialog_as_float(state, dialog):
    """Show a modal dialog as a float and await its result."""
    float_ = Float(content=dialog, transparent=False)
    state.root_container.floats.append(float_)
    app = get_app()
    focused_before = app.layout.current_window
    app.layout.focus(dialog)
    result = await dialog.future
    if float_ in state.root_container.floats:
        state.root_container.floats.remove(float_)
    try:
        app.layout.focus(focused_before)
    except ValueError:
        pass
    app.invalidate()
    return result


def _detect_printers():
    """Return list of available printer names via lpstat."""
    try:
        result = subprocess.run(
            ["lpstat", "-a"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        return [
            line.split()[0]
            for line in result.stdout.strip().splitlines()
            if line.split()
        ]
    except Exception:
        return []


def _clipboard_copy(text):
    """Copy text to system clipboard."""
    for cmd in [["wl-copy"], ["xclip", "-selection", "clipboard"]]:
        try:
            subprocess.run(cmd, input=text, text=True, timeout=2)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return False


def _clipboard_paste():
    """Get text from system clipboard."""
    for cmd in [["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"]]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _para_count(text):
    """Count paragraphs in text (excluding YAML frontmatter)."""
    body = re.sub(r"^---\n.*?\n---\n?", "", text, count=1, flags=re.DOTALL)
    return sum(1 for p in re.split(r"\n\s*\n", body) if p.strip())


def _word_count(text):
    """Count words in text (excluding YAML frontmatter)."""
    body = re.sub(r"^---\n.*?\n---\n?", "", text, count=1, flags=re.DOTALL)
    return len(body.split())


# ════════════════════════════════════════════════════════════════════════
#  Dialogs
# ════════════════════════════════════════════════════════════════════════


class InputDialog:
    """Text input dialog (new entry, rename)."""

    def __init__(self, title="", label_text="", initial="", ok_text="OK"):
        self.future = asyncio.Future()
        self.text_area = TextArea(
            text=initial, multiline=False, width=D(preferred=40),
        )

        def accept(_buf=None):
            val = self.text_area.text.strip()
            if not self.future.done():
                self.future.set_result(val if val else None)

        self.text_area.buffer.accept_handler = accept
        ok_btn = Button(text=ok_text, handler=accept)
        cancel_btn = Button(text="(c) Cancel", handler=self.cancel)
        self.dialog = Dialog(
            title=title,
            body=HSplit([Label(text=label_text), self.text_area]),
            buttons=[ok_btn, cancel_btn],
            modal=True,
        )

    def cancel(self):
        if not self.future.done():
            self.future.set_result(None)

    def __pt_container__(self):
        return self.dialog


class ConfirmDialog:
    """Yes/No confirmation dialog with y/n key bindings."""

    def __init__(self, question="Are you sure?"):
        self.future = asyncio.Future()
        kb = KeyBindings()

        @kb.add("y")
        def _yes(event):
            if not self.future.done():
                self.future.set_result(True)

        @kb.add("n")
        def _no(event):
            if not self.future.done():
                self.future.set_result(False)

        self._control = FormattedTextControl(
            [("", f"\n  {question}\n")],
            focusable=True,
            key_bindings=kb,
        )

        def yes_handler():
            if not self.future.done():
                self.future.set_result(True)

        def no_handler():
            if not self.future.done():
                self.future.set_result(False)

        self.dialog = Dialog(
            title="Confirm",
            body=Window(content=self._control, height=3),
            buttons=[
                Button(text="(y) Yes", handler=yes_handler),
                Button(text="(n) No", handler=no_handler),
            ],
            modal=True,
            width=D(preferred=50),
        )

    def cancel(self):
        if not self.future.done():
            self.future.set_result(False)

    def __pt_container__(self):
        return self.dialog


class ExportFormatDialog:
    """Pick export format: PDF or DOCX."""

    def __init__(self):
        self.future = asyncio.Future()
        self.list = SelectableList(on_select=self._select)
        self.list.set_items([
            ("pdf", "PDF (.pdf)"),
            ("docx", "Word (.docx)"),
        ])
        @self.list._kb.add("c")
        def _cancel(event):
            self.cancel()

        @self.list._kb.add("escape", eager=True)
        def _escape(event):
            self.cancel()

        self.dialog = Dialog(
            title="Export as",
            body=HSplit([self.list], padding=0),
            buttons=[Button(text="(c) Cancel", handler=self.cancel)],
            modal=True,
            width=D(preferred=40, max=50),
        )

    def _select(self, fmt):
        if not self.future.done():
            self.future.set_result(fmt)

    def cancel(self):
        if not self.future.done():
            self.future.set_result(None)

    def __pt_container__(self):
        return self.dialog


class PrinterPickerDialog:
    """Pick a printer from available system printers."""

    def __init__(self, printers, file_path):
        self.future = asyncio.Future()
        self.file_path = file_path
        self.list = SelectableList(on_select=self._select)
        self.list.set_items([(p, p) for p in printers])
        @self.list._kb.add("c")
        def _cancel(event):
            self.cancel()

        @self.list._kb.add("escape", eager=True)
        def _escape(event):
            self.cancel()

        self.dialog = Dialog(
            title="Print to",
            body=HSplit([self.list]),
            buttons=[Button(text="(c) Cancel", handler=self.cancel)],
            modal=True,
            width=D(preferred=50, max=60),
        )

    def _select(self, printer):
        try:
            subprocess.Popen([
                "lp", "-d", printer, "-o", "sides=two-sided-long-edge",
                str(self.file_path),
            ])
        except Exception:
            pass
        if not self.future.done():
            self.future.set_result(printer)

    def cancel(self):
        if not self.future.done():
            self.future.set_result(None)

    def __pt_container__(self):
        return self.dialog


class CitePickerDialog:
    """Fuzzy-search .bib entries and pick one to insert as @citekey."""

    def __init__(self, bib_entries):
        self.future = asyncio.Future()
        self.all_entries = bib_entries
        self.filtered = list(bib_entries)
        self.search_buf = Buffer(multiline=False)
        self.search_buf.on_text_changed += self._on_search_changed
        search_kb = KeyBindings()

        @search_kb.add("escape", eager=True)
        def _escape(event):
            self.cancel()

        @search_kb.add("down")
        def _down(event):
            event.app.layout.focus(self.results.window)

        @search_kb.add("enter")
        def _enter(event):
            if self.filtered:
                idx = min(self.results.selected_index, len(self.filtered) - 1)
                e = self.filtered[idx]
                if not self.future.done():
                    self.future.set_result(f"@{e.citekey}")

        self.search_control = BufferControl(
            buffer=self.search_buf, key_bindings=search_kb,
        )
        self.search_window = Window(
            content=self.search_control, height=1, style="class:input",
        )
        self.results = SelectableList(on_select=self._on_select)
        @self.results._kb.add("escape", eager=True)
        def _escape_list(event):
            self.cancel()

        self._update_results("")
        self.dialog = Dialog(
            title="Insert Citation",
            body=HSplit([self.search_window, self.results], padding=0),
            buttons=[Button(text="Cancel", handler=self.cancel)],
            modal=True,
            width=D(preferred=80, max=100),
        )

    def _on_search_changed(self, buf):
        self._update_results(buf.text)

    def _update_results(self, query):
        self.filtered = fuzzy_filter(self.all_entries, query)
        items = [(e.citekey, e.citekey) for e in self.filtered]
        self.results.set_items(items)
        self.results.selected_index = 0

    def _on_select(self, citekey):
        for e in self.filtered:
            if e.citekey == citekey:
                if not self.future.done():
                    self.future.set_result(f"@{e.citekey}")
                return

    def cancel(self):
        if not self.future.done():
            self.future.set_result(None)

    def __pt_container__(self):
        return self.dialog


class CommandPaletteDialog:
    """Command palette with fuzzy search."""

    def __init__(self, commands):
        self.future = asyncio.Future()
        self.all_commands = commands
        self.filtered = list(commands)
        self.search_buf = Buffer(multiline=False)
        self.search_buf.on_text_changed += self._on_search_changed
        search_kb = KeyBindings()

        @search_kb.add("escape", eager=True)
        def _escape(event):
            self.cancel()

        @search_kb.add("down")
        def _down(event):
            event.app.layout.focus(self.results.window)

        @search_kb.add("enter")
        def _enter(event):
            if self.filtered:
                idx = min(self.results.selected_index, len(self.filtered) - 1)
                if not self.future.done():
                    self.future.set_result(self.filtered[idx][2])

        self.search_control = BufferControl(
            buffer=self.search_buf, key_bindings=search_kb,
        )
        self.search_window = Window(
            content=self.search_control, height=1, style="class:input",
        )
        self.results = SelectableList(on_select=self._on_select)
        @self.results._kb.add("escape", eager=True)
        def _escape_list(event):
            self.cancel()

        self._update_results("")
        self.dialog = Dialog(
            title="Command Palette",
            body=HSplit([self.search_window, self.results], padding=0),
            buttons=[Button(text="Cancel", handler=self.cancel)],
            modal=True,
            width=D(preferred=60, max=80),
        )

    def _on_search_changed(self, buf):
        self._update_results(buf.text)

    def _update_results(self, query):
        if not query:
            self.filtered = list(self.all_commands)
        else:
            q = query.lower()
            scored = []
            for cmd in self.all_commands:
                name = cmd[0].lower()
                if q in name:
                    scored.append((100.0, cmd))
                else:
                    ratio = SequenceMatcher(None, q, name).ratio() * 100
                    if ratio > 30:
                        scored.append((ratio, cmd))
            scored.sort(key=lambda x: x[0], reverse=True)
            self.filtered = [c for _, c in scored]
        self.results.set_items([
            (str(i), cmd[0]) for i, cmd in enumerate(self.filtered)
        ])
        self.results.selected_index = 0

    def _on_select(self, item_id):
        idx = int(item_id)
        if idx < len(self.filtered):
            if not self.future.done():
                self.future.set_result(self.filtered[idx][2])

    def cancel(self):
        if not self.future.done():
            self.future.set_result(None)

    def __pt_container__(self):
        return self.dialog


class FindReplacePanel:
    """Non-modal side panel for find/replace with match cycling."""

    def __init__(self, editor_buf, state, last_query="", editor_area=None):
        self.editor_buf = editor_buf
        self.state = state
        self.editor_area = editor_area
        self.matches = []
        self.match_idx = -1
        self.status_text = ""

        self.search_buf = Buffer(multiline=False, name="find-search")
        self.replace_buf = Buffer(multiline=False, name="find-replace")
        if last_query:
            self.search_buf.set_document(
                Document(last_query, len(last_query)), bypass_readonly=True,
            )

        search_kb = KeyBindings()
        replace_kb = KeyBindings()

        @search_kb.add("enter")
        def _search_enter(event):
            self._move(1)
            get_app().layout.focus(self.editor_area)

        @search_kb.add("tab")
        def _search_tab(event):
            get_app().layout.focus(self.replace_window)

        @replace_kb.add("enter")
        def _replace_enter(event):
            self._replace_one()

        @replace_kb.add("tab")
        def _replace_tab(event):
            get_app().layout.focus(self.replace_all_window)

        self.search_control = BufferControl(
            buffer=self.search_buf, key_bindings=search_kb,
        )
        self.search_window = Window(
            content=self.search_control, height=1, style="class:input",
        )
        self.replace_control = BufferControl(
            buffer=self.replace_buf, key_bindings=replace_kb,
        )
        self.replace_window = Window(
            content=self.replace_control, height=1, style="class:input",
        )
        self.status_control = FormattedTextControl(
            lambda: [("class:hint", self.status_text)],
        )

        # Replace All button
        btn_kb = KeyBindings()

        @btn_kb.add("enter")
        @btn_kb.add(" ")
        def _btn_activate(event):
            self._replace_all()

        @btn_kb.add("tab")
        def _btn_tab(event):
            get_app().layout.focus(self.search_window)

        self.replace_all_control = FormattedTextControl(
            [("class:button", " Replace All ")],
            key_bindings=btn_kb, focusable=True,
        )
        self.replace_all_window = Window(
            content=self.replace_all_control, height=1,
        )

        self.search_buf.on_text_changed += self._on_changed

        def get_hints():
            return [
                ("class:accent bold", " ret"), ("", "  Highlight in editor\n"),
                ("class:accent bold", "  ^k"), ("", "  Next result\n"),
                ("class:accent bold", "  ^j"), ("", "  Previous result\n"),
                ("class:accent bold", "  ^f"), ("", "  Shift panel focus\n"),
                ("class:accent bold", " esc"), ("", "  Close\n"),
            ]

        self.container = HSplit([
            Window(FormattedTextControl(
                [("class:accent bold", " Find/Replace\n")],
            ), height=1),
            Window(height=1, char="\u2500", style="class:hint"),
            Label(text=" Find:"),
            self.search_window,
            Window(content=self.status_control, height=1),
            Label(text=" Replace:"),
            self.replace_window,
            self.replace_all_window,
            Window(height=1),
            Window(FormattedTextControl(get_hints), height=5),
        ], width=28, style="class:find-panel")

    def _scroll_to_cursor(self):
        if self.editor_area is not None:
            row = self.editor_buf.document.cursor_position_row
            target = max(0, row)
            window = self.editor_area.window
            original_scroll = window._scroll

            def _forced_scroll(ui_content, width, height):
                original_scroll(ui_content, width, height)
                window.vertical_scroll = target
                window._scroll = original_scroll

            window._scroll = _forced_scroll

    def _rebuild_matches(self):
        query = self.search_buf.text
        if not query:
            self.matches = []
            self.match_idx = -1
            self.status_text = ""
            return
        text = self.editor_buf.text
        lq = query.lower()
        lt = text.lower()
        self.matches = []
        start = 0
        while True:
            pos = lt.find(lq, start)
            if pos == -1:
                break
            self.matches.append(pos)
            start = pos + 1

    def _on_changed(self, buf):
        self._rebuild_matches()
        if self.matches:
            cur = self.editor_buf.cursor_position
            self.match_idx = 0
            for i, pos in enumerate(self.matches):
                if pos >= cur:
                    self.match_idx = i
                    break
            self.editor_buf.cursor_position = self.matches[self.match_idx]
            n = len(self.matches)
            self.status_text = f" {self.match_idx + 1} of {n} match{'es' if n != 1 else ''}"
            self._scroll_to_cursor()
        else:
            self.match_idx = -1
            if self.search_buf.text:
                self.status_text = " No matches"
            else:
                self.status_text = ""
        get_app().invalidate()

    def _move(self, direction):
        if not self.matches:
            return
        self.match_idx = (self.match_idx + direction) % len(self.matches)
        self.editor_buf.cursor_position = self.matches[self.match_idx]
        n = len(self.matches)
        self.status_text = f" {self.match_idx + 1} of {n} match{'es' if n != 1 else ''}"
        self._scroll_to_cursor()
        get_app().invalidate()

    def _replace_one(self):
        if not self.matches or self.match_idx < 0:
            return
        pos = self.matches[self.match_idx]
        query = self.search_buf.text
        replacement = self.replace_buf.text
        text = self.editor_buf.text
        new_text = text[:pos] + replacement + text[pos + len(query):]
        self.editor_buf.set_document(
            Document(new_text, pos + len(replacement)), bypass_readonly=True,
        )
        self._rebuild_matches()
        if self.matches:
            self.match_idx = min(self.match_idx, len(self.matches) - 1)
            self.editor_buf.cursor_position = self.matches[self.match_idx]
            n = len(self.matches)
            self.status_text = f" {self.match_idx + 1} of {n} match{'es' if n != 1 else ''}"
            self._scroll_to_cursor()
        else:
            self.match_idx = -1
            self.status_text = " No matches"
        get_app().invalidate()

    def _replace_all(self):
        query = self.search_buf.text
        if not query or not self.matches:
            return
        replacement = self.replace_buf.text
        text = self.editor_buf.text
        count = len(self.matches)
        new_text = re.sub(re.escape(query), replacement, text, flags=re.IGNORECASE)
        self.editor_buf.set_document(
            Document(new_text, min(self.editor_buf.cursor_position, len(new_text))),
            bypass_readonly=True,
        )
        self._rebuild_matches()
        self.match_idx = -1
        self.status_text = f" Replaced {count} occurrence{'s' if count != 1 else ''}"
        get_app().invalidate()

    def is_focused(self):
        """Return True if any window in this panel has focus."""
        cur = get_app().layout.current_window
        return (cur is self.search_window or cur is self.replace_window
                or cur is self.replace_all_window)

    def __pt_container__(self):
        return self.container


# ════════════════════════════════════════════════════════════════════════
#  Application
# ════════════════════════════════════════════════════════════════════════


_FRONTMATTER_PROPS = ["title", "author", "instructor", "date", "spacing", "style"]


def create_app(storage):
    """Build and return the prompt_toolkit Application."""
    state = AppState(storage)

    # Load .bib cache on startup
    state.bib_entries, state.bib_path, state.bib_mtime, state.bib_error = (
        _load_bib_entries(storage.vault_dir))

    def _refresh_bib_cache():
        """Re-parse .bib if file mtime changed."""
        if state.bib_path and state.bib_path.exists():
            try:
                cur_mtime = state.bib_path.stat().st_mtime
                if cur_mtime != state.bib_mtime:
                    state.bib_entries, state.bib_path, state.bib_mtime, state.bib_error = (
                        _load_bib_entries(storage.vault_dir))
            except OSError:
                pass
        else:
            state.bib_entries, state.bib_path, state.bib_mtime, state.bib_error = (
                _load_bib_entries(storage.vault_dir))

    # ── Journal screen widgets ────────────────────────────────────────

    entry_search = TextArea(
        multiline=False, prompt=" Search: ", height=1,
        style="class:input",
    )
    entry_list = SelectableList()
    export_list = SelectableList()

    def _get_title_hints():
        return [
            ("class:title bold", " Journal"),
            ("class:hint", "  (n) new (r) rename (d) delete (e) exports (/) search"),
        ]

    def _get_shutdown_hint():
        now = time.monotonic()
        if state.shutdown_pending and now - state.shutdown_pending < 2.0:
            return [("class:accent bold", " (^s) press again to shut down ")]
        return [("class:hint", " (^s) shut down ")]

    shutdown_hint_control = FormattedTextControl(_get_shutdown_hint)
    title_hints_window = VSplit([
        Window(content=FormattedTextControl(_get_title_hints), height=1),
        Window(content=shutdown_hint_control, height=1, align=WindowAlign.RIGHT),
    ])

    def refresh_entries(query=""):
        state.entries = state.storage.list_entries()
        filtered = fuzzy_filter_entries(state.entries, query)
        if not state.entries:
            entry_list.set_items([
                ("__empty__", "No entries yet \u2014 press n to create one.")])
        elif not filtered:
            entry_list.set_items([("__empty__", "No matching entries.")])
        else:
            items = []
            for e in filtered:
                try:
                    mod = datetime.fromtimestamp(e.modified).strftime(
                        "%Y-%m-%d %H:%M")
                except (ValueError, TypeError, OSError):
                    mod = ""
                # Right-align date with dots
                name_part = e.name
                if mod:
                    items.append((str(e.path), f"{name_part}  {mod}"))
                else:
                    items.append((str(e.path), name_part))
            entry_list.set_items(items)

    def refresh_exports():
        files = []
        for d in (state.storage.pdf_dir, state.storage.docx_dir):
            if d.is_dir():
                for ext in ("*.pdf", "*.docx"):
                    files.extend(d.glob(ext))
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        state.export_paths = files
        if not files:
            export_list.set_items([("__empty__", "No exports yet.")])
        else:
            items = []
            for f in files:
                try:
                    mod = datetime.fromtimestamp(f.stat().st_mtime).strftime(
                        "%b %d, %Y %H:%M")
                except (ValueError, OSError):
                    mod = ""
                size_kb = f.stat().st_size // 1024
                items.append((str(f), f"{f.name}  ({mod}, {size_kb} KB)"))
            export_list.set_items(items)

    entry_search.buffer.on_text_changed += lambda buf: refresh_entries(buf.text)
    refresh_entries()

    def open_entry(path_str):
        if path_str == "__empty__":
            return
        path = Path(path_str)
        # Find the entry by path
        entry = None
        for e in state.entries:
            if str(e.path) == path_str:
                entry = e
                break
        if not entry:
            # Try to construct from path
            if path.exists():
                entry = Entry(path=path, name=path.stem,
                              modified=path.stat().st_mtime)
            else:
                return
        state.current_entry = entry
        state.editor_dirty = False
        content = state.storage.read_entry(entry)
        editor_area.text = content
        state.screen = "editor"
        get_app().layout.focus(editor_area.window)
        if state.auto_save_task:
            state.auto_save_task.cancel()
        state.auto_save_task = asyncio.ensure_future(auto_save_loop())
        get_app().invalidate()

    entry_list.on_select = open_entry

    def open_export(path_str):
        if path_str == "__empty__":
            return
        path = Path(path_str)
        if path.suffix.lower() == ".pdf":
            printers = _detect_printers()
            if printers:
                async def _show():
                    dlg = PrinterPickerDialog(printers, path)
                    result = await show_dialog_as_float(state, dlg)
                    if result:
                        show_notification(state, f"Sent to {result}.")
                asyncio.ensure_future(_show())
                return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass

    export_list.on_select = open_export

    journal_view = HSplit([
        title_hints_window,
        entry_list,
        entry_search,
    ])

    exports_hints_control = FormattedTextControl(
        lambda: [("class:hint", " (j) Journal  (d) Delete")])
    exports_hints_window = Window(content=exports_hints_control, height=1)

    exports_view = HSplit([
        Window(FormattedTextControl([("class:title", " Exports")]),
               height=1, dont_extend_height=True),
        export_list,
        exports_hints_window,
    ])

    def get_journal_screen():
        if state.showing_exports:
            return exports_view
        return journal_view

    journal_screen = DynamicContainer(get_journal_screen)

    # ── Editor screen widgets ────────────────────────────────────────

    editor_area = TextArea(
        text="",
        multiline=True,
        wrap_lines=True,
        scrollbar=False,
        style="class:editor",
        focus_on_click=True,
        lexer=MarkdownLexer(),
        input_processors=[WordWrapProcessor()],
    )
    editor_area.buffer.on_text_changed += lambda buf: setattr(state, 'editor_dirty', True)

    # ── Clipboard (Ctrl+C / Ctrl+V) on editor control ────────────
    _editor_cb_kb = KeyBindings()

    @_editor_cb_kb.add("c-v")
    def _paste(event):
        text = _clipboard_paste()
        if text:
            event.current_buffer.insert_text(text)

    @_editor_cb_kb.add("c-c")
    def _copy(event):
        buf = event.current_buffer
        if buf.selection_state:
            start = buf.selection_state.original_cursor_position
            end = buf.cursor_position
            if start > end:
                start, end = end, start
            selected = buf.text[start:end]
            if selected:
                _clipboard_copy(selected)
                show_notification(state, "Copied.")
            buf.exit_selection()

    @_editor_cb_kb.add("c-a")
    def _select_all(event):
        buf = event.current_buffer
        buf.cursor_position = 0
        buf.start_selection()
        buf.cursor_position = len(buf.text)

    @_editor_cb_kb.add("c-x")
    def _cut(event):
        buf = event.current_buffer
        if buf.selection_state:
            start = buf.selection_state.original_cursor_position
            end = buf.cursor_position
            if start > end:
                start, end = end, start
            selected = buf.text[start:end]
            if selected:
                _clipboard_copy(selected)
                show_notification(state, "Cut.")
            buf.exit_selection()
            new_text = buf.text[:start] + buf.text[end:]
            buf.set_document(Document(new_text, start), bypass_readonly=True)

    @_editor_cb_kb.add("c-u")
    def _ctrl_u(event):
        pass  # Disable unix-line-discard

    @_editor_cb_kb.add("c-m")
    def _ctrl_m(event):
        event.current_buffer.newline()  # Explicit newline

    editor_area.control.key_bindings = _editor_cb_kb

    def get_status_text():
        if state.notification:
            return [("class:status", f" {state.notification}")]
        if state.current_entry:
            if state.show_word_count:
                words = _word_count(editor_area.text)
                return [("class:status",
                         f" {state.current_entry.name}  {words} words")]
            else:
                paras = _para_count(editor_area.text)
                return [("class:status",
                         f" {state.current_entry.name}  {paras} \u00b6")]
        return [("class:status", "")]

    status_bar = Window(
        FormattedTextControl(get_status_text), height=1, style="class:status",
    )

    _KB_ALL = [
        ("esc", "Journal"), ("^p", "Commands"), ("^q", "Quit"), ("^s", "Save"),
        ("^b", "Bold"), ("^i", "Italic"), ("^n", "Footnote"), ("^r", "Cite"),
        ("^f", "Find/Replace"), ("^z", "Undo"), ("^⇧z", "Redo"),
    ]
    _KB_SECTIONS = [
        [("esc", "Journal"),
         ("^p", "Commands"), ("^q", "Quit"), ("^s", "Save")],
        [("^b", "Bold"), ("^i", "Italic"), ("^n", "Footnote"),
         ("^r", "Cite"), ("^f", "Find/Replace")],
        [("^z", "Undo"), ("^⇧z", "Redo")],
    ]
    _KB_EXTRAS = [
        ("^up", "Go to top"), ("^dn", "Go to bottom"),
        ("^w", "Toggle word/¶ count"),
        ("^g", "Show keybindings"), ("^s", "Shut down"),
    ]

    def get_keybindings_text():
        cols = shutil.get_terminal_size().columns
        if cols >= 100:
            mid = (len(_KB_ALL) + 1) // 2
            left, right = _KB_ALL[:mid], _KB_ALL[mid:]
            result = []
            for i in range(max(len(left), len(right))):
                if i < len(left):
                    k, d = left[i]
                    result.append(("class:accent bold", f" {k:>4}"))
                    result.append(("", f"  {d:<12}"))
                else:
                    result.append(("", " " * 18))
                if i < len(right):
                    k, d = right[i]
                    result.append(("class:accent bold", f"  {k:>4}"))
                    result.append(("", f"  {d}"))
                result.append(("", "\n"))
        else:
            result = []
            for i, section in enumerate(_KB_SECTIONS):
                if i > 0:
                    result.append(("", "\n"))
                for key, desc in section:
                    result.append(("class:accent bold", f" {key:>4}"))
                    result.append(("", f"  {desc}\n"))
        result.append(("", "\n"))
        for key, desc in _KB_EXTRAS:
            result.append(("class:accent bold", f" {key:>4}"))
            result.append(("", f"  {desc}\n"))
        return result

    def _keybindings_panel_width():
        return 40 if shutil.get_terminal_size().columns >= 100 else 22

    def get_editor_body():
        parts = []
        if state.show_find_panel and state.find_panel:
            parts.append(state.find_panel)
            parts.append(Window(width=1, char="\u2502", style="class:hint"))
        parts.append(editor_area)
        if state.show_keybindings:
            parts.append(Window(width=1, char="\u2502", style="class:hint"))
            parts.append(Window(
                FormattedTextControl(get_keybindings_text),
                width=_keybindings_panel_width(),
                style="class:keybindings-panel",
            ))
        return VSplit(parts)

    editor_screen = HSplit([
        DynamicContainer(get_editor_body),
        status_bar,
    ])

    # ── Screen switcher ──────────────────────────────────────────────

    def get_current_screen():
        if state.screen == "editor":
            return editor_screen
        return journal_screen

    root = FloatContainer(
        content=DynamicContainer(get_current_screen),
        floats=[],
    )
    state.root_container = root

    # ── Auto-save ────────────────────────────────────────────────────

    async def auto_save_loop():
        while state.screen == "editor":
            await asyncio.sleep(30)
            if state.editor_dirty and state.current_entry:
                content = editor_area.text
                state.editor_dirty = False
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, state.storage.save_entry, state.current_entry, content)

    # ── Export pipeline ──────────────────────────────────────────────

    async def run_export(export_format):
        entry = state.current_entry
        if not entry:
            return
        safe_name = entry.name or "export"
        content = editor_area.text
        loop = asyncio.get_running_loop()

        yaml = parse_yaml_frontmatter(content)
        pandoc = detect_pandoc()
        if not pandoc:
            show_notification(state, "Pandoc not found. Install pandoc for export.")
            return

        if export_format == "pdf":
            export_dir = state.storage.pdf_dir
            libreoffice = detect_libreoffice()
            if not libreoffice:
                show_notification(state, "LibreOffice not found for PDF export.")
                return
        else:
            export_dir = state.storage.docx_dir
            libreoffice = None

        ref_doc = resolve_reference_doc(yaml)
        if not ref_doc:
            show_notification(state, "No reference .docx found in refs/ directory.")
            return

        tmp_dir = tempfile.mkdtemp(prefix="journal_export_")
        md_path = Path(tmp_dir) / "source.md"
        lua_path = Path(tmp_dir) / "filter.lua"
        docx_path = export_dir / f"{safe_name}.docx" if export_format == "docx" else Path(tmp_dir) / f"{safe_name}.docx"
        pdf_path = export_dir / f"{safe_name}.pdf"

        try:
            await loop.run_in_executor(None, lambda: md_path.write_text(content))
            lua_code = _generate_lua_filter(yaml)
            await loop.run_in_executor(None, lambda: lua_path.write_text(lua_code))

            pandoc_args = [
                pandoc, str(md_path), "--standalone",
                f"--reference-doc={ref_doc}", f"--lua-filter={lua_path}",
            ]
            if "bibliography" in yaml:
                pandoc_args.append("--citeproc")
            pandoc_args.extend(["-o", str(docx_path)])

            steps = "1/3" if export_format == "pdf" else "1/2"
            show_notification(state, f"Exporting\u2026 ({steps}) Running pandoc", duration=60)
            result = await loop.run_in_executor(
                None, lambda: subprocess.run(
                    pandoc_args, capture_output=True, text=True, timeout=60))
            if result.returncode != 0:
                show_notification(state, "Export failed: pandoc error")
                return

            steps = "2/3" if export_format == "pdf" else "2/2"
            show_notification(state, f"Exporting\u2026 ({steps}) Post-processing", duration=60)
            try:
                await loop.run_in_executor(
                    None, lambda: _postprocess_docx(str(docx_path), yaml))
            except Exception:
                pass

            if export_format == "docx":
                show_notification(state, f"Exported: {docx_path.name}")
                return

            show_notification(state, "Exporting\u2026 (3/3) Converting to PDF", duration=60)
            lo_args = [
                libreoffice, "--headless", "--convert-to", "pdf",
                "--outdir", str(export_dir), str(docx_path),
            ]
            result = await loop.run_in_executor(
                None, lambda: subprocess.run(
                    lo_args, capture_output=True, text=True, timeout=60))
            if result.returncode != 0:
                show_notification(state, "Export failed: LibreOffice error")
                return
            show_notification(state, f"Exported: {pdf_path.name}")

        except subprocess.TimeoutExpired:
            show_notification(state, "Export failed: timed out")
        except Exception as exc:
            show_notification(state, f"Export failed: {str(exc)[:80]}")
        finally:
            # Clean up temp dir
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except OSError:
                pass

    # ── Editor actions ───────────────────────────────────────────────

    def do_save(notify=True):
        if state.current_entry:
            content = editor_area.text
            state.storage.save_entry(state.current_entry, content)
            state.editor_dirty = False
            if notify:
                show_notification(state, "Saved.")

    def return_to_journal():
        do_save(notify=False)
        if state.auto_save_task:
            state.auto_save_task.cancel()
            state.auto_save_task = None
        if state.show_find_panel and state.find_panel:
            state.last_find_query = state.find_panel.search_buf.text
        state.show_find_panel = False
        state.screen = "journal"
        state.current_entry = None
        state.showing_exports = False
        refresh_entries()
        get_app().layout.focus(entry_list.window)
        get_app().invalidate()

    def _word_at_cursor(buf):
        """Return (start, end) of the word at cursor, or None."""
        text = buf.text
        pos = buf.cursor_position
        if not text:
            return None
        def is_word(c):
            return c.isalnum() or c in ("'", "\u2019")
        at = is_word(text[pos]) if pos < len(text) else False
        before = is_word(text[pos - 1]) if pos > 0 else False
        if not at and not before:
            return None
        start = pos
        while start > 0 and is_word(text[start - 1]):
            start -= 1
        if at:
            end = pos
            while end < len(text) and is_word(text[end]):
                end += 1
        else:
            end = pos
        return (start, end) if start < end else None

    def do_bold():
        buf = editor_area.buffer
        if buf.selection_state:
            start = buf.selection_state.original_cursor_position
            end = buf.cursor_position
            if start > end:
                start, end = end, start
            selected = buf.text[start:end]
            new_text = buf.text[:start] + f"**{selected}**" + buf.text[end:]
            buf.set_document(Document(new_text, start + len(selected) + 4), bypass_readonly=True)
            return
        word = _word_at_cursor(buf)
        if word:
            ws, we = word
            text = buf.text
            # Toggle: remove bold if already wrapped
            if ws >= 2 and we + 2 <= len(text) and text[ws-2:ws] == "**" and text[we:we+2] == "**":
                new_text = text[:ws-2] + text[ws:we] + text[we+2:]
                buf.set_document(Document(new_text, ws - 2), bypass_readonly=True)
            else:
                new_text = text[:ws] + f"**{text[ws:we]}**" + text[we:]
                buf.set_document(Document(new_text, we + 4), bypass_readonly=True)
        else:
            pos = buf.cursor_position
            new_text = buf.text[:pos] + "****" + buf.text[pos:]
            buf.set_document(Document(new_text, pos + 2), bypass_readonly=True)

    def do_italic():
        buf = editor_area.buffer
        if buf.selection_state:
            start = buf.selection_state.original_cursor_position
            end = buf.cursor_position
            if start > end:
                start, end = end, start
            selected = buf.text[start:end]
            new_text = buf.text[:start] + f"*{selected}*" + buf.text[end:]
            buf.set_document(Document(new_text, start + len(selected) + 2), bypass_readonly=True)
            return
        word = _word_at_cursor(buf)
        if word:
            ws, we = word
            text = buf.text
            # Toggle: remove italic if wrapped in single * (but not **)
            before_ok = ws >= 1 and text[ws-1] == "*" and (ws < 2 or text[ws-2] != "*")
            after_ok = we < len(text) and text[we] == "*" and (we + 1 >= len(text) or text[we+1] != "*")
            if before_ok and after_ok:
                new_text = text[:ws-1] + text[ws:we] + text[we+1:]
                buf.set_document(Document(new_text, ws - 1), bypass_readonly=True)
            else:
                new_text = text[:ws] + f"*{text[ws:we]}*" + text[we:]
                buf.set_document(Document(new_text, we + 2), bypass_readonly=True)
        else:
            pos = buf.cursor_position
            new_text = buf.text[:pos] + "**" + buf.text[pos:]
            buf.set_document(Document(new_text, pos + 1), bypass_readonly=True)

    def do_footnote():
        buf = editor_area.buffer
        pos = buf.cursor_position
        new_text = buf.text[:pos] + "^[]" + buf.text[pos:]
        buf.set_document(Document(new_text, pos + 2), bypass_readonly=True)

    def do_insert_frontmatter():
        text = editor_area.text
        m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if m:
            existing = set()
            for line in m.group(1).split("\n"):
                idx = line.find(":")
                if idx > 0:
                    existing.add(line[:idx].strip())
            missing = [p for p in _FRONTMATTER_PROPS if p not in existing]
            if not missing:
                show_notification(state, "All frontmatter properties already present.")
                return
            new_lines = "\n".join(f"{p}: " for p in missing)
            end_pos = m.end(1)
            new_text = text[:end_pos] + "\n" + new_lines + text[end_pos:]
        else:
            block = "\n".join(f"{p}: " for p in _FRONTMATTER_PROPS)
            new_text = f"---\n{block}\n---\n" + text
        editor_area.buffer.set_document(Document(new_text, 0), bypass_readonly=True)
        show_notification(state, "Frontmatter inserted.")

    # ── Get commands for palette ─────────────────────────────────────

    def toggle_keybindings():
        state.show_keybindings = not state.show_keybindings
        get_app().invalidate()

    def toggle_exports():
        state.showing_exports = not state.showing_exports
        if state.showing_exports:
            refresh_exports()
            get_app().layout.focus(export_list.window)
        else:
            get_app().layout.focus(entry_list.window)
        get_app().invalidate()

    # ── Key bindings ─────────────────────────────────────────────────

    kb = KeyBindings()

    is_journal = Condition(lambda: state.screen == "journal")
    is_editor = Condition(lambda: state.screen == "editor")
    no_float = Condition(lambda: len(state.root_container.floats) == 0)
    find_panel_open = Condition(
        lambda: state.show_find_panel and state.find_panel is not None)
    search_not_focused = Condition(
        lambda: get_app().layout.current_window != entry_search.window)
    entry_list_focused = is_journal & no_float & search_not_focused

    # -- Global --
    @kb.add("escape", eager=True)
    def _(event):
        if state.root_container.floats:
            dialog = state.root_container.floats[-1].content
            if hasattr(dialog, 'cancel'):
                dialog.cancel()
            elif hasattr(dialog, 'future') and not dialog.future.done():
                dialog.future.set_result(None)
        elif state.screen == "editor":
            if state.show_find_panel and state.find_panel and state.find_panel.is_focused():
                state.last_find_query = state.find_panel.search_buf.text
                state.show_find_panel = False
                event.app.layout.focus(editor_area)
                event.app.invalidate()
                return
            now = time.monotonic()
            if now - state.escape_pending < 2.0:
                state.escape_pending = 0.0
                return_to_journal()
            else:
                state.escape_pending = now
                show_notification(state,
                                  "Press Esc again to return to journal.",
                                  duration=2.0)
        elif state.screen == "journal":
            if state.showing_exports:
                toggle_exports()
            else:
                event.app.layout.focus(entry_search.window)

    @kb.add("c-q")
    def _(event):
        if state.root_container.floats:
            return
        now = time.monotonic()
        if now - state.quit_pending < 2.0:
            event.app.exit()
        else:
            state.quit_pending = now
            show_notification(state, "Press Ctrl+Q again to quit.", duration=2.0)

    # -- Journal screen --
    @kb.add("n", filter=entry_list_focused)
    def _(event):
        if state.showing_exports:
            return

        async def _do():
            dlg = InputDialog(title="New Entry", label_text="Name:",
                              ok_text="Create")
            name = await show_dialog_as_float(state, dlg)
            if name:
                entry = state.storage.create_entry(name)
                open_entry(str(entry.path))

        asyncio.ensure_future(_do())

    @kb.add("r", filter=entry_list_focused)
    def _(event):
        if state.showing_exports:
            return
        filtered = fuzzy_filter_entries(state.entries, entry_search.text)
        idx = entry_list.selected_index
        if idx >= len(filtered):
            return
        entry = filtered[idx]

        async def _do():
            dlg = InputDialog(title="Rename", label_text="New name:",
                              initial="", ok_text="Rename")
            new_name = await show_dialog_as_float(state, dlg)
            if new_name:
                state.storage.rename_entry(entry, new_name)
                refresh_entries(entry_search.text)
                show_notification(state, f"Renamed to '{new_name}'.")

        asyncio.ensure_future(_do())

    @kb.add("d", filter=entry_list_focused)
    def _(event):
        if state.showing_exports:
            idx = export_list.selected_index
            if idx >= len(state.export_paths):
                return
            path = state.export_paths[idx]

            async def _do():
                dlg = ConfirmDialog(f"Delete '{path.name}'?")
                ok = await show_dialog_as_float(state, dlg)
                if ok:
                    try:
                        path.unlink()
                    except OSError:
                        pass
                    refresh_exports()
                    show_notification(state, "Export deleted.")

            asyncio.ensure_future(_do())
            return
        filtered = fuzzy_filter_entries(state.entries, entry_search.text)
        idx = entry_list.selected_index
        if idx >= len(filtered):
            return
        entry = filtered[idx]

        async def _do():
            dlg = ConfirmDialog(f"Delete '{entry.name}'?")
            ok = await show_dialog_as_float(state, dlg)
            if ok:
                state.storage.delete_entry(entry)
                refresh_entries(entry_search.text)
                show_notification(state, "Entry deleted.")

        asyncio.ensure_future(_do())

    @kb.add("e", filter=entry_list_focused)
    def _(event):
        toggle_exports()

    @kb.add("j", filter=entry_list_focused)
    def _(event):
        if state.showing_exports:
            toggle_exports()

    @kb.add("/", filter=entry_list_focused)
    def _(event):
        event.app.layout.focus(entry_search.window)

    search_focused = Condition(
        lambda: state.screen == "journal"
        and len(state.root_container.floats) == 0
        and get_app().layout.current_window == entry_search.window)

    @kb.add("down", filter=search_focused)
    def _(event):
        if state.showing_exports:
            event.app.layout.focus(export_list.window)
        else:
            event.app.layout.focus(entry_list.window)

    @kb.add("enter", filter=search_focused)
    def _(event):
        filtered = fuzzy_filter_entries(state.entries, entry_search.text)
        if filtered:
            open_entry(str(filtered[0].path))

    @kb.add("c-s", filter=is_journal & no_float)
    def _(event):
        now = time.monotonic()
        if now - state.shutdown_pending < 2.0:
            subprocess.Popen(['sudo', 'shutdown', 'now'])
            event.app.exit()
        else:
            state.shutdown_pending = now
            show_notification(state, "Press Ctrl+S again to shut down.", duration=2.0)

    # -- Editor screen --
    @kb.add("c-s", filter=is_editor & no_float)
    def _(event):
        do_save()

    @kb.add("c-z", filter=is_editor & no_float)
    def _(event):
        editor_area.buffer.undo()

    @kb.add("c-y", filter=is_editor & no_float)
    def _(event):
        editor_area.buffer.redo()

    @kb.add("c-b", filter=is_editor & no_float)
    def _(event):
        do_bold()

    @kb.add("c-i", filter=is_editor & no_float)
    def _(event):
        do_italic()

    @kb.add("c-n", filter=is_editor & no_float)
    def _(event):
        do_footnote()

    @kb.add("c-w", filter=is_editor & no_float)
    def _(event):
        state.show_word_count = not state.show_word_count
        get_app().invalidate()

    @kb.add("c-g", filter=is_editor & no_float)
    def _(event):
        toggle_keybindings()

    @kb.add("c-f", filter=is_editor & no_float)
    def _(event):
        if state.show_find_panel and state.find_panel:
            if state.find_panel.is_focused():
                # Panel focused -> switch to editor
                state.last_find_query = state.find_panel.search_buf.text
                event.app.layout.focus(editor_area)
            else:
                # Editor focused -> switch to panel
                event.app.layout.focus(state.find_panel.search_window)
        else:
            # Open the panel
            panel = FindReplacePanel(
                editor_area.buffer, state, state.last_find_query,
                editor_area=editor_area)
            state.find_panel = panel
            state.show_find_panel = True
            event.app.invalidate()
            try:
                event.app.layout.focus(panel.search_window)
            except ValueError:
                pass

    @kb.add("c-k", filter=is_editor & no_float & find_panel_open)
    def _(event):
        state.find_panel._rebuild_matches()
        state.find_panel._move(1)

    @kb.add("c-j", filter=is_editor & no_float & find_panel_open)
    def _(event):
        state.find_panel._rebuild_matches()
        state.find_panel._move(-1)

    @kb.add("c-r", filter=is_editor & no_float)
    def _(event):
        _refresh_bib_cache()
        if not state.bib_entries:
            if state.bib_error == "no_file":
                show_notification(state, f"No .bib file found in {storage.vault_dir}")
            elif state.bib_error == "no_entries":
                show_notification(state, f"0 entries parsed from {state.bib_path}")
            elif state.bib_error:
                show_notification(state, f".bib error: {state.bib_error[:60]}")
            else:
                show_notification(state, "No .bib file found.")
            return

        async def _do():
            dlg = CitePickerDialog(state.bib_entries)
            citekey = await show_dialog_as_float(state, dlg)
            if citekey:
                editor_area.buffer.insert_text(citekey)

        asyncio.ensure_future(_do())

    @kb.add("c-p", filter=no_float)
    def _(event):
        async def _do_full():
            if state.screen == "editor":

                async def cmd_export():
                    dlg = ExportFormatDialog()
                    fmt = await show_dialog_as_float(state, dlg)
                    if fmt:
                        await run_export(fmt)

                async def cmd_cite():
                    _refresh_bib_cache()
                    if not state.bib_entries:
                        if state.bib_error == "no_file":
                            show_notification(state, f"No .bib file found in {storage.vault_dir}")
                        elif state.bib_error:
                            show_notification(state, f".bib error: {state.bib_error[:60]}")
                        else:
                            show_notification(state, "No .bib file found.")
                        return
                    dlg = CitePickerDialog(state.bib_entries)
                    ck = await show_dialog_as_float(state, dlg)
                    if ck:
                        editor_area.buffer.insert_text(ck)

                def cmd_find():
                    if not state.show_find_panel or not state.find_panel:
                        panel = FindReplacePanel(
                            editor_area.buffer, state, state.last_find_query,
                            editor_area=editor_area)
                        state.find_panel = panel
                        state.show_find_panel = True
                    get_app().invalidate()
                    try:
                        get_app().layout.focus(state.find_panel.search_window)
                    except ValueError:
                        pass

                cmds = [
                    ("Export", "Export document", cmd_export),
                    ("Find", "^F", cmd_find),
                    ("Insert blank footnote", "^N", do_footnote),
                    ("Insert frontmatter", "YAML frontmatter", do_insert_frontmatter),
                    ("Insert citation", "^R", cmd_cite),
                    ("Keybindings", "^G", toggle_keybindings),
                    ("Return to journal", "Esc", return_to_journal),
                    ("Save", "^S", lambda: do_save()),
                ]
            else:
                cmds = [
                    ("Exports", "Toggle exports", toggle_exports),
                    ("New entry", "Create new", None),
                    ("Quit", "Exit app", None),
                ]
            dlg = CommandPaletteDialog(cmds)
            action = await show_dialog_as_float(state, dlg)
            if action is not None:
                if asyncio.iscoroutinefunction(action):
                    await action()
                elif callable(action):
                    action()

        asyncio.ensure_future(_do_full())

    # ── Visual-line cursor movement ─────────────────────────────────

    def _editor_width():
        ri = editor_area.window.render_info
        return ri.window_width if ri else 60

    @kb.add("up", filter=is_editor & no_float)
    def _(event):
        buf = editor_area.buffer
        doc = buf.document
        row, col = doc.cursor_position_row, doc.cursor_position_col
        width = _editor_width()
        line = doc.lines[row]
        starts, _ = _word_wrap_boundaries(line, width)
        # Find which visual line the cursor is on.
        vline = 0
        for idx, s in enumerate(starts):
            if col >= s:
                vline = idx
        visual_col = col - starts[vline]
        if vline > 0:
            # Move up within the same paragraph.
            prev_start = starts[vline - 1]
            prev_end = starts[vline] - 1
            new_col = min(prev_start + visual_col, prev_end)
            buf.cursor_position = doc.translate_row_col_to_index(row, new_col)
        elif row > 0:
            # Move to last visual line of previous paragraph.
            prev_line = doc.lines[row - 1]
            prev_starts, _ = _word_wrap_boundaries(prev_line, width)
            last_start = prev_starts[-1]
            new_col = min(last_start + visual_col, len(prev_line))
            buf.cursor_position = doc.translate_row_col_to_index(row - 1, new_col)

    @kb.add("down", filter=is_editor & no_float)
    def _(event):
        buf = editor_area.buffer
        doc = buf.document
        row, col = doc.cursor_position_row, doc.cursor_position_col
        width = _editor_width()
        line = doc.lines[row]
        starts, _ = _word_wrap_boundaries(line, width)
        vline = 0
        for idx, s in enumerate(starts):
            if col >= s:
                vline = idx
        visual_col = col - starts[vline]
        if vline < len(starts) - 1:
            # Move down within the same paragraph.
            next_start = starts[vline + 1]
            next_end = starts[vline + 2] - 1 if vline + 2 < len(starts) else len(line)
            new_col = min(next_start + visual_col, next_end)
            buf.cursor_position = doc.translate_row_col_to_index(row, new_col)
        elif row < doc.line_count - 1:
            # Move to first visual line of next paragraph.
            next_line = doc.lines[row + 1]
            next_starts, _ = _word_wrap_boundaries(next_line, width)
            first_end = next_starts[1] - 1 if len(next_starts) > 1 else len(next_line)
            new_col = min(visual_col, first_end)
            buf.cursor_position = doc.translate_row_col_to_index(row + 1, new_col)

    @kb.add("c-up", filter=is_editor & no_float)
    def _(event):
        editor_area.buffer.cursor_position = 0

    @kb.add("c-down", filter=is_editor & no_float)
    def _(event):
        editor_area.buffer.cursor_position = len(editor_area.text)

    @kb.add("left", filter=is_editor & no_float)
    def _(event):
        buf = editor_area.buffer
        if buf.cursor_position > 0:
            buf.cursor_position -= 1

    @kb.add("right", filter=is_editor & no_float)
    def _(event):
        buf = editor_area.buffer
        if buf.cursor_position < len(buf.text):
            buf.cursor_position += 1

    # ── Style ────────────────────────────────────────────────────────

    style = PtStyle.from_dict({
        "": "#e0e0e0 bg:#2a2a2a",
        "title": "#e0e0e0",
        "status": "#8a8a8a bg:#333333",
        "hint": "#777777",
        "accent": "#e0af68",
        "input": "bg:#333333 #e0e0e0",
        "editor": "",
        "select-list": "",
        "select-list.selected": "bg:#444444",
        "select-list.empty": "#777777",
        "keybindings-panel": "bg:#2a2a2a",
        "find-panel": "bg:#2a2a2a",
        "form-label": "#aaaaaa",
        "dialog": "#e0e0e0 bg:#2a2a2a",
        "dialog.body": "#e0e0e0 bg:#2a2a2a",
        "dialog text-area": "#e0e0e0 bg:#333333",
        "dialog frame.label": "#e0e0e0 bold",
        "dialog shadow": "bg:#111111",
        "button": "#e0e0e0 bg:#555555",
        "button.focused": "#e0e0e0 bg:#777777",
        "label": "#e0e0e0",
        # Markdown inline styles
        "md.heading-marker": "#666666",
        "md.heading": "bold #e0af68",
        "md.bold": "bold",
        "md.italic": "italic",
        "md.code": "#a0a0a0",
        "md.footnote": "#7aa2f7",
        "md.link": "#7aa2f7",
    })

    # ── Build Application ────────────────────────────────────────────

    layout = Layout(root, focused_element=entry_list.window)

    app = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=True,
        mouse_support=False,
    )
    app.ttimeoutlen = 0.05

    return app


# ════════════════════════════════════════════════════════════════════════
#  Entry point
# ════════════════════════════════════════════════════════════════════════


def main() -> None:
    if os.environ.get("JOURNAL_VAULT"):
        data_dir = Path(os.environ["JOURNAL_VAULT"])
    else:
        data_dir = Path.home() / "Documents"

    app = create_app(VaultStorage(data_dir))
    app.run()


if __name__ == "__main__":
    main()
