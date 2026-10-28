# Architecture

## The idea in one sentence

The model reads and labels; code computes and decides. Every number that matters is either
copied from the page and then reconciled against the statement's own arithmetic, or computed
in code from those copied rows. The verdict is a rule, not an opinion.

## v1 pipeline (LangGraph)

```
          +---------+    +-----------+    +-----------+   issues?   +--------+
files --> | extract | -> | forensics | -> | reconcile | ----------> |  heal  |
          +---------+    +-----------+    +-----------+  <--------  +--------+
                                                |        re-check   (max 2 rounds)
                                                v
                                         +------------+    +---------+    +--------+
                                         | categorise | -> | figures | -> | decide |
                                         +------------+    +---------+    +--------+
```

| Step | Module | Model? | What it does |
|---|---|---|---|
| extract | `v1/extract.py`, `v1/pdfio.py` | Haiku 5.5, one call per page | Transcribes rows (date, descriptor, signed amount, printed balance), the summary box and daily-balance tables. Pages with a text layer are sent as text (exact characters, fewer tokens); image-only pages as a PDF page (the model reads the image). The first page of each statement is read first so later pages get the statement period for full dates. |
| forensics | `v1/forensics.py` | no | Compares the statements of one file with each other. **Strong** (flags the file): text in a font no sibling statement uses, an embedded subset font only this statement has, an incremental update (a second cross-reference section that isn't linearisation). **Weak** (reported only): a different producer, modified long after creation - innocent re-saves look like this too. |
| reconcile | `v1/reconcile.py` | no | Row by row: previous balance + amount = printed balance (rows without a printed balance move the running total). Daily-balance tables against the rows. Rows against the summary totals and counts; opening + credits - debits = closing; statements chain in period order. Two logged repairs, both proven by balances: a sign read wrong, a balance digit misread. An edited amount can't pass either - the later printed balances still follow the original amount. |
| heal | `v1/agent.py` | Haiku, then Sonnet 5.5 | Re-reads only the pages behind an issue: first as PDF / higher-resolution image, last attempt on Sonnet. A re-read replaces the old one only if the statement reconciles better (never if it loses the header). A page that reads the same twice is not escalated: the inconsistency is printed, not misread. Statements with strong edit evidence are not re-read. |
| categorise | `v1/categorize.py` | Haiku, one call per file | Bank-agnostic keyword rules for what no bank prints ambiguously (NSF / overdraft / returned-deposit fees, transfers between own accounts, card bills, rent / payroll / tax entries); the model labels each distinct descriptor pattern once (IDs masked), shown how often it occurs, whether the amount is always the same and the median gap in days. Key-figure labels are gated by wording: the model can't create an NSF, an overdraft or an owner transfer the descriptor doesn't name. |
| figures | `v1/figures.py` | no | Revenue per statement (income credits minus returned deposits), average daily balance over every calendar day, negative days, NSF and overdraft counts, existing debt (fixed-amount recurring series only; frequency from the gaps). Each figure keeps the row references behind it (`S2p3r14` = statement 2, page 3, row 14). |
| decide | `v1/agent.py` | no | *Needs review* if anything doesn't reconcile, a page wasn't fully readable, or forensics found strong evidence - with every reason listed. Otherwise *Ready*. Weak findings and repairs are reported as notes. |

Cost lever: the expensive part of a statement is reading ~100 rows per statement. A small model
does that well when the task is pure transcription; everything that needs judgement or
arithmetic is code. Sonnet is only called for a page that still fails after a re-read.

## Baselines

`systems/v0.py` - the "tutorial" approach: all statement PDFs of a file in one call with the
ground-truth definitions; the model reads, computes and judges at once. Run on Opus 5.5 (v0) and
Haiku 5.5 (v0-small).

## Data and evaluation

- `synth/` - the generator: day-by-day cash-flow simulation (Federal Reserve calendar,
  industry economics, MCA stacking, returns and re-presentment, overdrafts), five fictional bank
  layouts with their own descriptors, scans, re-saves and four tamper types. Ground truth is
  exact by construction. `scripts/verify_dataset.py` checks every PDF against it.
- `scoring.py` - the client-facing metrics; `scripts/run_eval.py` - runs a system over a split
  (cached, cost-capped, `--estimate` from exact token counts, `--tag` for independent repeats).
- `tests/test_v1_oracle.py` - feeds v1's code half a perfect transcription built from the
  ground truth: figures must equal the truth exactly on every genuine file, and forensics must
  separate edited documents from innocent ones. So whatever v1 gets wrong in a real run is a
  reading or labelling error, never arithmetic.

## Demo

`app/streamlit_app.py` - pre-computed sample files (no key needed) and "analyse your own
statements" with the visitor's own Anthropic key, which is passed explicitly per call and never
cached, stored or logged.
