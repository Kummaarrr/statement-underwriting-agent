"""v1: the layout-aware statement agent (LangGraph).

    extract -> forensics -> reconcile <-> heal (failing pages, at most `heal_attempts` times)
            -> categorise -> figures -> verdict

The small model only reads pages and labels descriptor patterns. Everything that must be exact
- reconciliation, the key figures, the verdict - is code. A statement with strong edit evidence
is not re-read (the document itself doesn't tie); a page that reads the same twice is not
escalated (a misread doesn't repeat itself, a printed inconsistency does).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from stmt_uw.config import PROJECT_ROOT
from stmt_uw.llm import CallInfo, Effort
from stmt_uw.schema import Assessment
from stmt_uw.v1 import categorize, figures, forensics, reconcile
from stmt_uw.v1.extract import PageExtraction, extract_page
from stmt_uw.v1.pdfio import page_count, pdf_facts, text_pages

HAIKU, SONNET = "claude-haiku-5-5", "claude-sonnet-5-5"
REREAD = {"balance_break", "daily_balance", "totals", "counts", "closing", "read", "header"}


@dataclass(frozen=True)
class Config:
    model: str = HAIKU  # reads pages and labels patterns
    escalate_model: str | None = SONNET  # last re-read attempt for a page that still fails
    digital_mode: str = "text"  # first read of pages with a text layer: text / pdf / image
    scan_mode: str = "pdf"  # first read of image-only pages: pdf / image
    heal_attempts: int = 2
    use_rules: bool = True
    use_forensics: bool = True
    workers: int = 8


class State(TypedDict, total=False):
    files: list[Path]
    pages: list[list[PageExtraction]]
    modes: list[list[str]]
    texts: list[list[bool]]  # per file, per page: has a text layer
    statements: list[reconcile.StatementData]
    issues: list[reconcile.Issue]
    findings: list[forensics.Finding]
    attempt: int
    labels: dict[str, categorize.Label]
    figures: figures.Figures
    verdict: str
    reason: str
    notes: list[str]


@dataclass
class Run:
    """Calls and timing of one file (shared by the graph's nodes)."""

    config: Config
    calls: list[CallInfo] = field(default_factory=list)
    api_key: str | None = None  # a visitor's own key: never cached, stored or logged
    pool: ThreadPoolExecutor | None = None

    def read(
        self,
        path: Path,
        page: int,
        mode: str,
        period: tuple[str, str] | None,
        model: str | None = None,
    ) -> PageExtraction:
        m = model or self.config.model
        effort: Effort = "low" if m == HAIKU else "medium"
        result, info = extract_page(
            path, page, mode, m, period, effort=effort, api_key=self.api_key
        )
        self.calls.append(info)
        return result


def build(run: Run) -> Any:
    cfg = run.config

    def extract(state: State) -> State:
        files = state["files"]
        assert run.pool is not None
        texts = [text_pages(f) for f in files]
        modes = [[cfg.digital_mode if t else cfg.scan_mode for t in pages] for pages in texts]
        firsts = list(
            run.pool.map(lambda i: run.read(files[i], 0, modes[i][0], None), range(len(files)))
        )
        jobs = []
        for i, f in enumerate(files):
            h = firsts[i].header
            period = (h.period_start, h.period_end) if h.period_start and h.period_end else None
            jobs += [(i, p, period) for p in range(1, page_count(f))]
        rest = list(
            run.pool.map(lambda j: run.read(files[j[0]], j[1], modes[j[0]][j[1]], j[2]), jobs)
        )
        pages = [[firsts[i]] for i in range(len(files))]
        for (i, _, _), ex in zip(jobs, rest, strict=True):
            pages[i].append(ex)
        return {"pages": pages, "modes": modes, "texts": texts, "attempt": 0}

    def inspect(state: State) -> State:
        found = (
            forensics.inspect([pdf_facts(f) for f in state["files"]]) if cfg.use_forensics else []
        )
        return {"findings": found}

    def check(state: State) -> State:
        sts = [
            reconcile.assemble(i, f, p)
            for i, (f, p) in enumerate(zip(state["files"], state["pages"], strict=True))
        ]
        issues = [i for st in sts for i in reconcile.check(st)] + reconcile.chain_issues(sts)
        return {"statements": sts, "issues": issues}

    def route(state: State) -> str:
        edited = {f.statement for f in state["findings"] if f.strength == "strong"}
        healable = [i for i in state["issues"] if i.kind in REREAD and i.statement not in edited]
        return "heal" if healable and state["attempt"] < cfg.heal_attempts else "categorise"

    def heal(state: State) -> State:
        attempt = state["attempt"] + 1
        edited = {f.statement for f in state["findings"] if f.strength == "strong"}
        pages = [list(p) for p in state["pages"]]
        modes = [list(m) for m in state["modes"]]
        jobs = []
        for st in state["statements"]:
            mine = [i for i in state["issues"] if i.statement == st.index and i.kind in REREAD]
            if not mine or st.index in edited:
                continue
            period = (st.header.period_start, st.header.period_end)
            for p in reconcile.suspect_pages(st, mine):
                scanned = not state["texts"][st.index][p]
                last = attempt == cfg.heal_attempts and cfg.escalate_model
                mode = "image" if scanned and attempt == 1 else "pdf"
                jobs.append(
                    (
                        st.index,
                        p,
                        mode,
                        period if all(period) else None,
                        cfg.escalate_model if last else None,
                    )
                )
        assert run.pool is not None
        fresh = list(
            run.pool.map(lambda j: run.read(state["files"][j[0]], j[1], j[2], j[3], j[4]), jobs)
        )
        changed = False
        for (s, p, mode, _, _), ex in zip(jobs, fresh, strict=True):
            old = [(r.amount, r.balance) for r in pages[s][p].rows]
            if [(r.amount, r.balance) for r in ex.rows] != old or ex.header != pages[s][p].header:
                changed = True
            candidate = [*pages[s][:p], ex, *pages[s][p + 1 :]]
            before = reconcile.read_score(reconcile.assemble(s, state["files"][s], pages[s]))
            after = reconcile.read_score(reconcile.assemble(s, state["files"][s], candidate))
            if after <= before:
                pages[s], modes[s][p] = candidate, mode
        # nothing new was read: the inconsistency is printed, re-reading won't change it
        return {
            "pages": pages,
            "modes": modes,
            "attempt": attempt if changed else cfg.heal_attempts,
        }

    def label(state: State) -> State:
        rows = [r for st in state["statements"] for r in st.rows]
        labels, infos = categorize.label_patterns(
            rows, cfg.model, cfg.use_rules, api_key=run.api_key
        )
        run.calls.extend(infos)
        return {"labels": labels}

    def compute(state: State) -> State:
        return {"figures": figures.compute(state["statements"], state["labels"])}

    def decide(state: State) -> State:
        problems: list[str] = []
        notes: list[str] = []
        for f in state["findings"]:
            text = f"statement {f.statement + 1}: {f.detail}"
            (problems if f.strength == "strong" else notes).append(
                f"possible tampering - {text}" if f.strength == "strong" else text
            )
        for i in state["issues"]:
            where = f"statement {i.statement + 1}" + (
                f" page {i.page + 1}" if i.page is not None else ""
            )
            problems.append(f"{where}: {i.detail}")
        notes += [r for st in state["statements"] for r in st.repairs]
        verdict = "needs_review" if problems else "ready"
        reason = (
            "; ".join(problems)
            if problems
            else (
                "All statements reconcile row by row, totals and balances tie across statements, "
                "and no sign of editing was found."
            )
        )
        return {"verdict": verdict, "reason": reason, "notes": notes}

    g = StateGraph(State)
    for name, fn in [
        ("extract", extract),
        ("inspect", inspect),
        ("check", check),
        ("heal", heal),
        ("label", label),
        ("compute", compute),
        ("decide", decide),
    ]:
        g.add_node(name, fn)
    g.set_entry_point("extract")
    g.add_edge("extract", "inspect")
    g.add_edge("inspect", "check")
    g.add_conditional_edges("check", route, {"heal": "heal", "categorise": "label"})
    g.add_edge("heal", "check")
    g.add_edge("label", "compute")
    g.add_edge("compute", "decide")
    g.add_edge("decide", END)
    return g.compile()


@dataclass
class Result:
    assessment: Assessment
    state: State
    calls: list[CallInfo]
    seconds: float

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)


def analyse(files: list[Path], config: Config | None = None, api_key: str | None = None) -> Result:
    run = Run(config or Config(), api_key=api_key)
    start = time.perf_counter()
    with ThreadPoolExecutor(run.config.workers) as pool:
        run.pool = pool
        state: State = build(run).invoke({"files": files}, {"recursion_limit": 50})
    f = state["figures"]
    return Result(
        figures.to_assessment(f, state["verdict"], state["reason"]),
        state,
        run.calls,
        time.perf_counter() - start,
    )


def analyse_record(record: dict[str, Any], config: Config | None = None) -> Result:
    return analyse([PROJECT_ROOT / st["file"] for st in record["statements"]], config)
