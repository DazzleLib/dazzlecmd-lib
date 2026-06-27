"""Read-surface presentation -- the dz card / list / tree renderers (SD-E).

SD-E decomposition (DWP 2026-06-26__22-56-24): the heavy ``render_*`` functions
move here VERBATIM from ``default_meta_commands.py`` so that module becomes a
thin parser-factory + handler + registry layer. ``interrogation`` stays the
facet-data + card-walker core; this module is the presentation layer that uses
it. Import direction (one-way): ``interrogation <- rendering <- default_meta_commands``.

Slice 1 lands the shared display utils + layout constants; the renderers follow
in later slices. ``default_meta_commands`` re-exports every public name moved
here so existing importers keep resolving unchanged (parsers.py:
``MIN_DESC_WIDTH``/``TERM_SIZE_FALLBACK``; cli.py: ``render_*``).
"""
from __future__ import annotations

import shutil as _shutil

from . import colors as _colors
from .core import is_constitutional as _is_constitutional


def _constitutional_entry(entry) -> bool:
    """True if a list entry is a constitutional ``dazzlecmd_lib.core`` tool.

    Such a tool's canonical home is ``dazzlecmd_lib:core:<name>`` (the engine
    lives in the lib's constitutional namespace); ``core:<name>`` is the
    consumer projection shown in ``dz list``. The ``[lib]`` marker surfaces
    that distinction.
    """
    return (entry.get("namespace") == "core"
            and _is_constitutional(entry.get("name", "")))


# ---------------------------------------------------------------------------
# Display-layout constants (shared by the lib renderers and consumer CLIs).
# The preferred pattern for COLUMN widths is data-computed (the #48 drill-in
# discipline: width = max over the actual rows); these constants exist for the
# genuinely fixed parts of the layout that data can't derive.
# ---------------------------------------------------------------------------

# get_terminal_size fallback when no TTY is attached (pipes, CI, byte-gate).
TERM_SIZE_FALLBACK = (80, 24)

# Never wrap or truncate a description below this many columns -- on absurdly
# narrow terminals, overflow beats unreadable two-character shards.
MIN_DESC_WIDTH = 20

# Kit-name column in the kit list/status summary views.
KIT_NAME_COL = 16

# Hanging indent for summary-view description lines (kit list/status).
SUMMARY_INDENT = 4


def _term_width():
    """The terminal's column count, with the standard non-TTY fallback."""
    return _shutil.get_terminal_size(TERM_SIZE_FALLBACK).columns


def _wrap_description(text, width):
    """Wrap a description string to fit within a given width.

    Returns a list of lines. Wraps at word boundaries when possible,
    falls back to hard break with hyphen when a single word exceeds
    the width.
    """
    if not text or width < 10:
        return [text or ""]
    if len(text) <= width:
        return [text]

    lines = []
    remaining = text
    while remaining:
        if len(remaining) <= width:
            lines.append(remaining)
            break

        # Find the last space within the width
        break_at = remaining.rfind(" ", 0, width)
        if break_at > 0:
            lines.append(remaining[:break_at])
            remaining = remaining[break_at + 1:]
        else:
            # No space found -- hard break with hyphen
            lines.append(remaining[:width - 1] + "-")
            remaining = remaining[width - 1:]

    return lines


def _print_legend_entry(marker, text, term_width, *, color=()):
    """Print a footer marker-legend entry with word-boundary wrapping and a
    hanging indent.

    The marker (``[*]``/``[+]``/``[lib]``) sits alone at the left of the first
    line; wrapped continuation lines align under the START of the text, so the
    marker stands out -- the same layout discipline as tool-description wrapping
    (render_list / render_info). Without this the legend was emitted as one long
    line and the terminal hard-wrapped it mid-word (e.g. "ali" / "as").

    ``color`` is a tuple of ``colors`` codes used to colorize the MARKER token
    (matching the row markers in ``_label_styled`` -- ``[*]`` bold+red, ``[+]``
    cyan, ``[lib]`` green). Width/indent math always uses the PLAIN marker
    length so the ANSI escapes never disturb alignment; color is applied only
    when ``should_use_color()`` (off for the byte-gate / pipes, so baselines
    stay plain).
    """
    prefix = f"  {marker} "                 # plain -> drives all width/indent math
    indent = " " * len(prefix)
    avail = max(MIN_DESC_WIDTH, term_width - len(prefix))
    lines = _wrap_description(text, avail)
    shown_marker = marker
    if color and _colors.should_use_color():
        shown_marker = _colors.colorize(marker, *color)
    print(f"  {shown_marker} {lines[0]}")
    for line in lines[1:]:
        print(f"{indent}{line}")
