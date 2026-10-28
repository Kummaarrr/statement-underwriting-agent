"""The scorer: a perfect answer scores perfect, and each kind of mistake is caught."""

import json

from stmt_uw.config import PROJECT_ROOT
from stmt_uw.schema import Assessment, Debt, StatementRevenue
from stmt_uw.scoring import FIGURES, aggregate, debt_correct, score_file, within

RECORDS = json.loads((PROJECT_ROOT / "data" / "dataset.json").read_text(encoding="utf-8"))


def _perfect(record, verdict="ready"):
    kf = record["key_figures"]
    return Assessment(
        revenue_by_statement=[
            StatementRevenue(period_end=end, revenue=c / 100)
            for end, c in kf["revenue_by_statement"].items()
        ],
        avg_monthly_revenue=kf["avg_monthly_revenue"] / 100,
        avg_daily_balance=kf["avg_daily_balance"] / 100,
        negative_days=kf["negative_days"],
        nsf_count=kf["nsf_count"],
        overdraft_count=kf["overdraft_count"],
        existing_debt=[
            Debt(
                provider=a["provider"].title() + " LLC",
                kind=a["kind"],
                frequency=a["frequency"],
                payment=a["payment"] / 100,
            )
            for a in kf["advances"]
        ],
        verdict=verdict,
        reason="ok",
    )


def test_perfect_answer_scores_perfect():
    for r in RECORDS:
        s = score_file(r, _perfect(r))
        assert s.all_correct, (r["id"], s.figures)


def test_each_mistake_is_caught():
    r = next(x for x in RECORDS if x["key_figures"]["advances"] and x["key_figures"]["nsf_count"])
    p = _perfect(r)
    p.revenue_by_statement[0].revenue *= 1.02
    assert not score_file(r, p).figures["revenue"]
    p = _perfect(r)
    p.nsf_count += 1
    assert not score_file(r, p).figures["nsf_count"]
    p = _perfect(r)
    p.existing_debt = p.existing_debt[1:]
    assert not score_file(r, p).figures["debt"]
    p = _perfect(r)
    p.existing_debt.append(
        Debt(provider="PHANTOM FUNDING", kind="mca", frequency="daily", payment=99)
    )
    assert not score_file(r, p).figures["debt"]


def test_tolerance_and_debt_matching():
    assert within(1010.0, 100_000) and not within(1011.0, 100_000)
    truth = [
        {
            "provider": "NORTHSTAR FUNDING GROUP",
            "kind": "mca",
            "frequency": "daily",
            "payment": 43500,
        }
    ]
    pred = [
        {"provider": "Northstar Funding", "kind": "mca", "frequency": "daily", "payment": 435.0}
    ]
    assert debt_correct(pred, truth)
    assert not debt_correct([{**pred[0], "frequency": "weekly"}], truth)


def test_safety_metric_counts_trusted_fakes():
    tampered = next(r for r in RECORDS if r["variant"] == "tampered")
    fooled = _perfect(tampered)
    fooled.avg_monthly_revenue *= 1.5  # read the inflated (printed) figures
    fooled.revenue_by_statement[0].revenue *= 1.5
    genuine = next(r for r in RECORDS if r["variant"] == "digital")
    scores = [score_file(tampered, fooled), score_file(genuine, None, error="boom")]
    agg = aggregate(scores)
    assert agg["wrong_on_ready"] == 1 and agg["errors"] == 1
    assert agg["tamper_recall_pct"] == 0.0  # the fake was trusted
    assert agg["false_alarm_pct"] == 100.0  # an error never auto-approves
    assert set(agg["figure_accuracy_pct"]) == set(FIGURES)
