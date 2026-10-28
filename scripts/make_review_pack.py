"""Copy whole applicant files into a blind review pack, with a separate answer key.

uv run python scripts/make_review_pack.py app-016 app-015 ...   # -> data/generated/review_pack/
"""

from __future__ import annotations

import json
import shutil
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "generated" / "review_pack"


def usd(cents: int) -> str:
    return f"-${-cents / 100:,.2f}" if cents < 0 else f"${cents / 100:,.2f}"


def main(ids: list[str]) -> int:
    records = {r["id"]: r for r in json.loads((ROOT / "data" / "dataset.json").read_text("utf-8"))}
    if OUT.exists():
        shutil.rmtree(OUT)
    key = ["# Review pack - answer key", "", "Open the files first; read this after.", ""]
    for letter, app_id in zip(string.ascii_uppercase, ids, strict=False):
        r = records[app_id]
        folder = OUT / f"file_{letter}"
        folder.mkdir(parents=True)
        for st in r["statements"]:
            shutil.copy(ROOT / st["file"], folder / Path(st["file"]).name)
        k = r["key_figures"]
        key += [
            f"## File {letter} ({app_id})",
            "",
            f"- {r['profile']['business']}, {r['profile']['industry']}, risk profile "
            f"**{r['profile']['risk']}**; bank layout {r['bank']}; variant **{r['variant']}**; "
            f"{len(r['statements'])} statements {r['period']['start']} to {r['period']['end']}",
            "- Revenue per statement: "
            + ", ".join(f"{end}: {usd(v)}" for end, v in k["revenue_by_statement"].items()),
            f"- Average monthly revenue {usd(k['avg_monthly_revenue'])}; average daily balance "
            f"{usd(k['avg_daily_balance'])}; negative days {k['negative_days']}; "
            f"NSF {k['nsf_count']}; overdraft {k['overdraft_count']}",
            "- Existing debt: "
            + (
                "; ".join(
                    f"{a['provider']} ({a['kind']}, {a['frequency']} {usd(a['payment'])})"
                    for a in k["advances"]
                )
                or "none"
            ),
            f"- Expected verdict: **{r['expected_verdict']}** (ready = the figures can be trusted "
            "without a human check; it is not a credit decision)",
        ]
        if r.get("resaved"):
            key.append(
                f"- Genuine, but statement {r['resaved']['statement_index'] + 1} was re-saved with "
                f"{r['resaved']['tool']} (a false-alarm test)"
            )
        if r["tamper"]:
            t = r["tamper"]
            st = r["statements"][t["statement_index"]]
            key.append(
                f"- Tampered: **{t['type']}** in {Path(st['file']).name} ({t['note']}). Rows:"
            )
            for tx in st["transactions"]:
                if tx["id"] in t["edited_ids"]:
                    key.append(f"  - {tx['date']} {tx['printed']} {usd(tx['amount'])}")
        key.append("")
    (OUT / "ANSWER_KEY.md").write_text("\n".join(key), encoding="utf-8")
    print(f"{len(ids)} files -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
