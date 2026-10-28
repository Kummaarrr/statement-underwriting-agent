# Statement Underwriting Agent: results report

## 1. Summary

A small-business lender or merchant-cash-advance (MCA) funder gets 3-6 months of business bank
statements per applicant. The underwriter needs a handful of figures and a judgement:

- real revenue per statement and its average;
- average daily balance;
- bounced payments (NSF), overdrafts and negative days;
- the loans and advances already pulling money out;
- whether the file is genuine.

This project turns a statement file into exactly that. Every figure links to the statement
lines behind it, and the verdict is *Ready* or *Needs review* with the reason.

Three systems were measured against synthetic statements with exact ground truth:

| | v0: whole file to **Claude Opus 5.5** | v0-small: whole file to **Claude Haiku 5.5** | **v1: agent on Claude Haiku 5.5** |
|---|---|---|---|
| Straight-through (marked Ready, every figure right) | 60% (53%-67%) | 20% | **73%** (the maximum: 11 of 15 test files are genuine) |
| Genuine files sent to a human (false alarms) | 18% (9%-27%) | 73% | **0%** |
| Wrong figures on files marked Ready | 0.7 (0.0-1.0) | 0.3 | 2: the two "perfect fakes" (see 6) |
| $ per file | $0.31 | $0.011 | **$0.031**, 10x cheaper than Opus |
| Same result on every run? | no (see 4) | no | **yes**: identical in all 3 runs |

**In one line:** on held-out files, the agent on the cheap model approves every genuine file
correctly and sends none to a human. It does this at a tenth of the frontier model's cost, and
its results don't vary between runs. The naive frontier-model approach is close on reading but
erratic on judgement: it flags 9-27% of genuine files and lets a doctored file through in
some runs. The limit, stated plainly: a forgery that rebuilds the whole PDF in the bank's own
format is invisible to v1's arithmetic and forensics (section 6).

## 2. Setup

**Data.** There is no real statement data (it is personal and financial), so a generator
builds the test files, with the ground truth exact by construction.

- **Applicants:** 40 applicant files and 160 statement PDFs (423 pages). They cover six
  industries, seasonality, payroll, sales tax, MCA stacking and funding wires, NSF returns with
  re-presentment, and overdrafts. Business days follow the Federal Reserve calendar.
- **Layouts:** five fictional bank layouts, each with its own descriptors, summary boxes,
  fee boxes and disclosures. They include:
  - debit/credit columns;
  - signed amounts;
  - multi-line ACH detail;
  - a credit-union layout with a daily-balance table instead of a running balance;
  - a legacy CR/DR printout.
- **Variants:**
  - digital files;
  - scans, with skew, uneven paper, blur, speckle and JPEG;
  - innocent re-saves through another PDF tool (to test false alarms);
  - four tamper types:
    1. amounts inflated with balances left alone;
    2. amounts inflated and balances recomputed;
    3. fake deposits inserted and balances recomputed;
    4. a "perfect fake" regenerated with the bank's own fonts and metadata.
- **Review:** realism was reviewed in three rounds, one by the author and two blind rounds by
  four AI reviewers acting as an underwriter, a bank-operations expert, a fraud analyst and a
  buyer. A script checks every PDF against the ground truth.
- **Split:** frozen by applicant. Dev has 25 files (used for building) and test has 15 files,
  run once at the end with 3 repeats. Test is the harder split: it holds all four re-saved
  files and both perfect fakes.

**Metrics** are what the client cares about:

- per figure, the % of files correct (revenue within ±1% for every statement and the average,
  average daily balance within ±1%, counts exact, every debt position found with its payment
  within ±1% and no phantom positions);
- **straight-through**: marked Ready with every figure right, so no human is needed;
- **wrong figures on files marked Ready**: the safety metric. For a tampered file the truth is
  the genuine figures, so trusting a fake counts as wrong;
- tamper recall and false alarms;
- seconds and $ per file.

**Systems.**

- **v0 / v0-small:** all statement PDFs in one call, with the ground-truth definitions. The
  model reads, computes and judges in one pass, which is the "tutorial" approach.
- **v1:** a LangGraph agent (see [architecture.md](architecture.md)). Haiku transcribes pages
  and labels descriptor patterns. Code reconciles every row and total, re-reads only the pages
  that don't tie, runs PDF forensics across the file's statements, computes every figure and
  applies the verdict rule.

## 3. Building v1 (dev split, Claude Haiku 5.5)

| Step | Straight-through | Wrong on Ready | What changed |
|---|---|---|---|
| v0-small baseline | 4% | 0 of 1 | Reads well, but flags 95% of genuine files: no automation |
| v1, first full run | 48% | **5 of 17** | Every digital file reconciles and every tamper is caught. Categorisation was the weak link: card bills and rent were called MCA debt, an MCA with a truncated name was missed, and a plain "DEPOSIT" was treated as non-revenue |
| + cadence evidence and fixed-payment rule | 64% | 3 of 19 | The model sees, per pattern, whether the amount is always the same and the typical gap. Code accepts a debt series only if the payment is fixed |
| + scan repairs | 72% | 1 of 19 | A misread balance digit or a misread sign is repaired only when the printed balances prove it (logged) |
| + wording gates | **76%** (= v0 Opus) | **0 of 19** | The model can't create an NSF, an overdraft or an owner transfer that the descriptor doesn't name |

Two lessons from building it:

1. **Reading was never the problem.** In the first test on four layouts, including a scan,
   Haiku transcribed 462 of 462 rows exactly. Every early failure was labelling: what a
   payment *is*. Each fix was a generic rule about money ("financing is repaid in fixed
   amounts"; "a fee counts as NSF only if it says so"), not a patch for one file.
2. **An oracle test separates reading errors from logic errors.** It feeds v1's code a
   perfect transcription built from the ground truth. The figures must then equal the truth
   exactly on all 30 genuine files, and forensics must separate every edited document from
   every innocent one. Both pass, so any error left in a real run is a reading or labelling
   error, never arithmetic.

Ablation on dev (one run each; [ABLATION_dev.md](../evals/results/ABLATION_dev.md)):

| Without... | Straight-through | Wrong on Ready | Tamper recall | False alarms |
|---|---|---|---|---|
| nothing (v1) | 76% | 0 of 19 | 100% | 0% |
| PDF forensics | 76% | **1 of 20** | **83%** | 0% |
| self-healing re-reads | **64%** | 0 of 16 | 100% | **16%** |
| keyword rules / Sonnet escalation / text mode | 76% | 0 of 19 | 100% | 0% |

Forensics is what catches careful edits, where balances were recomputed so the arithmetic holds.
Self-healing is what keeps scans from turning into false alarms. With the code-side label gates
in place, the keyword rules became redundant on this data. They stay as cheap insurance for
real-world descriptors. Escalation to Sonnet was never needed on dev.

## 4. Final results (held-out test split)

15 files, each system run 3 times with fresh model calls. Full table:
[FINAL_test.md](../evals/results/FINAL_test.md).

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

- **v1 is deterministic in practice.** All three runs give identical figures and verdicts. The
  model only transcribes and labels; everything that can vary in judgement is code.
- **v0 Opus reads as well as v1 does** (per-figure accuracy is identical), but its *verdicts*
  swing. In different runs it flagged genuine files because "the printed average ledger
  balance doesn't match the daily balances" (it does). It caught both perfect fakes in one run
  and marked one Ready in another.
- **v0-small** reads nearly as well, but it flags most genuine files, so it automates almost
  nothing.

## 5. Cost and speed

v1 costs about $0.031 per file at list price, roughly 61,000 input and 51,000 output tokens on
Haiku. Output dominates: the transcribed rows. That is 10x cheaper than v0 Opus, about $0.31,
and far below the ~$1-5+ per document quoted for enterprise tools. It takes about 50 s per file
with 8 pages read in parallel, against about 90 s for v0 Opus.

## 6. Failure analysis and limitations

- **Perfect fakes pass v1.** Both perfect fakes in the test split were marked Ready in all three
  runs. These forgeries rebuild the statement in the bank's own fonts and metadata, with every
  balance recomputed. They were included to measure this limit, and v1's arithmetic and
  forensics see nothing. Opus sometimes caught them through a *content* clue: the bounced
  payments and overdraft fees no longer fit the inflated balances. The clear next step for v1
  is a code check, "an item was returned for insufficient funds while the printed balance
  covered it". It was not added after seeing the test results, because that would invalidate
  them. In practice a lender pairs statements with a bank-data connection (Plaid and similar)
  or a direct bank verification for exactly this reason.
- **Synthetic data.** The banks, businesses and documents are generated. They were
  reviewed for realism, but real statements have layouts, descriptors and edge cases these five
  fictional banks don't, for example multiple accounts, check images and multi-currency. The
  code was hardened after review for some of them: balances printed once per day, linearised
  PDFs, out-of-order uploads and mixed digital/scanned files.
- **Small test split.** 15 files, 3 runs each; one file is about 7 points.
- **Timing.** A batch re-run replayed two v1 runs from the cache (replay times), and one Opus run
  stalled overnight on a dropped connection (since fixed with a client timeout), so the table
  reports seconds as the median of the fresh runs.

## 7. Reproduce

```bash
uv sync --group dev --extra agent --extra app
uv run python scripts/build_dataset.py      # regenerate the 160 PDFs (deterministic)
uv run python scripts/verify_dataset.py     # check every PDF against the ground truth
uv run pytest                               # incl. the oracle tests
export ANTHROPIC_API_KEY=...                # model runs need a key
uv run python scripts/run_eval.py --system v1 --model haiku --split test --estimate
uv run python scripts/run_eval.py --system v1 --model haiku --split test --tag r1
uv run python scripts/final_table.py
uv run streamlit run app/streamlit_app.py   # the demo
```
