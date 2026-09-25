# Eval run — gemini / gemini-3.6-flash — 20260925-130245

| scenario | status | checks | model calls | max request tokens (est) | s |
|---|---|---|---|---|---|
| pl_cs_fasting | completed | 12/12 | 2 | 3870 | 106.6 |
| uk_astronomy | completed | 8/9 | 2 | 4009 | 186.6 |
| english_report | provider_error | — | 0 | 2795 | 262.6 |
| ambiguous_mercury | provider_error | — | 0 | 2755 | 300.2 |
| astronomy_refinement | provider_error | — | 0 | 2756 | 299.8 |

**20/21 checks passed over 2 completed scenario(s)**; 3 aborted by the provider (not graded). 4 model calls, 14236 prompt + 1684 completion tokens.

## Failed checks
- `uk_astronomy` **no unsupported causes for spikes or drops** — **Pronounced Academic Seasonality:** Traffic exhibits sharp spikes tied to the Ukrainian school/academic calendar.

## Aborted (provider)
- `english_report` provider_error after 0 tool call(s): HTTP 429: [{
  "error": {
    "code": 429,
    "message": "You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.d
- `ambiguous_mercury` provider_error after 0 tool call(s): HTTP 429: [{
  "error": {
    "code": 429,
    "message": "You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.d
- `astronomy_refinement` provider_error after 0 tool call(s): HTTP 429: [{
  "error": {
    "code": 429,
    "message": "You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.d
