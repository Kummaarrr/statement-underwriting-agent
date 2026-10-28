"""PDF forensics across the statements of one file: what changed in one document but not the
others. Strong evidence flags the file; weak evidence is only reported.

Strong (an edit inside the document):
- text set in a font the rest of the file never uses (edited rows or recomputed cells);
- an embedded subset font in a statement whose siblings embed none;
- an incremental update (a second cross-reference section: how PDF editors save).
Weak (also true of innocent re-saves - printed to PDF, opened in Preview, downloaded again):
- a different producer than the other statements; modified long after it was created.
Image-only scans carry none of this; for them only the arithmetic can speak.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from stmt_uw.v1.pdfio import PdfFacts


@dataclass
class Finding:
    statement: int
    strength: str  # strong / weak
    detail: str


def _days_apart(a: str, b: str) -> float:
    try:
        import datetime as dt

        fa = dt.datetime.strptime(a[2:16], "%Y%m%d%H%M%S")
        fb = dt.datetime.strptime(b[2:16], "%Y%m%d%H%M%S")
        return abs((fb - fa).total_seconds()) / 86400
    except ValueError:
        return 0.0


def inspect(facts: list[PdfFacts]) -> list[Finding]:
    out: list[Finding] = []
    digital = [f for f in facts if f.text_layer]
    if not digital:
        return out
    producers = Counter(f.producer for f in digital)
    usual_producer = producers.most_common(1)[0][0]
    for i, f in enumerate(facts):
        if not f.text_layer:
            continue
        others = [g for j, g in enumerate(facts) if j != i and g.text_layer]
        other_fonts = set().union(*(g.fonts for g in others)) if others else set()
        if others:
            odd = {name: n for name, n in f.fonts.items() if name not in other_fonts}
            if odd:
                spans = sum(odd.values())
                out.append(
                    Finding(
                        i,
                        "strong",
                        f"{spans} text span(s) in font(s) "
                        f"{', '.join(sorted(odd))} used nowhere else in the file",
                    )
                )
            if f.embedded_fonts and not any(g.embedded_fonts for g in others):
                out.append(
                    Finding(
                        i,
                        "strong",
                        "embeds a subset font "
                        f"({', '.join(sorted(f.embedded_fonts))}); its siblings embed none",
                    )
                )
        if f.xref_sections > 1:
            out.append(
                Finding(
                    i,
                    "strong",
                    f"saved as an incremental update "
                    f"({f.xref_sections} cross-reference sections) - typical of PDF editors",
                )
            )
        if len(digital) > 1 and f.producer != usual_producer:
            out.append(
                Finding(i, "weak", f"produced by '{f.producer}', the others by '{usual_producer}'")
            )
        if f.created and f.modified and _days_apart(f.created, f.modified) > 2:
            out.append(Finding(i, "weak", "modified long after it was created"))
    return out
