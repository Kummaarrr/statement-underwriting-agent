"""One plain, JSON-serialisable dict per analysed file - the shape the demo renders.

Used by scripts/build_demo_samples.py (pre-computed samples) and by the app (a live run on the
visitor's own key), so both views render exactly the same data. No key ever enters this dict.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from stmt_uw.v1.agent import Result
from stmt_uw.v1.figures import ref

FIGURE_LABELS = {
    "revenue": "Revenue (per statement and average)",
    "avg_daily_balance": "Average daily balance",
    "nsf_count": "NSF / returned-item fees",
    "overdraft_count": "Overdraft fees",
    "negative_days": "Negative days",
    "debt": "Existing debt positions",
}


def _plain(value: Any) -> Any:
    """Dates to ISO, paths to names, dataclasses and tuples to plain JSON types."""
    if isinstance(value, dt.date):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_plain(v) for v in value]
    if hasattr(value, "__fspath__"):
        return str(value)
    return value


def result_to_dict(result: Result) -> dict[str, Any]:
    """A live agent Result in the demo's dict shape (without sample metadata or truth)."""
    state = result.state
    statements = []
    for st in state["statements"]:
        h = st.header
        statements.append(
            {
                "index": st.index,
                "file": st.file.name,
                "pages": len(st.pages),
                "header": {
                    "period_start": h.period_start,
                    "period_end": h.period_end,
                    "opening": h.opening_balance,
                    "closing": h.closing_balance,
                },
                "rows": [
                    {
                        "ref": ref(r),
                        "date": r.date.isoformat() if r.date else None,
                        "description": r.description,
                        "amount": r.amount,
                        "balance": r.balance,
                        "page": r.page + 1,
                        "statement": st.index + 1,
                        "file": st.file.name,
                    }
                    for r in st.rows
                ],
                "repairs": list(st.repairs),
            }
        )
    figures = state.get("figures")
    return {
        "assessment": result.assessment.model_dump(),
        "verdict": state["verdict"],
        "reason": state["reason"],
        "notes": list(state.get("notes", [])),
        "issues": _plain(state.get("issues", [])),
        "findings": _plain(state.get("findings", [])),
        "evidence": _plain(figures.evidence if figures is not None else {}),
        "statements": statements,
        "calls": len(result.calls),
        "cached_calls": sum(c.cached for c in result.calls),
        "cost_usd": round(result.cost_usd, 4),
        "seconds": round(result.seconds, 1),
    }


def truth_in_dollars(record: dict[str, Any]) -> dict[str, Any]:
    """The record's ground-truth key figures, money converted from cents to dollars."""
    kf = record["key_figures"]
    return {
        "revenue_by_statement": {k: v / 100 for k, v in kf["revenue_by_statement"].items()},
        "avg_monthly_revenue": kf["avg_monthly_revenue"] / 100,
        "avg_daily_balance": kf["avg_daily_balance"] / 100,
        "negative_days": kf["negative_days"],
        "nsf_count": kf["nsf_count"],
        "overdraft_count": kf["overdraft_count"],
        "existing_debt": [
            {
                "provider": a["provider"],
                "kind": a["kind"],
                "frequency": a["frequency"],
                "payment": a["payment"] / 100,
            }
            for a in kf["advances"]
        ],
    }


def tamper_summary(record: dict[str, Any]) -> dict[str, Any] | None:
    """What was done to a tampered file (or how a genuine one was re-saved), for the demo."""
    t, rs = record.get("tamper"), record.get("resaved")
    if t:
        return {
            "kind": "tamper",
            "type": t["type"],
            "statement": t["statement_index"] + 1,
            "note": t.get("note", ""),
            "edited_rows": len(t.get("edited_ids", [])),
            "recomputed_rows": len(t.get("recomputed_ids", [])),
        }
    if rs:
        return {"kind": "resaved", "statement": rs["statement_index"] + 1, "tool": rs["tool"]}
    return None
