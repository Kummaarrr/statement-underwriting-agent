"""Check the generated PDFs against data/dataset.json.

    uv run python scripts/verify_dataset.py

- digital and tampered PDFs: every printed amount and balance is in the text layer, no page
  text says the file is synthetic (only the metadata does), the producer is the bank's (or a
  re-save tool / PDF editor exactly where intended), modification date later than creation
  date only on re-saved and edited statements;
- scans: no text layer, scanner producer;
- running balances: break only in T1 (edit_amounts); statements chain (closing = next
  opening) except right after an edited statement that isn't the newest;
- fonts: other fonts only on edited statements (T1: the classic font swap; T2/T3: the editor's
  embedded font), none on T4 or genuine files.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pymupdf

from stmt_uw.synth.render import ALT_FONT, BANKS, EDITOR_FONT, money
from stmt_uw.synth.variants import EDITORS, RESAVERS, SCANNERS

ROOT = Path(__file__).resolve().parents[1]
STYLE = {"harborview": "trailing", "summit": "crdr", "pinecrest": "signed"}


def _alt_spans(doc: pymupdf.Document, fonts: set[str]) -> int:
    """Text spans set in one of `fonts` (ignoring the 'Page x of y' footer, always Helvetica)."""
    n = 0
    for i in range(doc.page_count):
        for block in doc[i].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["font"].split("+")[-1] in fonts and not span["text"].startswith(
                        "Page "
                    ):
                        n += 1
    return n


def main() -> int:
    records = json.loads((ROOT / "data" / "dataset.json").read_text(encoding="utf-8"))
    problems: list[str] = []
    pages: Counter[str] = Counter()
    for r in records:
        bank = BANKS[r["bank"]]
        tamper = r["tamper"] or {}
        resaved = r.get("resaved") or {}
        sts = r["statements"]
        for k, st in enumerate(sts):
            doc = pymupdf.open(ROOT / st["file"])
            pages[r["variant"]] += doc.page_count
            text = "".join(doc[i].get_text() for i in range(doc.page_count))
            meta = doc.metadata or {}
            where = f"{r['id']} {Path(st['file']).name}"
            producer = meta.get("producer")
            # balances recomputed in an older statement: the next one no longer chains
            want_gap = (
                bool(tamper)
                and tamper["statement_index"] == k - 1
                and tamper["type"] in ("edit_recalc", "insert_rows")
            )
            if k and (st["opening"] != sts[k - 1]["closing"]) != want_gap:
                problems.append(f"{where}: opening vs previous closing (gap expected={want_gap})")
            if r["variant"] == "scanned":
                if text.strip():
                    problems.append(f"{where}: scan has a text layer")
                if producer not in SCANNERS:
                    problems.append(f"{where}: scan producer {producer}")
                continue
            for bad in ("synthetic", "fictional", "software testing"):
                if bad in text.lower():
                    problems.append(f"{where}: page text contains '{bad}'")
            style = STYLE.get(r["bank"], "plain")
            for t in st["transactions"]:
                amount = money(
                    t["amount"],
                    "crdr" if r["bank"] == "summit" else (style if t["amount"] < 0 else "plain"),
                )
                if amount not in text:
                    problems.append(f"{where}: amount {amount} missing")
                bal_style = "signed" if r["bank"] == "meridian" else style
                if r["bank"] != "atlas" and money(t["balance"], bal_style) not in text:
                    problems.append(f"{where}: balance {t['balance']} missing")
            edited = bool(tamper) and tamper["statement_index"] == k
            traced = edited and tamper["type"] != "perfect_fake"
            is_resaved = bool(resaved) and resaved["statement_index"] == k
            expected = EDITORS if traced else RESAVERS if is_resaved else [bank.producer]
            if producer not in expected:
                problems.append(f"{where}: producer {producer}, expected one of {expected}")
            if (meta.get("modDate") != meta.get("creationDate")) != (traced or is_resaved):
                problems.append(
                    f"{where}: dates created {meta.get('creationDate')} "
                    f"modified {meta.get('modDate')}"
                )
            swap = _alt_spans(doc, {ALT_FONT[bank.font]})
            editor = _alt_spans(doc, {EDITOR_FONT, "BitstreamVeraSans-Roman"})  # embedded name
            want_swap = traced and tamper["type"] == "edit_amounts"
            want_editor = traced and tamper["type"] in ("edit_recalc", "insert_rows")
            if (swap > 0) != want_swap or (editor > 0) != want_editor:
                problems.append(f"{where}: font-swap spans {swap}, editor-font spans {editor}")
            bal, breaks = st["opening"], 0
            for t in st["transactions"]:
                bal += t["amount"]
                breaks += bal != t["balance"]
            if (breaks > 0) != (edited and tamper["type"] == "edit_amounts"):
                problems.append(f"{where}: {breaks} running-balance breaks")
    print(f"pages: {dict(pages)}  total {sum(pages.values())}")
    print(f"{len(problems)} problems")
    for p in problems[:30]:
        print("  ", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
