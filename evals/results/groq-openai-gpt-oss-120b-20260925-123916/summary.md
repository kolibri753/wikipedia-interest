# Eval run — groq / openai/gpt-oss-120b — 20260925-123916

| scenario | status | checks | model calls | max request tokens (est) | s |
|---|---|---|---|---|---|
| pl_cs_fasting | completed | 11/12 | 2 | 3752 | 5.3 |
| uk_astronomy | completed | 9/9 | 2 | 3869 | 62.4 |
| english_report | completed | 5/11 | 10 | 6503 | 604.0 |
| ambiguous_mercury | completed | 5/5 | 3 | 4205 | 179.9 |
| astronomy_refinement | completed | 7/7 | 4 | 6484 | 184.6 |

**37/44 checks passed over 5 completed scenario(s)**; 0 aborted by the provider (not graded). 21 model calls, 100641 prompt + 5530 completion tokens.

## Failed checks
- `pl_cs_fasting` **names the Czech article analysed** — no match in final answer
- `english_report` **wrote analyst notes** — no write_file call
- `english_report` **ran report.py** — commands: ['python scripts/analyze.py --qid Q1860 --langs pl cs uk', 'python scripts/analyze.py --qid Q1860 --langs pl']
- `english_report` **a PDF report exists** — no file matching **/*.pdf
- `english_report` **states that Ukrainian has the highest relative attention (from the comparison block)** — none of the alternatives matched
- `english_report` **states pageviews are not demand / willingness to pay** — no match in final answer
- `english_report` **finished with a final answer** — step limit reached without a final answer
