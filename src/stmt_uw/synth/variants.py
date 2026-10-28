"""Scanned, re-saved and tampered variants of rendered statements.

Scans: pages rasterised with per-file scanner settings (a month occasionally scanned on another
device), skew, smooth uneven paper tone, faded contrast, blur, grain, speckle and JPEG
artefacts, into an image-only PDF (no text layer).

Re-saves (genuine files): one statement re-saved through another tool (macOS Preview, Acrobat,
Chrome): producer and modification date change, content doesn't. These test false alarms.

Tampering (one statement per tampered file), from easy to impossible to catch:
- T1 edit_amounts:  deposit amounts inflated, printed balances left alone -> the maths breaks;
                    edited rows in a visibly different font, re-saved by a PDF tool;
- T2 edit_recalc:   deposits inflated and the statement's later balances recomputed. Edited
                    rows and recomputed balance cells carry the editor's embedded font (close
                    to the bank's), producer and modification date change;
- T3 insert_rows:   fake deposits (a genuine payer's format) inserted, balances recomputed,
                    same traces as T2;
- T4 perfect_fake:  as T3 in the newest statement, regenerated with the bank's own fonts and
                    metadata -> nothing in the document gives it away; kept to report the limit.
Only the edited statement changes, so when it isn't the newest, the next statement's opening
balance no longer matches its closing balance (a fraudster rarely re-edits every later month).
Existing NSF events stay where they were, so they may now sit on a healthy-looking balance.
"""

from __future__ import annotations

import copy
import datetime as dt
import io
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageChops, ImageEnhance, ImageFilter

from stmt_uw.model import REVENUE_CATS, Txn
from stmt_uw.synth.render import EDITOR_FONT, render_statement, set_dates
from stmt_uw.synth.simulate import PROCESSOR_ENTRY, Applicant, Statement

EDITORS = ["Adobe Acrobat Pro 24.2", "iLovePDF", "Sejda PDF Editor", "Microsoft: Print To PDF"]
RESAVERS = [
    "macOS Version 15.6 (Build 24G84) Quartz PDFContext",
    "Adobe Acrobat Pro (64-bit) 24.2.20759",
    "Skia/PDF m128",
]
SCANNERS = ["Canon iR-ADV C5535 Scan", "Epson Scan 2", "ScanSnap Home 2.6", "CamScanner"]
TAMPER_TYPES = ["edit_amounts", "edit_recalc", "insert_rows", "perfect_fake"]


@dataclass
class TamperInfo:
    type: str
    statement_index: int
    edited_ids: list[str] = field(default_factory=list)  # changed or inserted transaction ids
    recomputed_ids: list[str] = field(default_factory=list)  # rows whose balance was rewritten
    note: str = ""


def scan_profile(rng: random.Random) -> dict[str, Any]:
    """One office / phone scanner's settings."""
    return {
        "producer": rng.choice(SCANNERS),
        "dpi": rng.choice([150, 170, 200]),
        "angle": rng.uniform(-1.0, 1.0),
        "quality": rng.randint(35, 65),
    }


def scan_pdf(src: Path, dst: Path, rng: random.Random, profile: dict[str, Any]) -> None:
    """Image-only copy of `src` as scanned with `profile`."""
    doc, out = pymupdf.open(src), pymupdf.open()
    for page_no in range(doc.page_count):
        page = doc[page_no]
        pix = page.get_pixmap(dpi=profile["dpi"], colorspace=pymupdf.csGRAY)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        angle = profile["angle"] + rng.uniform(-0.3, 0.3)  # each sheet feeds slightly differently
        tone = rng.randint(200, 235)
        img = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=tone)
        img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.7, 0.92))
        # uneven paper / lighting: one smooth gradient from `tone` to white across the page
        shade = Image.linear_gradient("L").rotate(rng.choice([0, 90, 180, 270])).resize(img.size)
        shade = shade.point(lambda v, lo=tone: lo + (255 - lo) * v // 255)
        img = ImageChops.multiply(img, shade)
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 1.0)))
        noise = Image.effect_noise(img.size, rng.uniform(10, 22))
        img = Image.blend(img, noise, rng.uniform(0.06, 0.12))
        speckle = Image.effect_noise(img.size, 40).point(lambda v: 0 if v < 8 else 255)
        img = ImageChops.multiply(img, speckle)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=profile["quality"])
        rect = page.rect
        new = out.new_page(width=rect.width, height=rect.height)
        new.insert_image(rect, stream=buf.getvalue())
    out.set_metadata({"producer": profile["producer"], "creator": "scanner", "title": "Scan"})
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, deflate=True)


def resave(path: Path, rng: random.Random, when: dt.datetime) -> str:
    """Re-save a genuine statement through another tool (content unchanged). Returns the tool."""
    tool = rng.choice(RESAVERS)
    meta = pymupdf.open(path).metadata or {}
    created = dt.datetime.strptime(meta["creationDate"][2:16], "%Y%m%d%H%M%S")
    tmp = path.with_suffix(".orig.pdf")
    path.rename(tmp)
    set_dates(tmp, path, created, when, producer=tool, tz=meta["creationDate"][16:])
    tmp.unlink()
    return tool


def _recompute(st: Statement) -> list[str]:
    """Rewrite running balances; returns the ids whose printed balance changed."""
    changed, balance = [], st.opening
    for t in st.txns:
        balance += t.amount
        if t.balance != balance:
            changed.append(t.id)
        t.balance = balance
    st.closing = balance
    return changed


def _inflate(cents: int, rng: random.Random, lo: float, hi: float) -> int:
    """A larger, plausible-looking amount: cents kept most of the time (whole-dollar edits
    are a tell human reviewers spot), occasionally a round figure as a careless editor types."""
    value = round(cents * rng.uniform(lo, hi))
    return int(round(value, -2)) if rng.random() < 0.2 else value


def tamper(app: Applicant, rng: random.Random, kind: str) -> tuple[Applicant, TamperInfo]:
    """A tampered copy of the applicant (deep copy; the original stays the ground truth)."""
    fake = copy.deepcopy(app)
    n = len(fake.statements)
    idx = n - 1 if kind == "perfect_fake" else rng.randrange(n)
    st = fake.statements[idx]
    info = TamperInfo(kind, idx)
    revenue_rows = [t for t in st.txns if t.category in REVENUE_CATS]
    if kind in ("edit_amounts", "edit_recalc"):
        for t in rng.sample(revenue_rows, min(len(revenue_rows), rng.randint(3, 6))):
            # an amateur pads more (T1); a careful editor stays nearer the real figures
            lo, hi = (1.25, 1.8) if kind == "edit_amounts" else (1.3, 2.2)
            t.amount = _inflate(t.amount, rng, lo, hi)
            info.edited_ids.append(t.id)
        if kind == "edit_recalc":
            info.recomputed_ids = _recompute(st)
        info.note = "deposits inflated" + (
            " and balances recomputed" if kind == "edit_recalc" else ""
        )
    else:
        template = rng.choice(revenue_rows) if revenue_rows else st.txns[0]
        same = sorted(t.amount for t in revenue_rows if t.counterparty == template.counterparty)
        typical = same[len(same) // 2] if same else abs(template.amount)
        for k in range(rng.randint(2, 4)):
            pos = rng.randrange(1, len(st.txns))
            # same payer, channel and entry as a genuine deposit, so the descriptor looks native
            new = Txn(
                date=st.txns[pos - 1].date,
                description=template.description,
                amount=_inflate(typical, rng, 0.8, 1.6),  # sized like a normal deposit
                category=template.category,
                counterparty=template.counterparty,
                channel=template.channel,
                entry=(
                    PROCESSOR_ENTRY[template.counterparty].format(
                        d=st.txns[pos - 1].date - dt.timedelta(days=1)
                    )
                    if template.channel == "card_settlement"
                    and template.counterparty in PROCESSOR_ENTRY
                    else template.entry
                ),
                ref="".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(10)),
                id=f"{app.id}-fake{k}",
            )
            st.txns.insert(pos, new)
            info.edited_ids.append(new.id)
        info.recomputed_ids = [i for i in _recompute(st) if i not in info.edited_ids]
        info.note = "fabricated deposit rows inserted, balances recomputed"
    if idx < n - 1 and kind != "edit_amounts":
        info.note += "; next statement's opening balance no longer matches"
    return fake, info


def render_tampered(
    fake: Applicant,
    info: TamperInfo,
    bank: str,
    out_dir: Path,
    rng: random.Random,
    created: list[dt.datetime],
) -> list[Path]:
    """Render every statement of the tampered file; only the edited one carries traces."""
    paths = []
    edited_at = dt.datetime(2026, 10, rng.randint(1, 20), rng.randint(8, 22), rng.randint(0, 59))
    for i, st in enumerate(fake.statements):
        path = out_dir / f"statement_{st.period_end:%Y-%m-%d}.pdf"
        traced = i == info.statement_index and info.type != "perfect_fake"
        careful = info.type in ("edit_recalc", "insert_rows")
        render_statement(
            fake,
            st,
            bank,
            path,
            created=created[i],
            modified=edited_at if traced else None,
            alt_font_ids=set(info.edited_ids) if traced else None,
            alt_cell_ids=set(info.recomputed_ids) if traced and careful else None,
            alt_font=EDITOR_FONT if careful else None,
            producer=rng.choice(EDITORS) if traced else None,
        )
        if traced:  # editors append an incremental update instead of rewriting the file
            pdf = pymupdf.open(path)
            meta = dict(pdf.metadata or {})
            meta["keywords"] = ""
            pdf.set_metadata(meta)
            pdf.saveIncr()
            pdf.close()
        paths.append(path)
    return paths
