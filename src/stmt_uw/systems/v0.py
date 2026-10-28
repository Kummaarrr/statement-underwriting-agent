"""v0 / v0-small: the "tutorial" approach. Every statement PDF of the file goes to one model
call with the figure definitions; the model reads, categorises, computes and judges in one go.

Fair to the baseline: it gets exactly the definitions the ground truth uses, and the model reads
both the text layer and the page images of each PDF (scans included).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stmt_uw.config import PROJECT_ROOT
from stmt_uw.llm import CallInfo, Content, parse_full, pdf_block
from stmt_uw.schema import Assessment

SYSTEM = "You are an experienced small-business underwriting analyst at a US lender."

INSTRUCTIONS = """\
The documents above are one applicant's business checking statements, in order. Read every \
transaction and return the key figures the credit team decides on:

- revenue per statement period (keyed by the statement's closing date) and its average:
  business-income credits - card-processor settlements, customer payments (ACH, wire), mobile /
  branch / counter deposits - minus deposited items that were returned unpaid. Exclude transfers
  from the owner's own accounts, loan or advance proceeds, and reversals of returned debits.
- average daily balance: the mean end-of-day balance over every calendar day from the first
  statement's start to the last statement's end (days without activity carry the previous
  balance).
- negative days: calendar days whose end-of-day balance is below zero.
- NSF count: fee events for the business's own payments returned unpaid for insufficient funds
  (NSF / returned-item fees). A fee for a deposited check that bounced (a returned deposit) is
  not an NSF event. Overdraft count: overdraft / courtesy-pay fee events (items that were paid).
- existing debt: every merchant cash advance (MCA), term loan or equipment loan making recurring
  payments - provider, kind, frequency (daily / weekly / monthly) and payment amount.

Then give a verdict: "ready" if the figures can be trusted without a human check, or
"needs_review" with the reason (for example rows that don't reconcile, signs the document was
edited, pages you could not read)."""


def build_content(record: dict[str, Any]) -> Content:
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": f"Applicant file with {len(record['statements'])} statements."}
    ]
    for st in record["statements"]:
        path = PROJECT_ROOT / st["file"]
        blocks.append(pdf_block(path, title=Path(st["file"]).name))
    blocks.append({"type": "text", "text": INSTRUCTIONS})
    return blocks


def run(record: dict[str, Any], model: str) -> tuple[Assessment, CallInfo]:
    return parse_full(model, build_content(record), Assessment, system=SYSTEM, max_tokens=64000)
