# Plan: Fix editor scrolling + add file copy on journal screen

## Context

**Scrolling bug**: In `_scroll_when_linewrapping` (prompt_toolkit `containers.py:2373`), when a paragraph doesn't fill the whole viewport, `vertical_scroll_2` is unconditionally set to `0`. This means the viewport always snaps to show a paragraph from its very beginning. When the cursor crosses a paragraph boundary (via the custom up/down handlers), the viewport jumps by an entire paragraph instead of scrolling one visual line.

**Feature request**: Add a `(c)` key on the journal screen to duplicate the currently selected `.md` file.

---

## 1. Fix line-by-line editor scrolling

**File**: `journal.py` (after the cursor movement section, ~line 2770)

**Approach**: Replace the editor window's `_scroll` method with a custom one that tracks scroll state incrementally and advances by visual lines, using `vertical_scroll_2` for sub-paragraph offsets.

The custom scroll function will:
- Start from the window's current `vertical_scroll` / `vertical_scroll_2` (preserved from previous render)
- Compute the cursor's visual offset from the viewport top using `_word_wrap_boundaries` for the cursor's row and `get_height_for_line` for intervening rows
- If the cursor is above the viewport, scroll up by the exact deficit
- If the cursor is below the viewport, scroll down by the exact deficit
- Clamp to prevent scrolling beyond the bottom of the document

**Performance**: `_word_wrap_boundaries` is O(line_length) for the cursor's row (same work the processor already does during rendering). `get_height_for_line` is cached per render cycle. The loop over logical rows between viewport and cursor is typically 0-2 iterations for normal line-by-line movement.

**Key functions used**:
- `_word_wrap_boundaries(line, width)` — already defined at line 854
- `ui_content.get_height_for_line(lineno, width, ...)` — prompt_toolkit built-in, cached

**Root cause detail**: The prompt_toolkit `Window._scroll_when_linewrapping` method (installed at `.venv/lib/python3.14/site-packages/prompt_toolkit/layout/containers.py:2316`) has this on line 2373:
```python
else:
    self.vertical_scroll_2 = 0
```
This `else` branch fires when the cursor's paragraph fits within the viewport. It resets `vertical_scroll_2` to 0 every render, meaning the viewport always shows paragraphs from their first visual line. The rest of the method (`get_min_vertical_scroll`, `get_max_vertical_scroll`) only operates on logical line indices, so scrolling granularity is always whole paragraphs.

**Implementation sketch**:
```python
def _smooth_editor_scroll(ui_content, width, height):
    window = editor_area.window
    cursor_row = ui_content.cursor_position.y

    def get_line_height(lineno):
        return ui_content.get_height_for_line(lineno, width, window.get_line_prefix)

    # Cursor's visual line within its paragraph
    cursor_vline = 0
    if cursor_row < len(editor_area.buffer.document.lines):
        line = editor_area.buffer.document.lines[cursor_row]
        col = ui_content.cursor_position.x
        starts, _ = _word_wrap_boundaries(line, width)
        for idx, s in enumerate(starts):
            if col >= s:
                cursor_vline = idx

    # Start from previous scroll state (window preserves these between renders)
    vs = window.vertical_scroll
    vs2 = window.vertical_scroll_2

    # Validate
    if vs >= ui_content.line_count:
        vs, vs2 = max(0, ui_content.line_count - 1), 0
    vs_height = get_line_height(vs)
    if vs2 >= vs_height:
        vs2 = max(0, vs_height - 1)

    # Compute cursor's visual offset from viewport top
    if cursor_row == vs:
        offset = cursor_vline - vs2
    elif cursor_row > vs:
        offset = (get_line_height(vs) - vs2)
        for r in range(vs + 1, cursor_row):
            offset += get_line_height(r)
        offset += cursor_vline
    else:
        offset = cursor_vline - vs2
        for r in range(cursor_row, vs):
            offset -= get_line_height(r)

    # Scroll up if cursor above viewport
    if offset < 0:
        steps = -offset
        while steps > 0 and (vs > 0 or vs2 > 0):
            if vs2 > 0:
                step = min(steps, vs2)
                vs2 -= step
                steps -= step
            else:
                vs -= 1
                vs2 = get_line_height(vs) - 1
                steps -= 1

    # Scroll down if cursor below viewport
    elif offset >= height:
        steps = offset - height + 1
        while steps > 0:
            h = get_line_height(vs)
            remaining = h - vs2
            if steps < remaining:
                vs2 += steps
                steps = 0
            else:
                steps -= remaining
                vs += 1
                vs2 = 0
                if vs >= ui_content.line_count:
                    vs = ui_content.line_count - 1
                    vs2 = 0
                    break

    # Prevent scrolling beyond bottom
    if not window.allow_scroll_beyond_bottom():
        visible = get_line_height(vs) - vs2
        for r in range(vs + 1, ui_content.line_count):
            visible += get_line_height(r)
            if visible >= height:
                break
        if visible < height:
            deficit = height - visible
            while deficit > 0 and (vs > 0 or vs2 > 0):
                if vs2 > 0:
                    step = min(deficit, vs2)
                    vs2 -= step
                    deficit -= step
                else:
                    vs -= 1
                    vs2 = get_line_height(vs) - 1
                    deficit -= 1

    window.vertical_scroll = vs
    window.vertical_scroll_2 = vs2
    window.horizontal_scroll = 0

# Apply after editor_area is created:
editor_area.window._scroll = _smooth_editor_scroll
```

**Compatibility with FindReplacePanel**: The `_scroll_to_cursor` method in `FindReplacePanel` temporarily hooks `window._scroll`, calls our function as the "original", then overrides `vertical_scroll`. After one render it restores our function. This works because our function reads from `window.vertical_scroll`/`vertical_scroll_2` which will reflect the find/replace jump.

---

## 2. Add `(c)` copy/duplicate on journal screen

**File**: `journal.py`

**Changes**:
1. **Key binding** (~line 2495, before the `e` binding): Add `@kb.add("c", filter=entry_list_focused)` handler that:
   - Returns early if `state.showing_exports`
   - Gets the selected entry from the filtered list
   - Generates a copy name: `"Name (copy)"`, `"Name (copy 2)"`, etc.
   - Reads the source file text and writes it to the new path (gives fresh timestamp)
   - Calls `refresh_entries()` and `update_preview()`
   - Shows notification `"Copied to 'Name (copy)'."`

2. **Hint text** (line 1732): Add `(c) copy` to the hints:
   ```
   "(n) new (r) rename (c) copy (d) delete (e) exports (/) search"
   ```

---

## Verification

- Run `python3 journal.py` from the vault directory
- **Scrolling**: Open a file with long paragraphs. Use arrow keys to move through the text. Viewport should scroll one visual line at a time, never jumping by a whole paragraph.
- **Copy**: On the journal screen, select a `.md` file and press `c`. A copy should appear in the list with the name "Original (copy)". Press `c` again — a second copy named "Original (copy 2)" should appear.
