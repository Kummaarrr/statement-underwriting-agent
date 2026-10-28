"""Generate the synthetic applicant files and their exact ground truth.

    uv run python scripts/build_dataset.py            # -> data/generated/applicants/<id>/
                                                       #    and data/dataset.json (committed)

Deterministic (fixed seed). PDFs are git-ignored and recreated by this script; the ground truth
index data/dataset.json is committed and frozen, like P1's split.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from tqdm import tqdm

from stmt_uw.model import key_figures
from stmt_uw.synth.render import BANKS, printed_description, render_statement, visible_txns
from stmt_uw.synth.simulate import Applicant, simulate
from stmt_uw.synth.variants import render_tampered, resave, scan_pdf, scan_profile, tamper

SEED = 20261027
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "generated" / "applicants"
N = 40
# variant plan: 20 digital, 10 scanned, 10 tampered (T1 x3, T2 x3, T3 x2, T4 x2)
VARIANTS = (
    ["digital"] * 20
    + ["scanned"] * 10
    + ["edit_amounts"] * 3
    + ["edit_recalc"] * 3
    + ["insert_rows"] * 2
    + ["perfect_fake"] * 2
)
TEST_SHARE = {"digital": 7, "scanned": 4, "tampered": 4}
RESAVED = 4  # genuine digital files with one statement re-saved by another tool (false alarms)


def _txn_json(t, bank: str, app: Applicant) -> dict:  # type: ignore[no-untyped-def]
    d = asdict(t)
    d["date"] = t.date.isoformat()
    d["category"] = str(t.category)
    d["printed"] = printed_description(t, bank, app.profile)
    return d


def _statements_json(app: Applicant, files: list[Path], bank: str) -> list[dict]:
    return [
        {
            "file": f.relative_to(ROOT).as_posix(),
            "period_start": st.period_start.isoformat(),
            "period_end": st.period_end.isoformat(),
            "opening": st.opening,
            "closing": st.closing,
            # only the rows the bank prints (fee-only banks omit returned items)
            "transactions": [_txn_json(t, bank, app) for t in visible_txns(st, bank)],
        }
        for st, f in zip(app.statements, files, strict=True)
    ]


def main() -> int:
    rng = random.Random(SEED)
    variants = VARIANTS[:]
    rng.shuffle(variants)
    banks = list(BANKS)
    if OUT.exists():
        shutil.rmtree(OUT)
    resaved = set(rng.sample([i for i, v in enumerate(variants) if v == "digital"], RESAVED))
    records = []
    used_names: set[str] = set()  # no business or owner name repeats across files
    for i in tqdm(range(N), desc="applicants"):
        app_id = f"app-{i:03d}"
        bank = banks[i % len(banks)]
        app = simulate(
            app_id,
            rng,
            months=rng.choice([3, 4, 4, 5, 6]),
            cycle_day=BANKS[bank].cycle_day,
            used=used_names,
            states=list(BANKS[bank].states),
        )
        # each statement is generated in the bank's overnight batch a day or two after it closes
        created = [
            dt.datetime.combine(st.period_end + dt.timedelta(days=rng.randint(1, 2)), dt.time())
            + dt.timedelta(
                hours=rng.randint(1, 5), minutes=rng.randint(0, 59), seconds=rng.randint(0, 59)
            )
            for st in app.statements
        ]
        variant = variants[i]
        kf = key_figures(app.txns, app.opening_balance, app.periods, app.advances)
        folder = OUT / app_id
        tamper_info, printed, resave_info = None, app, None
        if variant in ("digital", "scanned"):
            files = []
            scanner = scan_profile(rng)
            for st, made in zip(app.statements, created, strict=True):
                path = folder / f"statement_{st.period_end:%Y-%m-%d}.pdf"
                render_statement(app, st, bank, path, created=made)
                if variant == "scanned":
                    clean = path.with_suffix(".clean.pdf")
                    path.rename(clean)
                    # a month is occasionally scanned on another device
                    scan_pdf(clean, path, rng, scanner if rng.random() < 0.8 else scan_profile(rng))
                    clean.unlink()
                files.append(path)
            if i in resaved:
                k = rng.randrange(len(files))
                when = dt.datetime(
                    2026, 10, rng.randint(1, 20), rng.randint(8, 22), rng.randint(0, 59)
                )
                resave_info = {"statement_index": k, "tool": resave(files[k], rng, when)}
        else:
            printed, tamper_info = tamper(app, rng, variant)
            files = render_tampered(printed, tamper_info, bank, folder, rng, created)
        records.append(
            {
                "id": app_id,
                "bank": bank,
                "variant": "tampered" if tamper_info else variant,
                "tamper": asdict(tamper_info) if tamper_info else None,
                "resaved": resave_info,  # genuine, re-saved by another tool: not tampering
                "expected_verdict": "needs_review" if tamper_info else "ready",
                "profile": {
                    "business": app.profile.business,
                    "industry": app.profile.industry,
                    "risk": app.profile.risk,
                },
                "period": {"start": app.start.isoformat(), "end": app.end.isoformat()},
                "statement_periods": [[a.isoformat(), b.isoformat()] for a, b in app.periods],
                "opening_balance": app.opening_balance,
                # the genuine figures - what a correct, untricked underwriter should arrive at
                "key_figures": {**asdict(kf), "advances": [asdict(a) for a in kf.advances]},
                # what is actually printed in the PDFs (differs from genuine only when tampered)
                "statements": _statements_json(printed, files, bank),
            }
        )

    # frozen split by applicant, stratified by variant
    split_rng = random.Random(SEED + 1)
    for variant, n_test in TEST_SHARE.items():
        group = [r for r in records if r["variant"] == variant]
        for j, r in enumerate(split_rng.sample(group, len(group))):
            r["split"] = "test" if j < n_test else "dev"
    (ROOT / "data" / "dataset.json").write_text(json.dumps(records, indent=1), encoding="utf-8")
    n_pdf = sum(len(r["statements"]) for r in records)
    print(f"{len(records)} applicants, {n_pdf} statement PDFs -> {OUT}")
    for v in ("digital", "scanned", "tampered"):
        g = [r for r in records if r["variant"] == v]
        print(
            f"  {v:9} {len(g):2} (dev {sum(r['split'] == 'dev' for r in g)}, test "
            f"{sum(r['split'] == 'test' for r in g)})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
