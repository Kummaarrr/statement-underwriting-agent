"""Day-by-day cash-flow simulation of a US small business's checking account.

Every transaction is created with its true category, so the key figures are exact. Realism
rules that matter for underwriting (from two rounds of human and model review):
- sales happen every calendar day; card processors settle on the next *business* day (Federal
  Reserve calendar). Batch processors (Clover, Toast, bankcard) send one deposit per sales day,
  so Monday carries three; Square / Stripe / Shopify combine. Amazon pays every 14 days;
- each originator keeps one ACH name, entry description and ID format; payroll is net pay,
  tax and fee from the provider's separate entities; expense ratios, vendors, insurers and card
  merchants are industry- and trade-specific; state tax goes to the right agency;
- merchant cash advances (MCAs) pull a fixed amount every business day or week, together at
  most MCA_CAP of revenue; funding arrives as a wire (payback / factor rate, less fees) from
  the funder's bank, with an incoming-wire fee, and is mostly spent within days;
- riskier files always contain stress: a slow period and/or a declining trend while costs stay
  sticky. A debit that would overdraw the account posts with an overdraft fee (accounts with
  coverage, up to a limit) or is returned with an NSF fee and presented once more 1-3 business
  days later. Transfers to the owner's own accounts and Zelle payments are never returned:
  they aren't initiated without funds, and sweeps to savings only happen above a cash buffer;
- checks clear 1-6 business days after they are written, so numbers clear out of order, with
  occasional gaps (voided checks); amounts that vary are never identical to the cent;
- names are drawn per role from separate pools and are unique within and across files.
"""

from __future__ import annotations

import bisect
import dataclasses
import datetime as dt
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from stmt_uw.model import DEBT_CATS, REVENUE_CATS, Advance, Cat, Txn
from stmt_uw.synth.calendar import business_days, next_business_day

FEE_NSF, FEE_OD, FEE_RETURNED_DEPOSIT = 3_500, 3_500, 1_200
FEE_WIRE_IN, FEE_WIRE_OUT = 1_500, 3_000
PAYROLL_CATS = {Cat.PAYROLL, Cat.PAYROLL_TAX}
# sales tax remitted on the prior month's sales (services are mostly exempt)
SALES_TAX_RATE = {"restaurant": 0.08, "retail": 0.075, "salon": 0.025, "ecommerce": 0.035}
ZELLE_LIMIT = 350_000  # cents per payment; larger owner draws go by online transfer
MCA_CAP = 0.30  # all MCA payments together take at most this share of monthly revenue
FILE_END = dt.date(2026, 9, 30)

WORDS = [
    "Blue Harbor",
    "Iron Creek",
    "Golden Oak",
    "Silverline",
    "Red Rock",
    "Maple Lane",
    "Summit",
    "Coastal",
    "Prairie",
    "Northgate",
    "Lakeside",
    "Cedar Point",
    "Granite",
    "Willow",
    "Copper Ridge",
    "Bayshore",
    "Highland",
    "Pioneer",
    "Riverbend",
    "Sunrise",
    "Juniper",
    "Stonebridge",
    "Harvest",
    "Bluebird",
    "Clearwater",
    "Redwood",
]
# business name stems (unique across files)
SURNAMES = [
    "Alvarez",
    "Bennett",
    "Chandler",
    "Dawson",
    "Ellison",
    "Fletcher",
    "Garza",
    "Hale",
    "Ingram",
    "Jensen",
    "Keller",
    "Lawson",
    "Monroe",
    "Nash",
    "Ortega",
    "Pruitt",
    "Quinn",
    "Ramsey",
    "Sutton",
    "Tate",
    "Underwood",
    "Vance",
    "Whitaker",
    "Yates",
    "Okafor",
    "Nguyen",
    "Patel",
    "Kowalski",
    "Brennan",
    "Delgado",
    "Abbott",
    "Barrett",
    "Castillo",
    "Donovan",
    "Espinoza",
    "Fischer",
    "Gallagher",
    "Hendricks",
    "Iverson",
    "Jacobs",
    "Kaplan",
    "Lindqvist",
    "Morales",
    "Novak",
    "OBrien",
    "Pham",
    "Rasmussen",
    "Salazar",
    "Thornton",
    "Uribe",
    "Valdez",
    "Wexler",
    "Zamora",
    "Becker",
    "Coleman",
    "Dumont",
    "Farrell",
    "Goldberg",
    "Haddad",
    "Ishikawa",
    "Jovanovic",
    "Khan",
]
# owners only
OWNER_FIRST = [
    "Maria",
    "James",
    "Priya",
    "David",
    "Angela",
    "Luis",
    "Tanya",
    "Omar",
    "Kevin",
    "Sarah",
    "Hector",
    "Grace",
    "Andre",
    "Mei",
    "Rafael",
    "Nicole",
    "Darnell",
    "Elena",
    "Marcus",
    "Fatima",
    "Brian",
    "Rosa",
    "Tyler",
    "Leah",
]
OWNER_LAST = [
    "Adeyemi",
    "Aguilar",
    "Bishop",
    "Carver",
    "Dalton",
    "Esposito",
    "Finch",
    "Greer",
    "Hughes",
    "Ito",
    "Juarez",
    "Kowal",
    "Lombardi",
    "Mercer",
    "Nakamura",
    "Oyelaran",
    "Pace",
    "Quintero",
    "Rhodes",
    "Sorensen",
    "Tolbert",
    "Varga",
    "Whitfield",
    "Ximenez",
    "Yilmaz",
    "Zeller",
    "Bauer",
    "Cruz",
    "Dimitrov",
    "Eriksen",
    "Ferreira",
    "Gutierrez",
    "Holloway",
    "Ibarra",
    "Jameson",
    "Kimura",
    "Larsen",
    "Mahoney",
    "Navarro",
    "Osei",
    "Petrov",
    "Reyes",
    "Sandoval",
    "Tran",
    "Vasquez",
    "Webb",
    "Young",
]
# counterparty company stems: two words, unique across files, first word unique within a file
CP_A = [
    "ALDER",
    "ASPEN",
    "BIRCH",
    "BRIAR",
    "CANYON",
    "CYPRESS",
    "EAGLE",
    "ELMWOOD",
    "FALCON",
    "FOXGLOVE",
    "HAWTHORNE",
    "HERON",
    "HICKORY",
    "IVY",
    "LAUREL",
    "LINDEN",
    "MAGNOLIA",
    "MESA",
    "OAKMONT",
    "ORCHARD",
    "PINE",
    "QUAIL",
    "RAVEN",
    "SAGE",
    "SEQUOIA",
    "SPRUCE",
    "SYCAMORE",
    "TIMBER",
    "WALNUT",
    "WREN",
    "ARROWHEAD",
    "BEACON",
    "CHESTNUT",
    "DRIFTWOOD",
    "EVERGREEN",
    "FLINT",
    "GLACIER",
    "HARBORSIDE",
    "INDIGO",
    "KEYSTONE",
]
CP_B = ["CREEK", "HILL", "PARK", "POINT", "VALLEY", "RIDGE", "BAY", "GROVE", "FIELD", "SPRINGS"]
FUNDERS = [
    "NORTHSTAR FUNDING GROUP",
    "RAPIDLINE CAPITAL",
    "MAPLEWOOD BUSINESS ADVANCE",
    "CROWNVIEW FUNDING",
    "BLUESTEM CAPITAL LLC",
    "IRONGATE ADVANCE",
    "SUMMITPEAK FUNDING",
    "LANTERN CAPITAL",
    "BRIGHTPATH FUNDING",
    "KESTREL ADVANCE",
]
FUNDER_BANKS = [
    "METRO COMMERCE BANK",
    "FIRST CAPITAL TRUST",
    "LIBERTY STATE BANK",
    "PEAK NATIONAL BANK",
]
LENDERS = ["CAPSTONE BANK", "HARBORLINE LENDING", "SBA EIDL"]
EQUIPMENT = ["FLEETLINE EQUIP FIN", "TRUCKWAY CAPITAL", "MACHINERY CREDIT CORP"]
CARD_ISSUERS = {"AMEX EPAYMENT": "ACH PMT", "CAPITAL ONE": "CRCARDPMT", "CHASE CREDIT CRD": "EPAY"}
# provider: ((originator, entry) for net pay, tax, fee)
PAYROLL = {
    "GUSTO": (
        ("GUSTO", "NET {d:%y%m%d}"),
        ("GUSTO", "TAX {d:%y%m%d}"),
        ("GUSTO", "FEE {d:%y%m%d}"),
    ),
    "ADP": (("ADP WAGE PAY", "WAGE PAY"), ("ADP TAX", "ADP TAX"), ("ADP PAYROLL FEES", "ADP FEES")),
    "PAYCHEX": (("PAYCHEX INC", "PAYROLL"), ("PAYCHEX TPS", "TAXES"), ("PAYCHEX EIB", "INVOICE")),
}
STREETS = [
    "Commerce Dr",
    "Industrial Blvd",
    "Main St",
    "Market St",
    "Oak Ave",
    "Parkway Plaza",
    "Riverside Dr",
    "Trade Center Way",
    "Elm St",
    "Westgate Rd",
]
CITIES = {
    "TX": ("Austin", ["78704", "78745", "78758"]),
    "FL": ("Tampa", ["33607", "33614", "33619"]),
    "GA": ("Atlanta", ["30318", "30324", "30339"]),
    "OH": ("Columbus", ["43204", "43215", "43229"]),
    "AZ": ("Phoenix", ["85008", "85034", "85040"]),
    "NC": ("Charlotte", ["28203", "28206", "28217"]),
    "IL": ("Naperville", ["60540", "60563", "60564"]),
    "NV": ("Henderson", ["89011", "89014", "89074"]),
}
UTILITIES = {
    "TX": "AUSTIN ENERGY",
    "FL": "TECO ENERGY",
    "GA": "GEORGIA POWER",
    "OH": "AEP OHIO",
    "AZ": "APS ELECTRIC",
    "NC": "DUKE ENERGY",
    "IL": "COMED",
    "NV": "NV ENERGY",
}
STATE_TAX = {
    "TX": "TX COMPTROLLER",
    "FL": "FL DEPT REVENUE",
    "GA": "GA DEPT OF REVENUE",
    "OH": "OHIO DEPT OF TAX",
    "AZ": "AZ DEPT OF REVENUE",
    "NC": "NC DEPT OF REVENUE",
    "IL": "IL DEPT OF REVENUE",
    "NV": "NV DEPT OF TAXATION",
}
PROCESSOR_ENTRY = {
    "SQUARE INC": "SDY{d:%y%m%d}",
    "TOAST INC": "DEP {d:%m%d}",
    "CLOVER": "DEPOSIT",
    "BANKCARD MERCH": "BTOT DEP",
    "STRIPE": "TRANSFER",
    "SHOPIFY PAYMENTS": "TRANSFER",
    "PAYPAL": "TRANSFER",
    "VAGARO INC": "PAYOUT",
}
BATCHED = {"CLOVER", "TOAST INC", "BANKCARD MERCH"}  # one deposit per sales day
MONTHLY_FEE_PROCESSORS = {"CLOVER", "BANKCARD MERCH"}  # bill fees monthly instead of netting
ROAD_TOWNS = [
    "AMARILLO TX",
    "EL PASO TX",
    "TUCSON AZ",
    "ALBUQUERQUE NM",
    "OKLAHOMA CITY OK",
    "JOPLIN MO",
    "LITTLE ROCK AR",
    "MEMPHIS TN",
    "DALLAS TX",
    "ONTARIO CA",
]
AMAZON = ("AMZN Mktp US*{r}", "Amzn.com/bill WA")

# per industry: revenue channels, payers, suppliers, card merchants (name, location: "" = the
# business's city, "road" = a highway town), insurers, and the operating-cost budget
INDUSTRIES: dict[str, dict[str, Any]] = {
    "restaurant": {
        "size": (4.5, 5.45),  # log10 of typical monthly revenue in dollars
        "names": ["Grill", "Kitchen", "Taqueria", "Bistro"],
        "processors": ["SQUARE INC", "TOAST INC", "CLOVER"],
        "week": [0.8, 0.85, 0.9, 1.0, 1.3, 1.45, 1.1],
        "card": 0.8,
        "deposits_per_week": 2,
        "ach": (["{cp} EVENTS", "EZCATER INC"], 0.5),
        "amazon": False,
        "suppliers": {"*": ["SYSCO", "US FOODS", "{cp} PRODUCE", "{cp} MEATS"]},
        "merchants": [
            ("COSTCO WHSE #{n}", ""),
            ("RESTAURANT DEPOT #{n}", ""),
            ("SHELL OIL {n}", ""),
            ("WEBSTAURANTSTORE", "717-392-7472 PA"),
            AMAZON,
        ],
        "card_per_day": 0.6,
        "insurers": ["NEXT INSURANCE", "THE HARTFORD", "EMPLOYERS INS"],
        "sales_tax": True,
        "equipment": False,
        "weekly_payroll": True,
        "budget": {
            Cat.PAYROLL: 0.28,
            Cat.SUPPLIER: 0.33,
            Cat.RENT: 0.08,
            Cat.UTILITIES: 0.035,
            Cat.TAX: 0.075,
            Cat.CARD_SPEND: 0.03,
        },
        "season": [0.85, 0.88, 0.98, 1.0, 1.05, 1.1, 1.12, 1.1, 1.0, 0.98, 0.95, 1.05],
    },
    "retail": {
        "size": (4.4, 5.4),  # log10 of typical monthly revenue in dollars
        "names": ["Outfitters", "Supply", "Market", "Goods"],
        "processors": ["SQUARE INC", "BANKCARD MERCH", "STRIPE"],
        "week": [0.85, 0.85, 0.9, 0.95, 1.1, 1.45, 1.0],
        "card": 0.85,
        "deposits_per_week": 1,
        "ach": (["{cp} SCHOOLS", "{cp} TRADING CO"], 0.8),
        "amazon": False,
        "suppliers": {"*": ["{cp} WHOLESALE", "{cp} DISTRIBUTORS", "ULINE", "FAIRE WHOLESALE"]},
        "merchants": [
            ("STAPLES #{n}", ""),
            ("THE UPS STORE #{n}", ""),
            ("SAMS CLUB #{n}", ""),
            AMAZON,
        ],
        "card_per_day": 0.5,
        "insurers": ["THE HARTFORD", "HISCOX INC", "NEXT INSURANCE"],
        "sales_tax": True,
        "equipment": False,
        "weekly_payroll": False,
        "budget": {
            Cat.SUPPLIER: 0.48,
            Cat.PAYROLL: 0.14,
            Cat.PAYROLL_TAX: 0.04,
            Cat.RENT: 0.09,
            Cat.TAX: 0.07,
        },
        "season": [0.8, 0.82, 0.95, 0.98, 1.0, 1.0, 0.98, 1.02, 0.97, 1.02, 1.15, 1.35],
    },
    "salon": {
        "size": (4.3, 5.0),  # log10 of typical monthly revenue in dollars
        "names": ["Salon", "Studio", "Spa", "Barbers"],
        "processors": ["SQUARE INC", "VAGARO INC"],
        "week": [0.6, 1.0, 1.0, 1.05, 1.25, 1.5, 0.4],
        "card": 0.85,
        "deposits_per_week": 1,
        "ach": None,
        "amazon": False,
        "suppliers": {"*": ["SALONCENTRIC", "COSMOPROF", "{cp} BEAUTY SUPPLY"]},
        "merchants": [("SALLY BEAUTY #{n}", ""), ("TARGET #{n}", ""), ("ULTA #{n}", ""), AMAZON],
        "card_per_day": 0.4,
        "insurers": ["HISCOX INC", "NEXT INSURANCE"],
        "sales_tax": True,
        "equipment": False,
        "weekly_payroll": False,
        "budget": {
            Cat.PAYROLL: 0.38,
            Cat.PAYROLL_TAX: 0.10,
            Cat.SUPPLIER: 0.10,
            Cat.RENT: 0.12,
            Cat.TAX: 0.02,
        },
        "season": [0.9, 0.95, 1.0, 1.0, 1.05, 1.05, 1.0, 0.98, 1.0, 1.0, 1.05, 1.15],
    },
    "ecommerce": {
        "size": (4.3, 5.5),  # log10 of typical monthly revenue in dollars
        "names": ["Brands", "Direct", "Commerce", "Online"],
        "processors": ["SHOPIFY PAYMENTS", "STRIPE", "PAYPAL"],
        "week": [1.05, 1.0, 1.0, 1.0, 0.95, 0.95, 1.05],
        "card": 0.85,
        "deposits_per_week": 0,
        "ach": None,
        "amazon": True,
        "suppliers": {
            "*": ["{cp} FULFILLMENT", "SHIPSTATION", "{cp} MANUFACTURING", "PIRATE SHIP"]
        },
        "merchants": [
            ("FACEBK *{r}", "fb.me/ads CA"),
            ("GOOGLE *ADS{n}", "cc@google.com CA"),
            ("USPS PO {n}", ""),
            ("ADOBE *ACROPRO SUBS", "408-536-6000 CA"),
            AMAZON,
        ],
        "card_per_day": 1.2,
        "insurers": ["HISCOX INC", "THE HARTFORD"],
        "sales_tax": True,
        "equipment": False,
        "weekly_payroll": False,
        "budget": {
            Cat.SUPPLIER: 0.33,
            Cat.CARD_SPEND: 0.16,
            Cat.PAYROLL: 0.12,
            Cat.PAYROLL_TAX: 0.035,
            Cat.RENT: 0.03,
            Cat.TAX: 0.04,
            Cat.PROCESSOR_FEE: 0.0,
        },
        "season": [0.85, 0.85, 0.92, 0.95, 0.98, 0.95, 0.95, 1.0, 1.0, 1.08, 1.3, 1.45],
    },
    "trucking": {
        "size": (4.5, 5.6),  # log10 of typical monthly revenue in dollars
        "names": ["Transport", "Freight", "Logistics", "Hauling"],
        "processors": [],
        "week": None,
        "card": 0.0,
        "deposits_per_week": 0,
        "ach": (["{cp} LOGISTICS", "{cp} FREIGHT", "{cp} BROKERAGE", "{cp} TRANSPORT SVCS"], 3.0),
        "amazon": False,
        "suppliers": {"*": ["{cp} TRUCK REPAIR", "TRUCKPRO", "{cp} TIRE CO"]},
        "merchants": [
            ("PILOT_{n}", "road"),
            ("LOVE'S #{n}", "road"),
            ("FLYING J #{n}", "road"),
            ("TA TRAVEL CENTER {n}", "road"),
            ("PREPASS*TOLLS", "800-773-7277 AZ"),
        ],
        "card_per_day": 3.0,
        "insurers": ["PROGRESSIVE COMM", "GREAT WEST CASUALTY", "NATL INTERSTATE"],
        "sales_tax": False,
        "equipment": True,
        "weekly_payroll": True,
        "budget": {
            Cat.CARD_SPEND: 0.30,
            Cat.SUPPLIER: 0.08,
            Cat.PAYROLL: 0.25,
            Cat.INSURANCE: 0.07,
            Cat.RENT: 0.02,
            Cat.TAX: 0.02,
        },
        "season": [0.9, 0.9, 1.0, 1.0, 1.05, 1.05, 1.0, 1.05, 1.05, 1.1, 1.0, 0.95],
    },
    "contractor": {
        "size": (4.5, 5.6),  # log10 of typical monthly revenue in dollars
        "names": ["Builders", "Roofing", "Plumbing", "Electric"],
        "processors": ["STRIPE"],
        "week": [1.0, 1.0, 1.0, 1.0, 1.0, 0.3, 0.0],
        "card": 0.1,
        "deposits_per_week": 1,
        "ach": (["{cp} HOMES", "{cp} DEVELOPMENT", "{cp} PROPERTY MGMT"], 1.5),
        "amazon": False,
        "suppliers": {
            "Builders": ["84 LUMBER", "{cp} LUMBER", "HOME DEPOT PRO"],
            "Roofing": ["ABC SUPPLY CO", "BEACON ROOFING", "SRS DISTRIBUTION"],
            "Plumbing": ["FERGUSON ENT", "WINSUPPLY", "{cp} PLUMBING SUP"],
            "Electric": ["GRAYBAR ELECTRIC", "CED ELECTRICAL", "{cp} ELECTRIC SUP"],
        },
        "merchants": [
            ("THE HOME DEPOT #{n}", ""),
            ("LOWE'S #{n}", ""),
            ("SHELL OIL {n}", ""),
            ("CHEVRON {n}", ""),
            ("SUNBELT RENTALS #{n}", ""),
        ],
        "card_per_day": 0.8,
        "insurers": ["NEXT INSURANCE", "THE HARTFORD", "TRAVELERS"],
        "sales_tax": False,
        "equipment": True,
        "weekly_payroll": True,
        "budget": {
            Cat.SUPPLIER: 0.33,
            Cat.PAYROLL: 0.28,
            Cat.CARD_SPEND: 0.06,
            Cat.INSURANCE: 0.04,
            Cat.RENT: 0.03,
        },
        "season": [0.75, 0.8, 0.95, 1.05, 1.12, 1.15, 1.15, 1.12, 1.05, 1.0, 0.9, 0.8],
    },
}
# share of each period's operating budget (industries override); amounts vary around it
BUDGET = {
    Cat.PAYROLL: 0.28,
    Cat.PAYROLL_TAX: 0.08,
    Cat.RENT: 0.08,
    Cat.UTILITIES: 0.02,
    Cat.INSURANCE: 0.025,
    Cat.SUPPLIER: 0.30,
    Cat.CARD_SPEND: 0.04,
    Cat.CARD_AUTOPAY: 0.04,
    Cat.TAX: 0.04,
    Cat.PROCESSOR_FEE: 0.01,
    Cat.OWNER_DRAW: 0.06,
}
# categories whose amounts vary: never identical to the cent across a file
VARIABLE = {
    Cat.SUPPLIER,
    Cat.CARD_SPEND,
    Cat.CARD_AUTOPAY,
    Cat.TAX,
    Cat.UTILITIES,
    Cat.PAYROLL,
    Cat.PAYROLL_TAX,
    Cat.OWNER_DRAW,
    Cat.TRANSFER_IN,
    Cat.TRANSFER_OUT,
    Cat.REVENUE_CARD,
    Cat.REVENUE_ACH,
    Cat.REVENUE_DEPOSIT,
}


@dataclass
class Profile:
    business: str
    industry: str
    trade: str
    street: str
    city: str
    state: str
    zip_code: str
    owner: str
    monthly_revenue: int  # cents, before seasonality / trend / stress
    risk: str  # low / medium / high
    overdraft_coverage: bool
    transfer_heavy: bool  # frequent moves between the owner's accounts (not revenue)
    account_last4: str
    savings_last4: str
    personal_last4: str
    names: dict[str, Any] = field(default_factory=dict)  # per-applicant counterparties


@dataclass
class Statement:
    period_start: dt.date
    period_end: dt.date
    opening: int
    closing: int
    txns: list[Txn]


@dataclass
class Applicant:
    id: str
    profile: Profile
    start: dt.date
    end: dt.date
    opening_balance: int
    txns: list[Txn]
    advances: list[Advance]
    periods: list[tuple[dt.date, dt.date]]
    statements: list[Statement] = field(default_factory=list)


def _cents(dollars: float) -> int:
    return round(dollars * 100)


def _ref(rng: random.Random, n: int = 10) -> str:
    return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(n))


def _month_back(d: dt.date, day: int) -> dt.date:
    first_prev = (d.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    return first_prev.replace(day=day)


def statement_periods(months: int, cycle_day: int | None) -> list[tuple[dt.date, dt.date]]:
    """`months` consecutive statement cycles ending on or before FILE_END. cycle_day None means
    calendar months; otherwise each cycle ends on that day (e.g. 08/16 - 09/15)."""
    ends = []
    if cycle_day is None:
        end = FILE_END
        for _ in range(months):
            ends.append(end)
            end = end.replace(day=1) - dt.timedelta(days=1)
    else:
        end = FILE_END.replace(day=cycle_day)
        for _ in range(months):
            ends.append(end)
            end = _month_back(end, cycle_day)
    ends.sort()
    first = (
        ends[0].replace(day=1)
        if cycle_day is None
        else _month_back(ends[0], cycle_day) + dt.timedelta(days=1)
    )
    starts = [first] + [e + dt.timedelta(days=1) for e in ends[:-1]]
    return list(zip(starts, ends, strict=True))


def _unique(used: set[str], key: str, make: Callable[[], str]) -> str:
    for _ in range(2_000):
        value = make()
        if f"{key}:{value.upper()}" not in used:
            used.add(f"{key}:{value.upper()}")
            return value
    raise RuntimeError(f"name pool exhausted for {key}")


def make_profile(rng: random.Random, used: set[str], states: list[str] | None = None) -> Profile:
    industry = rng.choice(list(INDUSTRIES))
    cfg = INDUSTRIES[industry]
    stem = _unique(used, "stem", lambda: rng.choice(WORDS + SURNAMES))
    owner = f"{rng.choice(OWNER_FIRST)} {_unique(used, 'owner', lambda: rng.choice(OWNER_LAST))}"
    trade = rng.choice(cfg["names"])
    state = rng.choice(states or list(CITIES))
    city, zips = CITIES[state]
    first_words: set[str] = set()

    def company(template: str) -> str:
        if "{cp}" not in template:
            return template

        def make() -> str:
            a = rng.choice([w for w in CP_A if w not in first_words])
            return f"{a} {rng.choice(CP_B)}"

        cp = _unique(used, "cp", make)
        first_words.add(cp.split()[0])
        return template.format(cp=cp)

    def entry() -> str:
        return rng.choice(["PAYMENT", "INV PMT", "ACH PMT", "VENDOR PAY", "CORP PMT", "AP PMT"])

    payers: list[str] = []
    if cfg["ach"]:
        templates, per_week = cfg["ach"]
        payers = [company(t) for t in templates[: 1 if per_week < 1.5 else len(templates)]]
    suppliers = [company(s) for s in cfg["suppliers"].get(trade, cfg["suppliers"].get("*"))]
    merchants = [
        (name.replace("{n}", f"{rng.randint(100, 9999):04d}"), loc)
        for name, loc in cfg["merchants"]
    ]
    names = {
        "landlord": company(rng.choice(["{cp} PROPERTIES", "{cp} REALTY", "{cp} HOLDINGS LLC"])),
        "processor": rng.choice(cfg["processors"]) if cfg["processors"] else "",
        "payroll": rng.choice(list(PAYROLL)),
        "card_issuer": rng.choice(list(CARD_ISSUERS)),
        "insurer": rng.choice(cfg["insurers"]),
        "utility": UTILITIES[state],
        "state_tax": STATE_TAX[state],
        "payers": payers,
        "suppliers": suppliers,
        "merchants": merchants,
        # one entry description per originator, as real ACH files have
        "entries": {cp: entry() for cp in payers}
        | {s: rng.choice(["PAYMENT", "INVOICE", "AUTOPAY", "BILL PMT"]) for s in suppliers},
    }
    risk = rng.choices(["low", "medium", "high"], weights=[0.4, 0.35, 0.25])[0]
    return Profile(
        business=f"{stem} {trade} {rng.choice(['LLC', 'Inc', 'LLC', 'Co'])}",
        industry=industry,
        trade=trade,
        street=f"{rng.randint(100, 9899)} {rng.choice(STREETS)}",
        city=city,
        state=state,
        zip_code=rng.choice(zips),
        owner=owner.upper(),
        monthly_revenue=_cents(round(10 ** rng.uniform(*cfg["size"]), -2)),
        risk=risk,
        overdraft_coverage=rng.random() < 0.5,
        transfer_heavy=risk != "low" and rng.random() < 0.3,
        account_last4=f"{rng.randint(0, 9999):04d}",
        savings_last4=f"{rng.randint(0, 9999):04d}",
        personal_last4=f"{rng.randint(0, 9999):04d}",
        names=names,
    )


def _plan_debt(rng: random.Random, p: Profile, start: dt.date, end: dt.date) -> list[dict]:
    """0-3 MCAs (more for riskier profiles, together at most MCA_CAP of revenue), maybe a
    term loan, maybe an equipment loan."""
    weights = {"low": [6, 3, 1, 0], "medium": [3, 4, 2, 1], "high": [1, 3, 3, 3]}[p.risk]
    n_mca = rng.choices([0, 1, 2, 3], weights=weights)[0]
    plans: list[dict[str, Any]] = []
    for funder in rng.sample(FUNDERS, n_mca):
        daily = rng.random() < 0.65
        share = rng.uniform(0.003, 0.0065) if daily else rng.uniform(0.015, 0.032)
        funded = None
        if rng.random() < 0.35:  # funded during the file: a large wire appears
            funded = start + dt.timedelta(days=rng.randint(10, max(11, (end - start).days - 25)))
        plans.append(
            {
                "provider": funder,
                "frequency": "daily" if daily else "weekly",
                "kind": "mca",
                "payment": p.monthly_revenue * share,
                "funded": funded,
                "weekday": rng.randint(0, 4),
                "entry": ["PMT", "ACH PMT", "DEBIT", "REMIT"][FUNDERS.index(funder) % 4],
                "bank": rng.choice(FUNDER_BANKS),
            }
        )
    burden = sum(a["payment"] * (21 if a["frequency"] == "daily" else 4.33) for a in plans)
    scale = min(1.0, MCA_CAP * p.monthly_revenue / burden) if burden else 1.0
    for a in plans:
        a["payment"] = int(round(a["payment"] * scale, -2))
    if rng.random() < 0.3:
        lender = rng.choice(LENDERS)
        payment = int(round(p.monthly_revenue * rng.uniform(0.01, 0.04), -2))
        if lender.startswith("SBA"):
            payment = min(payment, _cents(rng.randint(450, 2_600)))  # typical EIDL installment
        plans.append(
            {
                "provider": lender,
                "frequency": "monthly",
                "kind": "term_loan",
                "payment": payment,
                "funded": None,
                "day": rng.choice([5, 10, 15, 20]),
                "entry": "LOAN PMT",
            }
        )
    if INDUSTRIES[p.industry]["equipment"] and rng.random() < 0.6:
        plans.append(
            {
                "provider": rng.choice(EQUIPMENT),
                "frequency": "monthly",
                "kind": "equipment_loan",
                "funded": None,
                "day": rng.choice([1, 8, 12, 25]),
                "payment": int(round(p.monthly_revenue * rng.uniform(0.02, 0.06), -2)),
                "entry": rng.choice(["LEASE PMT", "EQUIP PMT"]),
            }
        )
    return plans


def _calendar_days(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def _monthly(start: dt.date, end: dt.date, dom: int) -> list[dt.date]:
    """The business day on or after day `dom` of every month, within [start, end]."""
    out, m = [], start.replace(day=1)
    while m <= end:
        d = next_business_day(m.replace(day=dom))
        if start <= d <= end:
            out.append(d)
        m = (m + dt.timedelta(days=32)).replace(day=1)
    return out


def _count(rng: random.Random, rate: float) -> int:
    """How many events today for an average of `rate` per day."""
    return int(rate) + (rng.random() < rate - int(rate))


def simulate(
    applicant_id: str,
    rng: random.Random,
    months: int,
    cycle_day: int | None = None,
    used: set[str] | None = None,
    states: list[str] | None = None,
) -> Applicant:
    p = make_profile(rng, used if used is not None else set(), states)
    cfg = INDUSTRIES[p.industry]
    periods = statement_periods(months, cycle_day)
    start, end = periods[0][0], periods[-1][1]
    bdays = business_days(start, end)
    names = p.names

    # operating margin before financing; advance proceeds and owner top-ups lift net cash flow
    margin = {
        "low": rng.uniform(0.03, 0.10),
        "medium": rng.uniform(-0.05, 0.02),
        "high": rng.uniform(-0.14, -0.04),
    }[p.risk]
    trend = {
        "low": rng.uniform(-0.01, 0.03),
        "medium": rng.uniform(-0.03, 0.0),
        "high": rng.uniform(-0.07, -0.03),
    }[p.risk]
    slow = None  # a slow period: always for high risk, usually for medium
    if p.risk == "high" or (p.risk == "medium" and rng.random() < 0.6):
        slow = rng.randrange(len(periods))
    opening = _cents(
        p.monthly_revenue
        / 100
        * {
            "low": rng.uniform(0.3, 0.8),
            "medium": rng.uniform(0.08, 0.3),
            "high": rng.uniform(0.02, 0.08),
        }[p.risk]
    )

    events: list[Txn] = []
    fixed: set[int] = set()  # id() of events whose amount calibration must not touch

    def add(
        day: dt.date,
        desc: str,
        cents: int,
        cat: Cat,
        channel: str,
        cp: str = "",
        entry: str = "",
        check: str = "",
        memo: str = "",
    ) -> Txn:
        t = Txn(
            day,
            desc,
            cents,
            cat,
            counterparty=cp,
            channel=channel,
            entry=entry,
            ref=_ref(rng),
            check_no=check,
            memo=memo,
        )
        events.append(t)
        return t

    # --- revenue (amounts are relative here, scaled to each period's target below) ---------
    proc = names["processor"]
    if cfg["card"] and proc:
        week = cfg["week"]
        sporadic = p.industry == "contractor"
        sales: dict[dt.date, float] = {}
        for d in _calendar_days(start - dt.timedelta(days=4), end):
            if week[d.weekday()] and rng.random() > (0.6 if sporadic else 0.02):
                sales[d] = week[d.weekday()] * rng.lognormvariate(0, 0.9 if sporadic else 0.3)
        settle: dict[dt.date, list[dt.date]] = {}
        for sd in sales:
            day = next_business_day(sd + dt.timedelta(days=1))
            if start <= day <= end:
                settle.setdefault(day, []).append(sd)
        for day, sale_days in sorted(settle.items()):
            batches = [[s] for s in sale_days] if proc in BATCHED else [sale_days]
            for batch in batches:
                entry = PROCESSOR_ENTRY[proc].format(d=batch[-1])
                add(
                    day,
                    f"{proc} {entry}",
                    round(sum(sales[s] for s in batch) * 1000),
                    Cat.REVENUE_CARD,
                    "card_settlement",
                    proc,
                    entry,
                )
    if cfg["ach"]:
        per_week = cfg["ach"][1]
        for day in bdays:
            if rng.random() < per_week / 5:
                payer = rng.choice(names["payers"])
                entry = names["entries"][payer]
                add(
                    day,
                    f"{payer} {entry}",
                    round(rng.lognormvariate(0, 0.45) * 1000),
                    Cat.REVENUE_ACH,
                    "ach_credit",
                    payer,
                    entry,
                )
    if cfg["amazon"]:  # marketplace disbursements every 14 days
        d = start + dt.timedelta(days=rng.randint(0, 13))
        while d <= end:
            payout = next_business_day(d)
            if payout <= end:
                add(
                    payout,
                    "AMAZON.COM SVCS EDI PYMNTS",
                    round(rng.lognormvariate(0, 0.25) * 1000),
                    Cat.REVENUE_ACH,
                    "ach_credit",
                    "AMAZON.COM SVCS",
                    "EDI PYMNTS",
                )
            d += dt.timedelta(days=14)
    if cfg["deposits_per_week"]:
        for day in bdays:
            if rng.random() < cfg["deposits_per_week"] / 5:
                kind = rng.choices(["MOBILE", "BRANCH", "COUNTER"], weights=[5, 3, 1])[0]
                add(
                    day,
                    f"{kind} DEPOSIT",
                    round(rng.lognormvariate(0, 0.5) * 1000),
                    Cat.REVENUE_DEPOSIT,
                    "deposit",
                    entry=kind,
                )
    for i, (ps, pe) in enumerate(periods):
        factor = cfg["season"][pe.month - 1] * (1 + trend) ** i
        factor *= rng.uniform(0.55, 0.72) if i == slow else rng.uniform(0.95, 1.05)
        rev = [e for e in events if ps <= e.date <= pe and e.category in REVENUE_CATS]
        present = {e.category for e in rev}
        others = present - {Cat.REVENUE_CARD}
        weight = {c: (1 - cfg["card"]) / len(others) for c in others}
        if Cat.REVENUE_CARD in present:
            weight[Cat.REVENUE_CARD] = cfg["card"]
        norm = sum(weight.values())
        for cat in present:
            group = [e for e in rev if e.category == cat]
            f = p.monthly_revenue * factor * weight[cat] / norm / sum(e.amount for e in group)
            for e in group:
                e.amount = max(1_500, round(e.amount * f))

    # a deposited check occasionally comes back unpaid
    deposits = [e for e in events if e.category == Cat.REVENUE_DEPOSIT]
    if deposits and rng.random() < 0.35:
        dep = rng.choice(deposits)
        back = next((b for b in bdays if b > dep.date + dt.timedelta(days=2)), None)
        if back:
            fixed.add(id(dep))
            for t in (
                add(back, "DEPOSITED ITEM RETURNED", -dep.amount, Cat.DEPOSIT_RETURN, "return"),
                add(
                    back,
                    "RETURNED DEPOSIT ITEM FEE",
                    -FEE_RETURNED_DEPOSIT,
                    Cat.FEE_SERVICE,
                    "fee",
                    entry="RDI",
                ),
            ):
                fixed.add(id(t))

    # --- operating costs (amounts calibrated per period below) -------------------------------
    fridays = [d for d in _calendar_days(start, end) if d.weekday() == 4]
    if not cfg["weekly_payroll"]:
        fridays = fridays[rng.randint(0, 1) :: 2]
    (net_cp, net_entry), (tax_cp, tax_entry), (fee_cp, fee_entry) = PAYROLL[names["payroll"]]
    payroll_fee = rng.randint(4_000, 16_000)
    for f in fridays:  # a Friday holiday pays on the business day before
        pay = next((b for b in reversed(bdays) if b <= f), None)
        if pay is None:
            continue
        for cp, entry, cat in (
            (net_cp, net_entry, Cat.PAYROLL),
            (tax_cp, tax_entry, Cat.PAYROLL_TAX),
        ):
            entry = entry.format(d=pay)
            add(pay, f"{cp} {entry}", -1_000_00, cat, "ach_debit", cp, entry)
        fee_day = next((b for b in bdays if b > pay), None)
        if fee_day:
            entry = fee_entry.format(d=fee_day)
            t = add(
                fee_day,
                f"{fee_cp} {entry}",
                -(payroll_fee + rng.randint(0, 900)),
                Cat.PROCESSOR_FEE,
                "ach_debit",
                fee_cp,
                entry,
            )
            fixed.add(id(t))
    issuer = names["card_issuer"]
    monthly = [
        (1, Cat.RENT, names["landlord"], "RENT"),
        (10, Cat.UTILITIES, names["utility"], "BILL PAY"),
        (5, Cat.INSURANCE, names["insurer"], "INS PREM"),
        (22, Cat.CARD_AUTOPAY, issuer, CARD_ISSUERS[issuer]),
    ]
    if cfg["sales_tax"]:
        monthly.append((20, Cat.TAX, names["state_tax"], "SALES TAX"))
    sales_tax = []
    for dom, cat, cp, entry in monthly:
        for d in _monthly(start, end, dom):
            t = add(d, f"{cp} {entry}", -1_000_00, cat, "ach_debit", cp, entry)
            if entry == "SALES TAX":  # set from prior-month sales below, not calibrated
                sales_tax.append(t)
                fixed.add(id(t))
    merch_fees = []
    if proc in MONTHLY_FEE_PROCESSORS:
        for d in _monthly(start, end, 3):
            merch_fees.append(
                add(d, f"{proc} MERCH FEES", -1, Cat.PROCESSOR_FEE, "ach_debit", proc, "MERCH FEES")
            )
            fixed.add(id(merch_fees[-1]))
    for d in _monthly(start, end, 15):
        if d.month in (1, 4, 6, 9):  # estimated income tax
            add(d, "IRS USATAXPYMT", -1_000_00, Cat.TAX, "ach_debit", "IRS", "USATAXPYMT")
    suppliers = names["suppliers"]
    check_no = rng.randint(1001, 4000)
    for k, d in enumerate(bdays):
        if rng.random() < 0.35:
            supplier = rng.choice(suppliers)
            if rng.random() < 0.2:  # a check written today clears 1-6 business days later
                check_no += 1 + (rng.random() < 0.08)  # occasionally a voided number
                clears = bdays[min(len(bdays) - 1, k + rng.randint(1, 6))]
                add(
                    clears,
                    f"CHECK {check_no}",
                    -1_000_00,
                    Cat.SUPPLIER,
                    "check",
                    supplier,
                    check=str(check_no),
                )
            else:
                entry = names["entries"][supplier]
                add(d, f"{supplier} {entry}", -1_000_00, Cat.SUPPLIER, "ach_debit", supplier, entry)
        for _ in range(_count(rng, cfg["card_per_day"])):
            name, loc = rng.choice(names["merchants"])
            name = name.replace("{r}", _ref(rng, 9))
            if loc == "road":
                loc = rng.choice(ROAD_TOWNS)
            add(
                d, f"CARD PURCHASE {name}", -1_000_00, Cat.CARD_SPEND, "debit_card", f"{name}|{loc}"
            )
    for d in [b for b in bdays if b.weekday() == 0][1::2]:  # owner draws every other Monday
        add(d, f"ZELLE TO {p.owner}", -1_000_00, Cat.OWNER_DRAW, "zelle_out", p.owner)
    transfer_rate = 0.07 if p.transfer_heavy else 0.012
    for d in bdays:
        if rng.random() < transfer_rate:
            t = add(
                d,
                f"TRANSFER FROM SAV {p.savings_last4}",
                round(p.monthly_revenue * rng.uniform(0.03, 0.12)),
                Cat.TRANSFER_IN,
                "transfer_in",
                f"SAV {p.savings_last4}",
            )
            fixed.add(id(t))
        elif (p.risk == "low" or p.transfer_heavy) and rng.random() < transfer_rate * 1.2:
            t = add(
                d,
                f"TRANSFER TO SAV {p.savings_last4}",
                -round(p.monthly_revenue * rng.uniform(0.03, 0.1)),
                Cat.TRANSFER_OUT,
                "transfer_out",
                f"SAV {p.savings_last4}",
            )
            fixed.add(id(t))

    # --- debt ------------------------------------------------------------------------------
    advances: list[Advance] = []
    for a in _plan_debt(rng, p, start, end):
        provider = a["provider"]
        if a["funded"]:  # payback / factor rate, rounded to $1,000, less origination fees
            n = rng.randint(80, 160) if a["frequency"] == "daily" else rng.randint(16, 36)
            funded = round(a["payment"] * n / rng.uniform(1.25, 1.45), -5)
            fund_day = next_business_day(a["funded"])
            wire = add(
                fund_day,
                f"WIRE IN {provider}",
                int(round(funded * (1 - rng.uniform(0.02, 0.06)), -2)),
                Cat.ADVANCE_PROCEEDS,
                "wire_in",
                provider,
                "FUNDING",
                memo=a["bank"],
            )
            wire_fee = add(
                fund_day, "INCOMING WIRE FEE", -FEE_WIRE_IN, Cat.FEE_SERVICE, "fee", entry="WIRE"
            )
            spend_day = bdays[min(len(bdays) - 1, bdays.index(fund_day) + rng.randint(2, 8))]
            vendor = rng.choice(suppliers)
            use = add(
                spend_day,
                f"{vendor} {names['entries'][vendor]}",
                -round(wire.amount * rng.uniform(0.6, 0.9)),
                Cat.SUPPLIER,
                "ach_debit",
                vendor,
                names["entries"][vendor],
            )
            fixed |= {id(wire), id(wire_fee), id(use)}
        if a["frequency"] == "daily":
            due = bdays
        elif a["frequency"] == "weekly":
            due = sorted(
                {
                    next_business_day(d)
                    for d in _calendar_days(start, end)
                    if d.weekday() == a["weekday"]
                }
            )
        else:
            due = _monthly(start, end, a["day"])
        first = a["funded"] or (start - dt.timedelta(days=1))
        cat = Cat.ADVANCE_PAYMENT if a["kind"] == "mca" else Cat.LOAN_PAYMENT
        for d in due:
            if first < d <= end:
                t = add(
                    d,
                    f"{provider} {a['entry']}",
                    -a["payment"],
                    cat,
                    "ach_debit",
                    provider,
                    a["entry"],
                )
                fixed.add(id(t))
        advances.append(Advance(provider, a["frequency"], a["payment"], a["kind"]))

    budget = {c: share * rng.uniform(0.8, 1.25) for c, share in (BUDGET | cfg["budget"]).items()}
    _calibrate(events, fixed, periods, margin, budget, rng)
    rate = rng.uniform(0.024, 0.031)  # monthly-billed processing fees on last month's volume
    for fee in merch_fees:
        volume = _trailing(events, {Cat.REVENUE_CARD}, fee.date, start)
        fee.amount = -max(2_500, round(volume * rate))
    tax_rate = SALES_TAX_RATE.get(p.industry, 0.0)
    for t in sales_tax:
        taxable = _trailing(events, REVENUE_CATS, t.date, start)
        t.amount = -max(5_000, round(taxable * tax_rate * rng.uniform(0.97, 1.03)))
    for e in events:  # large owner draws exceed the Zelle limit: online transfer instead
        if e.channel == "zelle_out" and -e.amount > ZELLE_LIMIT:
            e.channel, e.counterparty = "transfer_out", f"CHK {p.personal_last4}"
            e.description = f"TRANSFER TO CHK {p.personal_last4}"
    _dedupe(events, fixed, rng)
    txns = _post(events, opening, p, rng, periods)
    for i, t in enumerate(txns):
        t.id = f"{applicant_id}-t{i:05d}"
    app = Applicant(applicant_id, p, start, end, opening, txns, advances, periods)
    app.statements = _statements(app)
    return app


def _trailing(events: list[Txn], cats: set[Cat], day: dt.date, start: dt.date) -> float:
    """The 30 days of `cats` amounts before `day` (the next 30 days when the file has too
    little history before it)."""
    lo, hi = max(day - dt.timedelta(days=30), start), day
    if (hi - lo).days < 10:
        lo, hi = day, day + dt.timedelta(days=30)
    total = sum(e.amount for e in events if e.category in cats and lo <= e.date < hi)
    return total * 30 / max(1, (hi - lo).days)


def _calibrate(
    events: list[Txn],
    fixed: set[int],
    periods: list[tuple[dt.date, dt.date]],
    margin: float,
    budget_share: dict[Cat, float],
    rng: random.Random,
) -> None:
    """Set each period's operating costs so the net margin matches the profile. Costs are sticky:
    they follow the file's average revenue half-way, so slow months run a deficit.
    costs = blended revenue x (1 - margin) - debt service (never below 30% of revenue).
    Rent and insurance keep one amount for the whole file; payroll is one per-run amount
    (headcount doesn't follow a slow month) and payroll tax follows net pay."""

    def revenue(ps: dt.date, pe: dt.date) -> int:
        return sum(e.amount for e in events if ps <= e.date <= pe and e.category in REVENUE_CATS)

    avg = sum(revenue(ps, pe) for ps, pe in periods) / len(periods)
    constant: dict[Cat, int] = {}
    # payroll follows headcount, not this month's sales: one per-run amount for the file
    runs = [e for e in events if e.category == Cat.PAYROLL and id(e) not in fixed]
    present = {e.category for e in events if e.category in budget_share and id(e) not in fixed}
    avg_debt = -sum(e.amount for e in events if e.category in DEBT_CATS) / len(periods)
    avg_target = max(0.3 * avg, avg * (1 - margin) - avg_debt)
    share = budget_share[Cat.PAYROLL] / sum(budget_share[c] for c in present)
    per_run = avg_target * share * len(periods) / max(1, len(runs))
    for e in runs:
        e.amount = -round(per_run * rng.lognormvariate(0, 0.05))
    for ps, pe in periods:
        inside = [e for e in events if ps <= e.date <= pe]
        rev = revenue(ps, pe)
        debt = -sum(e.amount for e in inside if e.category in DEBT_CATS)
        target = max(0.3 * rev, (0.5 * rev + 0.5 * avg) * (1 - margin) - debt)
        by_cat: dict[Cat, list[Txn]] = {}
        for e in inside:
            if e.category in budget_share and id(e) not in fixed:
                by_cat.setdefault(e.category, []).append(e)
        weight = sum(budget_share[c] for c in by_cat)
        for cat, group in by_cat.items():
            budget = target * budget_share[cat] / weight
            if cat in (Cat.RENT, Cat.INSURANCE):
                constant.setdefault(cat, int(round(budget / len(group), -2)))
                for e in group:
                    e.amount = -constant[cat]
                continue
            if cat in PAYROLL_CATS:
                continue
            spread = 0.45 if cat in (Cat.SUPPLIER, Cat.CARD_SPEND) else 0.08
            noise = [rng.lognormvariate(0, spread) for _ in group]
            scale = budget / sum(noise)
            for e, n in zip(group, noise, strict=True):
                e.amount = -max(500, round(n * scale))
        net_pay = {e.date: e.amount for e in by_cat.get(Cat.PAYROLL, [])}
        for e in by_cat.get(Cat.PAYROLL_TAX, []):
            e.amount = round(net_pay.get(e.date, -50_000) * rng.uniform(0.26, 0.31))


def _dedupe(events: list[Txn], fixed: set[int], rng: random.Random) -> None:
    """Amounts that vary are never identical to the cent within a file (a lender notices)."""
    seen = {abs(e.amount) for e in events if e.category not in VARIABLE or id(e) in fixed}
    for e in events:
        if e.category in VARIABLE and id(e) not in fixed:
            while abs(e.amount) in seen:
                e.amount += rng.randint(1, 97) * (1 if e.amount > 0 else -1)
            seen.add(abs(e.amount))


def _post(
    events: list[Txn],
    opening: int,
    p: Profile,
    rng: random.Random,
    periods: list[tuple[dt.date, dt.date]],
) -> list[Txn]:
    """Post each business day's items, applying overdraft coverage, returns (with one
    re-presentment), declines, top-ups and the end-of-cycle service fee."""
    posted: list[Txn] = []
    balance = opening
    od_limit = max(200_000, round(p.monthly_revenue * 0.05))
    buffer = round(p.monthly_revenue * 0.25)  # owners sweep to savings only above this
    fee = {
        "low": 0,
        "medium": rng.choice([1_200, 1_500, 2_500]),
        "high": rng.choice([1_500, 2_500, 3_000]),
    }[p.risk]
    bdays = business_days(periods[0][0], periods[-1][1])
    cycle_last = {business_days(ps, pe)[-1] for ps, pe in periods}
    by_day: dict[dt.date, list[Txn]] = {}
    for e in events:
        by_day.setdefault(e.date, []).append(e)
    days = sorted(set(by_day) | cycle_last)
    represented: set[int] = set()
    prefunded = False  # after a returned payroll debit the processor wants wires

    def system(day: dt.date, desc: str, cents: int, cat: Cat, channel: str, **kw: Any) -> Txn:
        return Txn(day, desc, cents, cat, channel=channel, ref=_ref(rng), **kw)

    def schedule(t: Txn) -> None:
        if t.date not in by_day:
            by_day[t.date] = []
            bisect.insort(days, t.date)
        by_day[t.date].append(t)

    def later(day: dt.date, n: int) -> dt.date | None:
        """The n-th business day after `day`, if still inside the file."""
        k = bisect.bisect_right(bdays, day) + n - 1
        return bdays[k] if k < len(bdays) else None

    def represent(e: Txn, day: dt.date) -> None:
        """The payee presents a returned ACH debit once more 2-3 business days on, a check
        3-5 business days on. Payroll processors don't: they want the money wired."""
        if id(e) in represented or e.channel not in ("ach_debit", "check"):
            return
        if e.category in PAYROLL_CATS:
            when = later(day, 1)
            if when:
                wire = dataclasses.replace(e, date=when, ref=_ref(rng), balance=0)
                wire.returned_item, wire.channel = False, "wire_out"
                schedule(wire)
            return
        when = later(day, rng.randint(2, 3) if e.channel == "ach_debit" else rng.randint(3, 5))
        if when:
            again = dataclasses.replace(e, date=when, ref=_ref(rng), balance=0)
            again.returned_item = False
            represented.add(id(again))
            schedule(again)

    k = 0
    while k < len(days):
        day = days[k]
        k += 1
        items = by_day.get(day, [])
        if balance < 0 and rng.random() < 0.6:
            top_up = -balance + round(p.monthly_revenue * rng.uniform(0.02, 0.08))
            items = [
                system(
                    day,
                    f"TRANSFER FROM SAV {p.savings_last4}",
                    top_up,
                    Cat.TRANSFER_IN,
                    "transfer_in",
                    counterparty=f"SAV {p.savings_last4}",
                ),
                *items,
            ]
        for e in items:
            if prefunded and e.category in PAYROLL_CATS and e.channel == "ach_debit":
                e.channel = "wire_out"
        wires = [e for e in items if e.channel == "wire_out"]
        need = -sum(e.amount for e in wires)
        if wires and balance < need:  # the owner funds the payroll wire from savings first
            top_up = need - balance + round(p.monthly_revenue * rng.uniform(0.01, 0.03))
            items = [
                system(
                    day,
                    f"TRANSFER FROM SAV {p.savings_last4}",
                    top_up,
                    Cat.TRANSFER_IN,
                    "transfer_in",
                    counterparty=f"SAV {p.savings_last4}",
                ),
                *items,
            ]
        for _ in wires:
            wire_fee = system(day, "OUTGOING WIRE FEE", -FEE_WIRE_OUT, Cat.FEE_SERVICE, "fee")
            wire_fee.entry = "WIREOUT"
            items.append(wire_fee)
        credits = [e for e in items if e.amount > 0]
        debits = [e for e in items if e.amount < 0 and e.channel != "fee"]
        fees = [e for e in items if e.amount < 0 and e.channel == "fee"]
        rng.shuffle(debits)
        early = [d for d in debits if d.category in DEBT_CATS and rng.random() < 0.5]
        order = early + credits + [d for d in debits if d not in early] + fees
        od_fees_today = 0
        for e in order:
            if e.channel in ("fee", "wire_out"):  # fees always post; wires were funded above
                balance += e.amount
                posted.append(_with_balance(e, balance))
                continue
            if e.channel in ("transfer_out", "zelle_out"):
                floor = buffer if e.category == Cat.TRANSFER_OUT else 0
                if balance + e.amount < floor:
                    continue  # the owner doesn't move money that isn't there
            if e.amount < 0 and balance + e.amount < 0:
                covered = p.overdraft_coverage and balance + e.amount >= -od_limit
                if e.category == Cat.CARD_SPEND and not covered:
                    continue  # declined at the terminal: never posts
                balance += e.amount
                posted.append(_with_balance(e, balance))
                if covered:
                    if od_fees_today < 3:
                        balance -= FEE_OD
                        od_fees_today += 1
                        od = system(day, "OVERDRAFT ITEM FEE", -FEE_OD, Cat.FEE_OVERDRAFT, "fee")
                        od.entry = "OD"
                        posted.append(_with_balance(od, balance))
                    continue
                # returned: the item and its reversal (most banks print only the fee), the fee
                e.returned_item = True
                balance -= e.amount
                posted.append(
                    _with_balance(
                        system(
                            day,
                            f"RETURNED ITEM {e.counterparty}",
                            -e.amount,
                            Cat.RETURN_REVERSAL,
                            "return",
                            counterparty=e.counterparty,
                            entry=e.entry,
                            check_no=e.check_no,
                        ),
                        balance,
                    )
                )
                item = (
                    f"CHECK {e.check_no}" if e.check_no else f"{e.counterparty} {e.entry}".strip()
                )
                # the NSF fee posts the next business day ("for activity of <return date>")
                schedule(
                    system(
                        later(day, 1) or day,
                        "NSF RETURNED ITEM FEE",
                        -FEE_NSF,
                        Cat.FEE_NSF,
                        "fee",
                        entry="NSF",
                        memo=f"{item}|{-e.amount}|{day:%m-%d}",
                    )
                )
                if e.category in PAYROLL_CATS:
                    prefunded = True
                represent(e, day)
                continue
            balance += e.amount
            posted.append(_with_balance(e, balance))
        if fee and day in cycle_last:
            balance -= fee
            posted.append(
                _with_balance(
                    system(
                        day, "MONTHLY MAINTENANCE FEE", -fee, Cat.FEE_SERVICE, "fee", entry="MAINT"
                    ),
                    balance,
                )
            )
    return posted


def _with_balance(t: Txn, balance: int) -> Txn:
    t.balance = balance
    return t


def _statements(app: Applicant) -> list[Statement]:
    out, opening = [], app.opening_balance
    for ps, pe in app.periods:
        txns = [t for t in app.txns if ps <= t.date <= pe]
        closing = txns[-1].balance if txns else opening
        out.append(Statement(ps, pe, opening, closing, txns))
        opening = closing
    return out
