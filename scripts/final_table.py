"""Final tables from saved results (no model calls).

    uv run python scripts/final_table.py   # -> evals/results/FINAL_test.md, ABLATION_dev.md

Test split: each system run 3 times independently (tags r1-r3, fresh caches); the table shows the
mean and the range across repeats. Dev ablation: one run per v1 variant.
"""

from __future__ import annotations

import json
import statistics
from typing import Any

from stmt_uw.config import PROJECT_ROOT
from stmt_uw.scoring import FIGURES

RESULTS = PROJECT_ROOT / "evals" / "results"
SYSTEMS = [
    ("v0_opus", "v0 - Opus 5.5, whole file"),
    ("v0_haiku", "v0-small - Haiku 5.5, whole file"),
    ("v1_haiku", "v1 - Haiku 5.5 agent"),
]
ROWS = [
    ("all_correct_pct", "All key figures correct", "%"),
    ("straight_through_pct", "**Straight-through** (Ready and all correct)", "%"),
    ("flag_rate_pct", "Flagged Needs review", "%"),
    ("wrong_on_ready", "**Wrong figures on files marked Ready**", "files"),
    ("tamper_recall_pct", "Tampered files flagged (recall)", "%"),
    ("false_alarm_pct", "Genuine files flagged (false alarms)", "%"),
    ("seconds_per_file", "Seconds per file (median of fresh runs)", "s"),
    ("usd_per_file", "$ per file", "$"),
]


def _load(name: str) -> dict[str, Any] | None:
    path = RESULTS / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _cell(values: list[float], unit: str) -> str:
    if not values:
        return "-"
    mean = statistics.mean(values)
    fmt = (
        (lambda v: f"${v:.3f}")
        if unit == "$"
        else (lambda v: f"{v:.0f}")
        if unit == "s"
        else (lambda v: f"{v:.1f}" if unit == "files" else f"{v:.0f}%")
    )
    spread = f" ({fmt(min(values))}-{fmt(max(values))})" if max(values) != min(values) else ""
    return fmt(mean) + spread


def final() -> str:
    runs = {
        key: [r for t in ("r1", "r2", "r3") if (r := _load(f"{key}_test_{t}"))]
        for key, _ in SYSTEMS
    }
    head = "| Metric | " + " | ".join(label for _, label in SYSTEMS) + " |"
    lines = [
        "# Final results - test split",
        "",
        f"15 held-out applicant files ({_composition()}), each system run 3 times "
        "independently; mean (range across the 3 runs).",
        "",
        head,
        "|---|" + "---|" * len(SYSTEMS),
    ]
    for key, label, unit in ROWS:
        cells = []
        for name, _ in SYSTEMS:
            vals = [r["aggregate"][key] for r in runs[name] if r["aggregate"][key] is not None]
            if key == "seconds_per_file" and vals:
                # median of fresh runs: cache replays report replay time (< 5 s per file) and
                # one Opus run includes an overnight network stall
                fresh = [v for v in vals if v > 5] or vals
                vals = [statistics.median(fresh)]
            cells.append(_cell(vals, unit))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "Per key figure, % of files correct (mean of 3 runs):",
        "",
        "| Figure | " + " | ".join(label for _, label in SYSTEMS) + " |",
        "|---|" + "---|" * len(SYSTEMS),
    ]
    for fig in FIGURES:
        cells = [
            _cell([r["aggregate"]["figure_accuracy_pct"][fig] for r in runs[n]], "%")
            for n, _ in SYSTEMS
        ]
        lines.append(f"| {fig} | " + " | ".join(cells) + " |")
    lines += ["", "Runs available: " + ", ".join(f"{n} x{len(runs[n])}" for n, _ in SYSTEMS)]
    return "\n".join(lines) + "\n"


def _composition() -> str:
    records = json.loads((PROJECT_ROOT / "data" / "dataset.json").read_text(encoding="utf-8"))
    test = [r for r in records if r["split"] == "test"]
    kinds = [r["tamper"]["type"] for r in test if r["tamper"]]
    return (
        f"{sum(r['variant'] == 'digital' for r in test)} digital incl. "
        f"{sum(bool(r['resaved']) for r in test)} innocently re-saved, "
        f"{sum(r['variant'] == 'scanned' for r in test)} scanned, {len(kinds)} tampered: "
        + ", ".join(sorted(kinds))
    )


def ablation() -> str:
    variants = [
        ("v1", "v1 (all steps)"),
        ("v1-noforensics", "without PDF forensics"),
        ("v1-noheal", "without self-healing re-reads"),
        ("v1-norules", "without keyword rules (model labels only)"),
        ("v1-noescalate", "without escalation to Sonnet"),
        ("v1-pdf", "digital pages read as PDF instead of text"),
    ]
    lines = [
        "# Ablation - dev split (25 files, one run each, Haiku 5.5)",
        "",
        "| Variant | Straight-through | Wrong on Ready | Tamper recall | False alarms | $/file |",
        "|---|---|---|---|---|---|",
    ]
    for name, label in variants:
        r = _load(f"{name}_haiku_dev")
        if not r:
            continue
        a = r["aggregate"]
        lines.append(
            f"| {label} | {a['straight_through_pct']}% | {a['wrong_on_ready']} of "
            f"{a['ready_files']} | {a['tamper_recall_pct']}% | {a['false_alarm_pct']}% | "
            f"${a['usd_per_file']:.3f} |"
        )
    for name, label in (
        ("v0_opus_dev", "v0 Opus (reference)"),
        ("v0_haiku_dev", "v0 Haiku (reference)"),
    ):
        if r := _load(name):
            a = r["aggregate"]
            lines.append(
                f"| {label} | {a['straight_through_pct']}% | {a['wrong_on_ready']} of "
                f"{a['ready_files']} | {a['tamper_recall_pct']}% | "
                f"{a['false_alarm_pct']}% | ${a['usd_per_file']:.3f} |"
            )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    (RESULTS / "FINAL_test.md").write_text(final(), encoding="utf-8")
    (RESULTS / "ABLATION_dev.md").write_text(ablation(), encoding="utf-8")
    print((RESULTS / "ABLATION_dev.md").read_text(encoding="utf-8"))
    print((RESULTS / "FINAL_test.md").read_text(encoding="utf-8"))
