"""Core records and the exact definitions of the key figures.

Money is in integer cents everywhere, so totals and reconciliation are exact.

Key figures (what the lender decides on) - definitions used by the ground truth and by v1:
- revenue (per statement period): business-income credits - card-processor settlements,
  customer payments (ACH, wire), mobile / branch / counter deposits - minus deposited items
  that were returned unpaid. Excluded: transfers from the owner's own accounts, loan or
  advance proceeds, reversals of bounced debits, refunds, interest.
- average monthly revenue: mean of the per-statement revenue.
- average daily balance (ADB): mean end-of-day balance over every calendar day of the file
  (days without activity carry the previous balance).
- negative days: calendar days whose end-of-day balance is below zero.
- NSF count: fee events for the business's own payments returned unpaid for insufficient funds
  (a fee for a deposited check that bounced - a returned deposit - is not an NSF event).
- overdraft count: overdraft / courtesy-pay fee events (items the bank paid into overdraft).
- existing debt: recurring payments to the same advance or loan provider - provider, kind
  (MCA / term loan / equipment loan), frequency (daily / weekly / monthly), payment amount.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum


class Cat(StrEnum):
    # credits
    REVENUE_CARD = "revenue_card"
    REVENUE_ACH = "revenue_ach"
    REVENUE_DEPOSIT = "revenue_deposit"  # mobile / branch / counter deposits
    TRANSFER_IN = "transfer_in"
    ADVANCE_PROCEEDS = "advance_proceeds"
    RETURN_REVERSAL = "return_reversal"  # a bounced debit coming back
    # debits
    PAYROLL = "payroll"
    PAYROLL_TAX = "payroll_tax"
    RENT = "rent"
    SUPPLIER = "supplier"
    UTILITIES = "utilities"
    INSURANCE = "insurance"
    CARD_SPEND = "card_spend"
    CARD_AUTOPAY = "card_autopay"  # business credit card payment
    TAX = "tax"  # income / sales tax
    PROCESSOR_FEE = "processor_fee"
    TRANSFER_OUT = "transfer_out"
    OWNER_DRAW = "owner_draw"
    ADVANCE_PAYMENT = "advance_payment"  # MCA daily / weekly pull
    LOAN_PAYMENT = "loan_payment"  # term or equipment loan
    DEPOSIT_RETURN = "deposit_return"  # a deposited check returned unpaid
    FEE_NSF = "fee_nsf"
    FEE_OVERDRAFT = "fee_overdraft"
    FEE_SERVICE = "fee_service"


REVENUE_CATS = {Cat.REVENUE_CARD, Cat.REVENUE_ACH, Cat.REVENUE_DEPOSIT}
DEBT_CATS = {Cat.ADVANCE_PAYMENT, Cat.LOAN_PAYMENT}
FEE_CATS = {Cat.FEE_NSF, Cat.FEE_OVERDRAFT, Cat.FEE_SERVICE, Cat.PROCESSOR_FEE}


@dataclass
class Txn:
    date: dt.date
    description: str  # canonical text; banks render their own format from the fields below
    amount: int  # cents; credits > 0, debits < 0
    category: Cat
    counterparty: str = ""
    channel: str = ""  # card_settlement, ach_credit, ach_debit, deposit, check, debit_card, ...
    entry: str = ""  # ACH entry description (PAYROLL, PMT, RENT, ...)
    ref: str = ""
    balance: int = 0  # cents, running balance after this transaction (set when posted)
    check_no: str = ""
    id: str = ""
    memo: str = ""  # NSF fee: "<item>|<cents>" of the returned item; wire: sending bank
    returned_item: bool = False  # a debit that bounced (most banks don't print it, only the fee)


@dataclass
class Advance:
    provider: str
    frequency: str  # "daily" | "weekly" | "monthly"
    payment: int  # cents per payment
    kind: str  # "mca" | "term_loan" | "equipment_loan"


@dataclass
class KeyFigures:
    revenue_by_statement: dict[str, int]  # statement period end "2026-09-15" -> cents
    avg_monthly_revenue: int
    avg_daily_balance: int
    negative_days: int
    nsf_count: int
    overdraft_count: int
    advances: list[Advance] = field(default_factory=list)


def statement_revenue(txns: list[Txn]) -> int:
    return sum(t.amount for t in txns if t.category in REVENUE_CATS) + sum(
        t.amount for t in txns if t.category == Cat.DEPOSIT_RETURN
    )


def key_figures(
    txns: list[Txn],
    opening_balance: int,
    periods: list[tuple[dt.date, dt.date]],
    advances: list[Advance],
) -> KeyFigures:
    """Exact key figures from categorised transactions (used for ground truth and by v1)."""
    revenue = {
        f"{end:%Y-%m-%d}": statement_revenue([t for t in txns if start <= t.date <= end])
        for start, end in periods
    }
    eod: dict[dt.date, int] = {}
    for t in txns:
        eod[t.date] = t.balance  # last transaction of the day wins
    balance, total, days, negative = opening_balance, 0, 0, 0
    day, end = periods[0][0], periods[-1][1]
    while day <= end:
        balance = eod.get(day, balance)
        total += balance
        negative += balance < 0
        days += 1
        day += dt.timedelta(days=1)
    return KeyFigures(
        revenue_by_statement=revenue,
        avg_monthly_revenue=round(sum(revenue.values()) / max(1, len(revenue))),
        avg_daily_balance=round(total / days),
        negative_days=negative,
        nsf_count=sum(t.category == Cat.FEE_NSF for t in txns),
        overdraft_count=sum(t.category == Cat.FEE_OVERDRAFT for t in txns),
        advances=advances,
    )
