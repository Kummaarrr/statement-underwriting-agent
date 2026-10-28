"""Pre-compute the demo's sample files: copy their PDFs and run v1 once on each.

    uv run python scripts/build_demo_samples.py

Writes app/samples/<id>/*.pdf and app/samples/results.json (the app makes no API calls for
these). Responses are cached on disk, so files already evaluated cost nothing; a hard cap stops
the script before it can spend more than CAP_USD.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from stmt_uw import llm

CAP_USD = 0.75
llm.LEDGER.cap_usd = CAP_USD  # set before anything can call the API

from stmt_uw.config import PROJECT_ROOT  # noqa: E402
from stmt_uw.scoring import score_file  # noqa: E402
from stmt_uw.v1.agent import analyse_record  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT / "app"))
from demo_data import result_to_dict, tamper_summary, truth_in_dollars  # noqa: E402

OUT = PROJECT_ROOT / "app" / "samples"

# id -> (label, what it shows)
SAMPLES: dict[str, tuple[str, str]] = {
    "app-001": (
        "Clean digital file",
        "Four genuine text-layer statements from a retailer. Everything ties, so it is marked "
        "Ready - with 15 NSF fees and two cash advances a lender will still want to see.",
    ),
    "app-033": (
        "Distressed restaurant",
        "Genuine digital statements with NSF and overdraft fees and days below zero. Ready means "
        "the documents tie and the figures can be used; the risk call is the lender's.",
    ),
    "app-003": (
        "Scanned statements",
        "Six image-only scans (no text layer). The pages are read from images and every row "
        "is still reconciled against the printed running balance.",
    ),
    "app-006": (
        "Tampered: amounts edited",
        "Deposits were inflated in one statement with a PDF editor. The edit leaves a foreign "
        "font, an incremental save and balances that no longer tie.",
    ),
    "app-018": (
        "Tampered: rows inserted",
        "Fake deposits inserted and the running balances recomputed so the page looks "
        "consistent - but the next statement opens at the old balance.",
    ),
    "app-037": (
        "Genuine, re-saved",
        "A genuine file where one statement was re-saved through another PDF tool. That is "
        "noted but does not flag the file: only evidence of an edit does.",
    ),
}


def main() -> None:
    records = {r["id"]: r for r in json.loads((PROJECT_ROOT / "data" / "dataset.json").read_text())}
    missing = [i for i in SAMPLES if i not in records]
    if missing:
        raise SystemExit(f"not in data/dataset.json: {missing}")
    OUT.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for sid, (label, description) in SAMPLES.items():
        record = records[sid]
        folder = OUT / sid
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir()
        for st in record["statements"]:
            shutil.copy2(PROJECT_ROOT / st["file"], folder / Path(st["file"]).name)
        result = analyse_record(record)
        score = score_file(record, result.assessment)
        item = {
            "id": sid,
            "label": label,
            "description": description,
            "bank": record["bank"],
            "variant": record["variant"],
            "split": record.get("split"),
            "business": record["profile"]["business"],
            "industry": record["profile"]["industry"],
            "expected_verdict": record["expected_verdict"],
            **result_to_dict(result),
            "truth": truth_in_dollars(record),
            "checks": score.figures,
            "tamper": tamper_summary(record),
        }
        items.append(item)
        print(
            f"{sid}: {item['verdict']} (expected {record['expected_verdict']}), "
            f"figures right {sum(score.figures.values())}/{len(score.figures)}, "
            f"list price ${item['cost_usd']:.4f}, {item['seconds']:.1f} s"
        )
    path = OUT / "results.json"
    path.write_text(json.dumps({"samples": items}, indent=1), encoding="utf-8")
    print(f"wrote {path} ({len(items)} samples)")
    print(f"spent this run: {llm.LEDGER.summary()}")


if __name__ == "__main__":
    main()
