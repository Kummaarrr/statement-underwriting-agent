"""Compute the key figures in code from categorised rows, with the lines behind each one."""

from __future__ import annotations

import datetime as dt
import itertools
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from stmt_uw.schema import Assessment, Debt, StatementRevenue
from stmt_uw.v1.categorize import Label, key_of
from stmt_uw.v1.reconcile import StatementData, TxnRow, in_period_order


@dataclass
class Figures:
    revenue_by_statement: dict[str, float]
    avg_monthly_revenue: float
    avg_daily_balance: float
    negative_days: int
    nsf_count: int
    overdraft_count: int
    debts: list[Debt]
    evidence: dict[str, Any] = field(default_factory=dict)  # figure -> supporting row refs


def ref(r: TxnRow) -> str:
    return f"S{r.statement + 1}p{r.page + 1}r{r.index + 1}"


def _frequency(dates: list[dt.date]) -> str:
    gaps = [(b - a).days for a, b in itertools.pairwise(dates) if b > a]
    if not gaps:
        return "monthly"
    g = statistics.median(gaps)
    return "daily" if g <= 2 else "weekly" if g <= 10 else "monthly"


def compute(statements: list[StatementData], labels: dict[str, Label]) -> Figures:
    statements = in_period_order(statements)
    cat = {id(r): labels[key_of(r)] for st in statements for r in st.rows}
    revenue, ev_rev = {}, {}
    for st in statements:
        rows = [r for r in st.rows if cat[id(r)].category in ("revenue", "returned_deposit")]
        end = st.header.period_end or f"statement-{st.index + 1}"
        if end in revenue:  # two statements closing the same day (e.g. two accounts)
            end = f"{end}#{st.index + 1}"
        revenue[end] = round(sum(r.amount for r in rows), 2)
        ev_rev[end] = [ref(r) for r in rows]
    # end-of-day balances over every calendar day of the file
    first, last = statements[0], statements[-1]
    eod: dict[dt.date, float] = {}
    for st in statements:
        if st.daily:
            eod.update(st.daily)
            continue
        bal = st.header.opening_balance or 0.0
        for r in st.rows:
            bal = r.balance if r.balance is not None else bal + r.amount
            if r.date:
                eod[r.date] = bal
    total = negative = days = 0
    if first.period and last.period:
        bal, day = first.header.opening_balance or 0.0, first.period[0]
        while day <= last.period[1]:
            bal = eod.get(day, bal)
            total += bal  # type: ignore[assignment]
            negative += bal < -0.004
            days += 1
            day += dt.timedelta(days=1)
    rows = [r for st in statements for r in st.rows]
    nsf = [r for r in rows if cat[id(r)].category == "nsf_fee"]
    od = [r for r in rows if cat[id(r)].category == "overdraft_fee"]
    # existing debt: recurring payments per counterparty
    series: dict[str, list[TxnRow]] = {}
    for r in rows:
        lab = cat[id(r)]
        if lab.category == "debt_payment":
            name = (lab.counterparty or r.description).upper().strip()
            series.setdefault(name, []).append(r)
    debts, ev_debt = [], {}
    for name, group in series.items():
        amount, times = Counter(round(-r.amount, 2) for r in group).most_common(1)[0]
        if times < 2 or times / len(group) < 0.6:
            continue  # financing is repaid in fixed amounts; a varying series isn't debt
        dates = sorted({r.date for r in group if r.date})
        kinds = Counter(cat[id(r)].debt_kind for r in group).most_common(1)[0][0]
        kind = kinds if kinds != "none" else "mca"
        debts.append(Debt(provider=name, kind=kind, frequency=_frequency(dates), payment=amount))  # type: ignore[arg-type]
        ev_debt[name] = [ref(r) for r in group]
    return Figures(
        revenue_by_statement=revenue,
        avg_monthly_revenue=round(sum(revenue.values()) / max(1, len(revenue)), 2),
        avg_daily_balance=round(total / days, 2) if days else 0.0,
        negative_days=negative,
        nsf_count=len(nsf),
        overdraft_count=len(od),
        debts=debts,
        evidence={
            "revenue": ev_rev,
            "nsf": [ref(r) for r in nsf],
            "overdraft": [ref(r) for r in od],
            "debt": ev_debt,
        },
    )


def to_assessment(f: Figures, verdict: str, reason: str) -> Assessment:
    return Assessment(
        revenue_by_statement=[
            StatementRevenue(period_end=k, revenue=v) for k, v in f.revenue_by_statement.items()
        ],
        avg_monthly_revenue=f.avg_monthly_revenue,
        avg_daily_balance=f.avg_daily_balance,
        negative_days=f.negative_days,
        nsf_count=f.nsf_count,
        overdraft_count=f.overdraft_count,
        existing_debt=f.debts,
        verdict=verdict,  # type: ignore[arg-type]
        reason=reason,
    )
