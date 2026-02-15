#!/usr/bin/env python3
"""
Manuscripts — A writing appliance for students.

A Markdown editor with integrated source management, Chicago citation
insertion, and PDF export.  Built on prompt_toolkit.

Designed for write-decks running on Raspberry Pi, but works anywhere
Python 3.9+ and a modern terminal are available.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass, field
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
class Source:
    """A bibliographic source with simplified metadata."""

    id: str
    source_type: str  # book | book_section | article | website
    author: str
    title: str
    year: str
    # Book
    publisher: str = ""
    city: str = ""
    # Article
    journal: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    # Book section (chapter in edited book)
    book_title: str = ""
    editor: str = ""
    # Website
    url: str = ""
    access_date: str = ""
    site_name: str = ""

    # ── Citation formatting (Chicago / Turabian) ──────────────────────

    def to_citekey(self) -> str:
        last = self.author.split(",")[0].split()[-1].lower() if self.author else "unknown"
        return re.sub(r"[^a-z]", "", last) + self.year

    def to_chicago_footnote(self, page: str = "") -> str:
        a = self._author_first()
        if self.source_type == "book":
            c = f"{a}, *{self.title}*"
            if self.publisher:
                c += f" ({self.publisher}, {self.year})"
            elif self.year:
                c += f" ({self.year})"
            if page:
                c += f", {page}"
            return c + "."
        if self.source_type == "article":
            c = f'{a}, "{self.title}," *{self.journal}*'
            if self.volume:
                c += f" {self.volume}"
                if self.issue:
                    c += f", no. {self.issue}"
            if self.year:
                c += f" ({self.year})"
            if self.pages:
                c += f": {self.pages}"
            elif page:
                c += f": {page}"
            return c + "."
        if self.source_type == "book_section":
            c = f'{a}, "{self.title}," in *{self.book_title}*'
            if self.editor:
                c += f", ed. {self.editor}"
            if self.publisher:
                c += f" ({self.publisher}, {self.year})"
            elif self.year:
                c += f" ({self.year})"
            if self.pages:
                c += f", {self.pages}"
            elif page:
                c += f", {page}"
            return c + "."
        if self.source_type == "website":
            c = f'{a}, "{self.title},"'
            if self.site_name:
                c += f" *{self.site_name}*,"
            if self.access_date:
                c += f" accessed {self.access_date},"
            if self.url:
                c += f" {self.url}"
            return c.rstrip(",") + "."
        return f"{a}, *{self.title}* ({self.year})."

    def to_chicago_bibliography(self) -> str:
        a = self._author_last()
        if self.source_type == "book":
            c = f"{a}. *{self.title}*."
            if self.publisher:
                c += f" {self.publisher}, {self.year}."
            elif self.year:
                c += f" {self.year}."
            return c
        if self.source_type == "article":
            c = f'{a}. "{self.title}." *{self.journal}*'
            if self.volume:
                c += f" {self.volume}"
                if self.issue:
                    c += f", no. {self.issue}"
            if self.year:
                c += f" ({self.year})"
            if self.pages:
                c += f": {self.pages}"
            return c + "."
        if self.source_type == "book_section":
            c = f'{a}. "{self.title}." In *{self.book_title}*'
            if self.editor:
                c += f", edited by {self.editor}"
            if self.pages:
                c += f", {self.pages}"
            c += "."
            if self.publisher:
                c += f" {self.publisher}, {self.year}."
            elif self.year:
                c += f" {self.year}."
            return c
        if self.source_type == "website":
            c = f'{a}. "{self.title}."'
            if self.site_name:
                c += f" *{self.site_name}*."
            if self.access_date:
                c += f" Accessed {self.access_date}."
            if self.url:
                c += f" {self.url}."
            return c
        return f"{a}. *{self.title}*. {self.year}."

    # ── helpers ────────────────────────────────────────────────────────

    def _author_first(self) -> str:
        """First Last (for footnotes)."""
        if not self.author:
            return ""
        if "," in self.author:
            last, first = self.author.split(",", 1)
            return f"{first.strip()} {last.strip()}"
        return self.author

    def _author_last(self) -> str:
        """Last, First (for bibliography)."""
        if not self.author:
            return ""
        if "," not in self.author:
            parts = self.author.rsplit(" ", 1)
            if len(parts) == 2:
                return f"{parts[1]}, {parts[0]}"
        return self.author


@dataclass
class Project:
    """A writing project."""

    id: str
    name: str
    created: str
    modified: str
    content: str = ""
    sources: list = field(default_factory=list)

    def get_sources(self) -> list[Source]:
        out: list[Source] = []
        for s in self.sources:
            try:
                out.append(Source(**s))
            except TypeError:
                continue
        return out

    def add_source(self, source: Source) -> None:
        self.sources.append(asdict(source))

    def remove_source(self, source_id: str) -> None:
        self.sources = [s for s in self.sources if s.get("id") != source_id]


# ════════════════════════════════════════════════════════════════════════
#  Storage
# ════════════════════════════════════════════════════════════════════════


class Storage:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.projects_dir = base / "projects"
        self.exports_dir = base / "exports"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)

    def list_projects(self) -> list[Project]:
        projects: list[Project] = []
        for p in self.projects_dir.glob("*.json"):
            try:
                with open(p) as f:
                    projects.append(Project(**json.load(f)))
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
        return sorted(projects, key=lambda x: x.modified, reverse=True)

    def save_project(self, project: Project) -> None:
        project.modified = datetime.now().isoformat()
        with open(self.projects_dir / f"{project.id}.json", "w") as f:
            json.dump(asdict(project), f, indent=2)

    def load_project(self, pid: str) -> Optional[Project]:
        path = self.projects_dir / f"{pid}.json"
        if path.exists():
            with open(path) as f:
                return Project(**json.load(f))
        return None

    def delete_project(self, pid: str) -> None:
        path = self.projects_dir / f"{pid}.json"
        if path.exists():
            path.unlink()

    def create_project(self, name: str) -> Project:
        pid = datetime.now().strftime("%Y%m%d_%H%M%S")
        now = datetime.now().isoformat()
        p = Project(id=pid, name=name, created=now, modified=now)
        self.save_project(p)
        return p


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


def fuzzy_filter(sources: list[Source], query: str) -> list[Source]:
    if not query:
        return list(sources)
    q = query.lower()
    scored: list[tuple[float, Source]] = []
    for s in sources:
        hay = f"{s.author} {s.title} {s.year}".lower()
        if q in hay:
            scored.append((100.0, s))
        else:
            ratio = SequenceMatcher(None, q, hay).ratio() * 100
            if ratio > 30:
                scored.append((ratio, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored]


def fuzzy_filter_projects(projects: list[Project], query: str) -> list[Project]:
    if not query:
        return list(projects)
    q = query.lower()
    scored: list[tuple[float, Project]] = []
    for p in projects:
        hay = p.name.lower()
        if q in hay:
            scored.append((100.0, p))
        else:
            ratio = SequenceMatcher(None, q, hay).ratio() * 100
            if ratio > 70:
                scored.append((ratio, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored]


# ════════════════════════════════════════════════════════════════════════
#  Source‑type field definitions
# ════════════════════════════════════════════════════════════════════════

SOURCE_TYPES = ["book", "book_section", "article", "website"]

SOURCE_FIELDS: dict[str, list[tuple[str, str]]] = {
    "book": [
        ("author", "Author (Last, First)"),
        ("title", "Title"),
        ("year", "Year"),
        ("publisher", "Publisher"),
    ],
    "book_section": [
        ("author", "Author (Last, First)"),
        ("title", "Chapter Title"),
        ("book_title", "Book Title"),
        ("editor", "Editor"),
        ("year", "Year"),
        ("publisher", "Publisher"),
        ("pages", "Pages"),
    ],
    "article": [
        ("author", "Author (Last, First)"),
        ("title", "Title"),
        ("year", "Year"),
        ("journal", "Journal"),
        ("volume", "Volume"),
        ("issue", "Issue"),
        ("pages", "Pages"),
    ],
    "website": [
        ("author", "Author (Last, First)"),
        ("title", "Title"),
        ("year", "Year"),
        ("site_name", "Website Name"),
        ("url", "URL"),
        ("access_date", "Access Date"),
    ],
}


def parse_bibtex(text: str) -> list[Source]:
    """Parse BibTeX entries into Source objects.

    Handles @book{...}, @article{...}, @misc{...}, @online{...}, etc.
    """
    sources: list[Source] = []
    # Match @type{key, ... } entries (greedy within braces, balanced)
    entry_re = re.compile(r"@(\w+)\s*\{([^,]*),\s*(.*?)\}\s*(?=@|\Z)", re.DOTALL)
    field_re = re.compile(r"(\w+)\s*=\s*[{\"](.*?)[}\"]", re.DOTALL)

    type_map = {
        "book": "book",
        "inbook": "book_section",
        "incollection": "book_section",
        "article": "article",
        "inproceedings": "article",
        "conference": "article",
        "misc": "website",
        "online": "website",
        "electronic": "website",
    }

    for m in entry_re.finditer(text):
        bib_type = m.group(1).lower()
        body = m.group(3)
        fields: dict[str, str] = {}
        for fm in field_re.finditer(body):
            fields[fm.group(1).lower()] = fm.group(2).strip()

        stype = type_map.get(bib_type, "book")
        author = fields.get("author", "")
        title = fields.get("title", "")
        if not author and not title:
            continue

        sources.append(Source(
            id=datetime.now().strftime("%Y%m%d_%H%M%S_%f") + f"_{len(sources)}",
            source_type=stype,
            author=author,
            title=title,
            year=fields.get("year", ""),
            publisher=fields.get("publisher", ""),
            city=fields.get("address", ""),
            journal=fields.get("journal", fields.get("journaltitle", "")),
            volume=fields.get("volume", ""),
            issue=fields.get("number", ""),
            pages=fields.get("pages", ""),
            book_title=fields.get("booktitle", ""),
            editor=fields.get("editor", ""),
            url=fields.get("url", ""),
            access_date=fields.get("urldate", ""),
            site_name=fields.get("organization", fields.get("howpublished", "")),
        ))
    return sources


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
            s = "class:select-list.selected" if i == self.selected_index else ""
            result.append((s, f"  {label}\n"))
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
        self.projects = storage.list_projects()
        self.current_project = None
        self.screen = "projects"
        self.notification = ""
        self.notification_task = None
        self.quit_pending = 0.0
        self.escape_pending = 0.0
        self.mass_export_pending = 0.0
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
    """Text input dialog (new project, rename)."""

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
    """Pick export format: PDF, DOCX, or Markdown."""

    def __init__(self):
        self.future = asyncio.Future()
        self.list = SelectableList(on_select=self._select)
        self.list.set_items([
            ("pdf", "PDF (.pdf)"),
            ("docx", "Word (.docx)"),
            ("md", "Markdown (.md)"),
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
    """Fuzzy-search sources and pick one to insert as a footnote."""

    def __init__(self, sources):
        self.future = asyncio.Future()
        self.all_sources = sources
        self.filtered = list(sources)
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
                s = self.filtered[idx]
                if not self.future.done():
                    self.future.set_result(s.to_chicago_footnote())

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
        self.filtered = fuzzy_filter(self.all_sources, query)
        items = [(s.id, f"{s.author} ({s.year}) \u2014 {s.title}")
                 for s in self.filtered]
        self.results.set_items(items)
        self.results.selected_index = 0

    def _on_select(self, source_id):
        for s in self.filtered:
            if s.id == source_id:
                if not self.future.done():
                    self.future.set_result(s.to_chicago_footnote())
                return

    def cancel(self):
        if not self.future.done():
            self.future.set_result(None)

    def __pt_container__(self):
        return self.dialog


class SourceFormDialog:
    """Form for adding a new source with type selector and fields."""

    def __init__(self):
        self.future = asyncio.Future()
        self.current_type = ""
        self.field_inputs = {}
        for stype in SOURCE_TYPES:
            for field_key, label in SOURCE_FIELDS[stype]:
                self.field_inputs[(stype, field_key)] = Buffer(multiline=False)

        self._field_containers = {}
        for stype in SOURCE_TYPES:
            rows = []
            for field_key, label in SOURCE_FIELDS[stype]:
                buf = self.field_inputs[(stype, field_key)]
                rows.append(VSplit([
                    Window(
                        FormattedTextControl([("class:form-label", f" {label}: ")]),
                        width=22, height=1, dont_extend_height=True,
                    ),
                    Window(
                        BufferControl(buffer=buf), height=1,
                        style="class:input", dont_extend_height=True,
                    ),
                ], height=1))
            self._field_containers[stype] = HSplit(rows)

        type_kb = KeyBindings()
        shortcuts = {"b": "book", "s": "book_section",
                     "a": "article", "w": "website"}
        for key, stype in shortcuts.items():
            def _make_handler(st):
                def handler(event):
                    self._switch_type(st, event.app)
                return handler
            type_kb.add(key)(_make_handler(stype))

        self.type_control = FormattedTextControl(
            self._get_type_text, focusable=True, key_bindings=type_kb,
        )
        self.type_window = Window(
            content=self.type_control, height=1, dont_extend_height=True,
        )
        self._body_container = DynamicContainer(self._get_fields_container)

        def do_save():
            self._do_save()

        self.dialog = Dialog(
            title="Add Source",
            body=HSplit([
                self.type_window,
                self._body_container,
            ]),
            buttons=[
                Button(text="Save", handler=do_save),
                Button(text="(c) Cancel", handler=self.cancel),
            ],
            modal=True,
            width=D(preferred=80, max=100),
        )

    def _get_type_text(self):
        labels = {"book": "(b) Book", "book_section": "(s) Section",
                  "article": "(a) Article", "website": "(w) Website"}
        result = []
        for stype, label in labels.items():
            if stype == self.current_type:
                result.append(("class:accent bold", f" [{label}] "))
            else:
                result.append(("", f" {label} "))
        if not self.current_type:
            result.append(("class:hint", "  \u2190 Choose a type"))
        return result

    def _get_fields_container(self):
        if self.current_type and self.current_type in self._field_containers:
            return self._field_containers[self.current_type]
        return Window(FormattedTextControl(
            [("class:hint", "  Select a source type above.")]), height=2)

    def _switch_type(self, stype, app=None):
        self.current_type = stype
        if app:
            app.invalidate()

            async def _focus_later():
                await asyncio.sleep(0)
                try:
                    first_key = SOURCE_FIELDS[stype][0][0]
                    buf = self.field_inputs[(stype, first_key)]
                    for w in app.layout.find_all_windows():
                        c = w.content
                        if isinstance(c, BufferControl) and c.buffer is buf:
                            app.layout.focus(w)
                            break
                except (ValueError, KeyError):
                    pass

            asyncio.ensure_future(_focus_later())

    def _do_save(self):
        if not self.current_type:
            return
        data = {}
        for field_key, _ in SOURCE_FIELDS[self.current_type]:
            buf = self.field_inputs.get((self.current_type, field_key))
            data[field_key] = buf.text.strip() if buf else ""
        if not data.get("author") or not data.get("title"):
            return
        source = Source(
            id=datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
            source_type=self.current_type,
            author=data.get("author", ""),
            title=data.get("title", ""),
            year=data.get("year", ""),
            publisher=data.get("publisher", ""),
            city=data.get("city", ""),
            journal=data.get("journal", ""),
            volume=data.get("volume", ""),
            issue=data.get("issue", ""),
            pages=data.get("pages", ""),
            book_title=data.get("book_title", ""),
            editor=data.get("editor", ""),
            url=data.get("url", ""),
            access_date=data.get("access_date", ""),
            site_name=data.get("site_name", ""),
        )
        if not self.future.done():
            self.future.set_result(source)

    def cancel(self):
        if not self.future.done():
            self.future.set_result(None)

    def __pt_container__(self):
        return self.dialog


class SourcesDialog:
    """View / add / delete sources for a project."""

    def __init__(self, state, project):
        self.future = asyncio.Future()
        self.state = state
        self.project = project
        self._delete_pending = 0.0
        self.source_list = SelectableList()
        self._refresh_list()

        action_kb = KeyBindings()

        @action_kb.add("a")
        def _add(event):
            async def _do():
                dlg = SourceFormDialog()
                source = await show_dialog_as_float(state, dlg)
                if source:
                    self.project.add_source(source)
                    state.storage.save_project(self.project)
                    self._refresh_list()
                    show_notification(state, f"Added: {source.author}")
                get_app().layout.focus(self.source_list.window)
            asyncio.ensure_future(_do())

        @action_kb.add("i")
        def _import(event):
            async def _do():
                other = [p for p in state.storage.list_projects()
                         if p.id != self.project.id]
                if not other:
                    show_notification(state, "No other manuscripts to import from.")
                    return
                dlg = ImportSourcesDialog(other)
                sources = await show_dialog_as_float(state, dlg)
                if sources:
                    existing = self.project.get_sources()
                    existing_keys = {(s.author, s.title, s.year) for s in existing}
                    added = 0
                    for s in sources:
                        if (s.author, s.title, s.year) not in existing_keys:
                            s.id = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + f"_{added}"
                            self.project.add_source(s)
                            existing_keys.add((s.author, s.title, s.year))
                            added += 1
                    state.storage.save_project(self.project)
                    self._refresh_list()
                    skipped = len(sources) - added
                    msg = f"Imported {added} source(s)."
                    if skipped:
                        msg += f" {skipped} duplicate(s) skipped."
                    show_notification(state, msg)
                get_app().layout.focus(self.source_list.window)
            asyncio.ensure_future(_do())

        @action_kb.add("d")
        def _delete(event):
            sources = self.project.get_sources()
            idx = self.source_list.selected_index
            if idx >= len(sources):
                return
            now = time.monotonic()
            if now - self._delete_pending < 2.0:
                self._delete_pending = 0.0
                s = sources[idx]
                self.project.remove_source(s.id)
                self.state.storage.save_project(self.project)
                self._refresh_list()
                show_notification(self.state, "Source deleted.")
            else:
                self._delete_pending = now
                show_notification(self.state, "Press d again to confirm delete.", duration=2.0)

        def close():
            if not self.future.done():
                self.future.set_result(None)

        @action_kb.add("c")
        def _close(event):
            close()

        @action_kb.add("escape", eager=True)
        def _escape(event):
            close()

        self.source_list.control.key_bindings = KeyBindings()
        for b in self.source_list._kb.bindings:
            self.source_list.control.key_bindings.bindings.append(b)
        for b in action_kb.bindings:
            self.source_list.control.key_bindings.bindings.append(b)

        self.dialog = Dialog(
            title=f"Sources: {project.name}",
            body=HSplit([self.source_list]),
            buttons=[
                Button(text="(a) Add", handler=lambda: _add(None)),
                Button(text="(i) Import", handler=lambda: _import(None)),
                Button(text="(c) Close", handler=close),
            ],
            modal=True,
            width=D(preferred=80, max=100),
        )

    def _refresh_list(self):
        sources = self.project.get_sources()
        if not sources:
            self.source_list.set_items([
                ("__empty__", "No sources yet \u2014 press a to add one.")])
        else:
            self.source_list.set_items([
                (s.id, f"{s.author} ({s.year}) \u2014 {s.title}")
                for s in sources
            ])

    def cancel(self):
        if not self.future.done():
            self.future.set_result(None)

    def __pt_container__(self):
        return self.dialog


class BibImportDialog:
    """Modal to import a .bib file."""

    def __init__(self):
        self.future = asyncio.Future()
        self.text_area = TextArea(
            text="", multiline=False, width=D(preferred=60),
        )

        def do_import(_buf=None):
            path_str = self.text_area.text.strip()
            if not path_str:
                return
            p = Path(path_str).expanduser()
            if not p.exists():
                return
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                return
            sources = parse_bibtex(text)
            if sources and not self.future.done():
                self.future.set_result(sources)

        self.text_area.buffer.accept_handler = do_import
        self.dialog = Dialog(
            title="Import .bib File",
            body=HSplit([
                Label(text="Path to .bib file:"),
                self.text_area,
            ]),
            buttons=[
                Button(text="Import", handler=do_import),
                Button(text="(c) Cancel", handler=self.cancel),
            ],
            modal=True,
        )

    def cancel(self):
        if not self.future.done():
            self.future.set_result(None)

    def __pt_container__(self):
        return self.dialog


class ImportSourcesDialog:
    """Two-phase: pick a project, then pick sources to import."""

    def __init__(self, projects):
        self.future = asyncio.Future()
        self._projects = projects
        self._phase = "projects"
        self._selected_project = None
        self._sources = []
        self.list = SelectableList(on_select=self._on_select)
        self._show_projects()

        action_kb = KeyBindings()

        @action_kb.add("a")
        def _import_all(event):
            if self._phase == "sources" and self._sources:
                if not self.future.done():
                    self.future.set_result(list(self._sources))

        self.list.control.key_bindings = KeyBindings()
        for b in self.list._kb.bindings:
            self.list.control.key_bindings.bindings.append(b)
        for b in action_kb.bindings:
            self.list.control.key_bindings.bindings.append(b)

        def import_all_btn():
            if self._phase == "sources" and self._sources:
                if not self.future.done():
                    self.future.set_result(list(self._sources))

        self.dialog = Dialog(
            title="Select a manuscript",
            body=HSplit([self.list]),
            buttons=[
                Button(text="(a) Import All", handler=import_all_btn),
                Button(text="Back", handler=self._go_back),
            ],
            modal=True,
            width=D(preferred=70, max=90),
        )

    def _show_projects(self):
        self._phase = "projects"
        self._sources = []
        self.list.set_items([
            (p.id, f"{p.name}  ({len(p.get_sources())} sources)")
            for p in self._projects
        ])
        self.list.selected_index = 0

    def _show_sources(self, project):
        self._phase = "sources"
        self._selected_project = project
        self._sources = project.get_sources()
        if not self._sources:
            self.list.set_items([("__empty__", "No sources in this manuscript.")])
        else:
            self.list.set_items([
                (s.id, f"{s.author} ({s.year}) \u2014 {s.title}")
                for s in self._sources
            ])
        self.list.selected_index = 0

    def _on_select(self, item_id):
        if item_id == "__empty__":
            return
        if self._phase == "projects":
            for p in self._projects:
                if p.id == item_id:
                    self._show_sources(p)
                    return
        elif self._phase == "sources":
            for s in self._sources:
                if s.id == item_id:
                    if not self.future.done():
                        self.future.set_result([s])
                    return

    def _go_back(self):
        if self._phase == "sources":
            self._show_projects()
        else:
            self.cancel()

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
                ("class:accent bold", "  Ret"), ("", "  Next / Repl\n"),
                ("class:accent bold", "  Tab"), ("", "  Next field\n"),
                ("class:accent bold", "  ^F "), ("", "  Editor\n"),
                ("class:accent bold", "  Esc"), ("", "  Close\n"),
            ]

        self.container = HSplit([
            Window(FormattedTextControl(
                [("class:accent bold", " Find/Replace\n")],
            ), height=1),
            Window(height=1, char="─", style="class:hint"),
            Label(text=" Find:"),
            self.search_window,
            Window(content=self.status_control, height=1),
            Label(text=" Replace:"),
            self.replace_window,
            self.replace_all_window,
            Window(height=1),
            Window(FormattedTextControl(get_hints), height=4),
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

    # ── Projects screen widgets ──────────────────────────────────────

    project_search = TextArea(
        multiline=False, prompt=" Search: ", height=1,
        style="class:input",
    )
    project_list = SelectableList()
    export_list = SelectableList()
    hints_control = FormattedTextControl(
        lambda: [("class:hint", " (n) New  (r) Rename  (d) Delete  (e) Exports  (/) Search")])
    shutdown_hint_control = FormattedTextControl(
        lambda: [("class:hint", "⌃S Shut down ")])
    hints_window = VSplit([
        Window(content=hints_control, height=1),
        Window(content=shutdown_hint_control, height=1, align=WindowAlign.RIGHT),
    ])

    def refresh_projects(query=""):
        state.projects = state.storage.list_projects()
        filtered = fuzzy_filter_projects(state.projects, query)
        if not state.projects:
            project_list.set_items([
                ("__empty__", "No manuscripts yet \u2014 press n to create one.")])
        elif not filtered:
            project_list.set_items([("__empty__", "No matching manuscripts.")])
        else:
            items = []
            for p in filtered:
                try:
                    mod = datetime.fromisoformat(p.modified).strftime("%b %d, %Y")
                except (ValueError, TypeError):
                    mod = ""
                items.append((p.id, f"{p.name}  ({mod})"))
            project_list.set_items(items)

    def refresh_exports():
        export_dir = state.storage.exports_dir
        files = []
        for ext in ("*.pdf", "*.docx", "*.md"):
            files.extend(export_dir.glob(ext))
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

    project_search.buffer.on_text_changed += lambda buf: refresh_projects(buf.text)
    refresh_projects()

    def open_project(pid):
        if pid == "__empty__":
            return
        project = state.storage.load_project(pid)
        if project:
            state.current_project = project
            state.editor_dirty = False
            editor_area.text = project.content
            state.screen = "editor"
            get_app().layout.focus(editor_area.window)
            if state.auto_save_task:
                state.auto_save_task.cancel()
            state.auto_save_task = asyncio.ensure_future(auto_save_loop())
            get_app().invalidate()

    project_list.on_select = open_project

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

    projects_view = HSplit([
        Window(FormattedTextControl([("class:title bold", " Manuscripts")]),
               height=1, dont_extend_height=True),
        project_search,
        project_list,
        hints_window,
    ])

    exports_hints_control = FormattedTextControl(
        lambda: [("class:hint", " (m) Manuscripts  (d) Delete")])
    exports_hints_window = Window(content=exports_hints_control, height=1)

    exports_view = HSplit([
        Window(FormattedTextControl([("class:title", " Exports")]),
               height=1, dont_extend_height=True),
        export_list,
        exports_hints_window,
    ])

    def get_projects_screen():
        if state.showing_exports:
            return exports_view
        return projects_view

    projects_screen = DynamicContainer(get_projects_screen)

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
        if state.current_project:
            if state.show_word_count:
                words = _word_count(editor_area.text)
                return [("class:status",
                         f" {state.current_project.name}  {words} words")]
            else:
                paras = _para_count(editor_area.text)
                return [("class:status",
                         f" {state.current_project.name}  {paras} \u00b6")]
        return [("class:status", "")]

    status_bar = Window(
        FormattedTextControl(get_status_text), height=1, style="class:status",
    )

    def get_keybindings_text():
        sections = [
            [("Esc", "Manuscripts"), ("^O", "Sources"),
             ("^P", "Commands"), ("^Q", "Quit"), ("^S", "Save")],
            [("^B", "Bold"), ("^I", "Italic"), ("^N", "Footnote"),
             ("^R", "Cite"), ("^F", "Find/Replace")],
            [("^Z", "Undo"), ("^sZ", "Redo"),
             ("^Up", "Top"), ("^Dn", "Bottom")],
            [("^W", "Word/para"), ("^G", "This panel"),
             ("^S", "Shutdown*")],
        ]
        result = []
        for i, section in enumerate(sections):
            if i > 0:
                result.append(("", "\n"))
            for key, desc in section:
                result.append(("class:accent bold", f" {key:>4}"))
                result.append(("", f"  {desc}\n"))
        return result

    keybindings_panel = Window(
        FormattedTextControl(get_keybindings_text),
        width=22, style="class:keybindings-panel",
    )

    def get_editor_body():
        parts = []
        if state.show_find_panel and state.find_panel:
            parts.append(state.find_panel)
            parts.append(Window(width=1, char="│", style="class:hint"))
        parts.append(editor_area)
        if state.show_keybindings:
            parts.append(Window(width=1, char="│", style="class:hint"))
            parts.append(keybindings_panel)
        return VSplit(parts)

    editor_screen = HSplit([
        DynamicContainer(get_editor_body),
        status_bar,
    ])

    # ── Screen switcher ──────────────────────────────────────────────

    def get_current_screen():
        if state.screen == "editor":
            return editor_screen
        return projects_screen

    root = FloatContainer(
        content=DynamicContainer(get_current_screen),
        floats=[],
    )
    state.root_container = root

    # ── Auto-save ────────────────────────────────────────────────────

    async def auto_save_loop():
        while state.screen == "editor":
            await asyncio.sleep(30)
            if state.editor_dirty and state.current_project:
                state.current_project.content = editor_area.text
                state.editor_dirty = False
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, state.storage.save_project, state.current_project)

    # ── Export pipeline ──────────────────────────────────────────────

    async def run_export(export_format):
        project = state.current_project
        if not project:
            return
        export_dir = state.storage.exports_dir
        safe_name = (re.sub(r'[^\w\s-]', '', project.name)
                     .strip().replace(' ', '_')[:50] or "export")
        loop = asyncio.get_running_loop()

        if export_format == "md":
            out = export_dir / f"{safe_name}.md"
            await loop.run_in_executor(
                None, lambda: out.write_text(project.content))
            show_notification(state, f"Exported: {out.name}")
            return

        yaml = parse_yaml_frontmatter(project.content)
        pandoc = detect_pandoc()
        if not pandoc:
            show_notification(state, "Pandoc not found. Install pandoc for export.")
            return

        if export_format == "pdf":
            libreoffice = detect_libreoffice()
            if not libreoffice:
                show_notification(state, "LibreOffice not found for PDF export.")
                return
        else:
            libreoffice = None

        ref_doc = resolve_reference_doc(yaml)
        if not ref_doc:
            show_notification(state, "No reference .docx found in refs/ directory.")
            return

        md_path = export_dir / f"{project.id}.md"
        lua_path = export_dir / f"{project.id}_filter.lua"
        docx_path = export_dir / f"{safe_name}.docx"
        pdf_path = export_dir / f"{safe_name}.pdf"

        try:
            await loop.run_in_executor(None, lambda: md_path.write_text(project.content))
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
            cleanup = [md_path, lua_path]
            if export_format == "pdf":
                cleanup.append(docx_path)
            for p in cleanup:
                try:
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass

    # ── Editor actions ───────────────────────────────────────────────

    def do_save(notify=True):
        if state.current_project:
            state.current_project.content = editor_area.text
            state.storage.save_project(state.current_project)
            state.editor_dirty = False
            if notify:
                show_notification(state, "Saved.")

    def return_to_projects():
        do_save(notify=False)
        if state.auto_save_task:
            state.auto_save_task.cancel()
            state.auto_save_task = None
        if state.show_find_panel and state.find_panel:
            state.last_find_query = state.find_panel.search_buf.text
        state.show_find_panel = False
        state.screen = "projects"
        state.current_project = None
        state.showing_exports = False
        refresh_projects()
        get_app().layout.focus(project_search.window)
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

    def do_bibliography():
        if not state.current_project:
            return
        sources = state.current_project.get_sources()
        if not sources:
            show_notification(state, "No sources. Add sources first.")
            return
        sorted_sources = sorted(
            sources, key=lambda s: s.author.split()[-1] if s.author else "")
        lines = ["## Bibliography", ""]
        for s in sorted_sources:
            lines.append(s.to_chicago_bibliography())
            lines.append("")
        editor_area.buffer.insert_text("\n".join(lines))
        show_notification(state, "Bibliography inserted.")

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

    def do_mass_export_md():
        if not state.projects:
            show_notification(state, "No manuscripts to export.")
            return

        async def _do():
            loop = asyncio.get_running_loop()
            count = 0
            for project in state.projects:
                full = await loop.run_in_executor(
                    None, state.storage.load_project, project.id)
                if not full or not full.content.strip():
                    continue
                safe = (re.sub(r'[^\w\s-]', '', full.name)
                        .strip().replace(' ', '_')[:50] or "export")
                out = state.storage.exports_dir / f"{safe}.md"
                await loop.run_in_executor(
                    None, lambda p=out, c=full.content: p.write_text(c))
                count += 1
            show_notification(
                state, f"Exported {count} manuscript{'s' if count != 1 else ''} as Markdown.")

        asyncio.ensure_future(_do())

    # ── Get commands for palette ─────────────────────────────────────

    def get_commands():
        if state.screen == "editor":
            return [
                ("Bibliography", "Insert bibliography", do_bibliography),
                ("Export", "Export document", lambda: asyncio.ensure_future(
                    show_dialog_as_float(state, ExportFormatDialog()).then(
                        lambda fmt: run_export(fmt) if fmt else None))),
                ("Insert blank footnote (^N)", "Insert footnote", do_footnote),
                ("Insert frontmatter", "Add YAML frontmatter", do_insert_frontmatter),
                ("Insert reference (^R)", "Insert a citation", None),
                ("Keybindings (^G)", "Toggle keybindings panel",
                 lambda: toggle_keybindings()),
                ("Return to manuscripts (Esc)", "Save and go back", return_to_projects),
                ("Save (^S)", "Save document", lambda: do_save()),
                ("Sources (^O)", "Manage sources", None),
                ("Import .bib file", "Import BibTeX", None),
            ]
        else:
            return [
                ("Exports (e)", "Toggle exports view", lambda: toggle_exports()),
                ("New manuscript (n)", "Create a new manuscript", None),
                ("Quit (q)", "Quit the application", None),
            ]

    def toggle_keybindings():
        state.show_keybindings = not state.show_keybindings
        get_app().invalidate()

    def toggle_exports():
        state.showing_exports = not state.showing_exports
        if state.showing_exports:
            refresh_exports()
            get_app().layout.focus(export_list.window)
        else:
            get_app().layout.focus(project_list.window)
        get_app().invalidate()

    # ── Key bindings ─────────────────────────────────────────────────

    kb = KeyBindings()

    is_projects = Condition(lambda: state.screen == "projects")
    is_editor = Condition(lambda: state.screen == "editor")
    no_float = Condition(lambda: len(state.root_container.floats) == 0)
    search_not_focused = Condition(
        lambda: get_app().layout.current_window != project_search.window)
    projects_list_focused = is_projects & no_float & search_not_focused

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
                return_to_projects()
            else:
                state.escape_pending = now
                show_notification(state,
                                  "Press Esc again to return to manuscripts.",
                                  duration=2.0)
        elif state.screen == "projects":
            if state.showing_exports:
                toggle_exports()
            else:
                event.app.layout.focus(project_search.window)

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

    # -- Projects screen --
    @kb.add("n", filter=projects_list_focused)
    def _(event):
        if state.showing_exports:
            return

        async def _do():
            dlg = InputDialog(title="New Manuscript", label_text="Name:",
                              ok_text="Create")
            name = await show_dialog_as_float(state, dlg)
            if name:
                project = state.storage.create_project(name)
                open_project(project.id)

        asyncio.ensure_future(_do())

    @kb.add("r", filter=projects_list_focused)
    def _(event):
        if state.showing_exports:
            return
        filtered = fuzzy_filter_projects(state.projects, project_search.text)
        idx = project_list.selected_index
        if idx >= len(filtered):
            return
        project = filtered[idx]

        async def _do():
            dlg = InputDialog(title="Rename", label_text="New name:",
                              initial="", ok_text="Rename")
            new_name = await show_dialog_as_float(state, dlg)
            if new_name:
                p = state.storage.load_project(project.id)
                if p:
                    p.name = new_name
                    state.storage.save_project(p)
                    refresh_projects(project_search.text)
                    show_notification(state, f"Renamed to '{new_name}'.")

        asyncio.ensure_future(_do())

    @kb.add("d", filter=projects_list_focused)
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
        filtered = fuzzy_filter_projects(state.projects, project_search.text)
        idx = project_list.selected_index
        if idx >= len(filtered):
            return
        project = filtered[idx]

        async def _do():
            dlg = ConfirmDialog(f"Delete '{project.name}'?")
            ok = await show_dialog_as_float(state, dlg)
            if ok:
                state.storage.delete_project(project.id)
                refresh_projects(project_search.text)
                show_notification(state, "Manuscript deleted.")

        asyncio.ensure_future(_do())

    @kb.add("e", filter=projects_list_focused)
    def _(event):
        toggle_exports()

    @kb.add("m", filter=projects_list_focused)
    def _(event):
        if state.showing_exports:
            toggle_exports()
            return
        now = time.monotonic()
        if now - state.mass_export_pending < 2.0:
            state.mass_export_pending = 0.0
            do_mass_export_md()
        else:
            state.mass_export_pending = now
            show_notification(state, "Press m again to export all as Markdown.", duration=2.0)

    @kb.add("/", filter=projects_list_focused)
    def _(event):
        event.app.layout.focus(project_search.window)

    search_focused = Condition(
        lambda: state.screen == "projects"
        and len(state.root_container.floats) == 0
        and get_app().layout.current_window == project_search.window)

    @kb.add("down", filter=search_focused)
    def _(event):
        if state.showing_exports:
            event.app.layout.focus(export_list.window)
        else:
            event.app.layout.focus(project_list.window)

    @kb.add("enter", filter=search_focused)
    def _(event):
        filtered = fuzzy_filter_projects(state.projects, project_search.text)
        if filtered:
            open_project(filtered[0].id)

    @kb.add("c-s", filter=is_projects & no_float)
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

    @kb.add("c-r", filter=is_editor & no_float)
    def _(event):
        if not state.current_project:
            return
        sources = state.current_project.get_sources()
        if not sources:
            show_notification(state, "No sources. Add sources first (^O).")
            return

        async def _do():
            dlg = CitePickerDialog(sources)
            footnote = await show_dialog_as_float(state, dlg)
            if footnote:
                editor_area.buffer.insert_text(footnote)

        asyncio.ensure_future(_do())

    @kb.add("c-o", filter=is_editor & no_float)
    def _(event):
        if not state.current_project:
            return
        do_save(notify=False)

        async def _do():
            dlg = SourcesDialog(state, state.current_project)
            await show_dialog_as_float(state, dlg)
            reloaded = state.storage.load_project(state.current_project.id)
            if reloaded:
                state.current_project = reloaded

        asyncio.ensure_future(_do())

    @kb.add("c-p", filter=no_float)
    def _(event):
        async def _do():
            cmds = []
            if state.screen == "editor":
                cmds = [
                    ("Bibliography", "Insert bibliography", do_bibliography),
                    ("Export", "Export document", None),
                    ("Find", "^F", None),
                    ("Insert blank footnote", "^N", do_footnote),
                    ("Insert frontmatter", "YAML frontmatter", do_insert_frontmatter),
                    ("Insert reference", "^R", None),
                    ("Keybindings", "^G", toggle_keybindings),
                    ("Return to manuscripts", "Esc", return_to_projects),
                    ("Save", "^S", lambda: do_save()),
                    ("Sources", "^O", None),
                    ("Import .bib file", "BibTeX import", None),
                ]
            else:
                cmds = [
                    ("Exports", "Toggle exports", toggle_exports),
                    ("New manuscript", "Create new", None),
                    ("Quit", "Exit app", None),
                ]
            dlg = CommandPaletteDialog(cmds)
            action = await show_dialog_as_float(state, dlg)
            if action and callable(action):
                action()
            elif action is None:
                pass
            # Handle special commands that need async
            # (Export, Cite, Sources are handled inline below)

        async def _do_full():
            cmds_map = {}
            if state.screen == "editor":

                async def cmd_export():
                    dlg = ExportFormatDialog()
                    fmt = await show_dialog_as_float(state, dlg)
                    if fmt:
                        await run_export(fmt)

                async def cmd_cite():
                    sources = state.current_project.get_sources() if state.current_project else []
                    if not sources:
                        show_notification(state, "No sources.")
                        return
                    dlg = CitePickerDialog(sources)
                    fn = await show_dialog_as_float(state, dlg)
                    if fn:
                        editor_area.buffer.insert_text(fn)

                async def cmd_sources():
                    do_save(notify=False)
                    dlg = SourcesDialog(state, state.current_project)
                    await show_dialog_as_float(state, dlg)
                    reloaded = state.storage.load_project(state.current_project.id)
                    if reloaded:
                        state.current_project = reloaded

                async def cmd_bib_import():
                    dlg = BibImportDialog()
                    sources = await show_dialog_as_float(state, dlg)
                    if sources and state.current_project:
                        for s in sources:
                            state.current_project.add_source(s)
                        state.storage.save_project(state.current_project)
                        show_notification(state, f"Imported {len(sources)} source(s).")

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
                    ("Bibliography", "Insert bibliography", do_bibliography),
                    ("Export", "Export document", cmd_export),
                    ("Find", "^F", cmd_find),
                    ("Insert blank footnote", "^N", do_footnote),
                    ("Insert frontmatter", "YAML frontmatter", do_insert_frontmatter),
                    ("Insert reference", "^R", cmd_cite),
                    ("Keybindings", "^G", toggle_keybindings),
                    ("Return to manuscripts", "Esc", return_to_projects),
                    ("Save", "^S", lambda: do_save()),
                    ("Sources", "^O", cmd_sources),
                    ("Import .bib file", "BibTeX import", cmd_bib_import),
                ]
            else:
                cmds = [
                    ("Exports", "Toggle exports", toggle_exports),
                    ("New manuscript", "Create new", None),
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

    layout = Layout(root, focused_element=project_search.window)

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
    if os.environ.get("MANUSCRIPTS_DATA"):
        data_dir = Path(os.environ["MANUSCRIPTS_DATA"])
    else:
        data_dir = Path.home() / "Documents" / "Manuscripts"

    app = create_app(Storage(data_dir))
    app.run()


if __name__ == "__main__":
    main()
