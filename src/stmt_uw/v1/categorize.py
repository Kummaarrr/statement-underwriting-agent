"""Categorise transaction rows: bank-agnostic keyword rules first, the model for the rest.

The model sees each distinct descriptor pattern once (digits masked), with its direction, how
often it occurs and a typical amount, and returns a category plus the counterparty's name. Rules
cover what no bank prints ambiguously (fees, transfers between the owner's own accounts).
"""

from __future__ import annotations

import itertools
import re
import statistics
from typing import Literal

from pydantic import BaseModel, Field

from stmt_uw.llm import CallInfo, Effort, parse_full
from stmt_uw.v1.reconcile import TxnRow

Category = Literal[
    "revenue",  # sales / customer payments / processor settlements / business deposits
    "owner_transfer",  # between the owner's own accounts (savings, personal checking)
    "loan_proceeds",  # MCA / loan funding received
    "item_reversal",  # credit reversing a returned (bounced) debit
    "other_credit",  # refunds, interest, anything not business income
    "returned_deposit",  # a deposited item charged back unpaid
    "nsf_fee",
    "overdraft_fee",
    "other_fee",
    "debt_payment",  # MCA remittance, loan or equipment-finance payment
    "expense",  # everything else going out
]
CREDIT_CATS = {"revenue", "owner_transfer", "loan_proceeds", "item_reversal", "other_credit"}

_OWN = re.compile(r"\b(TRANSFER|XFER)\b.*\b(FROM|TO)\b.*\b(SAV|SAVINGS|SHARE|CHK|CHECKING|DRAFT)\b")
_FEE = re.compile(r"\b(FEE|CHG|CHARGE)\b")
_NSF = re.compile(r"\b(NSF|NON-SUFFICIENT|INSUFFICIENT FUNDS|RETURNED ITEM FEE)\b")
_OD = re.compile(r"\b(OVERDRAFT|COURTESY PAY|[OQ0]D ITEM)\b")
_DEP_RETURN = re.compile(
    r"\b(DEPOSIT(ED)? ITEM RETURNED|RETURN(ED)? DEP|RETURN OF DEPOSITED|"
    r"RETURNED DEPOSIT|CHGBK DEP)"
)
# paying the business's own credit card bill: an expense, never existing debt for underwriting
# operating costs whose ACH entry says what they are: never financing (equipment LEASE is)
_OPERATING = re.compile(
    r"\b(RENT|PAYROLL|WAGE PAY|TAX|TAXES|INS PREM|INSURANCE|BILL PAY|UTILITY)\b"
)
_CARD_BILL = re.compile(r"\b(CRCARDPMT|EPAYMENT|CREDIT CRD|CREDIT CARD|CARD PMT|CARDMEMBER)\b")


def pattern(description: str) -> str:
    """Descriptor with reference numbers masked (any token containing a digit, e.g. IDs, trace
    numbers, dates) and spacing normalised: one key per recurring item."""
    tokens = re.split(r"(\s+|:)", description.upper())
    return "".join("#" if re.search(r"\d", t) else t for t in tokens).strip()


def rule(row: TxnRow) -> str | None:
    d = row.description.upper()
    if row.amount < 0 and _FEE.search(d):
        if _DEP_RETURN.search(d):
            return "other_fee"  # fee for a returned deposit is not an NSF event
        if _NSF.search(d):
            return "nsf_fee"
        if _OD.search(d):
            return "overdraft_fee"
    if row.amount < 0 and _DEP_RETURN.search(d):
        return "returned_deposit"
    if _OWN.search(d):
        return "owner_transfer"
    if row.amount < 0 and (_CARD_BILL.search(d) or _OPERATING.search(d)):
        return "expense"
    return None


class Label(BaseModel):
    key: int
    category: Category
    counterparty: str = Field(description="Who the money is from / to, as a clean name")
    debt_kind: Literal["mca", "term_loan", "equipment_loan", "none"] = Field(
        description="For debt_payment: merchant cash advance, term loan or equipment finance"
    )


class Labels(BaseModel):
    labels: list[Label]


SYSTEM = "You are a US small-business underwriting analyst classifying bank transactions."
GUIDE = """Classify each distinct transaction pattern from one business checking account.

Categories:
- revenue: business income - card-processor settlements (Square, Stripe, Toast, Clover, ...),
  customer/broker payments, marketplace payouts, mobile/branch/counter deposits (a plain
  "DEPOSIT" line is a business deposit).
- owner_transfer: moves between the owner's own accounts (savings, personal checking).
- loan_proceeds: funding received from a lender or merchant-cash-advance (MCA) funder.
- item_reversal: a credit that reverses a returned / bounced debit ("RTN ITEM", "RETURNED ITEM").
- other_credit: refunds, interest, anything that is not business income.
- returned_deposit: a deposited item charged back unpaid.
- nsf_fee / overdraft_fee / other_fee: bank fees (an NSF fee is for the business's own payment
  returned unpaid; a fee for a returned deposit is other_fee).
- debt_payment: repayments of financing - MCA remittances (the SAME amount every business day
  or every week to a funder; names may be cut short, e.g. "MAPLEWOOD BUSINESS DEBIT"), loan
  payments, equipment finance or lease payments (fixed monthly). Not debt: payroll, taxes,
  rent / property payments, suppliers, utilities, insurance, credit-card bill payments.
- expense: everything else going out.
counterparty: the other party's name as printed in the descriptor - don't expand or rename
(e.g. "NORTHSTAR FUNDING GROUP", "SBA EIDL").
debt_kind: for debt_payment only (mca for advance funders; term_loan for bank / SBA loans;
equipment_loan for equipment or vehicle finance and leases), else none.

Patterns (key | direction | occurrences | typical amount | same amount every time? |
median days between occurrences | descriptor):
"""


def label_patterns(
    rows: list[TxnRow],
    model: str,
    use_rules: bool = True,
    effort: Effort | None = "low",
    api_key: str | None = None,
) -> tuple[dict[str, Label], list[CallInfo]]:
    """Category per descriptor pattern (rule-based ones carry counterparty '')."""
    groups: dict[str, list[TxnRow]] = {}
    for r in rows:
        groups.setdefault(f"{'+' if r.amount > 0 else '-'} {pattern(r.description)}", []).append(r)
    out: dict[str, Label] = {}
    todo = []
    for key, group in groups.items():
        hit = rule(group[0]) if use_rules else None
        if hit:
            out[key] = Label(key=-1, category=hit, counterparty="", debt_kind="none")  # type: ignore[arg-type]
        else:
            todo.append((key, group))
    infos: list[CallInfo] = []
    for start in range(0, len(todo), 150):  # one call per 150 patterns
        chunk = todo[start : start + 150]
        lines = [
            f"{i} | {'credit' if g[0].amount > 0 else 'debit'} | {len(g)} | "
            f"{statistics.median(abs(r.amount) for r in g):,.2f} | "
            f"{'yes' if len({r.amount for r in g}) == 1 and len(g) > 1 else 'no'} | "
            f"{_gap(g)} | {g[0].description}"
            for i, (_, g) in enumerate(chunk)
        ]
        labels, info = parse_full(
            model,
            GUIDE + "\n".join(lines),
            Labels,
            system=SYSTEM,
            effort=effort,
            max_tokens=32000,
            api_key=api_key,
        )
        infos.append(info)
        by_key = {lab.key: lab for lab in labels.labels}
        for i, (key, group) in enumerate(chunk):
            lab = by_key.get(i) or Label(
                key=i,
                category="expense" if group[0].amount < 0 else "other_credit",
                counterparty="",
                debt_kind="none",
            )
            # direction is a fact: a credit can't be a fee and a debit can't be revenue
            if (group[0].amount > 0) != (lab.category in CREDIT_CATS):
                lab.category = "other_credit" if group[0].amount > 0 else "expense"
            # an owner transfer says so (TRANSFER / XFER from own account): a credit from a named
            # payer without that wording is business income
            # NSF and overdraft counts are key figures: only fee lines that say so count
            text = group[0].description.upper()
            if lab.category == "nsf_fee" and not _NSF.search(text):
                lab.category = "overdraft_fee" if _OD.search(text) else "other_fee"
            elif lab.category == "overdraft_fee" and not _OD.search(text):
                lab.category = "nsf_fee" if _NSF.search(text) else "other_fee"
            if lab.category == "owner_transfer" and not _OWN.search(group[0].description.upper()):
                lab.category = "revenue" if group[0].amount > 0 else "expense"
            out[key] = lab
    return out, infos


def _gap(group: list[TxnRow]) -> str:
    dates = sorted({r.date for r in group if r.date})
    gaps = [(b - a).days for a, b in itertools.pairwise(dates)]
    return f"{statistics.median(gaps):g}" if gaps else "-"


def key_of(row: TxnRow) -> str:
    return f"{'+' if row.amount > 0 else '-'} {pattern(row.description)}"
