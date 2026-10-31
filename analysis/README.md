# Step 10: analysis

Turns the event logs into the numbers, tables and charts for Section 4. It uses only the Python standard library, so no pandas or SciPy install is needed.

| File | Contents |
|---|---|
| `analyse.py` | Reads `logs/` and writes `analysis/results/` |
| `stats.py` | Paired t-test, Wilcoxon signed-rank (exact or tie-corrected), effect sizes, 95% CI, Spearman |
| `make_synthetic_logs.py` | Makes **fake** participants by driving the real console, for testing only |
| `test_analysis.py` | Tests: statistics against known values, and the whole pipeline on synthetic data |

## Run it after the study

From the repository root:

```bash
python -m analysis.analyse
```

The analysis writes these files to `analysis/results/`:

- `summary.md`: every table for Section 4.
- `per_task.csv` and `per_participant.csv`: the same numbers, row by row.
- `advice_review.csv`: assisted tasks that still had errors, with the assistant's answers. Two researchers mark each one for the "advice-induced error" metric.
- `figures/errors_by_condition.svg`: each participant's errors without and with the assistant.
- `figures/category_by_condition.svg`: failed rules by category.

## What it computes

- **Main comparison:** per participant, the sum over the 3 tasks of each condition, analysed as the paired difference *with LLM minus without* (negative means fewer errors with the assistant). This covers error count, weighted score, tasks without a serious error, time and corrections. Both the Wilcoxon signed-rank test (the primary test) and the paired t-test are reported, with the 95% CI, Cohen's d_z and the rank-biserial r.
- **Breakdowns:** errors by category, and failure rate per rule.
- **Moderation:** novices (experience 1-2) against experienced participants (3-5), plus the Spearman correlation between experience and the paired difference.
- **Design checks:** Set A against Set B, and part 1 against part 2.
- **Secondary result:** only participants who asked the assistant at least once.
- **Questionnaire summaries,** and participants without the LLM set against the real-world baseline (`dataset/results/baseline.json`), as context only.

## Synthetic data

To check the pipeline before real data exists:

```bash
python -m analysis.make_synthetic_logs --out analysis/synthetic_logs --n 24
```

```bash
python -m analysis.analyse --logs analysis/synthetic_logs --out analysis/synthetic_results
```

Synthetic participants have IDs from P901 upwards, and their folder carries a `SYNTHETIC_DATA.txt` marker, which makes `summary.md` open with a warning. **These numbers are invented and must never be reported.**
