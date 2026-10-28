"""Compare v1's labels with the true categories for given files (cached runs: free).

uv run python scripts/diagnose_v1.py app-029 app-033
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from stmt_uw.config import PROJECT_ROOT
from stmt_uw.llm import LEDGER
from stmt_uw.v1.agent import analyse_record
from stmt_uw.v1.categorize import key_of

TRUE = {"revenue_card": "revenue", "revenue_ach": "revenue", "revenue_deposit": "revenue",
        "transfer_in": "owner_transfer", "transfer_out": "owner_transfer",
        "advance_proceeds": "loan_proceeds", "return_reversal": "item_reversal",
        "deposit_return": "returned_deposit", "fee_nsf": "nsf_fee",
        "fee_overdraft": "overdraft_fee", "fee_service": "other_fee",
        "advance_payment": "debt_payment", "loan_payment": "debt_payment"}  # fmt: skip


def main(ids: list[str]) -> None:
    LEDGER.cap_usd = 0.5
    records = {r["id"]: r for r in json.loads((PROJECT_ROOT / "data/dataset.json").read_text())}
    for app_id in ids:
        record = records[app_id]
        res = analyse_record(record)
        labels = res.state["labels"]
        truth = {
            (t["date"], t["amount"]): t for st in record["statements"] for t in st["transactions"]
        }
        wrong: Counter[tuple[str, str, str]] = Counter()
        for st in res.state["statements"]:
            for r in st.rows:
                t = truth.get((r.date.isoformat() if r.date else "", round(r.amount * 100)))
                if not t:
                    continue
                want = TRUE.get(t["category"], "expense")
                got = labels[key_of(r)].category
                if got != want:
                    wrong[(want, got, r.description[:70])] += 1
        print(f"== {app_id} ({record['profile']['industry']}) cost ${res.cost_usd:.4f}")
        print(
            "   debts found:",
            [(d.provider, d.kind, d.frequency, d.payment) for d in res.assessment.existing_debt],
        )
        print(
            "   truth debts:",
            [
                (a["provider"], a["kind"], a["frequency"], a["payment"] / 100)
                for a in record["key_figures"]["advances"]
            ],
        )
        for (want, got, desc), n in wrong.most_common(12):
            print(f"   {n:3}x true {want:15} got {got:15} {desc}")


if __name__ == "__main__":
    main(sys.argv[1:])
