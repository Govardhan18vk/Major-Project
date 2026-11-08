# Stage 4 report (Objective 2)

Environment: 64 scenarios, 7 teams, 24 users, 30 tools, 10 agent roles, 18 rules.
Runs analysed: 16 {'groq': 16}. **mock = scripted offline agent (tests the pipeline); groq = real LLM. They must never be mixed in conclusions.**
Distinct tool trajectories: 8. Mean path entropy: 0.956 bits.

## Label distribution

| Label | Runs |
|---|---|
| DECLINED | 13 |
| SAFE | 3 |

Unsafe rate 0.0%; unsafe but task still succeeded 0.0% of all runs (N/A of unsafe runs); task success 18.8%.

## Prompt-strength baseline (the number later policies must beat)

| Prompt | Runs | Unsafe | Outcome harm | Declined | Task success | Path entropy |
|---|---|---|---|---|---|---|
| p0 | 1 | 0.0% | 0.0% | 100.0% | 0.0% | 0.0 |
| p1 | 3 | 0.0% | 0.0% | 0.0% | 100.0% | 0.918 |
| p2 | 12 | 0.0% | 0.0% | 100.0% | 0.0% | 0.838 |

## Violations per rule

| Rule | Count |
|---|---|

Runs with a violation per team: {}
Violations per agent: {}
Delegation depth: {0: 16}; event-chain depth: {0: 16}
Time to first violation: {'mean_step': None, 'mean_sim_minutes': None}

## Top risk patterns

| ID | Sequence | Rules | Support | Missing control |
|---|---|---|---|---|

Pattern coverage by split (share of unsafe runs matching a mined pattern): {}

Tokens: {'total': 51717, 'mean_per_run': 3232.3}. Mean run time 11.7281 s. Model fingerprint: not computed: needs real runs from at least two different models.
