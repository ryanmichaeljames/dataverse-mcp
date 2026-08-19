"""Generate the GitHub wiki tool-reference pages from the live MCP tool registry.

Emits ``Tool-Index.md`` plus one ``Tools-<Category>.md`` page per category into
the wiki repo (default ``../dataverse-mcp.wiki``).  Everything is derived by
introspection — no hand-maintained tool list.

Usage::

    uv run python scripts/gen_wiki_tools.py                 # write pages
    uv run python scripts/gen_wiki_tools.py --out PATH      # write elsewhere
    uv run python scripts/gen_wiki_tools.py --check         # CI staleness gate

Registry access
---------------
``await mcp.list_tools()`` is the public accessor and is the source of truth for
*which* tools exist.  It returns wire-protocol ``mcp_types.Tool`` objects
(``.name`` / ``.description`` / ``.annotations``) but drops the Python callable,
so the Pydantic input model is unreachable from it.  For that this script also
reads ``mcp._tool_manager.get_tool(name).fn`` — the documented way to reach the
internal registration object in the MCP Python SDK 2.x — and pulls the model off
the function signature.  Both are stable across the 1.x/2.x rename.

Gate forcing
------------
``DATAVERSE_ALLOW_WRITE`` / ``DATAVERSE_ALLOW_DELETE`` / ``DATAVERSE_TOOLS`` are
read at ``dataverse_mcp._app`` import time, so this module sets them in
``os.environ`` *before* importing anything from the package.  That makes the
output identical from any shell, and documents the full tool surface rather than
whatever the current shell happens to expose.

How much of a docstring reaches the page
----------------------------------------
Tool docstrings are written for **LLM tool selection**, not as reference copy.
That audience wants routing ("for a count use X"), restatements of the field
descriptions, and server-wide advice repeated per tool, because the model sees
one tool description at a time and has no page around it.  A human reading the
category page has the parameter table two inches below, the index and the
sidebar for navigation, and ``How-It-Works`` for the general advice — so all
three are dead weight here.  Emitting the whole docstring (as this generator
used to) makes every entry read padded; emitting only the first sentence (as it
used to before that) deletes the caveats, which live in sentences 2-*n*.

So each tool gets its first paragraph verbatim plus **at most two notes**, and
the notes are chosen by rule at render time rather than by editing the
docstrings (which must stay tuned for selection):

* a sentence is dropped when it is pure tool routing, only the write/delete env
  gate (the page header and the access line already say it), a restatement of
  what a parameter's Notes cell says, or server-wide best-practice advice;
* what survives must then pass ``earns_a_note`` — would acting on the tool
  without knowing this produce a wrong result, a failure or an irreversible
  change?  Descriptions, glossary lines and when-to-use guidance fail it however
  true they are, because a bold **Note:** on one of those is louder than plain
  prose and reads as padding.  A sentence with no such signal is never emitted;
* those that pass are ranked by how badly they bite — shouted banners,
  contradicted expectations, failure modes and hand-derived values — and once a
  tool has one at ``STRONG_SCORE`` the weaker ones stand down;
* a shouted banner ("AN UNKNOWN SETTING NAME IS NOT AN ERROR.") is a heading
  rather than a sentence, so it travels with the sentence under it — in either
  direction, whichever of the two was picked;
* ``NOTES_BUDGET`` caps the prose, so a second note only lands on a tool whose
  first note was short.

Ambiguity resolves towards keeping: under-cutting shows up in review, silently
deleting a real warning does not.
"""

import os

# Must precede the dataverse_mcp import: the env flags are read at import time.
os.environ["DATAVERSE_ALLOW_WRITE"] = "true"
os.environ["DATAVERSE_ALLOW_DELETE"] = "true"
os.environ.pop("DATAVERSE_TOOLS", None)

import argparse  # noqa: E402
import ast  # noqa: E402
import asyncio  # noqa: E402
import inspect  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import textwrap  # noqa: E402
import types  # noqa: E402
import typing  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402

from pydantic import BaseModel  # noqa: E402
from pydantic_core import PydanticUndefined  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:  # allow `python scripts/gen_wiki_tools.py`
    sys.path.insert(0, str(_REPO_ROOT / "src"))

import dataverse_mcp.server  # noqa: E402,F401  side effect: registers every @tool
from dataverse_mcp._app import TOOL_CATEGORIES, mcp  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category presentation
# ---------------------------------------------------------------------------

# category token -> (wiki page stem, display title)
CATEGORY_PAGES: dict[str, tuple[str, str]] = {
    "core": ("Tools-Core", "Core"),
    "schema": ("Tools-Schema", "Schema"),
    "solutions": ("Tools-Solutions", "Solutions"),
    "plugins": ("Tools-Plugins", "Plugins"),
    "security": ("Tools-Security", "Security"),
    "customapis": ("Tools-Custom-APIs", "Custom APIs"),
    "apps": ("Tools-Apps", "Apps"),
    "variables": ("Tools-Variables", "Variables"),
    "flows": ("Tools-Flows", "Flows"),
    "views": ("Tools-Views", "Views"),
    "forms": ("Tools-Forms", "Forms"),
    "connections": ("Tools-Connections", "Connections"),
    "webresources": ("Tools-Web-Resources", "Web Resources"),
    "jobs": ("Tools-Jobs", "Jobs"),
}

INDEX_PAGE = "Tool-Index"

GENERATED_BANNER = (
    "**This page is generated from the code by `scripts/gen_wiki_tools.py` — "
    "do not edit by hand.**"
)

# The error contract is identical for all 200 tools, so the page header states
# it once and a tool only carries a Returns line when the success shape was
# actually derived from its body.
ERROR_CONTRACT = 'Errors return `{"error": true, "message": "..."}`.'

# Notes longer than this move out of the table cell into a bullet beneath the
# table.  Nothing is ever truncated — a 1,200-character cell just wrecks the
# table's column widths.  Sized so only genuine outliers move: 13 of 890 rows.
NOTES_INLINE_MAX = 400

# Description notes: at most two, and the second only lands when the first left
# room.  The budget is a character count rather than a sentence count because a
# banner plus its sentence is one note but two sentences.
MAX_NOTES = 2
NOTES_BUDGET = 400
# A sentence longer than NOTES_BUDGET is a paragraph, not a note, and cannot be
# one; a banner's trailing sentence has to be shorter still to ride along.
FOLLOWER_MAX = 300
# A sentence at or above this score is a caveat worth the reader's time.  When a
# tool has any, only those are emitted — a real warning should not share the
# space with a merely descriptive line.
STRONG_SCORE = 4

# Return-value helpers whose sole argument is the success payload.
_RESPONSE_WRAPPERS = frozenset({"json.dumps", "finalize_response"})
# Helpers that only ever produce the error envelope; ignored when deriving shapes.
_ERROR_HELPERS = frozenset({"tool_error_response"})
# Above this many distinct success shapes, the derived line stops being useful.
_MAX_DERIVED_SHAPES = 3


# ---------------------------------------------------------------------------
# Introspection model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    required: bool
    default: str
    notes: str


@dataclass(frozen=True)
class ToolDoc:
    name: str
    category: str
    access: str  # Read | Write | Delete
    idempotent: bool
    summary: str  # first sentence — index page only
    description: str  # full docstring — first paragraph + notes reach the page
    returns: str  # derived success shape, or "" when it could not be derived
    params: list[Param] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Type rendering
# ---------------------------------------------------------------------------


def render_type(annotation: object) -> str:
    """Render a type annotation the way a Python developer would write it."""
    if annotation is None or annotation is type(None):
        return "None"
    if annotation is typing.Any:
        return "Any"

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin is typing.Annotated:
        # list[Annotated[str, ...]] etc. — the constraints live on the metadata,
        # which the Notes column already covers; show the underlying type.
        return render_type(args[0])
    if origin is typing.Literal:
        return "Literal[" + ", ".join(repr(a) for a in args) + "]"
    if origin in (typing.Union, types.UnionType):
        return " | ".join(render_type(a) for a in args)
    if origin is not None:
        base = getattr(origin, "__name__", str(origin))
        if args:
            return f"{base}[{', '.join(render_type(a) for a in args)}]"
        return base
    if inspect.isclass(annotation):
        return annotation.__name__
    return str(annotation)


def render_default(fld: object) -> str:
    """Render a field's default for the table's Default column."""
    if getattr(fld, "default_factory", None) is not None:
        try:
            produced = fld.default_factory()  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - defensive
            return "(factory)"
        return f"`{produced!r}`"
    default = getattr(fld, "default", PydanticUndefined)
    if default is PydanticUndefined:
        return "—"
    return f"`{default!r}`"


def render_constraints(metadata: list) -> list[str]:
    """Render annotated-types / pydantic constraint metadata as short tokens."""
    out: list[str] = []
    for item in metadata:
        for attr, label in (
            ("ge", "ge"),
            ("gt", "gt"),
            ("le", "le"),
            ("lt", "lt"),
            ("min_length", "min_len"),
            ("max_length", "max_len"),
            ("multiple_of", "multiple_of"),
        ):
            value = getattr(item, attr, None)
            if value is not None:
                out.append(f"{label}={value}")
        pattern = getattr(item, "pattern", None)
        if pattern:
            out.append(f"pattern={pattern}")
    return out


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


# An existing inline code span — its contents are already literal, so leave it
# alone rather than nesting backticks inside it.
_CODE_SPAN_RE = re.compile(r"`[^`\n]*`")

# A tag-like run (``<guid>``, ``</filter>``, ``<entity name="...">``) or, failing
# that, a lone ``<`` that could still open something.
#
# Alternation order matters: the tag branch is tried first so the whole bracketed
# run lands in one code span.
#
# The leading ``[A-Za-z]`` (after an optional ``/``) is load-bearing — DO NOT
# RELAX IT.  Without it, ``<[^<>\n]*>`` turns a comparison like "severity >= 3 is
# an error and < 3 a warning ... any message > x" into one enormous match that
# swallows the sentence into a code span.  That failure is silent.
#
# The fallback exempts ``<`` followed by whitespace (or ending the text).
# CommonMark cannot begin a raw HTML tag, comment, declaration, processing
# instruction or autolink at ``<`` + whitespace, so such a ``<`` already renders
# literally; wrapping it would only put a lone operator in monospace next to the
# plain ``>=`` in the same sentence.
_ANGLE_RE = re.compile(r"</?[A-Za-z][^<>\n]{0,120}>|<(?!\s|$)")


def protect_angle_brackets(text: str) -> str:
    """Wrap angle-bracket tokens in inline code spans.

    Docstrings are full of placeholders and XML element names — ``<guid>``,
    ``<link-entity>``, ``<fetch ...>``.  Those whose inner text is a legal HTML
    tag name are parsed as raw HTML by GitHub's renderer and *vanish* from the
    page.  A backtick span is immune to HTML parsing, renders literally
    everywhere, survives being re-processed, and is semantically right: these
    are element names.  (An ``&lt;`` entity also stops the swallowing, but it
    leaves unreadable source and re-escapes into a visible ``&amp;lt;``.)

    Text already inside a code span is passed through untouched, as is a ``<``
    followed by whitespace — a less-than operator, which CommonMark cannot read
    as markup.  Idempotent: re-applying this to its own output is a no-op.
    """
    def wrap(chunk: str) -> str:
        return _ANGLE_RE.sub(lambda m: f"`{m.group(0)}`", chunk)

    out: list[str] = []
    pos = 0
    for span in _CODE_SPAN_RE.finditer(text):
        out.append(wrap(text[pos : span.start()]))
        out.append(span.group(0))
        pos = span.end()
    out.append(wrap(text[pos:]))
    return "".join(out)


def escape_cell(text: str) -> str:
    """Make a structural value safe inside a Markdown table cell.

    For names, types and defaults — values the renderer already wraps in
    backticks, so angle-bracket protection must not run here and nest a second
    span inside the first.
    """
    flat = " ".join(text.split())
    return flat.replace("\\", "\\\\").replace("|", r"\|")


def escape_prose_cell(text: str) -> str:
    """Make free text safe inside a Markdown table cell."""
    return protect_angle_brackets(escape_cell(text))


def escape_line_start(line: str) -> str:
    """Escape a leading ``>``/``#``/``|`` so a wrapped line stays prose.

    Only relevant for lines emitted verbatim (list blocks); a blockquote or
    heading conjured out of a wrapped sentence such as ">= 3 is counted as..."
    would silently restyle the paragraph.
    """
    stripped = line.lstrip()
    if stripped[:1] in (">", "#", "|"):
        indent = line[: len(line) - len(stripped)]
        return f"{indent}\\{stripped}"
    return line


_BULLET_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s")


def split_blocks(description: str) -> list[str]:
    """Split a docstring into its blank-line-separated blocks."""
    return [b for b in description.strip().split("\n\n") if b.strip()]


def render_block(block: str) -> list[str]:
    """Render one docstring block as Markdown lines, preserving structure.

    Docstrings are hard-wrapped, so a plain paragraph is reflowed onto one line
    and lets the renderer wrap it.  A block containing a list or hanging-indent
    continuations is emitted verbatim instead — reflowing those would mash a
    bullet list into a single run-on sentence.
    """
    lines = [line.rstrip() for line in block.split("\n")]
    has_bullet = any(_BULLET_RE.match(line) for line in lines)
    has_indent = any(line.strip() and line[:1].isspace() for line in lines)

    if not has_bullet and not has_indent:
        return [protect_angle_brackets(" ".join(block.split()))]

    out: list[str] = []
    seen_bullet = False
    for line in lines:
        is_bullet = bool(_BULLET_RE.match(line))
        # A list that directly follows a lead-in line ("Inputs:") is legal but
        # ambiguous; a blank line makes it render as a list every time.
        if is_bullet and not seen_bullet and out and out[-1]:
            out.append("")
        seen_bullet = seen_bullet or is_bullet
        out.append(protect_angle_brackets(escape_line_start(line)))
    return out


def render_lead(description: str) -> list[str]:
    """Render the first paragraph — what the tool does.  Never dropped."""
    blocks = split_blocks(description)
    if not blocks:
        return ["_No description._"]
    return render_block(blocks[0])


def first_sentence(description: str) -> str:
    """Return the first sentence of a tool description as a one-line summary."""
    if not description:
        return "(no description)"
    paragraph = " ".join(description.strip().split("\n\n")[0].split())
    for index, char in enumerate(paragraph):
        if char != ".":
            continue
        nxt = paragraph[index + 1 : index + 2]
        # A sentence end is a period followed by end-of-string or a space that
        # is not part of an abbreviation like "e.g." / "v9.2".
        if nxt in ("", " ") and not paragraph[max(0, index - 2) : index].endswith(
            ("e.g", "i.e", "etc")
        ):
            return paragraph[: index + 1]
    return paragraph


# ---------------------------------------------------------------------------
# Note selection — see the module docstring for why this exists
# ---------------------------------------------------------------------------


# Sentence boundary: whitespace after a terminator, or after a terminator plus
# one closing quote/bracket.  Only the whitespace is consumed — an earlier
# version swallowed the ")" of "(..., etc.)" into the separator and lost it.
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+|(?<=[.!?][)\]"\'’])\s+')
# Trailing tokens that end in a period without ending a sentence.
_ABBREVIATIONS = ("e.g", "i.e", "etc", "vs", "cf", "approx", "no", "fig", "al")


def split_sentences(text: str) -> list[str]:
    """Split reflowed prose into sentences, without a sentence tokenizer.

    Docstrings are full of ``e.g.``, ``v9.2``, ``0x80040217`` and enumerations
    like ``1. Access exists`` — each of which looks like a boundary to a naive
    split.  Over-splitting is the dangerous direction here (a fragment becomes a
    note), so a piece is merged back whenever the break looks manufactured.
    """
    pieces = [p for p in _SENTENCE_SPLIT_RE.split(" ".join(text.split())) if p]
    out: list[str] = []
    for piece in pieces:
        prev = out[-1] if out else ""
        last_token = prev.rsplit(" ", 1)[-1].strip("\"'‘’()[]{}").rstrip(".")
        merge = bool(prev) and (
            last_token.lower() in _ABBREVIATIONS
            # An elision inside quoted platform text: "... With Id = ... Does
            # Not Exist".  Never a sentence end in these docstrings.
            or prev.rstrip("\"'‘’)]").endswith("...")
            # Nothing but punctuation before the break.
            or not last_token
            # The next piece opens with punctuation, so the break fell inside a
            # sentence: 'To answer "WHICH tables are OK?" — the enumeration...'
            or piece[:1] in "—–-),;:"
            # "1." / "2)" opening a run-in enumeration, or an initial.  Only one
            # character: "Entra ID. Requires ..." is a real boundary.
            or len(last_token) == 1
            or last_token.isdigit()
            # A continuation: real sentences start with a capital, a digit or a
            # quote.  Identifiers ("setting_value is lifted out...") do begin
            # sentences here, but merging one is safer than emitting a fragment.
            or piece[:1].islower()
        )
        if merge:
            out[-1] = f"{prev} {piece}"
        else:
            out.append(piece)
    return out


def block_sentences(block: str) -> list[str]:
    """Candidate sentences from one docstring block.

    In a block that contains a list, only the lead-in prose above the first
    bullet is a candidate: the items are an enumeration (status codes, subtypes,
    targeting paths), and an enumeration cannot be compressed into a note.
    """
    lines = [line.rstrip() for line in block.split("\n")]
    for index, line in enumerate(lines):
        if _BULLET_RE.match(line):
            lines = lines[:index]
            break
    text = " ".join(" ".join(lines).split())
    return split_sentences(text) if text else []


# --- drop classes ----------------------------------------------------------

# The write/delete gate, and nothing else.  The page header states it once and
# the per-tool access line ("Write · idempotent") repeats it a third time.
_GATE_RE = re.compile(
    r"^(?:this tool\s+)?(?:additionally\s+)?requires?\s+"
    r"`?DATAVERSE_ALLOW_(?:WRITE|DELETE)`?\s*=\s*true"
    r"(?:\s+on the server)?\.?$",
    re.I,
)

# Server-wide advice that How-It-Works covers once for every tool, and pointers
# at the parameter table printed directly below.
_GENERIC_ADVICE_RE = re.compile(
    r"keep (?:the )?(?:payloads?|responses?) small"
    r"|always (?:specify|provide|pass|use) select"
    r"|specify select to (?:limit|keep|reduce)"
    r"|to (?:limit|reduce) (?:the )?(?:returned )?(?:columns|payload|response)"
    r"|^see the [\w, /]+ (?:field|fields|parameter|parameters) (?:for|below)",
    re.I,
)

# A clause boundary strong enough to carry an independent assertion.  Plain
# commas are deliberately excluded — "To ask about one table, use X" is one
# thought, not two.
_CLAUSE_SPLIT_RE = re.compile(r"\s+[—–]\s+|;\s+|:\s+")

# Navigational shapes: a verb pointing at another tool, a comparison against
# one, or a "use X instead / first".
_ROUTING_RES = (
    re.compile(
        r"\b(?:use|using|call|calling|see|try|prefer|run|poll|follow up with|"
        r"resolve|discover|find|fetch|check|start)\b[^.]*\bTOOL\b",
        re.I,
    ),
    re.compile(r"\bTOOL\b[^.]*\b(?:instead|first|beforehand|afterwards?)\b", re.I),
    re.compile(
        r"\b(?:companion|counterpart|sibling|equivalent|alternative|"
        r"see also|compare to)\b[^.]*\bTOOL\b",
        re.I,
    ),
    re.compile(
        r"\bTOOL\b[^.]*\b(?:is the (?:companion|counterpart|sibling)|"
        r"answers|is what reads)\b",
        re.I,
    ),
)

# "Required: a, b, c" / "Mutable fields: x, y" — a label plus a list of
# parameters, which is the table below in sentence form.
_ROLL_CALL_RE = re.compile(
    r"^(?:Required|Optional|Mutable|Immutable)(?:\s+fields?)?\s*:", re.I
)

_WORD_RE = re.compile(r"[a-z0-9_]{3,}")
_STOPWORDS = frozenset(
    """the and are but for from into its not now off out over that this
    they them then there these those with within without you your when which
    while who whom whose has have had can could may might must shall should
    will would use used using also only both each such same than too very
    per via any all one two 100 000 true false null none""".split()
)


def _content_words(text: str) -> set[str]:
    """Lower-cased content words, underscores removed.

    Docstrings write a parameter as ``displayname`` where the model calls it
    ``display_name``; folding the separator away makes the two comparable.
    """
    return {
        w.replace("_", "")
        for w in _WORD_RE.findall(text.lower())
        if w not in _STOPWORDS
    } - {""}


def is_routing(sentence: str, tool_names: frozenset[str], self_name: str) -> bool:
    """True when every clause of *sentence* only points at another tool.

    A sentence that names another tool *and* asserts something ("Navigation
    property names are case-sensitive — use dataverse_list_relationships to
    discover the correct name") keeps its assertion, so it is judged clause by
    clause and kept whole if any clause stands on its own.
    """
    referenced = {
        name
        for name in re.findall(r"\bdataverse_[a-z0-9_]+\b", sentence)
        if name in tool_names and name != self_name
    }
    if not referenced:
        return False

    routing_seen = False
    for index, clause in enumerate(_CLAUSE_SPLIT_RE.split(sentence)):
        clause = clause.strip()
        if not clause:
            continue
        clause_refs = [name for name in referenced if name in clause]
        if not clause_refs:
            # An independent assertion keeps the whole sentence.  It has to
            # open the sentence or start a new one: a mid-sentence clause in
            # lower case ("... — the enumeration, when you do not yet know
            # which table to point at — use X") qualifies the pointer rather
            # than standing apart from it.  Four content words, because a
            # shorter lead-in ('To answer "WHICH tables are OK?"') introduces
            # the pointer too.
            independent = index == 0 or clause[:1].isupper()
            if independent and len(_content_words(clause)) >= 4:
                return False
            continue
        blanked = clause
        for name in clause_refs:
            blanked = blanked.replace(name, "TOOL")
        if any(pattern.search(blanked) for pattern in _ROUTING_RES):
            routing_seen = True
        else:
            return False
    return routing_seen


def is_restatement(
    sentence: str, param_words: frozenset[str], param_names: frozenset[str]
) -> bool:
    """True when the sentence says what the parameter table says below it.

    Two shapes, both containment rather than similarity — the table is the
    authority, so a sentence already covered by it is a repeat:

    * its vocabulary is drawn from the Notes column ("The job must be in a
      cancellable state (statecode 0=Ready, ...)"), needing enough words to be
      evidence rather than coincidence;
    * it is a roll-call of parameter names ("Mutable fields: name, displayname,
      description." / "Required: uniquename ..., name ..."), which the
      Req/Default columns already lay out.
    """
    if _ROLL_CALL_RE.match(sentence):
        return True
    words = _content_words(sentence)
    if len(words) >= 5 and len(words & param_words) / len(words) >= 0.85:
        return True
    hits = words & param_names
    return len(words) >= 3 and len(hits) >= 2 and len(hits) / len(words) >= 0.5


def is_dropped(
    sentence: str,
    tool_names: frozenset[str],
    self_name: str,
    param_words: frozenset[str],
    param_names: frozenset[str],
) -> bool:
    text = sentence.strip()
    if not text:
        return True
    if _GATE_RE.match(text):
        return True
    if _GENERIC_ADVICE_RE.search(text):
        return True
    if is_routing(text, tool_names, self_name):
        return True
    return is_restatement(text, param_words, param_names)


# --- scoring ---------------------------------------------------------------

# Two shouted words in a row, or an explicit marker: the docstrings use these as
# paragraph headings for the caveats.
_CAPS_WORD = r"[A-Z][A-Z0-9'’/-]{2,}"
_BANNER_RE = re.compile(rf"\b{_CAPS_WORD}\s+{_CAPS_WORD}\b")
_MARKER_RE = re.compile(r"^\s*(?:WARNING|IMPORTANT|CAUTION|NOTE|Note)\b\s*[:,—–-]")

_SIGNALS: tuple[tuple[re.Pattern[str], int], ...] = (
    # An expectation contradicted.
    (re.compile(r"\b(?:not|never|cannot|can't|no|nothing|neither|nor|none)\b", re.I), 2),
    # A way this bites: an error, a silent wrong answer, a cap.
    (
        re.compile(
            r"\b(?:fail\w*|error\w*|reject\w*|refus\w*|silently|stale|lags?|lagging|"
            r"clobber\w*|truncat\w*|capp?e?d?|lies|wrong|breaks?|broken|irreversible|"
            r"misdiagnos\w*|invalid|timeout|throttl\w*|ambiguous|conflict\w*)\b"
            r"|\bHTTP\s*\d{3}\b|\b0x[0-9A-Fa-f]{8}\b",
            re.I,
        ),
        2,
    ),
    # A value this server derived rather than read from a contract.  "guessed"
    # is deliberately absent: "nothing is guessed" is boilerplate about the
    # server's own discipline on a dozen tools, not a caveat for the reader.
    (
        re.compile(
            r"\b(?:hand-rolled|hand rolled|empirical\w*|undocumented|not documented|"
            r"approximate\w*|snapshot|inferred|heuristic|unmapped)\b",
            re.I,
        ),
        2,
    ),
    # An obligation on the caller.
    (re.compile(r"\b(?:must|require[sd]?|only|before|until)\b", re.I), 1),
)

# --- what earns a note -----------------------------------------------------
#
# The score above ranks caveats against each other; it cannot decide whether a
# sentence is a caveat at all.  A bold "**Note:**" on a merely descriptive line
# is *louder* than plain prose, so it adds exactly the padding this pass exists
# to remove.  The test is therefore: would acting on the tool without knowing
# this produce a wrong result, a failure, or an irreversible change?  Glossary
# definitions, response descriptions and when-to-use guidance all fail it,
# however true they are, and are not notes.

# Unmistakable, and strong enough to survive a descriptive opening: a one-way
# door, an immutable field, a hard limit, a named failure or a silent success.
_STRICT_BURN_RE = re.compile(
    r"\b(?:cannot|can't|never|irreversible|permanently)\b"
    r"|\b(?:immutable|not mutable|not safely mutable)\b"
    r"|case[- ]sensitive"
    r"|\bwill fail\b|\bwait until\b|\basynchronous\w*\b"
    r"|\b(?:returns?|is|as) an error\b|\ban ERROR\b"
    r"|\bHTTP\s*\d{3}\b|\b0x[0-9A-Fa-f]{8}\b"
    r"|\b(?:silently|rejects?|rejected|refuses?|clobber\w*)\b"
    r"|\b(?:up to|at most|maximum of|capped at|a maximum of|limit of)\s+~?[\d,]+"
    r"|\bcapped\b|\bcap (?:applies|of)\b",
    re.I,
)

# Also a real caveat, but only from a sentence that is not already announcing
# itself as a description: a restriction, an obligation, a plain failure.
_HARD_BURN_RE = re.compile(
    rf"{_STRICT_BURN_RE.pattern}"
    r"|\b(?:fails?|failing|failed)\b"
    r"|\bonly (?:valid|works|supported|applies|possible|unmanaged|custom)\b"
    r"|^Only\b"
    r"|\b(?:must|requires?|required)\b",
    re.I,
)

# Weaker on its own: a plain negation, a stale/derived value, a hard number.
# Enough for a sentence whose shape is already a caveat, not enough to promote
# a description into one.
_SOFT_BURN_RE = re.compile(
    r"\b(?:not|no|nothing|neither|nor|none|without)\b"
    r"|\b(?:stale|lags?|lagging|snapshot|approximate\w*|truncat\w*|wrong|lies|"
    r"broken|breaks?|invalid|timeout|throttl\w*|ambiguous|unmapped|"
    r"hand-rolled|hand rolled|empirical\w*|undocumented|inferred|heuristic)\b"
    r"|\bwithin \d+[\d–-]*\s*(?:seconds|minutes|hours|days)\b",
    re.I,
)

# Openings that announce a description, a roll-call or a piece of when-to-use
# guidance.  Written for a model choosing between tools, not for a reader who
# has already chosen one.
_DESCRIPTIVE_SHAPE_RE = re.compile(
    # "Each record includes ..." is a description; "Each record is PATCHed
    # individually (not in a change set)" is a caveat, so the verb matters.
    r"^(?:Returns?|Also returns|Also includes|Includes|"
    r"Each (?:record|entry) (?:includes|carries|has|contains)|"
    r"Use|Using|Call|Run|Try|Provide|Supply|Pass|Set|Omit|Filter|Scope|"
    r"Results?|Records are|Tracks|Gathers|Merges|Parses|Builds|Resolves|"
    r"Prerequisite|First step|Second step|Final step)\b",
    re.I,
)

# A glossary line — "X lets you …", "X represents …", "A Y links …".  True, and
# not something a reader can act wrongly on.
_GLOSSARY_RE = re.compile(r"^[^.]{0,60}?\b(?:lets?|represents?|links?|denotes?)\b")


def earns_a_note(sentence: str) -> bool:
    """True when not knowing this sentence could burn the reader.

    The bar is deliberately behavioural: a wrong result, a failure or an
    irreversible change.  A sentence that only describes, defines or advises
    fails it however true it is, because a bold **Note:** makes it louder than
    the prose around it and that is what reads as padding.
    """
    # The docstring author flagged it themselves ("WARNING: Setting 'all'
    # generates high log volume") — that is a judgement worth trusting over any
    # keyword test.
    if _MARKER_RE.match(sentence):
        return True
    if _DESCRIPTIVE_SHAPE_RE.match(sentence) or _GLOSSARY_RE.match(sentence):
        return bool(_STRICT_BURN_RE.search(sentence))
    return bool(_HARD_BURN_RE.search(sentence) or _SOFT_BURN_RE.search(sentence))


# Past this, a "note" is a paragraph again; nudge it below a shorter caveat.
_LONG_SENTENCE = 240
# An opening that points back at a sentence the page may not be showing.
_ANAPHOR_RE = re.compile(
    r"^(?:That|This|These|Those|Its|Their|It|They|Both|Such|Also)\b"
)


def is_banner(sentence: str) -> bool:
    return bool(_BANNER_RE.search(sentence) or _MARKER_RE.match(sentence))


def score_sentence(sentence: str, mid_block: bool = False) -> int:
    """Rate how badly a reader would be burned by not knowing this sentence."""
    score = 3 if is_banner(sentence) else 0
    for pattern, weight in _SIGNALS:
        if pattern.search(sentence):
            score += weight
    if len(sentence) > _LONG_SENTENCE:
        score -= 1
    # A note has to stand alone.  "That join table is private..." mid-paragraph
    # refers to something the page dropped, so prefer a sibling that doesn't.
    if mid_block and _ANAPHOR_RE.match(sentence):
        score -= 1
    return score


# A sentence opening with one of these cannot stand alone: it completes the
# sentence before it, so the two travel together or not at all.
_CONNECTIVE_RE = re.compile(
    r"^(?:Worse|However|But|Yet|Conversely|Otherwise|In other words|Note that)\b"
)

# Only a banner this short is a heading standing in for a paragraph; a long
# sentence that merely shouts a phrase says enough on its own.
BANNER_MAX = 130


def tidy_note(text: str) -> str:
    """Drop a leading ``Note:`` / ``IMPORTANT —`` — the label already says it.

    A trailing colon also loses its job here: whatever it introduced in the
    docstring is a list this page does not render.
    """
    stripped, marker_count = _MARKER_RE.subn("", text)
    stripped = stripped.strip()
    if marker_count:
        head = stripped.split(" ", 1)[0]
        # Only a plain word; "total_count COMES FROM..." keeps its identifier.
        if head.isalpha() and head.islower():
            stripped = stripped[0].upper() + stripped[1:]
    if stripped.endswith(":"):
        stripped = f"{stripped[:-1]}."
    return stripped


@dataclass(frozen=True)
class _Candidate:
    block: int
    index: int
    text: str
    score: int


def select_notes(
    description: str, self_name: str, tool_names: frozenset[str], params: list["Param"]
) -> list[str]:
    """Pick the notes for a tool: the caveats a reader cannot infer below.

    Chosen by score, emitted in document order.  When any sentence scores as a
    real caveat, only those are eligible — a warning should not have to share
    the page with a merely descriptive line.
    """
    blocks = split_blocks(description)
    param_words = frozenset(_content_words(" ".join(p.notes for p in params)))
    param_names = frozenset(_content_words(" ".join(p.name for p in params)))

    candidates: list[_Candidate] = []
    for block_index, block in enumerate(blocks[1:], start=1):
        for index, sentence in enumerate(block_sentences(block)):
            if is_dropped(sentence, tool_names, self_name, param_words, param_names):
                continue
            score = score_sentence(sentence, mid_block=index > 0)
            # A sentence ending in ":" announces a list this page does not
            # render, so on its own it is a dangling lead-in — unless it makes
            # its point before the colon ("MIND THE SINGULAR/PLURAL SPLIT — ...
            # they are NOT interchangeable:").
            if sentence.endswith(":") and score < STRONG_SCORE:
                continue
            candidates.append(_Candidate(block_index, index, sentence, score))

    # Two tiers.  Everything that survived the drop classes stays available for
    # pairing (a banner's own sentence need not be a caveat by itself), but only
    # a sentence that could burn the reader can be *selected* as a note.
    eligible = [c for c in candidates if earns_a_note(c.text)]
    strong = [c for c in eligible if c.score >= STRONG_SCORE]
    picks = sorted(
        (c for c in (strong or eligible) if len(c.text) <= NOTES_BUDGET),
        key=lambda c: (-c.score, c.block, c.index),
    )
    by_position = {(c.block, c.index): c for c in candidates}

    chosen: list[tuple[tuple[int, int], str]] = []
    consumed: set[tuple[int, int]] = set()
    budget = 0
    for candidate in picks:
        if len(chosen) >= MAX_NOTES or (chosen and budget >= NOTES_BUDGET):
            break
        if (candidate.block, candidate.index) in consumed:
            continue
        text = candidate.text
        position = (candidate.block, candidate.index)
        # A heading belongs to the sentence under it: if the paragraph this
        # sentence sits in opens with a banner, bring the banner along.
        heading_above = by_position.get((candidate.block, candidate.index - 1))
        if (
            heading_above is not None
            and heading_above.index == 0
            and heading_above.text not in text
            and (heading_above.block, heading_above.index) not in consumed
            and is_banner(heading_above.text)
            and len(heading_above.text) <= BANNER_MAX
        ):
            text = f"{heading_above.text} {text}"
            position = (heading_above.block, heading_above.index)
            consumed.add(position)

        follower = by_position.get((candidate.block, candidate.index + 1))
        # A shouted banner is a heading rather than a sentence, and a sentence
        # opening "Worse, ..." completes the one before it: either way the pair
        # is one note.
        heading = is_banner(candidate.text) and len(candidate.text) <= BANNER_MAX
        if (
            follower is not None
            and len(follower.text) <= FOLLOWER_MAX
            and (heading or _CONNECTIVE_RE.match(follower.text))
        ):
            text = f"{text} {follower.text}"
            consumed.add((follower.block, follower.index))
        consumed.add((candidate.block, candidate.index))
        chosen.append((position, tidy_note(text)))
        budget += len(text)
    return [text for _position, text in sorted(chosen)]


# ---------------------------------------------------------------------------
# Return-shape derivation
# ---------------------------------------------------------------------------


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return None


def derive_returns(fn: object) -> str:
    """Derive the Returns line from literal success payloads in the tool body.

    Only literal ``dict`` payloads handed straight to ``json.dumps`` /
    ``finalize_response`` are read.  If any success-path return is not literal
    the derived shape would be incomplete, so nothing is emitted — the page
    header already states the contract every tool shares, and repeating it per
    tool was 36 identical lines of noise.  Never guesses.
    """
    try:
        source = textwrap.dedent(inspect.getsource(fn))  # type: ignore[arg-type]
        tree = ast.parse(source)
    except (OSError, TypeError, SyntaxError):  # pragma: no cover - defensive
        return ""

    shapes: list[tuple[str, ...]] = []
    opaque = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        value = node.value
        if isinstance(value, ast.Call):
            name = _call_name(value)
            if name in _ERROR_HELPERS:
                continue
            if name in _RESPONSE_WRAPPERS and value.args:
                payload = value.args[0]
                if (
                    isinstance(payload, ast.Dict)
                    and payload.keys
                    and all(
                        isinstance(k, ast.Constant) and isinstance(k.value, str)
                        for k in payload.keys
                    )
                ):
                    keys = tuple(k.value for k in payload.keys)  # type: ignore[union-attr]
                    if "error" in keys:
                        continue  # error envelope, documented separately
                    if keys not in shapes:
                        shapes.append(keys)
                    continue
        opaque = True

    if opaque or not shapes or len(shapes) > _MAX_DERIVED_SHAPES:
        return ""

    rendered = " or ".join(
        "{" + ", ".join(f'"{k}": ...' for k in keys) + "}" for keys in shapes
    )
    return f"Returns `{rendered}`."


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def build_params(model: type[BaseModel]) -> list[Param]:
    """Build the parameter table rows for a Pydantic input model."""
    params: list[Param] = []
    for name, fld in model.model_fields.items():
        notes_parts: list[str] = []
        if fld.description:
            notes_parts.append(fld.description)
        constraints = render_constraints(list(fld.metadata))
        if constraints:
            notes_parts.append("(" + ", ".join(constraints) + ")")
        params.append(
            Param(
                name=name,
                type=render_type(fld.annotation),
                required=fld.is_required(),
                default=render_default(fld),
                notes=" ".join(" ".join(notes_parts).split()) or "—",
            )
        )
    return params


async def collect_tools() -> list[ToolDoc]:
    """Introspect every registered tool into a ToolDoc."""
    docs: list[ToolDoc] = []
    for wire_tool in await mcp.list_tools():
        name = wire_tool.name
        category = TOOL_CATEGORIES.get(name)
        if category is None:
            logger.warning("Tool %s has no recorded category — skipping", name)
            continue
        if category not in CATEGORY_PAGES:
            logger.warning("Tool %s has unknown category %r — skipping", name, category)
            continue

        annotations = wire_tool.annotations
        read_only = bool(getattr(annotations, "read_only_hint", False))
        destructive = bool(getattr(annotations, "destructive_hint", False))
        idempotent = bool(getattr(annotations, "idempotent_hint", False))
        access = "Read" if read_only else "Delete" if destructive else "Write"

        # The wire Tool drops the callable; reach the internal registration
        # object for the function so the Pydantic input model is available.
        fn = mcp._tool_manager.get_tool(name).fn
        model = inspect.signature(fn).parameters["params"].annotation
        if not (inspect.isclass(model) and issubclass(model, BaseModel)):
            logger.warning("Tool %s has no Pydantic input model — no param table", name)
            params: list[Param] = []
        else:
            params = build_params(model)

        description = wire_tool.description or ""
        docs.append(
            ToolDoc(
                name=name,
                category=category,
                access=access,
                idempotent=idempotent,
                summary=first_sentence(description),
                description=description,
                returns=derive_returns(fn),
                params=params,
            )
        )
    docs.sort(key=lambda d: d.name)
    return docs


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_tool_block(doc: ToolDoc, tool_names: frozenset[str]) -> list[str]:
    lines = [
        f"### {doc.name}",
        "",
        f"{doc.access} · {'idempotent' if doc.idempotent else 'non-idempotent'} "
        f"· category `{doc.category}`",
        "",
    ]
    lines += render_lead(doc.description)
    lines.append("")
    for note in select_notes(doc.description, doc.name, tool_names, doc.params):
        lines += [f"**Note:** {protect_angle_brackets(note)}", ""]

    if doc.params:
        lines += [
            "| Param | Type | Req | Default | Notes |",
            "|---|---|---|---|---|",
        ]
        overflow: list[Param] = []
        for param in doc.params:
            if len(param.notes) > NOTES_INLINE_MAX:
                overflow.append(param)
                notes = "See note below."
            else:
                notes = param.notes
            lines.append(
                f"| {escape_cell(param.name)} "
                f"| `{escape_cell(param.type)}` "
                f"| {'yes' if param.required else 'no'} "
                f"| {escape_cell(param.default)} "
                f"| {escape_prose_cell(notes)} |"
            )
        if overflow:
            lines.append("")
            for param in overflow:
                lines.append(f"- **{param.name}** — {protect_angle_brackets(param.notes)}")
    else:
        lines.append("_No parameters._")
    lines.append("")
    if doc.returns:
        lines += [doc.returns, ""]
    return lines


def render_category_page(
    category: str, docs: list[ToolDoc], tool_names: frozenset[str]
) -> str:
    _, title = CATEGORY_PAGES[category]
    lines = [
        f"# {title} tools",
        "",
        GENERATED_BANNER,
        "",
        f"{len(docs)} tool{'s' if len(docs) != 1 else ''} · category token "
        f"`{category}` · enable with `DATAVERSE_TOOLS={category}` "
        "(unset enables every category)",
        "",
        f"[[{INDEX_PAGE}]] · [[Home]]",
        "",
        f"Every tool returns JSON. {ERROR_CONTRACT} Write tools require "
        "`DATAVERSE_ALLOW_WRITE=true`; delete tools require "
        "`DATAVERSE_ALLOW_DELETE=true`.",
        "",
        "---",
        "",
    ]
    for doc in docs:
        lines += render_tool_block(doc, tool_names)
        lines += ["---", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def render_index_page(docs: list[ToolDoc]) -> str:
    by_category: dict[str, int] = {}
    for doc in docs:
        by_category[doc.category] = by_category.get(doc.category, 0) + 1

    lines = [
        "# Tool index",
        "",
        GENERATED_BANNER,
        "",
        f"All {len(docs)} tools, alphabetically. Use your browser's find "
        "(Ctrl+F) to locate a tool, then follow the link for its parameters.",
        "",
        "[[Home]]",
        "",
        "## Tools by category",
        "",
        "| Category | `DATAVERSE_TOOLS` token | Page | Tools |",
        "|---|---|---|---|",
    ]
    for category, (page, title) in CATEGORY_PAGES.items():
        lines.append(
            f"| {title} | `{category}` | [[{page}]] | {by_category.get(category, 0)} |"
        )
    lines += [
        f"| **Total** | | | **{len(docs)}** |",
        "",
        "`core` is always enabled. Write tools also require "
        "`DATAVERSE_ALLOW_WRITE=true`; delete tools require "
        "`DATAVERSE_ALLOW_DELETE=true`.",
        "",
        "## All tools",
        "",
        "| Tool | Category | Access | Purpose |",
        "|---|---|---|---|",
    ]
    for doc in docs:
        page, title = CATEGORY_PAGES[doc.category]
        link = f"[{doc.name}]({page}#{doc.name})"
        lines.append(
            f"| {link} | {title} | {doc.access} | {escape_prose_cell(doc.summary)} |"
        )
    return "\n".join(lines).rstrip("\n") + "\n"


def render_pages(docs: list[ToolDoc]) -> dict[str, str]:
    """Return {filename: content} for every page this generator owns."""
    tool_names = frozenset(d.name for d in docs)
    pages = {f"{INDEX_PAGE}.md": render_index_page(docs)}
    for category, (page, _title) in CATEGORY_PAGES.items():
        in_category = [d for d in docs if d.category == category]
        pages[f"{page}.md"] = render_category_page(category, in_category, tool_names)
    return pages


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_pages(pages: dict[str, str], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in sorted(pages.items()):
        (out_dir / filename).write_text(content, encoding="utf-8", newline="\n")
        logger.info("Wrote %s", out_dir / filename)


def check_pages(pages: dict[str, str], out_dir: Path) -> int:
    """Regenerate into a temp dir and byte-compare against *out_dir*.

    Returns a process exit code: 0 when every page is current, 1 otherwise.
    """
    stale: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        write_pages(pages, tmp_dir)
        for filename in sorted(pages):
            expected = (tmp_dir / filename).read_bytes()
            target = out_dir / filename
            if not target.exists():
                stale.append(f"  missing: {filename}")
                continue
            actual = target.read_bytes()
            if actual != expected:
                stale.append(
                    f"  changed: {filename} "
                    f"({len(actual)} bytes on disk vs {len(expected)} generated)"
                )
    if stale:
        logger.error(
            "Generated wiki pages are stale in %s:\n%s\n"
            "Re-run: uv run python scripts/gen_wiki_tools.py --out <wiki>",
            out_dir,
            "\n".join(stale),
        )
        return 1
    logger.info("All %d generated wiki pages in %s are up to date", len(pages), out_dir)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=_REPO_ROOT.parent / "dataverse-mcp.wiki",
        help="Wiki repo directory to write into (default: ../dataverse-mcp.wiki)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit non-zero if the committed pages are stale.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s: %(message)s",
    )

    docs = asyncio.run(collect_tools())
    if not docs:
        logger.error("No tools collected — refusing to write empty wiki pages")
        return 1
    logger.info("Collected %d tools across %d categories", len(docs), len(CATEGORY_PAGES))

    pages = render_pages(docs)
    if args.check:
        return check_pages(pages, args.out)
    write_pages(pages, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
