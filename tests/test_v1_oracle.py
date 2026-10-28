"""v1's code half, given a perfect reading: figures must equal the truth exactly, genuine files
must reconcile, and forensics must separate edited from innocent documents.

No model calls: page extractions are rebuilt from the printed transactions in dataset.json, and
labels from the true categories. Whatever v1 gets wrong on real runs is then a reading or
labelling error, never arithmetic.
"""

import json
from pathlib import Path

import pytest

from stmt_uw.config import PROJECT_ROOT
from stmt_uw.scoring import score_file
from stmt_uw.v1 import figures, forensics, reconcile
from stmt_uw.v1.categorize import Label, key_of, rule
from stmt_uw.v1.extract import DailyBalance, Header, PageExtraction, Row
from stmt_uw.v1.pdfio import pdf_facts

RECORDS = json.loads((PROJECT_ROOT / "data" / "dataset.json").read_text(encoding="utf-8"))
HAVE_PDFS = (PROJECT_ROOT / RECORDS[0]["statements"][0]["file"]).exists()
CATEGORY = {
    "revenue_card": "revenue",
    "revenue_ach": "revenue",
    "revenue_deposit": "revenue",
    "transfer_in": "owner_transfer",
    "transfer_out": "owner_transfer",
    "advance_proceeds": "loan_proceeds",
    "return_reversal": "item_reversal",
    "deposit_return": "returned_deposit",
    "fee_nsf": "nsf_fee",
    "fee_overdraft": "overdraft_fee",
    "fee_service": "other_fee",
    "processor_fee": "expense",
    "advance_payment": "debt_payment",
    "loan_payment": "debt_payment",
}


def _perfect_reading(record):
    statements, labels = [], {}
    advances = {a["provider"]: a["kind"] for a in record["key_figures"]["advances"]}
    for k, st in enumerate(record["statements"]):
        txns = st["transactions"]
        atlas = record["bank"] == "atlas"  # no running balance, a daily balance table instead
        rows = [
            Row(
                date=t["date"],
                description=t["printed"],
                amount=t["amount"] / 100,
                balance=None if atlas else t["balance"] / 100,
                section="",
            )
            for t in txns
        ]
        daily = {t["date"]: t["balance"] / 100 for t in txns} if atlas else {}
        credits = [t["amount"] for t in txns if t["amount"] > 0]
        debits = [-t["amount"] for t in txns if t["amount"] < 0]
        header = Header(
            period_start=st["period_start"],
            period_end=st["period_end"],
            opening_balance=st["opening"] / 100,
            closing_balance=st["closing"] / 100,
            total_credits=sum(credits) / 100,
            total_debits=sum(debits) / 100,
            credit_count=len(credits),
            debit_count=len(debits),
        )
        page = PageExtraction(
            header=header,
            rows=rows,
            unreadable=False,
            daily_balances=[DailyBalance(date=d, balance=b) for d, b in daily.items()],
        )
        sd = reconcile.assemble(k, Path(st["file"]), [page])
        for r, t in zip(sd.rows, txns, strict=True):
            category = CATEGORY.get(t["category"], "expense")
            labels[key_of(r)] = Label(
                key=0,
                category=category,
                counterparty=t["counterparty"],  # type: ignore[arg-type]
                debt_kind=advances.get(t["counterparty"], "none"),
            )  # type: ignore[arg-type]
        statements.append(sd)
    return statements, labels


@pytest.mark.parametrize(
    "record", [r for r in RECORDS if r["variant"] != "tampered"], ids=lambda r: r["id"]
)
def test_genuine_files_reconcile_and_figures_are_exact(record):
    statements, labels = _perfect_reading(record)
    issues = [i for st in statements for i in reconcile.check(st)]
    issues += reconcile.chain_issues(statements)
    assert not issues, issues
    f = figures.compute(statements, labels)
    s = score_file(record, figures.to_assessment(f, "ready", "oracle"))
    assert s.all_correct, (s.figures, s.detail)


def test_tampered_arithmetic_is_caught_where_the_edit_breaks_it():
    for record in (r for r in RECORDS if r["variant"] == "tampered"):
        statements, _ = _perfect_reading(record)
        issues = [i for st in statements for i in reconcile.check(st)]
        issues += reconcile.chain_issues(statements)
        t = record["tamper"]
        last = t["statement_index"] == len(record["statements"]) - 1
        if t["type"] == "edit_amounts":
            assert any(i.kind == "balance_break" for i in issues), record["id"]
        elif t["type"] in ("edit_recalc", "insert_rows") and not last:
            assert any(i.kind == "chain" for i in issues), record["id"]


def test_rules_never_call_a_returned_deposit_fee_an_nsf():
    r = reconcile.TxnRow(0, 0, 0, None, "RETURNED DEPOSIT ITEM FEE", -12.0, None, "")
    assert rule(r) == "other_fee"
    r.description = "NSF: RETURNED ITEM FEE FOR ACTIVITY OF 07-01 CROWNVIEW FUNDING DEBIT"
    assert rule(r) == "nsf_fee"


@pytest.mark.skipif(not HAVE_PDFS, reason="generate the PDFs with scripts/build_dataset.py")
def test_forensics_flags_edits_but_not_innocent_resaves():
    for record in RECORDS:
        facts = [pdf_facts(PROJECT_ROOT / st["file"]) for st in record["statements"]]
        strong = {f.statement for f in forensics.inspect(facts) if f.strength == "strong"}
        t = record["tamper"]
        if t and t["type"] in ("edit_amounts", "edit_recalc", "insert_rows"):
            assert strong == {t["statement_index"]}, record["id"]
        else:  # genuine, re-saved, scanned, perfect fake: nothing strong
            assert not strong, (record["id"], strong)
