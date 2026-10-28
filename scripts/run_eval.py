"""Run a system over a split and score it against the ground truth.

    uv run python scripts/run_eval.py --system v0 --model haiku --split dev --estimate
    uv run python scripts/run_eval.py --system v0 --model haiku --split dev
    uv run python scripts/run_eval.py --system v0 --model opus --split dev --max-cost 8

--estimate counts the exact input tokens (free endpoint) and prices the run before spending.
Results: evals/results/<system>_<model>_<split>[_<tag>].json (per-file scores + aggregate).
Responses are cached, so re-running an unchanged run is free; --tag gives independent repeats.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pymupdf

from stmt_uw import llm
from stmt_uw.config import PROJECT_ROOT, Env
from stmt_uw.llm import LEDGER, BudgetExceeded
from stmt_uw.schema import Assessment
from stmt_uw.scoring import FIGURES, FileScore, aggregate, score_file
from stmt_uw.systems import v0, v1

MODELS = {"opus": "claude-opus-5-5", "haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}
SYSTEMS: dict[str, Any] = {"v0": v0, **{k: v1.make(k) for k in v1.VARIANTS}}
EST_OUTPUT_TOKENS = 2_500  # per file, structured answer without extended thinking


def _records(split: str, ids: list[str] | None, limit: int | None) -> list[dict[str, Any]]:
    records = json.loads((PROJECT_ROOT / "data" / "dataset.json").read_text(encoding="utf-8"))
    chosen = [r for r in records if (ids and r["id"] in ids) or (not ids and r["split"] == split)]
    return chosen[:limit] if limit else chosen


def _pages(path: Path) -> int:
    with pymupdf.open(path) as doc:
        return doc.page_count


def estimate(system: Any, records: list[dict[str, Any]], model: str) -> None:
    """Exact token counts on a sample of statements (scans are slow to count), extrapolated
    by page count: tokens per page differ between text-layer PDFs and image-only scans."""
    sample: dict[str, list[dict[str, Any]]] = {"scanned": [], "digital": []}
    for r in records:
        kind = "scanned" if r["variant"] == "scanned" else "digital"
        if len(sample[kind]) < (2 if kind == "scanned" else 4):
            sample[kind].append(r["statements"][-1])
    per_page = {}
    for kind, sts in sample.items():
        if not sts:
            continue
        tokens = pages = 0
        for st in sts:
            path = PROJECT_ROOT / st["file"]
            tokens += llm.count_tokens(model, [llm.pdf_block(path), {"type": "text", "text": "."}])
            pages += _pages(path)
        per_page[kind] = tokens / pages
    overhead = llm.count_tokens(model, v0.INSTRUCTIONS, system.SYSTEM)
    per_file = []
    for r in records:
        kind = "scanned" if r["variant"] == "scanned" else "digital"
        pages = sum(_pages(PROJECT_ROOT / st["file"]) for st in r["statements"])
        per_file.append(overhead + pages * per_page[kind])
    usage_in = {"input_tokens": round(sum(per_file))}
    usage_out = {"output_tokens": EST_OUTPUT_TOKENS * len(records)}
    cost_in, cost_out = llm.price(model, usage_in), llm.price(model, usage_out)
    rates = ", ".join(f"{k} {v:,.0f}" for k, v in per_page.items())
    print(f"{model}: {len(records)} files, ~{usage_in['input_tokens']:,} input tokens "
          f"(tokens/page: {rates}; largest file ~{max(per_file):,.0f})")  # fmt: skip
    print(f"  input ~${cost_in:.2f} + output ~${cost_out:.2f} (~{EST_OUTPUT_TOKENS:,} "
          f"tokens/file) = ~${cost_in + cost_out:.2f} per run (+/-10%)")  # fmt: skip


def run_one(system: Any, record: dict[str, Any], model: str) -> FileScore:
    start = time.perf_counter()
    try:
        pred, info = system.run(record, model)
    except BudgetExceeded:
        raise
    except Exception as e:
        return score_file(
            record, None, time.perf_counter() - start, 0.0, f"{type(e).__name__}: {e}"
        )
    seconds = info.latency_s if info.cached else time.perf_counter() - start
    return score_file(record, Assessment.model_validate(pred), seconds, info.cost_usd)


def report(agg: dict[str, Any], label: str) -> str:
    f = agg["figure_accuracy_pct"]
    lines = [
        f"## {label}",
        "",
        "| Metric | Value |",
        "|---|---|",
        *[f"| {name} correct | {f[name]}% |" for name in FIGURES],
        f"| **all figures correct** | {agg['all_correct_pct']}% |",
        f"| **straight-through** (Ready and all correct) | {agg['straight_through_pct']}% |",
        f"| flagged Needs review | {agg['flag_rate_pct']}% |",
        f"| **wrong figures on files marked Ready** | {agg['wrong_on_ready']} of "
        f"{agg['ready_files']} |",
        f"| tamper recall | {agg['tamper_recall_pct']}% |",
        f"| false alarms on genuine files | {agg['false_alarm_pct']}% |",
        f"| seconds / file | {agg['seconds_per_file']} |",
        f"| $ / file | {agg['usd_per_file']} |",
        f"| errors | {agg['errors']} of {agg['files']} |",
    ]  # fmt: skip
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", default="v0", choices=SYSTEMS)
    ap.add_argument("--model", default="haiku", choices=MODELS)
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--tag", default="", help="independent repeat (fresh cache namespace)")
    ap.add_argument("--max-cost", type=float, default=Env().max_run_cost_usd)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    system, model = SYSTEMS[args.system], MODELS[args.model]
    records = _records(args.split, args.ids, args.limit)
    if args.estimate:
        estimate(system, records, model)
        return 0
    llm.set_cache_namespace(args.tag)
    LEDGER.cap_usd = args.max_cost
    with ThreadPoolExecutor(args.workers) as pool:
        scores = list(pool.map(lambda r: run_one(system, r, model), records))
    agg = aggregate(scores)
    name = f"{args.system}_{args.model}_{args.split}" + (f"_{args.tag}" if args.tag else "")
    out = PROJECT_ROOT / "evals" / "results" / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"aggregate": agg, "files": [asdict(s) for s in scores]}, indent=1),
        encoding="utf-8",
    )
    print(report(agg, f"{args.system} / {model} / {args.split} ({len(records)} files)"))
    print(f"\n{LEDGER.summary()}  ->  {Path(out).relative_to(PROJECT_ROOT).as_posix()}")
    for s in scores:
        if s.verdict == "error":
            print(f"  ERROR {s.id}: {s.reason[:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
