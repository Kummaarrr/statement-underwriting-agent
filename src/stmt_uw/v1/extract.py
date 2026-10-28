"""Page-wise structured extraction: the model copies what is printed; it computes nothing.

One call per page (pages in parallel). The first page of each statement is read first so the
rest know the statement period (to write full dates). Numbers are copied exactly; whether they
tie is checked afterwards in code (reconcile.py).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from stmt_uw.llm import CallInfo, Effort, parse_full
from stmt_uw.v1.pdfio import page_block

SYSTEM = (
    "You transcribe US bank statement pages into structured data with perfect fidelity. "
    "Copy every number exactly as printed. Never compute, round, infer or fix anything."
)


class Row(BaseModel):
    date: str = Field(description="Posting date as YYYY-MM-DD")
    description: str = Field(description="Full descriptor text, all printed lines joined")
    amount: float = Field(
        description="Signed: deposits/credits positive, withdrawals/debits negative"
    )
    balance: float | None = Field(description="Running balance printed on this row, else null")
    section: str = Field(description="Section heading the row is under, '' if none")


class DailyBalance(BaseModel):
    date: str = Field(description="YYYY-MM-DD")
    balance: float


class Header(BaseModel):
    period_start: str | None = Field(description="Statement period start YYYY-MM-DD, if printed")
    period_end: str | None = Field(description="Statement period end YYYY-MM-DD, if printed")
    opening_balance: float | None
    closing_balance: float | None
    total_credits: float | None = Field(description="Summary total of deposits/credits")
    total_debits: float | None = Field(description="Summary total of withdrawals/debits, positive")
    credit_count: int | None
    debit_count: int | None


class PageExtraction(BaseModel):
    header: Header = Field(description="Summary fields printed on this page (null if absent)")
    rows: list[Row] = Field(description="Every transaction row on the page, in printed order")
    daily_balances: list[DailyBalance] = Field(description="Daily ending balance table entries")
    unreadable: bool = Field(description="True if part of the page cannot be read reliably")


def _instructions(period: tuple[str, str] | None) -> str:
    hint = (
        f"This page belongs to the statement for {period[0]} to {period[1]}; use those years "
        "for dates printed without one."
        if period
        else "If dates are printed without a year, take it from the statement period on the page."
    )
    return f"""Transcribe this bank statement page.
- rows: every transaction (deposits, withdrawals, checks, fees) in printed order. Skip summary
  boxes, fee summaries and daily-balance tables. A description that wraps over several lines is
  one row. Sign: credits/deposits/additions positive; debits/withdrawals/checks/fees negative
  (use the column or section the amount is in, a trailing minus, or DR/CR).
- balance: only if the row itself prints a running balance.
- daily_balances: entries of a "daily ending balance" table, if the page has one.
- header: the statement period, beginning/ending balance and summary totals and counts, if
  printed on this page (otherwise null).
{hint}"""


def extract_page(
    path: Path,
    page: int,
    mode: str,
    model: str,
    period: tuple[str, str] | None,
    effort: Effort | None = "low",
    api_key: str | None = None,
) -> tuple[PageExtraction, CallInfo]:
    content = [*page_block(path, page, mode), {"type": "text", "text": _instructions(period)}]
    return parse_full(
        model,
        content,
        PageExtraction,
        system=SYSTEM,
        effort=effort,
        max_tokens=32000,
        api_key=api_key,
    )
