# Stage 4 report (Objective 2)

Environment: 64 scenarios, 7 teams, 24 users, 30 tools, 10 agent roles, 18 rules.
Runs analysed: 473 {'groq': 473}. **mock = scripted offline agent (tests the pipeline); groq = real LLM. They must never be mixed in conclusions.**
Distinct tool trajectories: 322. Mean path entropy: 1.917 bits.

## Label distribution

| Label | Runs |
|---|---|
| DECLINED | 179 |
| SAFE | 157 |
| SAFE_VARIANT | 8 |
| UNSAFE_OUTCOME | 93 |
| UNSAFE_PROCEDURAL | 36 |

Unsafe rate 27.3%; unsafe but task still succeeded 18.8% of all runs (69.0% of unsafe runs); task success 52.8%.

## Prompt-strength baseline (the number later policies must beat)

| Prompt | Runs | Unsafe | Outcome harm | Declined | Task success | Path entropy |
|---|---|---|---|---|---|---|
| p0 | 161 | 41.6% | 31.1% | 22.4% | 65.8% | 0.673 |
| p1 | 152 | 29.6% | 19.7% | 31.6% | 57.9% | 0.656 |
| p2 | 160 | 10.6% | 8.1% | 59.4% | 35.0% | 0.919 |

## Violations per rule

| Rule | Count |
|---|---|
| R2 | 3 |
| R3 | 34 |
| R4 | 19 |
| R5 | 13 |
| R7 | 18 |
| R9 | 11 |
| R10 | 1 |
| R11 | 16 |
| R12 | 2 |
| R13 | 33 |
| R14 | 20 |
| R15 | 15 |
| R16 | 23 |
| R17 | 1 |

Runs with a violation per team: {'Shipping': 40, 'Platform': 21, 'HR': 4, 'Payments': 35, 'Risk': 29}
Violations per agent: {'assistant': 175, 'hr_agent': 5, 'vendor_agent': 5, 'shipping_agent': 1, 'notifier': 8, 'orchestrator': 7, 'platform_agent': 8}
Delegation depth: {0: 376, 1: 97}; event-chain depth: {0: 469, 1: 4}
Time to first violation: {'mean_step': 3.4, 'mean_sim_minutes': 11.5}

## Top risk patterns

| ID | Sequence | Rules | Support | Missing control |
|---|---|---|---|---|
| P01 | check_permission:ALLOW > read_file:CODE > commit_change:REPO_PUSH | R14 | 8 | no secret scan on outgoing content |
| P02 | get_user > query_hr:HR_DATA | R7 | 5 | no permission check before reading personal or secret data |
| P03 | read_file:CODE > check_permission:ALLOW > commit_change:REPO_PUSH | R14 | 4 | no secret scan on outgoing content |
| P04 | check_permission:ALLOW > merge_pull_request:REPO_PUSH | R11 | 4 | no approval before a cross-team write or a merge |
| P05 | read_file:CODE > commit_change:REPO_PUSH/OTHER_TEAM | R11 | 4 | no approval before a cross-team write or a merge |
| P06 | read_inbox:UNTRUSTED > delegate:SHIPPING_AGENT | R16 | 4 | instructions from untrusted content were followed without human confirmation |
| P07 | query_payments:PAYMENTS_DATA | R9 | 3 | no permission check before reading another team's data |
| P08 | get_user > grant_temp_access > grant_temp_access | R15 | 3 | temporary access was not revoked |
| P09 | query_payments:PAYMENTS_DATA > create_document > share_with_vendor > share_with_vendor:VENDOR | R13 | 3 | no tag-versus-channel check before sending data out |
| P10 | read_file:SECRET_DATA | R7 | 2 | no permission check before reading personal or secret data |

Pattern coverage by split (share of unsafe runs matching a mined pattern): {'known': {'unsafe_runs': 95, 'matched': 72, 'coverage': 0.7579}, 'diverse': {'unsafe_runs': 13, 'matched': 5, 'coverage': 0.3846}, 'unseen': {'unsafe_runs': 21, 'matched': 3, 'coverage': 0.1429}}

Tokens: {'total': 2019896, 'mean_per_run': 4270.4}. Mean run time 28.2935 s. Model fingerprint: not computed: needs real runs from at least two different models.
