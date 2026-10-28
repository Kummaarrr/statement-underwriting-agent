# Statement Underwriting Agent

Reads a small business's US bank statements (digital or scanned PDFs) and returns the figures a
lender or merchant-cash-advance funder decides on. Every figure is traced to the statement lines
behind it, and the verdict is *Ready* or *Needs review* with the reason. The figures are:

- real revenue per statement;
- average daily balance;
- bounced payments (NSF), overdrafts and negative days;
- the loans and advances already pulling money out.

It is measured against synthetic statements with exact ground truth, including scans,
innocently re-saved files and four kinds of doctored statements.

**Write-up:** [docs/REPORT.md](docs/REPORT.md) · **Architecture:** [docs/architecture.md](docs/architecture.md) · **Live demo:** https://statement-underwriting-agent-ycgnoettucijclvazywgbe.streamlit.app/

## The story

1. **v0, the tutorial approach.** Send the whole statement file to a frontier model (Claude
   Opus 5.5) with the figure definitions. It reads well, but its judgement swings between
   runs: it sends 9-27% of genuine files to a human and lets a doctored file through in some
   runs. It costs about $0.31 per file. On a model 30x cheaper (Claude Haiku 5.5) the same
   approach flags most genuine files, so nothing gets automated.
2. **v1: the model reads, code decides.** A LangGraph agent in which Haiku only transcribes pages
   and labels descriptor patterns. Code does everything that must be exact:
   - reconciles every row, total and statement-to-statement chain;
   - re-reads only the pages that don't tie;
   - runs PDF forensics across the file's statements (fonts, embedded subsets, incremental
     saves);
   - computes every figure and applies the verdict rule.
3. **Measured at every step.** It was built on a dev split, with every fix a generic rule about
   money and each step ablated. An oracle test proves the code half exact, and final numbers
   come from a held-out test split run three times.

## Results

**Held-out test split:** 15 applicant files (3-6 statements each) never used during
development. Each system was run 3 times with fresh model calls; mean, with the range across runs.

| Metric | v0 - Opus 5.5, whole file | v0-small - Haiku 5.5, whole file | v1 - Haiku 5.5 agent |
|---|---|---|---|
| All key figures correct | 73% | 64% (60%-67%) | 73% |
| **Straight-through** (Ready and all correct) | 60% (53%-67%) | 20% (7%-33%) | 73% |
| Flagged Needs review | 36% (33%-40%) | 78% (67%-87%) | 13% |
| **Wrong figures on files marked Ready** | 0.7 (0.0-1.0) | 0.3 (0.0-1.0) | 2.0 |
| Tampered files flagged (recall) | 83% (75%-100%) | 92% (75%-100%) | 50% |
| Genuine files flagged (false alarms) | 18% (9%-27%) | 73% (54%-91%) | 0% |
| Seconds per file (median of fresh runs) | 88 | 57 | 52 |
| $ per file | $0.312 ($0.305-$0.318) | $0.011 ($0.010-$0.012) | $0.031 ($0.031-$0.032) |

**In one line:** the agent on the cheap model sends no genuine file to a human and gets every
figure right on every genuine file. It costs **a tenth of the frontier model** and gives the
**same answer on every run**. Stated plainly: a "perfect fake", a statement rebuilt in the
bank's own format, passes v1. Section 6 of the report explains why and gives the next check.

Ablation (what each step buys): [evals/results/ABLATION_dev.md](evals/results/ABLATION_dev.md) ·
All test runs: [evals/results/FINAL_test.md](evals/results/FINAL_test.md)

## How it works

```
extract (Haiku, per page) -> PDF forensics -> reconcile <-> self-heal failing pages
   -> categorise (rules + Haiku) -> figures in code -> verdict + evidence
```

- **Extraction:** pages with a text layer go to the model as text (exact characters, fewer
  tokens); scanned pages go as images. The model copies and never computes.
- **Reconciliation:** previous balance + amount = printed balance on every row; summary totals
  and counts; opening + credits - debits = closing; each statement opens at the previous
  closing balance. Two read errors can be repaired, only when the printed balances prove them,
  and every repair is logged.
- **Forensics:** an edit inside a PDF leaves traces the sibling statements don't share: a font
  nowhere else in the file, an embedded subset font, an incremental update. A different
  producer or a late modification date is reported but not flagged, because innocent re-saves
  look like that too.
- **Figures:** revenue (income credits minus returned deposits), average daily balance over
  every calendar day, counts, and debt (fixed-amount recurring series only). Each figure keeps
  its row references.

## Repository

```
src/stmt_uw/
  synth/      statement generator: cash-flow simulation, 5 bank layouts, scans, tampering
  v1/         the agent: pdfio, extract, reconcile, forensics, categorize, figures, agent
  systems/    v0 baseline and v1 variants (for the ablation)
  llm.py      Claude access: structured outputs over PDFs, streaming, cache, cost cap
  scoring.py  the client-facing metrics
scripts/      build_dataset, verify_dataset, run_eval (--estimate), final_table, demo samples
app/          Streamlit demo: sample files + "analyse your own statements" (your own key)
data/         dataset.json (ground truth; PDFs are regenerated by build_dataset.py)
evals/        results of every run
tests/        generator guarantees, scorer, v1 oracle tests, real-world edge cases
```

## Run it

```bash
uv sync --group dev --extra agent --extra app
uv run python scripts/build_dataset.py     # 160 synthetic statement PDFs, deterministic
uv run pytest
export ANTHROPIC_API_KEY=...               # for model runs (results are cached)
uv run python scripts/run_eval.py --system v1 --model haiku --split dev --estimate
uv run streamlit run app/streamlit_app.py
```

The demo never holds an owner key. Live analysis uses the visitor's own Anthropic key, which is
passed per call and never cached, stored or logged. Please upload only synthetic statements or
your own documents.

## Notes

All banks, businesses, people and documents are fictional and generated; any resemblance is
coincidental.

## License

MIT
