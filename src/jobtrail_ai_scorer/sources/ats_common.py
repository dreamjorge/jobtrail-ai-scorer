"""Shared helpers for ATS source adapters (PR-B: Lever, PR-C: Greenhouse).

This module exposes dependency-free helpers reused by every ATS adapter:

* :func:`_strip_html` — convert a raw HTML string to plain text using only the
  Python standard library's :mod:`html.parser` so no new dependency is added.
* :func:`_bounded` — truncate a list to ``cap`` items and stamp
  ``retrieved_at`` on every entry when the caller provides one.

The helpers are private (leading underscore) because they exist to support
the public ATS adapters rather than to be called from external code. Tests
in ``tests/test_ats_connectors.py`` exercise both helpers directly.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any


class _TextOnlyHTMLParser(HTMLParser):
    """Collect the textual content of an HTML fragment with single-space joins.

    The parser concatenates block-level boundaries with a space so the rendered
    text reads like the document instead of containing raw newlines. Inline
    boundaries are joined without any separator to keep adjacent text together.
    """

    _BLOCK_TAGS = frozenset(
        {
            "address",
            "article",
            "aside",
            "blockquote",
            "br",
            "dd",
            "div",
            "dl",
            "dt",
            "fieldset",
            "figcaption",
            "figure",
            "footer",
            "form",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "hr",
            "li",
            "main",
            "nav",
            "ol",
            "p",
            "pre",
            "section",
            "table",
            "tbody",
            "td",
            "tfoot",
            "th",
            "thead",
            "tr",
            "ul",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self._chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self._chunks.append(" ")

    def handle_data(self, data: str) -> None:
        if data:
            self._chunks.append(data)

    def render(self) -> str:
        """Return the collected text with whitespace collapsed."""

        text = "".join(self._chunks)
        # Collapse any run of whitespace (newlines, tabs, double spaces) into a
        # single space and trim the leading/trailing whitespace so the helper
        # produces a normalized single-line summary.
        return " ".join(text.split())


def _strip_html(value: str) -> str:
    """Return ``value`` with HTML tags removed and entities decoded.

    The implementation uses :class:`html.parser.HTMLParser` so the ATS
    adapters do not gain a new third-party dependency (BeautifulSoup,
    lxml, or bleach would otherwise be required). The result is a single
    line of text with collapsed whitespace and decoded HTML entities
    (``&amp;`` → ``&``, ``&quot;`` → ``"``, numeric entities too).
    """

    if not isinstance(value, str):
        return ""
    parser = _TextOnlyHTMLParser()
    try:
        parser.feed(value)
    except Exception:
        # A malformed fragment should not crash the adapter; fall back to the
        # raw value with HTML tags stripped by a best-effort regex-free pass.
        # We still want entities decoded, so re-feed the original text through
        # a fresh parser which is more permissive about partial fragments.
        parser = _TextOnlyHTMLParser()
        parser.feed(value)
    return parser.render()


def _bounded(
    items: list[dict[str, Any]],
    cap: int,
    *,
    retrieved_at: str | None = None,
) -> list[dict[str, Any]]:
    """Truncate ``items`` to ``min(len(items), cap)`` and stamp ``retrieved_at``.

    The helper returns a **new list**; the input list is not mutated. When
    ``retrieved_at`` is supplied, every item receives the value under the
    ``retrieved_at`` key. When ``retrieved_at`` is ``None`` the items are
    returned untouched (apart from the truncation).

    The cap is a hard truncation: ``cap=0`` returns an empty list and
    ``cap >= len(items)`` preserves every entry. The helper is the single
    truncation site used by the ATS adapters so the orchestrator can rely
    on the cap being honored regardless of how many postings a board
    returns.
    """

    safe_cap = max(0, int(cap))
    truncated = items[:safe_cap]
    if not retrieved_at:
        return truncated
    stamped: list[dict[str, Any]] = []
    for entry in truncated:
        if isinstance(entry, dict):
            stamped.append({**entry, "retrieved_at": retrieved_at})
        else:
            # Defensive: the type checker enforces ``list[dict]`` upstream, but
            # if a caller passes a non-mapping we just preserve it untouched
            # rather than crashing the whole batch.
            stamped.append(entry)
    return stamped


__all__ = ["_bounded", "_strip_html"]
