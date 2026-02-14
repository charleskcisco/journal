# Journal

A micro-journal companion for Obsidian vaults. Journal is a terminal-based Markdown editor that reads and writes `.md` files directly in your vault directory, with `.bib` citation search and PDF/DOCX export via Pandoc.

Designed for a Raspberry Pi Zero 2 W with a 1280x400 display, synced via Syncthing.

## Requirements

- Python 3.9+
- prompt_toolkit
- For PDF/DOCX export: Pandoc
- For PDF export: LibreOffice

### Setup

```bash
chmod +x setup.sh run.sh
./setup.sh     # creates venv, installs prompt_toolkit
./run.sh       # launches journal
```

## Usage

```bash
python3 journal.py                    # if prompt_toolkit is pip-installed
./run.sh                               # if using venv
JOURNAL_VAULT=~/notes ./run.sh         # custom vault directory
```

By default, Journal reads `.md` files from `~/Documents/`. Exports go to `~/Documents/pdf/` and `~/Documents/docx/`.

## Keyboard Shortcuts

### File Browser

| Key | Action |
|-----|--------|
| n | New entry |
| r | Rename entry |
| d | Delete entry |
| e | Toggle exports view |
| / | Focus search |

### Editor

| Key | Action |
|-----|--------|
| Ctrl+R | Insert citation (@citekey from .bib) |
| Ctrl+N | Insert blank footnote (`^[]`) |
| Ctrl+B | Bold |
| Ctrl+I | Italic |
| Ctrl+S | Save |
| Ctrl+F | Find/Replace |
| Ctrl+P | Command palette |
| Ctrl+G | Toggle keybindings panel |
| Ctrl+W | Toggle word/paragraph count |
| Esc (x2) | Return to file browser |

## Citations

Place a `.bib` file (exported from Zotero or similar) in `~/Documents/sources/`. Press `Ctrl+R` in the editor to fuzzy-search by author, title, or citekey. Selecting an entry inserts `@citekey` at the cursor.

## YAML Frontmatter

```yaml
---
title: "My Essay"
author: "First Last"
instructor: "Prof. Name"
date: "2026-02-13"
spacing: double
style: chicago
bibliography: sources/library.bib
---
```

- **spacing**: `single`, `double`, `dg.single`, `dg.double`
- **style**: `chicago` (Turabian cover page) or `mla` (MLA header)
- **bibliography**: path to `.bib` file (enables `--citeproc` during export)

## Data Storage

Journal reads and writes `.md` files directly in the vault directory (default `~/Documents/`). No JSON, no database -- just plain Markdown files compatible with Obsidian and any other editor.

## License

MIT
