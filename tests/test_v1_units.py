"""Real-world layouts and inputs the synthetic banks don't produce (from code review)."""

import pymupdf

from stmt_uw.v1 import reconcile
from stmt_uw.v1.extract import Header, PageExtraction, Row
from stmt_uw.v1.pdfio import pdf_facts


def _statement(index, start, end, opening, closing, rows):
    header = Header(
        period_start=start,
        period_end=end,
        opening_balance=opening,
        closing_balance=closing,
        total_credits=None,
        total_debits=None,
        credit_count=None,
        debit_count=None,
    )
    page = PageExtraction(header=header, rows=rows, daily_balances=[], unreadable=False)
    return reconcile.assemble(index, None, [page])


def test_balance_printed_once_per_day_reconciles():
    rows = [
        Row(date="2026-01-05", description="A", amount=-50.0, balance=None, section=""),
        Row(date="2026-01-05", description="B", amount=-20.0, balance=930.0, section=""),
        Row(date="2026-01-06", description="C", amount=100.0, balance=1030.0, section=""),
    ]
    st = _statement(0, "2026-01-01", "2026-01-31", 1000.0, 1030.0, rows)
    assert not reconcile.check(st)
    assert not st.repairs  # nothing to repair: the earlier rows move the running total


def test_a_misread_balance_is_repaired_but_an_edited_amount_is_not():
    rows = [
        Row(date="2026-01-05", description="A", amount=-50.0, balance=950.0, section=""),
        Row(date="2026-01-06", description="B", amount=-20.0, balance=390.0, section=""),  # 930
        Row(date="2026-01-07", description="C", amount=100.0, balance=1030.0, section=""),
    ]
    st = _statement(0, "2026-01-01", "2026-01-31", 1000.0, 1030.0, rows)
    assert st.repairs and not reconcile.check(st)
    edited = [
        Row(date="2026-01-05", description="A", amount=-50.0, balance=950.0, section=""),
        Row(date="2026-01-06", description="DEPOSIT", amount=500.0, balance=930.0, section=""),
        Row(date="2026-01-07", description="C", amount=100.0, balance=1030.0, section=""),
    ]
    st = _statement(0, "2026-01-01", "2026-01-31", 1000.0, 1030.0, edited)
    assert any(i.kind == "balance_break" for i in reconcile.check(st))


def test_statements_uploaded_out_of_order_still_chain():
    jan = _statement(0, "2026-01-01", "2026-01-31", 100.0, 100.0, [])
    feb = _statement(1, "2026-02-01", "2026-02-28", 100.0, 100.0, [])
    assert not reconcile.chain_issues([feb, jan])


def test_linearized_pdf_is_not_an_incremental_update(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Statement")
    path = tmp_path / "lin.pdf"
    try:
        doc.save(path, linear=True)
    except Exception:
        doc.save(path)
    assert pdf_facts(path).xref_sections == 1
