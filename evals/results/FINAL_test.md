# Final results - test split

15 held-out applicant files (7 digital incl. 4 innocently re-saved, 4 scanned, 4 tampered: edit_recalc, edit_recalc, perfect_fake, perfect_fake), each system run 3 times independently; mean (range across the 3 runs).

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

Per key figure, % of files correct (mean of 3 runs):

| Figure | v0 - Opus 5.5, whole file | v0-small - Haiku 5.5, whole file | v1 - Haiku 5.5 agent |
|---|---|---|---|
| revenue | 73% | 71% (67%-73%) | 73% |
| avg_daily_balance | 73% | 69% (67%-73%) | 73% |
| nsf_count | 100% | 100% | 100% |
| overdraft_count | 100% | 100% | 100% |
| negative_days | 93% | 91% (87%-93%) | 93% |
| debt | 100% | 98% (93%-100%) | 100% |

Runs available: v0_opus x3, v0_haiku x3, v1_haiku x3
