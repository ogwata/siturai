"""Isolated VFM rendering.

This is the ONLY place in the PoC that knows about VFM syntax.

VFM image rules (https://docs.vivliostyle.org/ja/vfm/vfm/#image):
  * A paragraph containing a SINGLE image is wrapped in <figure>, and the
    image's alt text becomes the <figcaption>. So we do NOT emit a separate
    caption line — the alt IS the caption.
  * `![alt](src "title"){attr}` — the `{...}` block holds HTML attributes.
    Class shorthand is `{.class-name}` (a dot prefix). Values that need
    quoting use `key="value"`.
  * Physical sizing (width, full-width spread, float) belongs in CSS/theme,
    keyed off these classes — NOT hardcoded as inline dimensions in Markdown.
    A theme then defines e.g. `figure.full-width img { width: 100%; }`.

Placement → markup:
  * inline            → image inside a line of text (no figure wrapping)
  * figure            → standalone paragraph image → <figure> (default look)
  * figure-full-width → standalone image with class .full-width
  * float             → standalone image with class .float
"""
from __future__ import annotations

PLACEMENT_CLASS = {
    "figure-full-width": "full-width",
    "float": "float",
}


def _escape_quotes(s: str) -> str:
    return s.replace('"', '\\"')


def render_placement(proposal: dict, image_relpath: str) -> str:
    """Return the VFM snippet to insert after the chosen block."""
    alt = (proposal.get("caption") or proposal.get("alt") or "").strip()
    placement = proposal.get("placement", "figure")

    # inline: keep the image within a short line of text (no figure/caption).
    if placement == "inline":
        return f"text ![{alt}]({image_relpath})"

    # standalone image → VFM wraps it in <figure> and alt becomes <figcaption>.
    cls = PLACEMENT_CLASS.get(placement)
    attr = f"{{.{cls}}}" if cls else ""
    return f"![{_escape_quotes(alt)}]({image_relpath}){attr}"
