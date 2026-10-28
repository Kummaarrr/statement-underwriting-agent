"""Assemble extracted pages into statements and check that everything ties - in code.

Checks (each failure is an Issue that names the statement and page):
- running balance: previous balance + amount = printed balance, row by row;
- daily-balance tables (layouts without a running balance): end-of-day balances rebuilt from
  the rows must equal the printed table;
- summary: rows add up to the printed credit / debit totals and counts; opening + credits -
  debits = closing; the last balance equals the closing balance;
- chain: each statement opens with the previous statement's closing balance.

One deterministic repair is allowed and logged (never silent): an amount whose sign was read
wrong, when the printed balances prove the opposite sign exactly.
"""

from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stmt_uw.v1.extract import Header, PageExtraction

CENT = 0.005


@dataclass
class TxnRow:
    statement: int
    page: int
    index: int  # position in the statement
    date: dt.date | None
    description: str
    amount: float
    balance: float | None
    section: str


@dataclass
class Issue:
    kind: str  # balance_break / daily_balance / totals / counts / closing / chain / header / read
    statement: int
    page: int | None
    detail: str


@dataclass
class StatementData:
    index: int
    file: Path
    pages: list[PageExtraction]
    header: Header = field(
        default_factory=lambda: Header.model_validate({k: None for k in Header.model_fields})
    )
    rows: list[TxnRow] = field(default_factory=list)
    daily: dict[dt.date, float] = field(default_factory=dict)
    repairs: list[str] = field(default_factory=list)

    @property
    def period(self) -> tuple[dt.date, dt.date] | None:
        try:
            return (
                dt.date.fromisoformat(self.header.period_start or ""),
                dt.date.fromisoformat(self.header.period_end or ""),
            )
        except ValueError:
            return None


def _date(s: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def assemble(index: int, file: Path, pages: list[PageExtraction]) -> StatementData:
    st = StatementData(index, file, pages)
    merged: dict[str, Any] = {}
    for p in pages:  # first printed value of each header field wins
        for k, v in p.header.model_dump().items():
            if merged.get(k) is None and v is not None:
                merged[k] = v
    st.header = Header.model_validate({k: merged.get(k) for k in Header.model_fields})
    for page_no, p in enumerate(pages):
        for r in p.rows:
            st.rows.append(
                TxnRow(
                    index,
                    page_no,
                    len(st.rows),
                    _date(r.date),
                    r.description,
                    round(r.amount, 2),
                    r.balance,
                    r.section,
                )
            )
        for d in p.daily_balances:
            if (day := _date(d.date)) is not None:
                st.daily[day] = d.balance
    _repair_signs(st)
    return st


def _repair_signs(st: StatementData) -> None:
    """Two logged repairs of reading errors, each proven by the printed balances:
    - a sign read wrong: running total - amount = printed balance (+ amount doesn't);
    - a balance digit misread: the printed balance disagrees with the running total, but the
      next printed balance (or the closing balance) continues exactly from the running total.
      An edited amount can't pass this - later printed balances follow the original amount.
    Rows without a printed balance (layouts that print one per day) move the running total."""
    prev = st.header.opening_balance
    if prev is None:
        return
    rows = st.rows
    for k, r in enumerate(rows):
        if r.balance is None:
            prev += r.amount
            continue
        if abs(prev + r.amount - r.balance) > CENT and abs(prev - r.amount - r.balance) <= CENT:
            st.repairs.append(
                f"statement {st.index + 1} page {r.page + 1}: sign of "
                f"{r.amount:,.2f} ({r.description[:40]}) corrected from balances"
            )
            r.amount = -r.amount
        computed = prev + r.amount
        if abs(computed - r.balance) > CENT:
            running, follows = computed, False
            nxt = next((x for x in rows[k + 1 :] if x.balance is not None), None)
            if nxt is not None:
                for x in rows[k + 1 : rows.index(nxt) + 1]:
                    running += x.amount
                follows = abs(running - nxt.balance) <= CENT  # type: ignore[operator]
            else:
                closing = st.header.closing_balance
                running += sum(x.amount for x in rows[k + 1 :])
                follows = closing is not None and abs(running - closing) <= CENT
            if follows:
                st.repairs.append(
                    f"statement {st.index + 1} page {r.page + 1}: printed balance "
                    f"{r.balance:,.2f} read as misprinted digits; the chain continues from "
                    f"{computed:,.2f}"
                )
                r.balance = round(computed, 2)
        prev = r.balance


def check(st: StatementData) -> list[Issue]:
    issues: list[Issue] = []
    h, s = st.header, st.index
    if st.period is None or h.opening_balance is None or h.closing_balance is None:
        issues.append(Issue("header", s, 0, "statement period or balances not found"))
        return issues
    if any(p.unreadable for p in st.pages):
        pages = [i for i, p in enumerate(st.pages) if p.unreadable]
        issues.append(
            Issue("read", s, pages[0], f"page(s) {[p + 1 for p in pages]} not fully readable")
        )
    if any(r.date is None or not (st.period[0] <= r.date <= st.period[1]) for r in st.rows):
        bad = next(
            r for r in st.rows if r.date is None or not st.period[0] <= r.date <= st.period[1]
        )
        issues.append(
            Issue(
                "read",
                s,
                bad.page,
                f"row date outside the statement period: {bad.description[:40]}",
            )
        )
    # running balance
    running, breaks = h.opening_balance, []
    for r in st.rows:  # rows without a printed balance still move the running total
        running += r.amount
        if r.balance is None:
            continue
        if abs(running - r.balance) > CENT:
            breaks.append(r)
        running = r.balance
    if breaks:
        issues.append(
            Issue(
                "balance_break",
                s,
                breaks[0].page,
                f"{len(breaks)} row(s) don't tie to the printed running balance, first "
                f"{breaks[0].date} {breaks[0].description[:40]} {breaks[0].amount:,.2f}",
            )
        )
    # daily balance table
    if st.daily:
        eod, bal = {}, h.opening_balance
        for r in sorted(st.rows, key=lambda r: (r.date or dt.date.min, r.index)):
            bal += r.amount
            if r.date:
                eod[r.date] = bal
        # days without activity carry the previous balance
        carried, wrong = h.opening_balance, []
        for d in sorted(set(st.daily) | set(eod)):
            carried = eod.get(d, carried)
            if d in st.daily and abs(carried - st.daily[d]) > CENT:
                wrong.append(d)
        if wrong:
            issues.append(
                Issue(
                    "daily_balance",
                    s,
                    None,
                    f"{len(wrong)} daily ending balance(s) differ from the rows",
                )
            )
    # summary totals and counts
    credits = [r.amount for r in st.rows if r.amount > 0]
    debits = [-r.amount for r in st.rows if r.amount < 0]
    if h.total_credits is not None and abs(sum(credits) - h.total_credits) > CENT:
        issues.append(
            Issue(
                "totals",
                s,
                None,
                f"credits add to {sum(credits):,.2f}, summary says {h.total_credits:,.2f}",
            )
        )
    if h.total_debits is not None and abs(sum(debits) - abs(h.total_debits)) > CENT:
        issues.append(
            Issue(
                "totals",
                s,
                None,
                f"debits add to {sum(debits):,.2f}, summary says {abs(h.total_debits):,.2f}",
            )
        )
    if h.credit_count is not None and h.credit_count != len(credits):
        issues.append(
            Issue("counts", s, None, f"{len(credits)} credit rows, summary says {h.credit_count}")
        )
    if h.debit_count is not None and h.debit_count != len(debits):
        issues.append(
            Issue("counts", s, None, f"{len(debits)} debit rows, summary says {h.debit_count}")
        )
    computed = h.opening_balance + sum(credits) - sum(debits)
    if abs(computed - h.closing_balance) > CENT:
        issues.append(
            Issue(
                "closing",
                s,
                None,
                f"opening + credits - debits = {computed:,.2f}, "
                f"closing balance printed {h.closing_balance:,.2f}",
            )
        )
    if h.total_credits is not None and h.total_debits is not None:
        printed = h.opening_balance + h.total_credits - abs(h.total_debits)
        if abs(printed - h.closing_balance) > CENT:
            issues.append(Issue("closing", s, None, "the summary box itself doesn't add up"))
    last = next((r.balance for r in reversed(st.rows) if r.balance is not None), None)
    if last is not None and abs(last - h.closing_balance) > CENT:
        issues.append(
            Issue(
                "closing",
                s,
                None,
                f"last running balance {last:,.2f} != closing {h.closing_balance:,.2f}",
            )
        )
    return issues


def in_period_order(statements: list[StatementData]) -> list[StatementData]:
    """Statements sorted by period start (files may arrive in any order)."""
    return sorted(statements, key=lambda st: st.period[0] if st.period else dt.date.max)


def chain_issues(statements: list[StatementData]) -> list[Issue]:
    out = []
    for prev, cur in itertools.pairwise(in_period_order(statements)):
        a, b = prev.header.closing_balance, cur.header.opening_balance
        if a is not None and b is not None and abs(a - b) > CENT:
            out.append(
                Issue(
                    "chain",
                    cur.index,
                    0,
                    f"opens at {b:,.2f} but the previous statement closed at {a:,.2f}",
                )
            )
        if prev.period and cur.period and (cur.period[0] - prev.period[1]).days != 1:
            out.append(Issue("chain", cur.index, 0, "statement periods are not consecutive"))
    return out


def read_score(st: StatementData) -> tuple[int, int, int]:
    """Lower is better: a read that loses the header is never better, then fewer issues, then
    more rows."""
    issues = check(st)
    return (any(i.kind == "header" for i in issues), len(issues), -len(st.rows))


def suspect_pages(st: StatementData, issues: list[Issue]) -> set[int]:
    """Pages to re-read: where a break or unreadable part is, else every page of the statement
    (a missing row or a misread total can be anywhere)."""
    pages = {i.page for i in issues if i.page is not None and i.kind in ("balance_break", "read")}
    return pages or set(range(len(st.pages)))
