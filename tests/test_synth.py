"""Guarantees the ground truth depends on."""

import datetime as dt
import itertools
import random
from collections import Counter

from stmt_uw.model import REVENUE_CATS, Cat, key_figures
from stmt_uw.synth.calendar import fed_holidays, is_business_day
from stmt_uw.synth.render import BANKS, describe, money, visible_txns
from stmt_uw.synth.simulate import MCA_CAP, VARIABLE, ZELLE_LIMIT, simulate, statement_periods
from stmt_uw.synth.variants import tamper


def _apps(n=12, seed=3):
    rng, used = random.Random(seed), set()
    return [
        simulate(f"T{i}", rng, months=3, cycle_day=[None, 15][i % 2], used=used) for i in range(n)
    ]


def test_balances_and_statements_tie_exactly():
    for a in _apps():
        bal = a.opening_balance
        for t in a.txns:
            bal += t.amount
            assert bal == t.balance
        for prev, nxt in itertools.pairwise(a.statements):
            assert prev.closing == nxt.opening
        for s in a.statements:
            assert s.opening + sum(t.amount for t in s.txns) == s.closing


def test_postings_only_on_business_days():
    assert dt.date(2026, 9, 7) in fed_holidays(2026)  # Labor Day
    assert dt.date(2026, 7, 3) not in fed_holidays(2026)  # July 4 is a Saturday: Fed open Friday
    for a in _apps():
        assert all(is_business_day(t.date) for t in a.txns)


def test_statement_periods_are_contiguous():
    for cycle in (None, 15, 22):
        periods = statement_periods(5, cycle)
        assert periods[-1][1] <= dt.date(2026, 9, 30)
        for (_, e), (s, _) in itertools.pairwise(periods):
            assert s == e + dt.timedelta(days=1)


def test_revenue_excludes_financing_and_nets_returned_deposits():
    for a in _apps():
        kf = key_figures(a.txns, a.opening_balance, a.periods, a.advances)
        revenue = sum(t.amount for t in a.txns if t.category in REVENUE_CATS)
        returned = sum(t.amount for t in a.txns if t.category == Cat.DEPOSIT_RETURN)
        assert sum(kf.revenue_by_statement.values()) == revenue + returned
        assert len(kf.revenue_by_statement) == len(a.periods)
    non_revenue_credits = {Cat.TRANSFER_IN, Cat.ADVANCE_PROCEEDS, Cat.RETURN_REVERSAL}
    assert not non_revenue_credits & REVENUE_CATS


def test_returns_reverse_the_item_and_charge_a_fee_only_where_short():
    for a in _apps(30):
        fees = Counter(t.memo for t in a.txns if t.category == Cat.FEE_NSF)
        payroll_returned_on = None
        for i, t in enumerate(a.txns):
            if t.category == Cat.RETURN_REVERSAL:
                item = a.txns[i - 1]
                assert item.returned_item and item.amount == -t.amount
                assert item.balance < 0  # returned only when the balance was short
                assert item.channel not in ("transfer_out", "zelle_out", "debit_card", "fee")
                name = f"CHECK {item.check_no}" if item.check_no else item.counterparty
                assert any(m.startswith(name) and m.endswith(f"|{item.date:%m-%d}") for m in fees)
                if item.category in (Cat.PAYROLL, Cat.PAYROLL_TAX) and payroll_returned_on is None:
                    payroll_returned_on = item.date
            later = payroll_returned_on is not None and t.date > payroll_returned_on
            if later and t.category in (Cat.PAYROLL, Cat.PAYROLL_TAX):
                assert t.channel == "wire_out" or t.returned_item  # processor wants wires
        for fee in (t for t in a.txns if t.category == Cat.FEE_NSF):
            assert not fee.returned_item


def test_fee_only_banks_hide_returned_items_without_breaking_balances():
    for a in _apps(30):
        for st in a.statements:
            rows = visible_txns(st, "meridian")
            assert not any(t.returned_item or t.category == Cat.RETURN_REVERSAL for t in rows)
            bal = st.opening
            for t in rows:
                bal += t.amount
                assert bal == t.balance
            assert visible_txns(st, "summit") == st.txns


def test_names_unique_and_zelle_capped():
    apps = _apps(30)
    stems = {a.profile.business.split()[0].upper() for a in apps}
    landlords = {a.profile.names["landlord"] for a in apps}
    assert len(stems) == len(landlords) == len(apps)
    assert not any(word in stems for name in landlords for word in name.split())
    assert len({a.profile.owner for a in apps}) == len(apps)
    for a in apps:
        assert all(-t.amount <= ZELLE_LIMIT for t in a.txns if t.channel == "zelle_out")


def test_varying_amounts_never_repeat_and_mca_load_is_capped():
    for a in _apps(20):
        # a returned item presented again repeats its amount; different parties never do
        parties: dict[int, set[str]] = {}
        for t in a.txns:
            if t.category in VARIABLE:
                parties.setdefault(abs(t.amount), set()).add(t.counterparty)
        assert max(len(p) for p in parties.values()) == 1
        counts = Counter(
            abs(t.amount) for t in a.txns if t.category in VARIABLE and not t.returned_item
        )
        assert max(counts.values()) <= 2
        monthly = sum(
            x.payment * (21 if x.frequency == "daily" else 4.33)
            for x in a.advances
            if x.kind == "mca"
        )
        assert monthly <= MCA_CAP * a.profile.monthly_revenue * 1.01


def test_every_transaction_has_a_descriptor_at_every_bank():
    for a in _apps(6):
        for t in a.txns:
            for bank in BANKS:
                assert describe(t, bank, a.profile).strip()


def test_tamper_types_leave_the_intended_traces():
    a = _apps(1)[0]
    original = a.statements[0].txns[0].amount
    kept_cents = 0
    for seed, kind in itertools.product(range(4), TAMPER_KINDS):
        fake, info = tamper(a, random.Random(seed), kind)
        st = fake.statements[info.statement_index]
        bal, breaks = st.opening, 0
        for t in st.txns:
            bal += t.amount
            breaks += bal != t.balance
        assert (breaks > 0) == (kind == "edit_amounts")
        assert info.edited_ids
        if kind == "perfect_fake":
            assert info.statement_index == len(fake.statements) - 1
        later = fake.statements[info.statement_index + 1 :]
        if later and kind in ("edit_recalc", "insert_rows"):
            assert later[0].opening != st.closing  # the edit doesn't leak into later months
        edited = [t for t in st.txns if t.id in info.edited_ids]
        kept_cents += sum(t.amount % 100 != 0 for t in edited)
    assert kept_cents  # edits are not all whole dollars
    assert a.statements[0].txns[0].amount == original  # original intact


TAMPER_KINDS = ("edit_amounts", "edit_recalc", "insert_rows", "perfect_fake")


def test_money_formats_and_routing_checksums():
    assert money(-123456, "trailing") == "1,234.56-"
    assert money(-123456, "crdr") == "1,234.56 DR"
    assert money(0, "crdr") == "0.00"
    assert money(123456, "signed") == "1,234.56"
    for bank in BANKS.values():
        d = [int(c) for c in bank.routing]
        assert len(d) == 9
        assert sum(w * x for w, x in zip([3, 7, 1] * 3, d, strict=True)) % 10 == 0
