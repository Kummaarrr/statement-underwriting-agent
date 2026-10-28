"""PDF access for v1: per-page content in three modes, and the raw facts forensics needs.

Modes (each measured in the ablation):
- "text":  the page's text layer only (cheapest; exact characters; none on scans);
- "pdf":   the single page as a PDF document block (the model gets the text layer and an image);
- "image": the page rendered to PNG (for scans, or when the text layer is misleading).
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import pymupdf


@cache
def page_count(path: Path) -> int:
    with pymupdf.open(path) as doc:
        return doc.page_count


def text_pages(path: Path) -> list[bool]:
    """Per page: does it have a text layer? (mixed files: digital pages + scanned pages)"""
    with pymupdf.open(path) as doc:
        return [bool(doc[i].get_text().strip()) for i in range(doc.page_count)]


def has_text_layer(path: Path) -> bool:
    with pymupdf.open(path) as doc:
        return any(doc[i].get_text().strip() for i in range(doc.page_count))


def page_text(path: Path, page: int) -> str:
    with pymupdf.open(path) as doc:
        return str(doc[page].get_text("text", sort=True))


def page_block(path: Path, page: int, mode: str, dpi: int = 200) -> list[dict[str, Any]]:
    """Content blocks presenting one page in `mode`."""
    if mode == "text":
        return [{"type": "text", "text": f"<page_text>\n{page_text(path, page)}\n</page_text>"}]
    with pymupdf.open(path) as doc:
        if mode == "pdf":
            single = pymupdf.open()
            single.insert_pdf(doc, from_page=page, to_page=page)
            data = single.tobytes(deflate=True, no_new_id=True)  # stable bytes: cacheable
            single.close()
            return [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.b64encode(data).decode(),
                    },
                }
            ]
        png = doc[page].get_pixmap(dpi=dpi).tobytes("png")
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(png).decode(),
            },
        }
    ]


@dataclass
class PdfFacts:
    """What a fraud analyst inspects in a statement file."""

    producer: str
    creator: str
    created: str
    modified: str
    xref_sections: int  # >1: incremental update (how editors save)
    fonts: dict[str, int] = field(default_factory=dict)  # font -> text spans (page footer excluded)
    embedded_fonts: set[str] = field(default_factory=set)
    text_layer: bool = True


def pdf_facts(path: Path) -> PdfFacts:
    raw = path.read_bytes()
    with pymupdf.open(path) as doc:
        meta = doc.metadata or {}
        fonts: dict[str, int] = {}
        embedded: set[str] = set()
        text = False
        for i in range(doc.page_count):
            for f in doc.get_page_fonts(i):
                name = f[3]
                if "+" in name:  # subset prefix = embedded subset font
                    embedded.add(name.split("+", 1)[1])
            for block in doc[i].get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        if not span["text"].strip() or re.match(r"Page \d+ of \d+", span["text"]):
                            continue
                        text = True
                        name = span["font"].split("+")[-1]
                        fonts[name] = fonts.get(name, 0) + 1
    return PdfFacts(
        producer=meta.get("producer", "") or "",
        creator=meta.get("creator", "") or "",
        created=meta.get("creationDate", "") or "",
        modified=meta.get("modDate", "") or "",
        # a linearized ("fast web view") file has two by design; more means saved again
        xref_sections=raw.count(b"startxref") - (b"/Linearized" in raw[:4096]),
        fonts=fonts,
        embedded_fonts=embedded,
        text_layer=text,
    )
