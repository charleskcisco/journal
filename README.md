# Journal---a writerdeck-compatible companion for your Obsidian vault 

![PXL_20260215_210709684](https://github.com/user-attachments/assets/c1d0cc66-68d1-4fe3-a967-05bf44961b97)

![PXL_20260215_210658640](https://github.com/user-attachments/assets/7f9084b5-5ca3-4c06-9540-73a36168e6df)

## About
One simple idea undergirds Journal, a terminal-based Markdown editor built on prompt_toolkit: my Obsidian vault should stretch across different devices. Obsidian, as an Electron application, works best in a standard desktop environment; but sometimes  I don't want a standard desktop environment. Different devices offer other computing paradigms with distinct benefits. One device category in particular, the writerdeck, overlaps in functionality with Obsidian, but with its stripped down operating system cannot run that application effectively. Thus, I designed Journal as a CLI text editor for reading and writing `.md` files directly in my synced vault directory.

Some background before I explain more: I love Obsidian. I've been using it for more than seven years at this point as my primary text processing surface. I fled from the chilly embrace of Microsoft Word (I'm too old to have ever been a serious Google Docs devotee) when the time came to start writing my PhD thesis in 2018. At that point, I came to regard dependence upon proprietary software and file formats to compose, edit, and store my thoughts as abhorrent. I had played around in Evernote, Onenote, Word, looked at programs like Scrivener and Ulysses when I finally stumbled on Obsidian and cobbled together a suite of plug-ins to manage citations and conversion in concert with marvelous open source tools like Zotero and Pandoc. That setup served me well for five years, and produced what my examiners called "one of the cleanest and best formatted submissions" they 
had ever seen. For work wholly produced in a clean, portable, and lightweight format using free tools, that's not bad.

Obsidian, for all of its praiseworthy attributes, has some flaws. It's an Electron app, so certain configurations can be resource-intensive, and good luck running it on truly minimal hardware (more on that in a second). It's also a Swiss Army knife in terms of options and potential for varying configurations, which while useful at times can be distracting and counter-productive. To its credit on these points though, I reckon the vast majority of people are using it on their Apple Silicon Macbooks Air plugged into the outlets at the corner tables of their favorite local cafes and don't feel the performance weight at all. Moreover, the weight of the app is concomitant to its flexible configurability that is one of its virtues.

But let's imagine that sleek, performant, fruit logo'd clamshell laptops are not the only relevant kind of device. Perhaps the writer wants to draft on a simpler, distraction-free device and edit elsewhere. Enter the writerdeck, a form of single-use device increasingly popular for composition among students and professional authors. Craig Mod, in a recent newsletter talking about an Obsidian-powered pseudo-writerdeck he's been using, puts it well:

> Perhaps the sweet spot was word processors---dedicated writing machines that afforded some simplicity (no dependence on physical media to write at length; search; inline editing; compact-ish) without (overly) compromising the act itself. No extraneous distractions, just thoughts and words.

The writerdeck is a single-purpose composition appliance, sometimes manufactured at scale running proprietary software (see Pomera, Freewrite, etc.) and sometimes assembled by hobbyists with 3D printers and off-the-shelf components for themselves or small groups of others (as with Micro-Journal) and powered by Linux (cf. the Micro-Journal rev. 2, a writerdeck designed around Raspberry Pi Zero 2 W with a 1280x400 display on which this project is designed to run by default).

At that point, the writer has two choices---well this writer had two choices. The first is this: to abandon a tool that has served him well for many years in favor of simple but limited alternatives (Wordgrinder, Micro) that could perhaps be plugged back into his working system. This could suit. The solution would be to draft on Wordgrinder, Micro, or whatever else writerdeck manufacturers employ and export the resulting files to a more robust system for editing and production in Obsidian later. Many people do precisely this and I suspect they do just fine.

Alternatively, the writer could design an entirely new system with the principles and functionality of Obsidian in mind, but minimal and lightweight enough to run in Linux on a Rasberry Pi Zero 2 W or similar hardware---to serve as a surface for the drafting portion of the writing process that can at the fullness of time give way to the editing portion in Obsidian. (Crucial to this is Syncthing, which runs on both my PC and my Linux-powered writerdeck). Thus was born Journal, a writerdeck-compatible companion for my Obsidian vault. 

## Dependencies

- Python 3.9+
- prompt_toolkit

## First-time use

```bash
git clone
chmod +x setup.sh run.sh
./setup.sh     # creates venv, installs prompt_toolkit and other dependencies
JOURNAL_VAULT=~/notes ./run.sh         # custom vault directory
./run.sh       # launches journal
```

By default, Journal reads `.md` files from `~/Documents/`. Exports go to `~/Documents/pdf/` and `~/Documents/docx/`.

## Specifics
Journal conforms to my vault, where I use a relatively minimal set of plugins for academic writing and note taking. Hence, its features, split between the Journal and the Editor, are as follows:

- Journal opens into the Journal (surprise), a two-pane layout that shows the .md files in one's vault on the left, organized in reverse-chronological order; on the right is a preview pane designed to give a glimpse into the file's contents (YAML excluded). From here, you can make a new file, rename, delete, or duplicate existing files, search your vault via filename, or shut down your writerdeck (assuming you're on a Linux-powered system with auto-login enabled).
- From the Journal, you can also view a list of and print exports, .docx or .pdf files created via a custom pandoc/libreoffice pipeline (more on that below).
- Once you enter the Editor screen, you may edit your document (surprise again) in the markdown syntax. 
- As in Obsidian, you can use ctrl+p to open a command palette, from which you can access a host of features (most of those are also available via a set of Journal-specific keybindings).

Let me talk about these features and their bindings in more detail (organized from least to most interesting, for whimsy's sake).

### Keybindings guide (ctrl+g)---the epitome of boring, as most essential things are
This opens a panel on the right that serves as a guide for the keybindings below. It can stay open as you edit as a reference if needed.

![PXL_20260215_210719197](https://github.com/user-attachments/assets/55e3e881-b969-427e-91a8-d2130c6c6b15)

### Copy (ctrl+c)/Cut (ctrl+x)/Paste (ctrl+v)---good luck convincing people to use a text editor that doesn't do these things.
These work as you'd expect them to do. Don't worry, it gets more interesting from here.

### Undo (ctrl+z) and redo (ctrl+shift+z)---because I've accidentally deleted my entire document, too.
These binds do exactly what they say on the tin, and are only barely more interesting than copy, cut, and paste, by virtue of redo's old fashioned terminal-emulator style binding. 

### Bold (ctrl+b) and italicize (ctrl+i)---boldly going where *every* text editor has gone before.
Markdown is a plain text language that handles **bold**, *italics*, ***or a combination of the pair*** via enclosing words in asterisks. These clever bindings just place the appropriate number thereof around the word in which your cursor is currently resting or around your selection.

### Go to top (ctrl+up) or bottom (ctrl+down)---think about it and you'll realize that this is more interesting than you first thought.
By design, Journal places your cursor on the first line after any frontmatter. These bindings can move it either to the very top of the document or (probably more usefully) to its last line, so you can pick up where you left off.

### Toggle word and paragraph count (ctrl+w)---downright fascinating is what it is.
Word counts are a necessary evil (maybe), but they do prompt some really poor behavior from bad writers who need to hit them. Sometimes, though, it's helpful to measure the number of rounded, complete, coherent sets of thought you've produced. The paragraph count is your tool for that latter, more noble goal. You can also toggle this off, if you're the sort of writer who defies measurement of your process.

### Find and/or replace (ctrl+f)---this placement was less about how interesting it is in principle and more about how hard it was to theorize and implement.
Journal offers (if I may say so myself) a relatively robust find and replace feature. Ctrl+f summons a panel in which you may type a particular word. At that point, you have a choice. Enter will send you into the editor pane and highlight the term you sought. You can cycle through results with ctrl+k (next) and ctrl+j (previous), and you can return to the find panel with ctrl+f, from which you can then also replace that word you sought or replace every instance of it in your document.

![PXL_20260215_210757382](https://github.com/user-attachments/assets/ec115020-c86a-47a8-ad09-5cfcee50b5f0)

### Return to Journal (esc)---the pressing twice thing makes this fascinating if you mull it over.
If you press escape (twice to prevent accidental activation), you'll return to the Journal screen.

### Quit to CLI (ctrl+q)---how many times do you think I accidentally quit to CLI before I made this require a double press?
Likewise, a double press of ctrl+q sends the user back to the command line.

### Shut down (ctrl+s *from the Journal screen*)---a sudo command? HERE?!
My writerdeck boots into the Journal screen, and often I spend all of my time with this device in this app. I wanted to be able to shut down without exiting to CLI, so I set up a double press of ctrl+s to do the job. (*N*.*b*., this only works if you have auto-login set up on your device, because all it does is run 'sudo shutdown now'---I know, I'm a maverick). 

### Insert blank footnote (ctrl+n)---footnotes are fastinating.
The next two features are related. First, ctrl+n offers a quick and frictionless way to insert an inline markdown footnote (the correct kind of markdown footnote; do not @ me). Once you've done that, though, the real magic begins 

### Search for and insert citekey (ctrl+r)---this will revolutionize your academic writing once you figure out how to implement it. Even if you don't like Journal, you should get this into your workflow.
Ctrl+r will open a pop-up from which you can fuzzy search your .bib file in `~/Documents/sources/` (exported from a robust Zotero library, I'm guessing, you studious guy or gal, you) and insert a citekey at your cursor. That plus pandoc's --citeproc flag (more on that below) revolutionized citation workflow for me. You have got to try it.

![PXL_20260215_211150347](https://github.com/user-attachments/assets/91ba3a21-e1e3-4f3e-aa5e-72326165fb40)

![PXL_20260215_211158401](https://github.com/user-attachments/assets/b5df651e-cab4-4f79-9adc-f1c82177df8a)

### Insert frontmatter (palette only)---YAML is an old trick, but here it's used for a very specific function.
This will insert at the top of the document the frontmatter relevant to the export function. I reckon title, author, instructor, and date are pretty self-explanatory, or will be once you understand how this feature works. Style accepts one of two case sensitive inputs: "chicago" and "mla". Spacing, likewise, accepts "single" or "double". You can also add your own frontmatter elements, the most relevant of which might be "bibliography", "csl", and "tags". Now to talk about the final feature in this section.

#### Example

```yaml
---
title: "My Essay"
author: "First Last"
instructor: "Prof. Name"
date: "2026-02-13"
spacing: double
style: chicago
bibliography: /home/username/documents/sources/library.bib
csl: /home/username/documents/sources/chicago.csl 
---
```

- **spacing**: `single`, `double`
- **style**: `chicago` (Turabian cover page) or `mla` (MLA header)
- **bibliography**: path to `.bib` file (enables `--citeproc` during export)
- **csl**: path to `.*csl` file

### Export (palette only)---this is basically its own app, frankly.
This feature uses pandoc and libreoffice in the background to produce a .pdf formatted for submission in academic contexts. Pulling from the frontmatter, pandoc shapes your .md into a .docx formatted according to either Chicago style (with a title page containing your title, author, instructor, and date and page numbers centered in the footer with the final word of the author field appended to the front) or MLA (with a header on the first page according to MLA standards and page numbers on the top right). These can be either single- or double-spaced. Then, if you selected the .pdf output, it will use libreoffice to headlessly convert the .docx into a .pdf. You can either print these outputs from the exports screen or access them via your synced vault on your PC.

If you used a .bib, you can add "bibliography" and "csl" fields to the YAML and trigger pandoc's --citeproc, which will convert your citekeys to properly formatted citations and add a bibliography to your work.

This functionality mirrors two Obsidian plugins that I designed for my own personal use, citekey and md2pdf, both of which are available in other repositories.

## Keyboard shortcuts in tables for the prose-weary

### Journal

| Key | Action              |
| --- | ------------------- |
| n   | New entry           |
| r   | Rename entry        |
| d   | Delete entry        |
| c   | Duplicate entry     |
| e   | Toggle exports view |
| /   | Focus search        |
| Ctrl+S (x2) | Shut down        |

### Editor

| Key      | Action                               |
| -------- | ------------------------------------ |
| Ctrl+B   | Bold                                 |
| Ctrl+F   | Find/Replace                         |
| Ctrl+G   | Toggle keybindings panel             |
| Ctrl+I   | Italic                               |
| Ctrl+N   | Insert blank footnote (`^[]`)        |
| Ctrl+P   | Command palette                      |
| Ctrl+R   | Insert citation (@citekey from .bib) |
| Ctrl+S   | Save                                 |
| Ctrl+W   | Toggle word/paragraph count          |
| Esc (x2) | Return to file browser               |

## Advanced set-up (some of which, alas, requires explanation)

### Cage + Foot
Running this thing on the default terminal emulator in Raspberry Pi OS lite will leave you more disappointed than *The Rise of Skywalker* left me, which is...saying something. Enter Cage + Foot, which provide a terminal environment much better suited for what we're up to. [Cage](https://github.com/cage-kiosk/cage) is a "Wayland kiosk" that runs a single, maximized application. The single maximized application we want to run is [Foot](https://github.com/DanteAlighierin/foot), a fast, lightweight, minimalistic terminal emulator. If you want to start Cage, Foot, and Journal from the default terminal editor in Debian, you simply input the command `cage foot`, then `cd` into the folder where Journal is and `./run.sh`. I'll include my own foot.ini in the support folder, and you can place it in `~/.config/foot` (shocker). I'll also include in that folder my own startup script, which on my Micro-Journal boots straight into Journal in Cage + Foot with some help from .bashrc.

### Pandoc and Libreoffice
Pandoc and LibreOffice are awesome. Pandoc in particular is, I think, one of the most important pieces of software in the open source world. In this case, what we need it to do is mostly run in the background. These two applications form the pipeline from which a .md file can become a.pdf file. In a manner of speaking, these applications are the cocoon out of which your markdown caterpillar will emerge as a portable document butterfly. (That analogy might have been a little forced, but I'm only human). 

Technically, what's happening is less of a mystery than the whole butterfly thing. Journal is running `pandoc file.md -f markdown+smart -o file.docx` with some flags (`--citeproc --reference-doc=`). Then comes the command `libreoffice --headless --convert-to pdf file.docx`, which gives us the .pdf we wanted all along. Some LUA filters are handling formatting, pagination, etc. on top of a few bespoke reference.docx files. Would it have been easier to learn LaTeX? Maybe. But I didn't do that, did I? The road less travelled is my preference.

### Syncthing
Open source software is genuinely an under-appreciated treasure trove of incredible ideas that people around the world have had. And Syncthing like Pandoc is extremely important. I migrated from the corporate cloud services to running my own Syncthing instance a few years ago and have largely found it to be easy, profitable, and most importantly less icky.

Syncthing is not bundled with this application. You'll have to install it on both your writerdeck and your primary device and set it up yourself, but it's shockingly simple. You install Syncthing as you would install any application in Debian or your operating system of choice and you configure it. If you're in a CLI environment, you'll have to use SSH tunneling from a device with a graphical interface in order to do so, but it's not that complicated. The command you want to run on your computer is `ssh -L 8385:Localhost:8384 user@192.168.0.2` (where you obviously replace replace the username and IP address with those that pertain to you).  Then on that device enter your web browser of choice and go to Localhost:8385. From that point, you can configure Syncthing on your writerdeck and on your primary device to sync the folder that contains your Obsidian Vault to the ~/Documents folder of your writerdeck or wherever else you want it to go (I'm not the boss of you).

### Others that require no explantion (thank goodness)
cups, cups-client, lpr, python, prompt_toolkit, ttf-mscorefonts-installer (look it up if you have trouble with this)

## Data Storage
Journal reads and writes `.md` files directly in the vault directory (default `~/Documents/`). No JSON, no database---just plain Markdown files compatible with Obsidian and any other editor.

## License
MIT
