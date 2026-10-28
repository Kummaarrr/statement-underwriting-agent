"""Score a system's assessment of one file against the ground truth, and aggregate.

Client-facing metrics (docs/PROJECT_PLAN.md section 6):
- per key figure, % of files within tolerance: revenue (every statement and the average)
  +/-1%, average daily balance +/-1%, NSF / overdraft / negative-day counts exact, existing debt
  all found with no phantom positions (provider, kind, frequency, payment +/-1%);
- all-correct rate (every figure right) and straight-through rate (marked Ready and every
  figure right: no human touch needed);
- flag rate, and wrong figures on files marked Ready (the safety metric, target 0). For a
  tampered file the truth is the genuine figures, so trusting a fake counts as wrong;
- tamper recall (tampered files flagged) and false-alarm rate (genuine files flagged);
- seconds and dollars per file.
A system error counts as a flagged file with every figure wrong (it never auto-approves).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from stmt_uw.schema import Assessment

FIGURES = ["revenue", "avg_daily_balance", "nsf_count", "overdraft_count", "negative_days", "debt"]
SUFFIXES = {"LLC", "INC", "CO", "CORP", "GROUP", "LOAN", "PMT", "PAYMENT", "ACH", "DEBIT", "REMIT"}


def within(pred_dollars: float, truth_cents: int, tol: float = 0.01) -> bool:
    truth = truth_cents / 100
    return abs(pred_dollars - truth) <= max(tol * abs(truth), 1.0)


def _name(s: str) -> set[str]:
    words = re.sub(r"[^A-Z0-9 ]", " ", s.upper()).split()
    return {w for w in words if w not in SUFFIXES}


def _same_debt(pred: dict[str, Any], truth: dict[str, Any]) -> bool:
    p, t = _name(pred["provider"]), _name(truth["provider"])
    loans = {"term_loan", "equipment_loan"}
    kind_ok = pred["kind"] == truth["kind"] or {pred["kind"], truth["kind"]} <= loans
    return (
        bool(p & t)
        and kind_ok
        and pred["frequency"] == truth["frequency"]
        and within(pred["payment"], truth["payment"])
    )


def debt_correct(pred: list[dict[str, Any]], truth: list[dict[str, Any]]) -> bool:
    """Every real position found and no phantom ones (one-to-one)."""
    left = list(pred)
    for t in truth:
        match = next((p for p in left if _same_debt(p, t)), None)
        if match is None:
            return False
        left.remove(match)
    return not left


@dataclass
class FileScore:
    id: str
    variant: str
    expected_verdict: str
    verdict: str  # ready / needs_review / error
    figures: dict[str, bool]
    seconds: float = 0.0
    cost_usd: float = 0.0
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def all_correct(self) -> bool:
        return all(self.figures.values())

    @property
    def flagged(self) -> bool:
        return self.verdict != "ready"


def score_file(
    record: dict[str, Any],
    pred: Assessment | None,
    seconds: float = 0.0,
    cost: float = 0.0,
    error: str = "",
) -> FileScore:
    if pred is None:
        return FileScore(record["id"], record["variant"], record["expected_verdict"], "error",
                         dict.fromkeys(FIGURES, False), seconds, cost, error)  # fmt: skip
    kf = record["key_figures"]
    by_end = {s.period_end: s.revenue for s in pred.revenue_by_statement}
    revenue_ok = all(
        end in by_end and within(by_end[end], cents)
        for end, cents in kf["revenue_by_statement"].items()
    ) and within(pred.avg_monthly_revenue, kf["avg_monthly_revenue"])
    figures = {
        "revenue": revenue_ok,
        "avg_daily_balance": within(pred.avg_daily_balance, kf["avg_daily_balance"]),
        "nsf_count": pred.nsf_count == kf["nsf_count"],
        "overdraft_count": pred.overdraft_count == kf["overdraft_count"],
        "negative_days": pred.negative_days == kf["negative_days"],
        "debt": debt_correct([d.model_dump() for d in pred.existing_debt], kf["advances"]),
    }
    detail = {
        "avg_monthly_revenue": (pred.avg_monthly_revenue, kf["avg_monthly_revenue"] / 100),
        "avg_daily_balance": (pred.avg_daily_balance, kf["avg_daily_balance"] / 100),
        "nsf_count": (pred.nsf_count, kf["nsf_count"]),
        "overdraft_count": (pred.overdraft_count, kf["overdraft_count"]),
        "negative_days": (pred.negative_days, kf["negative_days"]),
        "debt": (len(pred.existing_debt), len(kf["advances"])),
    }
    return FileScore(record["id"], record["variant"], record["expected_verdict"], pred.verdict,
                     figures, seconds, cost, pred.reason, detail)  # fmt: skip


def _pct(n: int, d: int) -> float | None:
    return round(100 * n / d, 1) if d else None


def aggregate(scores: list[FileScore]) -> dict[str, Any]:
    n = len(scores)
    tampered = [s for s in scores if s.variant == "tampered"]
    genuine = [s for s in scores if s.variant != "tampered"]
    ready = [s for s in scores if not s.flagged]
    out: dict[str, Any] = {
        "files": n,
        "errors": sum(s.verdict == "error" for s in scores),
        "figure_accuracy_pct": {f: _pct(sum(s.figures[f] for s in scores), n) for f in FIGURES},
        "all_correct_pct": _pct(sum(s.all_correct for s in scores), n),
        "straight_through_pct": _pct(sum(s.all_correct and not s.flagged for s in scores), n),
        "flag_rate_pct": _pct(sum(s.flagged for s in scores), n),
        "wrong_on_ready": sum(not s.all_correct for s in ready),
        "ready_files": len(ready),
        "tamper_recall_pct": _pct(sum(s.flagged for s in tampered), len(tampered)),
        "false_alarm_pct": _pct(sum(s.flagged for s in genuine), len(genuine)),
        "seconds_per_file": round(statistics.mean(s.seconds for s in scores), 1) if n else None,
        "usd_per_file": round(statistics.mean(s.cost_usd for s in scores), 4) if n else None,
    }
    out["by_variant"] = {
        v: {
            "files": len(g),
            "all_correct_pct": _pct(sum(s.all_correct for s in g), len(g)),
            "flag_rate_pct": _pct(sum(s.flagged for s in g), len(g)),
        }
        for v in ("digital", "scanned", "tampered")
        if (g := [s for s in scores if s.variant == v])
    }
    return out
