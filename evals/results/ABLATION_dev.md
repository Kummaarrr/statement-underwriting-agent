# Ablation - dev split (25 files, one run each, Haiku 5.5)

| Variant | Straight-through | Wrong on Ready | Tamper recall | False alarms | $/file |
|---|---|---|---|---|---|
| v1 (all steps) | 76.0% | 0 of 19 | 100.0% | 0.0% | $0.038 |
| without PDF forensics | 76.0% | 1 of 20 | 83.3% | 0.0% | $0.039 |
| without self-healing re-reads | 64.0% | 0 of 16 | 100.0% | 15.8% | $0.038 |
| without keyword rules (model labels only) | 76.0% | 0 of 19 | 100.0% | 0.0% | $0.039 |
| without escalation to Sonnet | 76.0% | 0 of 19 | 100.0% | 0.0% | $0.038 |
| digital pages read as PDF instead of text | 76.0% | 0 of 19 | 100.0% | 0.0% | $0.038 |
| v0 Opus (reference) | 76.0% | 0 of 19 | 100.0% | 0.0% | $0.331 |
| v0 Haiku (reference) | 4.0% | 0 of 1 | 100.0% | 94.7% | $0.011 |
