"""Render statements as PDFs in five fictional bank layouts.

The layouts differ the way real US business statements do, and each difference trips up
naive extraction differently: debit/credit columns vs signed amounts, sectioned statements with
a daily-balance table instead of a running balance, multi-line descriptors with trailing-minus
debits, and a dense monospace "core banking" printout. Each bank has its own labels, fee box,
disclosures and continuation-page header, and prints its own transaction descriptors
(`describe`) from the structured fields: the same Square deposit reads differently at every
bank. Most banks print a returned (NSF) item only as a fee line naming the item; the legacy
core layout prints the item, its reversal and the fee.

Tamper traces: `alt_font_ids` rows (and `alt_cell_ids` balance cells) are set in `alt_font`;
dates and producer are written into the metadata. Pages carry no "synthetic" label (it would
be a giveaway to a model); the PDF metadata subject says so, identically on every file.
"""

from __future__ import annotations

import datetime as dt
import os
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pymupdf
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    KeepTogether,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from stmt_uw.model import Cat, Txn
from stmt_uw.synth.simulate import Applicant, Profile, Statement

SUBJECT = "Synthetic sample statement for software testing (fictional bank and business)"
EDITOR_FONT = "Vera"  # embedded subset font a PDF editor substitutes: close to Helvetica
pdfmetrics.registerFont(TTFont(EDITOR_FONT, "Vera.ttf"))
FEES = (Cat.FEE_NSF, Cat.FEE_OVERDRAFT, Cat.FEE_SERVICE)


def _routing(prefix8: str) -> str:
    """Complete an 8-digit prefix with the ABA checksum digit."""
    d = [int(c) for c in prefix8]
    s = 3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5])
    return prefix8 + str((10 - s % 10) % 10)


@dataclass(frozen=True)
class Bank:
    key: str
    name: str
    address: str
    producer: str  # the bank's statement software, as written into PDF metadata
    font: str
    font_bold: str
    account_type: str
    routing: str
    phone: str
    insured: str
    cycle_day: int | None  # None = calendar-month statements
    states: tuple[str, ...]  # footprint: customers come from these states
    fee_only_returns: bool  # True: a returned item prints only as its NSF fee
    labels: dict[str, str] = field(default_factory=dict)
    footer: str = ""
    tz: str = "-04'00'"  # UTC offset of the bank's statement system


ALL_STATES = ("TX", "FL", "GA", "OH", "AZ", "NC", "IL", "NV")
BANKS = {
    "meridian": Bank(
        "meridian",
        "Meridian National Bank",
        "PO Box 4410, Charlotte, NC 28201",
        "Meridian eStatements 4.2",
        "Helvetica",
        "Helvetica-Bold",
        "Business Essentials Checking",
        _routing("05310428"),
        "1-800-555-0142",
        "Member FDIC",
        None,
        ALL_STATES,
        True,
        {
            "account": "Account number",
            "summary": "Account summary",
            "open": "Beginning balance on {d}",
            "credits": "Deposits and other additions",
            "debits": "Withdrawals and other subtractions",
            "close": "Ending balance on {d}",
            "avg": "Average ledger balance",
            "fees": "Overdraft and returned item fees",
            "od": "Total overdraft fees",
            "nsf": "Total returned item fees",
            "svc": "Total service fees",
            "detail": "Transaction detail",
        },
        "Your deposit account is governed by the Business Deposit Agreement. Review this statement "
        "promptly; report any error or unauthorized transaction within 60 days of the statement "
        "date by calling Business Services at 1-800-555-0142. Meridian National Bank, Member FDIC.",
    ),
    "pinecrest": Bank(
        "pinecrest",
        "Pinecrest Bank",
        "PO Box 2290, Columbus, OH 43216",
        "Pinecrest DocGen 2.7",
        "Times-Roman",
        "Times-Bold",
        "Small Business Checking",
        _routing("04420731"),
        "1-800-555-0177",
        "Member FDIC",
        15,
        ("OH", "IL"),
        True,
        {
            "account": "Account number",
            "summary": "Activity summary",
            "open": "Opening balance {d}",
            "credits": "Deposits/Credits",
            "debits": "Withdrawals/Debits",
            "close": "Ending balance {d}",
            "avg": "Average ledger balance",
            "fees": "Fee summary",
            "od": "Overdraft fees",
            "nsf": "Non-sufficient funds fees",
            "svc": "Service and other fees",
            "detail": "Transaction history",
        },
        "Thank you for banking with Pinecrest. If you think your statement is wrong or need more "
        "information about a transaction, contact us at 1-800-555-0177 or visit your branch. "
        "Overdraft and NSF fees are assessed per item as disclosed in the Schedule of Fees. "
        "Member FDIC. Equal Housing Lender.",
    ),
    "atlas": Bank(
        "atlas",
        "Atlas Federal Credit Union",
        "1 Atlas Plaza, Phoenix, AZ 85004",
        "Atlas Statement Server 11",
        "Helvetica",
        "Helvetica-Bold",
        "Business Share Draft",
        _routing("32217506"),
        "1-800-555-0119",
        "Federally insured by NCUA",
        None,
        ("AZ", "NV"),
        True,
        {
            "account": "Member number",
            "summary": "Share draft summary",
            "open": "Previous balance",
            "credits": "Deposits & credits",
            "debits": "Withdrawals & debits",
            "close": "New balance",
            "avg": "Average daily balance",
            "fees": "Fees charged",
            "od": "Courtesy pay fees",
            "nsf": "NSF fees",
            "svc": "Other fees",
            "detail": "",
        },
        "Federally insured by NCUA. Courtesy Pay is a discretionary service and not a line of "
        "credit. For questions about electronic transfers, call Member Services at "
        "1-800-555-0119 or write to 1 Atlas Plaza, Phoenix, AZ 85004.",
        tz="-07'00'",
    ),
    "harborview": Bank(
        "harborview",
        "Harborview Bank & Trust",
        "PO Box 31780, Tampa, FL 33631",
        "HBT Output Manager 6.1",
        "Helvetica",
        "Helvetica-Bold",
        "Business Select Checking",
        _routing("06310095"),
        "1-800-555-0163",
        "Member FDIC",
        None,
        ("FL", "GA", "NC"),
        True,
        {
            "account": "Account Number",
            "summary": "CHECKING SUMMARY",
            "open": "Beginning Balance",
            "credits": "Deposits and Additions",
            "debits": "Withdrawals and Debits",
            "close": "Ending Balance",
            "avg": "Average Ledger Balance",
            "fees": "OVERDRAFT AND RETURNED ITEM FEES",
            "od": "Total Overdraft Fees",
            "nsf": "Total Returned Item Fees",
            "svc": "Total Service Fees",
            "detail": "TRANSACTION DETAIL",
        },
        "IN CASE OF ERRORS OR QUESTIONS ABOUT YOUR ELECTRONIC FUNDS TRANSFERS: call "
        "1-800-555-0163 or write to Harborview Bank & Trust, PO Box 31780, Tampa, FL 33631 as "
        "soon as you can. Deposit products provided by Harborview Bank & Trust. Member FDIC.",
    ),
    "summit": Bank(
        "summit",
        "Summit First Bank",
        "PO Box 1200, Austin, TX 78767",
        "SFB CoreBank Print 3.0",
        "Courier",
        "Courier-Bold",
        "COMMERCIAL CHECKING",
        _routing("11190023"),
        "1-800-555-0185",
        "Member FDIC",
        22,
        ("TX", "AZ", "NV"),
        False,
        {
            "account": "ACCOUNT NO",
            "summary": "BALANCE SUMMARY",
            "open": "PREVIOUS BALANCE",
            "credits": "CREDITS",
            "debits": "DEBITS",
            "close": "CURRENT BALANCE",
            "avg": "AVG LEDGER BAL",
            "fees": "SERVICE CHARGE SUMMARY",
            "od": "OD ITEM CHARGES",
            "nsf": "NSF CHARGES",
            "svc": "MAINT/OTHER",
            "detail": "ACTIVITY",
        },
        "SUMMIT FIRST BANK  MEMBER FDIC  *  CALL 1-800-555-0185 WITH QUESTIONS  *  "
        "EXAMINE THIS STATEMENT UPON RECEIPT AND REPORT DISCREPANCIES PROMPTLY.",
        tz="-05'00'",
    ),
}
ALT_FONT = {"Helvetica": "Times-Roman", "Times-Roman": "Helvetica", "Courier": "Helvetica"}
BRANCHES = {  # neighbourhood branch names per city; each bank has one or two of them
    "Austin": ["Congress Ave", "Mueller", "Arboretum"],
    "Tampa": ["Westshore", "Carrollwood", "Ybor City"],
    "Atlanta": ["Buckhead", "Midtown", "Perimeter"],
    "Columbus": ["German Village", "Easton", "Short North"],
    "Phoenix": ["Camelback", "Arcadia", "Ahwatukee"],
    "Charlotte": ["SouthPark", "Uptown", "Ballantyne"],
    "Naperville": ["Ogden Ave", "Downtown Naperville", "Route 59"],
    "Henderson": ["Green Valley", "Anthem", "Eastern Ave"],
}
BRANCH_STREETS = ["Main St", "Central Ave", "Commerce Blvd", "Market St", "Oak Ave", "Park Pl"]


def money(cents: int, style: str = "plain") -> str:
    """Format cents: plain '1,234.56', 'signed' '-1,234.56', 'trailing' '1,234.56-',
    'crdr' '1,234.56 DR' (zero prints without a suffix)."""
    v = f"{abs(cents) / 100:,.2f}"
    if style == "signed":
        return f"-{v}" if cents < 0 else v
    if style == "trailing":
        return f"{v}-" if cents < 0 else v
    if style == "crdr":
        return v if cents == 0 else f"{v} {'DR' if cents < 0 else 'CR'}"
    return v


def _h(s: str, digits: int) -> str:
    """Stable pseudo-random digits from a string (same input -> same number)."""
    return f"{zlib.crc32(s.encode()) * 2654435761 % 10**digits:0{digits}d}"


def branch(p: Profile, bank: str) -> tuple[str, str]:
    """The applicant's branch: (name, street address). Each bank has two fixed branches per
    city; the address belongs to the branch, so every customer of it sees the same one."""
    names = BRANCHES[p.city]
    own = [names[int(_h(bank + p.city, 2)) % 3], names[(int(_h(bank + p.city, 2)) + 1) % 3]]
    name = own[int(_h(p.business, 1)) % 2]
    n = int(_h(bank + name, 4))
    return name, f"{100 + n % 8900} {BRANCH_STREETS[n % len(BRANCH_STREETS)]}"


def _ach_id(cp: str, ref: str) -> str:
    """Per-originator ID formats: the IRS (EFTPS) is 15 digits, ADP numeric, Stripe 'ST-'."""
    if cp == "IRS":
        return "2704" + _h(ref, 11)
    if cp.startswith("ADP"):
        return _h(ref, 12)
    if cp == "STRIPE":
        return f"ST-{ref[:4]}{_h(ref, 8)}"
    if cp.startswith("AMAZON"):
        return _h(ref, 10)
    if cp == "GUSTO":
        return ref.lower()
    return ref[:8]


def describe(t: Txn, bank: str, p: Profile) -> str:
    """The descriptor `bank` prints for `t` ('\\n' separates printed lines)."""
    cp, entry, ref = t.counterparty, t.entry, t.ref or "0000000000"
    biz, city, st = p.business.upper(), p.city.upper(), p.state
    md, ymd = f"{t.date:%m/%d}", f"{t.date:%y%m%d}"
    credit = t.amount > 0
    card4 = _h(p.business + "card", 4)
    co_id = "1" + _h(cp, 9)
    ch = t.channel
    if t.category == Cat.RETURN_REVERSAL:  # printed by the legacy core layout only
        item = f"CHECK {t.check_no}" if t.check_no else cp
        return f"RTN ITEM {item[:20].strip()}"
    if t.category == Cat.DEPOSIT_RETURN:
        return {
            "meridian": f"DEPOSITED ITEM RETURNED REF {ref[:8]}",
            "pinecrest": "RETURN OF DEPOSITED ITEM",
            "atlas": "RETURNED DEPOSIT ITEM",
            "harborview": f"Deposited Item Returned\nReason: NSF Ref {ref[:8]}",
            "summit": "CHGBK DEP ITEM",
        }[bank]
    if ch == "fee":
        if entry == "NSF" and t.memo:
            item, cents, *activity = t.memo.split("|")
            on = activity[0] if activity else f"{t.date:%m-%d}"
            amount = f"${int(cents) / 100:,.2f}"
            return {
                "meridian": f"NSF: RETURNED ITEM FEE FOR ACTIVITY OF {on} {item}",
                "pinecrest": f"NON-SUFFICIENT FUNDS FEE FOR A {amount} ITEM - DETAILS: {item}",
                "atlas": f"NSF FEE - RETURNED {item}",
                "harborview": f"Insufficient Funds Fee For A {amount} Item\nDetails: {item}",
                "summit": "NSF CHG",
            }[bank]
        return {
            "NSF": {
                "meridian": "NSF: RETURNED ITEM FEE",
                "pinecrest": "NON-SUFFICIENT FUNDS FEE",
                "atlas": "NSF FEE",
                "harborview": "Insufficient Funds Fee",
                "summit": "NSF CHG",
            },
            "OD": {
                "meridian": "OVERDRAFT ITEM FEE",
                "pinecrest": "OVERDRAFT FEE",
                "atlas": "COURTESY PAY FEE",
                "harborview": "Overdraft Paid Fee",
                "summit": "OD ITEM CHG",
            },
            "MAINT": {
                "meridian": "MONTHLY MAINTENANCE FEE",
                "pinecrest": "MONTHLY SERVICE FEE",
                "atlas": "SHARE DRAFT SERVICE CHARGE",
                "harborview": "Monthly Service Fee",
                "summit": "MAINT FEE",
            },
            "RDI": {
                "meridian": "RETURNED DEPOSIT ITEM FEE",
                "pinecrest": "DEPOSITED ITEM RETURNED FEE",
                "atlas": "RETURN DEP ITEM FEE",
                "harborview": "Deposited Item Returned Fee",
                "summit": "RTN DEP CHG",
            },
            "WIREOUT": {
                "meridian": "Wire Transfer Fee - Outgoing",
                "pinecrest": "WIRE OUT SVC CHARGE",
                "atlas": "OUTGOING WIRE FEE",
                "harborview": "Domestic Wire Fee",
                "summit": "WIRE OUT FEE",
            },
            "WIRE": {
                "meridian": "Wire Transfer Fee",
                "pinecrest": "WIRE TRANS SVC CHARGE",
                "atlas": "INCOMING WIRE FEE",
                "harborview": "Domestic Incoming Wire Fee",
                "summit": "WIRE IN FEE",
            },
        }[entry][bank]
    if ch in ("card_settlement", "ach_credit", "ach_debit"):
        trace = _h(cp + "odfi", 8) + _h(ref, 7)  # first 8: the originator's bank (ODFI)
        return {
            "meridian": f"{cp} DES:{entry} ID:{_ach_id(cp, ref)} INDN:{biz[:22]} CO ID:{co_id} CCD",
            "pinecrest": f"{cp} {entry} {ymd} {_ach_id(cp, ref)} {biz[:22]}",
            "atlas": f"ACH {'DEPOSIT' if credit else 'WITHDRAWAL'} {cp} {entry}",
            "harborview": f"Orig CO Name:{cp} Orig ID:{co_id} Desc Date:{ymd} CO Entry "
            f"Descr:{entry} Sec:CCD Trace#:{trace} Eed:{ymd} Ind ID:{_ach_id(cp, ref)} "
            f"Ind Name:{biz[:22]} Trn: {_h(ref, 10)}Tc",
            "summit": f"ACH {'CR' if credit else 'DB'} {cp[:18].strip()} {entry[:10]}",
        }[bank]
    if ch == "debit_card":
        name, _, loc = cp.partition("|")
        loc = loc or f"{city} {st}"
        auth = t.date - dt.timedelta(days=1 + int(_h(ref, 1)) % 2)
        return {
            "meridian": f"CHECKCARD {auth:%m%d} {name} {loc} {_h(ref, 12)}{_h(ref + 'x', 11)}",
            "pinecrest": f"PURCHASE AUTHORIZED ON {auth:%m/%d} {name} {loc} "
            f"S{_h(ref, 15)} CARD {card4}",
            "atlas": f"POS PURCHASE {name} {loc}",
            "harborview": f"Card Purchase {auth:%m/%d} {name.title()} {loc} Card {card4}",
            "summit": f"POS DB {name[:18].strip()} {auth:%m%d}",
        }[bank]
    if ch == "zelle_out":
        return {
            "meridian": f"Zelle payment to {cp.title()} Conf# {ref[:9].lower()}",
            "pinecrest": f"ZELLE TO {cp} ON {md} REF # PP0{ref[:7]}",
            "atlas": f"ZELLE SENT {cp} CONF {ref[:8]}",
            "harborview": f"Zelle Payment To {cp.title()} Hbt{_h(ref, 11)}",
            "summit": f"ZELLE DB {cp[:20].strip()}",
        }[bank]
    if ch in ("transfer_in", "transfer_out"):
        kind, last4 = cp.split()
        way = "FROM" if credit else "TO"
        account = "BUSINESS SAVINGS" if kind == "SAV" else "PERSONAL CHECKING"
        return {
            "meridian": f"Online Banking transfer {way.lower()} {kind} {last4} "
            f"Confirmation# {_h(ref, 10)}",
            "pinecrest": f"ONLINE TRANSFER {way} {account} XXXXXX{last4} REF #IB0{ref[:7]}",
            "atlas": f"TRANSFER {way} {'SHARE' if kind == 'SAV' else 'DRAFT'} {last4}",
            "harborview": f"Online Transfer {way.title()} {kind.title()} ...{last4} "
            f"Transaction#: {_h(ref, 11)}",
            "summit": f"XFER {way} {kind} {last4}",
        }[bank]
    hhmm = f"{8 + int(_h(ref, 1)) % 9:02d}{int(_h(ref, 2)) % 60:02d}"
    if ch == "wire_out":
        return {
            "meridian": f"WIRE TYPE:WIRE OUT DATE:{ymd} TIME:{hhmm} ET "
            f"TRN:{t.date:%Y%m%d}{_h(ref, 8)} SERVICE REF:{_h(ref, 6)} BNF:{cp}",
            "pinecrest": f"WT {ymd}-{_h(ref, 6)} BNF={cp} SRF# {_h(ref, 16)} TRN#{_h(ref, 12)}",
            "atlas": f"OUTGOING WIRE TO {cp}",
            "harborview": f"Online Domestic Wire Transfer A/C: {cp.title()} "
            f"Imad: {t.date:%Y%m%d}B1Q8{_h(ref, 6)} Trn: {_h(ref, 10)}Es",
            "summit": f"WIRE OUT {cp[:21].strip()}",
        }[bank]
    if ch == "wire_in":
        sender = t.memo or "FIRST CAPITAL TRUST"
        return {
            "meridian": f"WIRE TYPE:WIRE IN DATE:{ymd} TIME:{hhmm} ET "
            f"TRN:{t.date:%Y%m%d}{_h(ref, 8)} SEQ:{_h(ref, 6)} SND BK:{sender} ORIG:{cp}",
            "pinecrest": f"WT FED#{_h(ref, 5)} {sender} /ORG={cp} SRF# {_h(ref, 16)} "
            f"TRN#{ymd}{_h(ref, 6)} RFB# {_h(cp, 9)}",
            "atlas": f"INCOMING WIRE {cp} VIA {sender}",
            "harborview": f"Fedwire Credit Via: {sender.title()} B/O: {cp} "
            f"Imad: {t.date:%Y%m%d}B1Q8{_h(ref, 6)} Trn: {_h(ref, 10)}Ff",
            "summit": f"WIRE IN {cp[:22].strip()}",
        }[bank]
    if ch == "deposit":
        b_name, b_street = branch(p, bank)
        hour = 9 + int(_h(ref, 1)) % 8  # banking hours, 12-hour clock
        stamp = (
            f"{t.date:%m/%d/%y} {(hour - 1) % 12 + 1:02d}:{int(_h(ref, 2)) % 60:02d}:"
            f"{int(_h(ref + 's', 2)) % 60:02d} {'AM' if hour < 12 else 'PM'}"
        )
        return {
            "MOBILE": {
                "meridian": f"MOBILE CHECK DEPOSIT {_h(ref, 8)}",
                "pinecrest": f"MOBILE DEPOSIT : REF NUMBER :{_h(ref, 12)}",
                "atlas": "DEPOSIT MOBILE",
                "harborview": f"Remote Online Deposit {1 + int(_h(ref, 1)) % 4}",
                "summit": "DEP MOBILE",
            },
            "BRANCH": {
                "meridian": "COUNTER CREDIT",
                "pinecrest": f"EDEPOSIT IN BRANCH/STORE {stamp} {b_street.upper()} {city} {st}",
                "atlas": f"DEPOSIT {b_name.upper()} BRANCH",
                "harborview": f"Deposit ID Number {_h(ref, 6)}",
                "summit": "DEPOSIT",
            },
            "COUNTER": {
                "meridian": "COUNTER CREDIT",
                "pinecrest": "DEPOSIT",
                "atlas": "DEPOSIT",
                "harborview": "Deposit",
                "summit": "DEPOSIT",
            },
        }[entry][bank]
    if ch == "check":
        return {
            "meridian": f"Check {t.check_no}",
            "pinecrest": f"CHECK # {t.check_no}",
            "atlas": f"CHECK {t.check_no}",
            "harborview": f"CHECK {t.check_no}",
            "summit": f"CHK {t.check_no}",
        }[bank]
    return t.description


def visible_txns(st: Statement, bank: str) -> list[Txn]:
    """The rows the bank prints: fee-only banks omit a returned item and its reversal (they
    net to zero, so the running balance is unaffected)."""
    if not BANKS[bank].fee_only_returns:
        return st.txns
    return [t for t in st.txns if not t.returned_item and t.category != Cat.RETURN_REVERSAL]


def printed_description(t: Txn, bank: str, p: Profile) -> str:
    """The descriptor as one line of text (what a reader extracts from the page)."""
    text = describe(t, bank, p).replace("\n", " ")
    return text[:38] if bank == "summit" else text


def _wrap(text: str, font: str, size: float, width_pt: float) -> str:
    """Greedy word wrap to the column width (each '\\n' line wrapped separately)."""
    out = []
    for line in text.split("\n"):
        cur = ""
        for word in line.split(" "):
            cand = f"{cur} {word}".strip()
            if cur and stringWidth(cand, font, size) > width_pt:
                out.append(cur)
                cur = word
            else:
                cur = cand
        out.append(cur)
    return "\n".join(out)


class _NumberedCanvas(rl_canvas.Canvas):
    """Adds 'Page x of y' and, from page 2, a continuation header with account and period."""

    def __init__(self, *args: Any, header: str = "", font: str = "Helvetica", **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._pages: list[dict] = []
        self._header, self._hfont = header, font

    def showPage(self) -> None:
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self.setFont("Helvetica", 7)
            self.drawRightString(
                LETTER[0] - 0.6 * inch, 0.45 * inch, f"Page {self._pageNumber} of {total}"
            )
            if self._pageNumber > 1 and self._header:
                self.setFont(self._hfont, 7.5)
                self.drawString(0.6 * inch, LETTER[1] - 0.38 * inch, self._header)
            super().showPage()
        super().save()


def _header_block(app: Applicant, st: Statement, bank: Bank, styles: dict) -> list:
    p = app.profile
    b_name, b_street = branch(p, bank.key)
    acct = f"XXXXXX{p.account_last4}"
    ps, pe = st.period_start, st.period_end
    left = [
        Paragraph(f"<b>{escape(bank.name)}</b>", styles["bank"]),
        Paragraph(escape(bank.address), styles["small"]),
        Spacer(1, 10),
        Paragraph(escape(p.business.upper()), styles["body"]),
        Paragraph(p.street.upper(), styles["body"]),
        Paragraph(f"{p.city.upper()}, {p.state} {p.zip_code}", styles["body"]),
    ]
    lines = {
        "meridian": [
            f"<b>{bank.account_type}</b>",
            f"for {ps:%B %d, %Y} to {pe:%B %d, %Y}",
            f"Account number: {acct}",
            f"Customer service: {bank.phone}",
        ],
        "pinecrest": [
            f"<b>{bank.account_type}</b>",
            f"Account number: {acct}",
            f"Statement period: {ps:%m/%d/%Y} - {pe:%m/%d/%Y}",
            f"Your branch: {b_name}",
            f"{b_street}, {p.city}, {p.state}",
            f"Questions? Call {bank.phone}",
        ],
        "atlas": [
            f"<b>{bank.account_type}</b>",
            f"Member number: {acct}",
            f"Statement period: {ps:%m/%d/%y} - {pe:%m/%d/%y}",
            f"Routing number: {bank.routing}",
            f"Member Services: {bank.phone}",
        ],
        "harborview": [
            f"<b>{ps:%B %d, %Y} through {pe:%B %d, %Y}</b>",
            f"Account Number: {acct}",
            f"Customer Service: {bank.phone}",
            "Hearing impaired: 711",
        ],
        "summit": [
            f"<b>{bank.account_type}</b>",
            f"ACCOUNT NO   {acct}",
            f"STATEMENT PERIOD {ps:%m/%d/%y} THRU {pe:%m/%d/%y}",
            f"BRANCH {_h(bank.key + b_name, 3)}  {p.city.upper()}",
            f"PHONE {bank.phone}",
        ],
    }[bank.key]
    right = [Paragraph(lines[0], styles["body"])] + [
        Paragraph(escape(x) if "<b>" not in x else x, styles["small"]) for x in lines[1:]
    ]
    t = Table([[left, right]], colWidths=[3.9 * inch, 3.2 * inch])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return [t, Spacer(1, 12)]


def _average_ledger(st: Statement) -> int:
    eod = {t.date: t.balance for t in st.txns}
    balance, total, day, n = st.opening, 0, st.period_start, 0
    while day <= st.period_end:
        balance = eod.get(day, balance)
        total += balance
        n += 1
        day += dt.timedelta(days=1)
    return round(total / n)


def _summary(
    rows_txns: list[Txn], st: Statement, bank: Bank, styles: dict, style: str, alt_values: bool
) -> list:
    lab = bank.labels
    credits = [t for t in rows_txns if t.amount > 0]
    debits = [t for t in rows_txns if t.amount < 0]
    by = {c: [t for t in st.txns if t.category == c] for c in FEES}
    credit_style = "crdr" if style == "crdr" else "plain"
    rows = [
        [lab["open"].format(d=f"{st.period_start:%m/%d/%Y}"), money(st.opening, "signed")],
        [f"{lab['credits']} ({len(credits)})", money(sum(t.amount for t in credits), credit_style)],
        [f"{lab['debits']} ({len(debits)})", money(sum(t.amount for t in debits), style)],
        [lab["close"].format(d=f"{st.period_end:%m/%d/%Y}"), money(st.closing, "signed")],
        [lab["avg"], money(_average_ledger(st), "signed")],
    ]
    fees = [[lab["fees"], "", ""], ["", "Items", "Amount"]] + [
        [lab[key], str(len(by[cat])), money(-sum(t.amount for t in by[cat]))]
        for key, cat in (("od", Cat.FEE_OVERDRAFT), ("nsf", Cat.FEE_NSF), ("svc", Cat.FEE_SERVICE))
    ]
    t = Table([[lab["summary"], ""], *rows], colWidths=[2.75 * inch, 1.15 * inch])
    f = Table(fees, colWidths=[1.85 * inch, 0.45 * inch, 0.9 * inch])
    base = [
        ("FONTNAME", (0, 0), (-1, 0), styles["bold"]),
        ("FONTNAME", (0, 1), (-1, -1), styles["font"]),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]
    edited = [("FONTNAME", (1, 2), (1, 5), styles["alt"])] if alt_values else []
    t.setStyle(TableStyle([*base, ("LINEABOVE", (0, 4), (-1, 4), 0.4, colors.grey), *edited]))
    f.setStyle(
        TableStyle(
            [
                *base,
                ("SPAN", (0, 0), (-1, 0)),
                ("ALIGN", (0, 0), (-1, 0), "LEFT"),
                ("FONTNAME", (0, 1), (-1, 1), styles["bold"]),
            ]
        )
    )
    outer = Table([[t, f]], colWidths=[4.0 * inch, 3.3 * inch])
    outer.setStyle(
        TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)])
    )
    return [outer, Spacer(1, 14)]


def _table(
    head: list[str],
    rows: list[list],
    widths: list[float],
    styles: dict,
    right_cols: list[int],
    alt_rows: set[int],
    alt_cells: set[tuple[int, int]] | None = None,
) -> LongTable:
    t = LongTable([head, *rows], colWidths=[w * inch for w in widths], repeatRows=1)
    cmds = [
        ("FONTNAME", (0, 0), (-1, 0), styles["bold"]),
        ("FONTNAME", (0, 1), (-1, -1), styles["font"]),
        ("FONTSIZE", (0, 0), (-1, -1), styles["size"]),
        ("LEADING", (0, 1), (-1, -1), styles["size"] + 1.4),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2),
    ]
    cmds += [("ALIGN", (c, 0), (c, -1), "RIGHT") for c in right_cols]
    cmds += [("FONTNAME", (0, r + 1), (-1, r + 1), styles["alt"]) for r in alt_rows]
    cmds += [("FONTNAME", (c, r + 1), (c, r + 1), styles["alt"]) for r, c in alt_cells or set()]
    t.setStyle(TableStyle(cmds))
    return t


def _rows_running(
    app: Applicant,
    txns: list[Txn],
    bank: str,
    alt: set[str],
    alt_bal: set[str],
    date_fmt: str,
    mode: str,
    desc_width: float,
    styles: dict,
) -> tuple[list, set[int], set[tuple[int, int]]]:
    rows, alt_rows, alt_cells = [], set(), set()
    balance_col = 3 if mode in ("signed", "crdr") else 4
    for i, t in enumerate(txns):
        if t.id in alt:
            alt_rows.add(i)
        elif t.id in alt_bal:
            alt_cells.add((i, balance_col))
        d = f"{t.date:{date_fmt}}"
        desc = describe(t, bank, app.profile)
        if mode == "crdr":  # fixed-width printout: one truncated line
            rows.append(
                [
                    d.upper(),
                    desc.replace("\n", " ")[:38],
                    money(t.amount, "crdr"),
                    money(t.balance, "crdr"),
                ]
            )
            continue
        desc = _wrap(desc, styles["font"], styles["size"], desc_width * inch - 8)
        if mode == "debit_credit":
            rows.append(
                [
                    d,
                    desc,
                    money(t.amount) if t.amount < 0 else "",
                    money(t.amount) if t.amount > 0 else "",
                    money(t.balance, "signed"),
                ]
            )
        elif mode == "signed":
            rows.append([d, desc, money(t.amount, "signed"), money(t.balance, "signed")])
        else:  # trailing minus
            rows.append(
                [
                    d,
                    desc,
                    money(t.amount, "trailing") if t.amount < 0 else "",
                    money(t.amount) if t.amount > 0 else "",
                    money(t.balance, "trailing"),
                ]
            )
    return rows, alt_rows, alt_cells


def _sectioned(
    app: Applicant, st: Statement, txns: list[Txn], alt: set[str], alt_bal: set[str], styles: dict
) -> list:
    """Atlas layout: credits, electronic debits, checks and fees in separate sections, then a
    daily ending-balance table. No running balance per row."""
    out: list = []
    groups = [
        ("Deposits and other additions", [t for t in txns if t.amount > 0]),
        (
            "Electronic withdrawals and other debits",
            [t for t in txns if t.amount < 0 and not t.check_no and t.category not in FEES],
        ),
        ("Checks paid", [t for t in txns if t.check_no and t.amount < 0]),
        ("Fees", [t for t in txns if t.category in FEES]),
    ]
    for title, group in groups:
        if not group:
            continue
        out.append(Paragraph(f"<b>{title}</b>", styles["section"]))
        if title == "Checks paid":
            group = sorted(group, key=lambda t: int(t.check_no))
            rows = [[t.check_no, f"{t.date:%m/%d}", money(t.amount)] for t in group]
            head, widths, right = ["Check No.", "Date paid", "Amount"], [1.2, 1.2, 1.4], [2]
        else:
            rows = [
                [
                    f"{t.date:%m/%d}",
                    _wrap(
                        describe(t, "atlas", app.profile),
                        styles["font"],
                        styles["size"],
                        5.0 * inch - 8,
                    ),
                    money(t.amount),
                ]
                for t in group
            ]
            head, widths, right = ["Date", "Description", "Amount"], [0.7, 5.0, 1.3], [2]
        alt_rows = {i for i, t in enumerate(group) if t.id in alt}
        out.append(_table(head, rows, widths, styles, right, alt_rows))
        total = sum(t.amount for t in group)
        out.append(Paragraph(f"Total {title.lower()}: {money(total)}", styles["small_right"]))
        out.append(Spacer(1, 8))
    # daily ending balances, three columns of (date, balance)
    eod: dict[dt.date, int] = {}
    edited_days: set[dt.date] = set()
    for t in st.txns:
        eod[t.date] = t.balance
        if t.id in alt_bal or t.id in alt:
            edited_days.add(t.date)
    days = sorted(eod)
    pairs = [(f"{d:%m/%d}", money(eod[d], "signed")) for d in days]
    third = -(-len(pairs) // 3)
    cols = [pairs[i * third : (i + 1) * third] for i in range(3)]
    grid, cells = [], set()
    for r in range(third):
        row: list[str] = []
        for c, col in enumerate(cols):
            row += list(col[r]) if r < len(col) else ["", ""]
            if r < len(col) and days[c * third + r] in edited_days:
                cells.add((r, 2 * c + 1))
        grid.append(row)
    out.append(Paragraph("<b>Daily ending balance</b>", styles["section"]))
    out.append(
        _table(["Date", "Balance"] * 3, grid, [0.7, 1.4] * 3, styles, [1, 3, 5], set(), cells)
    )
    return out


def _pdf_date(d: dt.datetime, tz: str = "-04'00'") -> str:
    return f"D:{d:%Y%m%d%H%M%S}{tz}"


def render_statement(
    app: Applicant,
    st: Statement,
    bank_key: str,
    path: Path,
    created: dt.datetime,
    modified: dt.datetime | None = None,
    alt_font_ids: set[str] | None = None,
    alt_cell_ids: set[str] | None = None,
    alt_font: str | None = None,
    producer: str | None = None,
) -> None:
    """Render one statement. `alt_font_ids`: rows set in `alt_font` (edited rows);
    `alt_cell_ids`: rows whose balance cell alone is in `alt_font` (recomputed by an editor)."""
    bank = BANKS[bank_key]
    alt, alt_bal = alt_font_ids or set(), alt_cell_ids or set()
    size = 7.2 if bank.key == "summit" else 7.6
    styles = {
        "font": bank.font,
        "bold": bank.font_bold,
        "size": size,
        "alt": alt_font or ALT_FONT[bank.font],
        "bank": ParagraphStyle("bank", fontName=bank.font_bold, fontSize=13, leading=16),
        "body": ParagraphStyle("body", fontName=bank.font, fontSize=9, leading=11),
        "small": ParagraphStyle("small", fontName=bank.font, fontSize=7.5, leading=9),
        "small_right": ParagraphStyle(
            "sr", fontName=bank.font, fontSize=7.5, leading=9, alignment=2
        ),
        "section": ParagraphStyle(
            "sec", fontName=bank.font_bold, fontSize=9, leading=12, spaceAfter=3, keepWithNext=1
        ),
    }
    txns = visible_txns(st, bank_key)
    story = _header_block(app, st, bank, styles)
    debit_style = {"harborview": "trailing", "summit": "crdr", "pinecrest": "signed"}.get(
        bank.key, "plain"
    )
    story += _summary(txns, st, bank, styles, debit_style, alt_values=bool(alt_bal))
    if bank.key == "atlas":
        story += _sectioned(app, st, txns, alt, alt_bal, styles)
    else:
        mode, date_fmt, head, widths, right = {
            "meridian": (
                "debit_credit",
                "%m/%d",
                ["Date", "Description", "Withdrawals", "Deposits", "Balance"],
                [0.5, 3.8, 0.95, 0.95, 1.0],
                [2, 3, 4],
            ),
            "pinecrest": (
                "signed",
                "%m/%d/%Y",
                ["Date", "Transaction description", "Amount", "Balance"],
                [0.75, 4.35, 1.0, 1.1],
                [2, 3],
            ),
            "harborview": (
                "trailing",
                "%m/%d",
                ["Post date", "Description", "Debits", "Credits", "Balance"],
                [0.6, 3.75, 0.95, 0.95, 1.0],
                [2, 3, 4],
            ),
            "summit": (
                "crdr",
                "%d-%b",
                ["DATE", "DESCRIPTION", "AMOUNT", "BALANCE"],
                [0.65, 3.9, 1.3, 1.35],
                [2, 3],
            ),
        }[bank.key]
        rows, alt_rows, alt_cells = _rows_running(
            app, txns, bank.key, alt, alt_bal, date_fmt, mode, widths[1], styles
        )
        story.append(Paragraph(f"<b>{bank.labels['detail']}</b>", styles["section"]))
        story.append(_table(head, rows, widths, styles, right, alt_rows, alt_cells))
    story.append(Spacer(1, 10))
    story.append(KeepTogether(Paragraph(escape(bank.footer), styles["small"])))

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.pdf")
    doc = SimpleDocTemplate(
        str(tmp),
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.7 * inch,
        title=f"{bank.name} statement {st.period_end:%Y-%m-%d}",
        author=bank.name,
        subject=SUBJECT,
        creator=bank.producer,
        producer=producer or bank.producer,
    )
    header = (
        f"{bank.name}   {bank.labels['account']} XXXXXX{app.profile.account_last4}   "
        f"{st.period_start:%m/%d/%Y} - {st.period_end:%m/%d/%Y}"
    )
    doc.build(
        story,
        canvasmaker=lambda *a, **kw: _NumberedCanvas(*a, header=header, font=bank.font, **kw),
    )
    set_dates(tmp, path, created, modified or created, tz=bank.tz)
    os.remove(tmp)


def set_dates(
    src: Path,
    dst: Path,
    created: dt.datetime,
    modified: dt.datetime,
    producer: str | None = None,
    tz: str = "-04'00'",
) -> None:
    """Copy `src` to `dst` with the given creation / modification dates (and producer)."""
    pdf = pymupdf.open(src)
    meta = dict(pdf.metadata or {})
    meta.update(creationDate=_pdf_date(created, tz), modDate=_pdf_date(modified, tz))
    if producer:
        meta["producer"] = producer
    pdf.set_metadata(meta)
    pdf.save(dst, garbage=1, deflate=True)
    pdf.close()
