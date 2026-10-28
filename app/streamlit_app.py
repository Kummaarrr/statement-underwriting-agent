"""Public demo (Streamlit Community Cloud): free to host and free to run for the owner.

- Sample files: pre-computed by scripts/build_demo_samples.py - no API calls at all.
- Analyse your own statements: runs v1 live on the visitor's own Anthropic API key, which is
  passed explicitly to each model call and never cached, stored or logged. The app holds no key.

Run locally:  uv run streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app"))

import pandas as pd  # noqa: E402
import pymupdf  # noqa: E402
import streamlit as st  # noqa: E402
from demo_data import result_to_dict  # noqa: E402

REPO_URL = "https://github.com/Kummaarrr/statement-underwriting-agent"
SAMPLES_DIR = ROOT / "app" / "samples"
PREVIEW_DPI = 110
MAX_FILES = 12
MAX_PAGES = 60
MAX_MB = 20
KINDS = {"mca": "Cash advance (MCA)", "term_loan": "Term loan", "equipment_loan": "Equipment loan"}

PageImage = Callable[[str, int], bytes]


# ---------------------------------------------------------------------------- data access


@st.cache_data
def load_samples() -> dict[str, dict[str, Any]]:
    data = json.loads((SAMPLES_DIR / "results.json").read_text(encoding="utf-8"))
    return {x["id"]: x for x in data["samples"]}


def page_png(pdf: bytes, page: int) -> bytes:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return bytes(doc[page].get_pixmap(dpi=PREVIEW_DPI).tobytes("png"))


@st.cache_data(max_entries=200)
def sample_page_png(sample_id: str, file: str, page: int) -> bytes:
    return page_png((SAMPLES_DIR / sample_id / file).read_bytes(), page)


# ---------------------------------------------------------------------------- formatting


def money(x: float | None, cents: bool = False) -> str:
    if x is None:
        return "-"
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}" if cents else f"{sign}${abs(x):,.0f}"


def within(pred: float, truth: float) -> bool:
    """The evaluation's tolerance for money: +/-1% (at least $1)."""
    return abs(pred - truth) <= max(0.01 * abs(truth), 1.0)


def tick(ok: bool) -> str:
    return "\u2705" if ok else "\u274c"


def humanise(s: str) -> str:
    return s.replace("_", " ")


# ---------------------------------------------------------------------------- the shared view


def render(res: dict[str, Any], page_image: PageImage, key: str) -> None:
    """Everything shown for one analysed file - samples and live runs alike."""
    a = res["assessment"]
    render_verdict(res)

    st.subheader("Key figures")
    c = st.columns(5)
    c[0].metric("Avg monthly revenue", money(a["avg_monthly_revenue"]))
    c[1].metric("Avg daily balance", money(a["avg_daily_balance"]))
    c[2].metric("NSF fees", a["nsf_count"])
    c[3].metric("Overdraft fees", a["overdraft_count"])
    c[4].metric("Negative days", a["negative_days"])

    left, right = st.columns([3, 2])
    with left:
        st.markdown("**Revenue per statement**")
        rev = pd.DataFrame(
            {
                "Statement ending": [r["period_end"] for r in a["revenue_by_statement"]],
                "Revenue ($)": [r["revenue"] for r in a["revenue_by_statement"]],
            }
        )
        if rev.empty:
            st.info("No revenue found.")
        else:
            st.bar_chart(rev, x="Statement ending", y="Revenue ($)", height=260)
    with right:
        st.markdown("**Revenue table**")
        st.dataframe(
            rev,
            hide_index=True,
            width="stretch",
            column_config={"Revenue ($)": st.column_config.NumberColumn(format="dollar")},
        )
        st.markdown("**Statements**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "#": s["index"] + 1,
                        "Period": f"{s['header']['period_start']} to {s['header']['period_end']}",
                        "Opening": s["header"]["opening"],
                        "Closing": s["header"]["closing"],
                        "Rows": len(s["rows"]),
                    }
                    for s in res["statements"]
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Opening": st.column_config.NumberColumn(format="dollar"),
                "Closing": st.column_config.NumberColumn(format="dollar"),
            },
        )

    st.subheader("Existing debt")
    if a["existing_debt"]:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Provider": d["provider"],
                        "Kind": KINDS.get(d["kind"], humanise(d["kind"])),
                        "Frequency": d["frequency"],
                        "Payment": d["payment"],
                    }
                    for d in a["existing_debt"]
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={"Payment": st.column_config.NumberColumn(format="dollar")},
        )
    else:
        st.caption("No recurring debt payments found.")

    if res.get("truth"):
        render_truth(res)
    first = render_trace(res, key)
    render_preview(res, page_image, key, first)

    cached = res.get("cached_calls", 0)
    replay = f", {cached} replayed from the on-disk cache" if cached else ""
    st.caption(
        f"Run: {res['calls']} model calls{replay}; ${res['cost_usd']:.4f} at list price; "
        f"{res['seconds']:.0f} s for this run."
        + (" Pre-computed: viewing this sample costs nothing." if res.get("id") else "")
    )


def render_verdict(res: dict[str, Any]) -> None:
    if res["verdict"] == "ready":
        st.success(f"**Ready** - {res['reason']}", icon="\u2705")
    else:
        bullets = "\n".join(f"- {p}" for p in res["reason"].split("; "))
        st.warning(f"**Needs review**\n\n{bullets}", icon="\u26a0\ufe0f")
    notes = res.get("notes") or []
    if notes:
        with st.expander(f"Notes ({len(notes)}) - reported, not grounds for review"):
            st.markdown("\n".join(f"- {n}" for n in notes))


def render_truth(res: dict[str, Any]) -> None:
    a, t, checks = res["assessment"], res["truth"], res.get("checks", {})
    st.subheader("Truth vs agent")
    st.caption(
        "Demo only: these statements are synthetic, so the exact figures are known. Tolerance "
        "as in the evaluation - money within 1%, counts exact, every debt position found with "
        "no phantoms."
    )
    by_end = {r["period_end"]: r["revenue"] for r in a["revenue_by_statement"]}
    lines: list[dict[str, Any]] = []
    for end, truth in t["revenue_by_statement"].items():
        pred = by_end.get(end)
        ok = pred is not None and within(pred, truth)
        lines.append(
            {
                "Figure": f"Revenue, statement ending {end}",
                "Truth": money(truth, True),
                "Agent": money(pred, True),
                "": tick(ok),
            }
        )
    for name, field, is_money in [
        ("Avg monthly revenue", "avg_monthly_revenue", True),
        ("Avg daily balance", "avg_daily_balance", True),
        ("NSF fees", "nsf_count", False),
        ("Overdraft fees", "overdraft_count", False),
        ("Negative days", "negative_days", False),
    ]:
        pred, truth = a[field], t[field]
        ok = within(pred, truth) if is_money else pred == truth
        lines.append(
            {
                "Figure": name,
                "Truth": money(truth, True) if is_money else str(truth),
                "Agent": money(pred, True) if is_money else str(pred),
                "": tick(ok),
            }
        )

    def debts(items: list[dict[str, Any]]) -> str:
        return (
            "; ".join(
                f"{d['provider']} {d['frequency']} {money(d['payment'], True)}" for d in items
            )
            or "none"
        )

    lines.append(
        {
            "Figure": "Existing debt",
            "Truth": debts(t["existing_debt"]),
            "Agent": debts(a["existing_debt"]),
            "": tick(bool(checks.get("debt"))),
        }
    )
    right = sum(bool(v) for v in checks.values())
    st.markdown(f"**{right} of {len(checks)} figure groups right.**")
    st.dataframe(pd.DataFrame(lines), hide_index=True, width="stretch")
    tamper = res.get("tamper")
    expected = "Needs review" if res.get("expected_verdict") == "needs_review" else "Ready"
    if tamper and tamper["kind"] == "tamper":
        st.info(
            f"**What was done to this file:** statement {tamper['statement']} - "
            f"{humanise(tamper['type'])}: {tamper['note']} ({tamper['edited_rows']} row(s) "
            f"edited or inserted, {tamper['recomputed_rows']} balance(s) recomputed). "
            f"The truth above is the genuine file. Expected verdict: **{expected}**."
        )
    elif tamper:
        st.info(
            f"**What was done to this file:** statement {tamper['statement']} was re-saved "
            f"with {tamper['tool']} - innocent, the content is genuine. "
            f"Expected verdict: **{expected}**."
        )
    else:
        st.caption(f"Genuine, unedited file. Expected verdict: {expected}.")


def _trace_options(res: dict[str, Any]) -> dict[str, list[str]]:
    ev = res.get("evidence") or {}
    options: dict[str, list[str]] = {}
    for end, refs in (ev.get("revenue") or {}).items():
        options[f"Revenue - statement ending {end}"] = refs
    options["NSF / returned-item fees"] = ev.get("nsf") or []
    options["Overdraft fees"] = ev.get("overdraft") or []
    for name, refs in (ev.get("debt") or {}).items():
        options[f"Debt payments - {name}"] = refs
    return options


def render_trace(res: dict[str, Any], key: str) -> dict[str, Any] | None:
    """Pick a figure, see the exact rows behind it. Returns the first row (for the preview)."""
    st.subheader("Traceability")
    st.caption(
        "Every figure is computed in code from reconciled rows. Row refs read S<statement>p<page>"
        "r<row>. Average daily balance and negative days use every calendar day's ending balance."
    )
    options = _trace_options(res)
    choice = st.selectbox("Figure", list(options), key=f"{key}-figure")
    rows_by_ref = {r["ref"]: r for s in res["statements"] for r in s["rows"]}
    rows = [rows_by_ref[r] for r in options.get(choice or "", []) if r in rows_by_ref]
    if not rows:
        st.caption("No rows behind this figure (it is zero).")
        return None
    df = pd.DataFrame(rows)[
        ["ref", "statement", "file", "page", "date", "description", "amount", "balance"]
    ]
    st.markdown(f"**{len(rows)} row(s), total {money(sum(r['amount'] for r in rows), True)}**")
    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        height=min(38 + 35 * len(rows), 360),
        column_config={
            "amount": st.column_config.NumberColumn(format="dollar"),
            "balance": st.column_config.NumberColumn(format="dollar"),
        },
    )
    return rows[0]


def render_preview(
    res: dict[str, Any], page_image: PageImage, key: str, first: dict[str, Any] | None
) -> None:
    st.subheader("Statement page")
    sts = res["statements"]
    names = [f"{s['index'] + 1}. {s['file']}" for s in sts]
    default_st = (first["statement"] - 1) if first else 0
    # keys follow the chosen figure, so the preview jumps to its first row
    tag = f"{key}-{first['ref'] if first else 'none'}"
    c1, c2 = st.columns([3, 1])
    pick = c1.selectbox("Statement", names, index=default_st, key=f"{tag}-st")
    s = sts[names.index(pick)] if pick in names else sts[0]
    default_page = first["page"] if first and first["statement"] == s["index"] + 1 else 1
    page = c2.number_input(
        f"Page (of {s['pages']})",
        min_value=1,
        max_value=max(1, s["pages"]),
        value=min(default_page, max(1, s["pages"])),
        key=f"{tag}-{s['index']}-page",
    )
    try:
        st.image(page_image(s["file"], int(page) - 1), width="stretch")
    except Exception:
        st.caption("Page preview unavailable.")


# ---------------------------------------------------------------------------- modes


def sample_mode() -> None:
    samples = load_samples()
    sid = st.selectbox(
        "Sample file",
        list(samples),
        format_func=lambda i: f"{samples[i]['label']} ({i})",
    )
    res = samples[sid]
    variant = {"digital": "digital PDFs", "scanned": "scanned images", "tampered": "digital PDFs"}
    st.markdown(
        f"**{res['business']}** ({res['industry']}) \xb7 "
        f"fictional bank *{res['bank'].title()}* \xb7 {len(res['statements'])} statements, "
        f"{variant.get(res['variant'], res['variant'])}"
    )
    st.markdown(res["description"])
    render(res, lambda f, p: sample_page_png(sid, f, p), key=sid)


def _unique_names(names: list[str]) -> list[str]:
    out: list[str] = []
    for i, n in enumerate(names):
        base = Path(n).name or f"statement_{i + 1}.pdf"
        out.append(base if base not in out else f"{i + 1:02d}_{base}")
    return out


def _friendly_error(err: Exception, key: str) -> str:
    import anthropic

    if isinstance(err, anthropic.AuthenticationError):
        return "The API key was rejected. Check that it is a valid Anthropic API key."
    if isinstance(err, anthropic.PermissionDeniedError):
        return "This key is not allowed to use the model. Check its workspace and permissions."
    if isinstance(err, anthropic.RateLimitError):
        return "Rate limit or credit limit reached on this key. Try again shortly."
    if isinstance(err, anthropic.APIConnectionError):
        return "Could not reach the Anthropic API. Try again shortly."
    if isinstance(err, anthropic.APIStatusError):
        return f"The Anthropic API returned an error (HTTP {err.status_code})."
    text = str(err).replace(key, "***") if key else str(err)
    return f"The analysis failed ({type(err).__name__}): {text[:300]}"


def live_mode() -> None:
    st.warning(
        "Use synthetic statements or your own documents only - never other people's financial "
        "data. Files are processed in memory and a temporary folder that is deleted at once; "
        "nothing is stored.",
        icon="\u26a0\ufe0f",
    )
    st.markdown(
        "**Expected cost:** about **$0.03-0.06** per applicant file of 3-6 digital statements "
        "on Claude Haiku 5.5, charged to your key; scans cost more (about $0.15 for six). Pages "
        "that fail to reconcile are re-read, the last time on Claude Sonnet 5.5. A run takes "
        "about a minute."
    )
    api_key = st.text_input(
        "Anthropic API key",
        type="password",
        placeholder="sk-ant-...",
        help="Used only for this run, passed straight to the API. Never stored or logged.",
    )
    uploads = st.file_uploader(
        "Bank statement PDFs, in chronological order (oldest first)",
        type=["pdf"],
        accept_multiple_files=True,
    )
    if st.button("Analyse", type="primary", disabled=not (api_key and uploads)):
        run_live(api_key.strip(), uploads or [])
    live = st.session_state.get("live_result")
    if live:
        pdfs: dict[str, bytes] = st.session_state.get("live_pdfs", {})
        st.divider()
        render(live, lambda f, p: page_png(pdfs[f], p), key="live")
        if st.button("Clear result"):
            st.session_state.pop("live_result", None)
            st.session_state.pop("live_pdfs", None)
            st.rerun()


def run_live(api_key: str, uploads: list[Any]) -> None:
    st.session_state.pop("live_result", None)
    st.session_state.pop("live_pdfs", None)
    if len(uploads) > MAX_FILES:
        st.error(f"At most {MAX_FILES} statements per file.")
        return
    names = _unique_names([u.name for u in uploads])
    pdfs = {n: u.getvalue() for n, u in zip(names, uploads, strict=True)}
    pages = 0
    for n, data in pdfs.items():
        if len(data) > MAX_MB * 1024 * 1024:
            st.error(f"{n} is larger than {MAX_MB} MB.")
            return
        try:
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                pages += doc.page_count
        except Exception:
            st.error(f"{n} could not be opened as a PDF.")
            return
    if pages > MAX_PAGES:
        st.error(f"{pages} pages in total; the demo accepts at most {MAX_PAGES}.")
        return

    from stmt_uw.v1.agent import analyse

    try:
        with (
            st.spinner(f"Reading {len(pdfs)} statement(s), {pages} pages - about a minute..."),
            tempfile.TemporaryDirectory(prefix="stmt-uw-") as tmp,
        ):
            paths = []
            for n, data in pdfs.items():
                p = Path(tmp) / n
                p.write_bytes(data)
                paths.append(p)
            result = analyse(paths, api_key=api_key)
            live = result_to_dict(result)
    except Exception as err:
        st.error(_friendly_error(err, api_key))
        return
    st.session_state["live_result"] = live
    st.session_state["live_pdfs"] = pdfs


# ---------------------------------------------------------------------------- page


def main() -> None:
    st.set_page_config(
        page_title="Statement Underwriting Agent", page_icon="\U0001f3e6", layout="wide"
    )
    st.title("Statement Underwriting Agent")
    st.markdown(
        "Reads a small business's US bank statement PDFs and returns the figures a lender "
        "decides on - revenue, average daily balance, NSFs, overdrafts, existing debt - with "
        "every row reconciled and a Ready / Needs review verdict."
    )
    st.caption(f"Synthetic statements, fictional banks. \xb7 [Source on GitHub]({REPO_URL})")
    mode = st.radio(
        "Mode",
        ["Sample files", "Analyse your own statements"],
        horizontal=True,
        label_visibility="collapsed",
    )
    if mode == "Sample files":
        sample_mode()
    else:
        live_mode()


main()
