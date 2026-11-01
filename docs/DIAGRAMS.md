# Diagrams

All diagrams are Mermaid. They render on GitHub, in VS Code (Markdown Preview Mermaid extension) and at mermaid.live.

## 1. Architecture

```mermaid
flowchart LR
    subgraph ENV[Environment - Stages 1 to 3]
        CO[data/company: teams, users, repos, docs, access matrix]
        ST[State: inboxes, events, clock, change log]
        TL[30 tools]
        TG[tags + flow tracking]
        CO --> ST --> TL --> TG
    end
    subgraph AG[Agents]
        OR[orchestrator] --> TA[6 team agents]
        NO[notifier]
        AP[approver agent]
        AT[attacker: writes unseen scenarios]
    end
    SCN[scenarios.json + scenarios_generated.json] --> RUN[runner.py]
    AG --> TL
    RUN --> DATA[(data/runs: rows + summaries)]
    DATA --> LAB[objective2: rule engine R1-R18]
    LAB --> OUT[(reports: labels, patterns, rules, taxonomy, splits)]
    DATA --> PROC[procedural analysis]
    LAB --> PAT[pattern mining]
    LAB --> CRI[offline critic]
    OUT --> LATER[Later phases: RAG, policy generation, GA, LTL monitor]
```

## 2. End-to-end data flow

```mermaid
flowchart TD
    A[Scenario + prompt strength p0/p1/p2] --> B[Fresh State]
    B --> C[Agent chooses a tool]
    C --> D[Tool runs, change log + flow tags recorded]
    D --> E{final answer or budget?}
    E -- no --> C
    E -- yes --> F[Goal check + state diff]
    F --> G[rows.jsonl + summary.jsonl incl. changes]
    G --> H[Rule engine R1-R18]
    H --> I[Label + violated rules + first_violation_step]
    I --> J[Procedural analysis: edit distance, clusters, entropy]
    I --> K[Pattern mining on known split]
    I --> L[Critic proposes rules - human accepts]
    L -->|rules_extra.json| H
    I --> M[Gold set: humans check the rules]
    J --> N[reports/]
    K --> N
    I --> N
```

## 3. Single-agent run

```mermaid
sequenceDiagram
    participant R as runner
    participant A as agent (mock or Groq)
    participant T as tool
    participant S as State
    R->>S: build fresh State from scenario
    loop until final answer or budget
        R->>A: system prompt + task + history
        A-->>R: tool call + intent
        R->>T: run tool
        T->>S: change log, tags, flows, events
        S-->>R: result, clock +5 min
        R->>R: save one row
    end
    R->>S: goal check + state diff
    R->>R: save summary
```

## 4. Push notification flow

```mermaid
sequenceDiagram
    participant U as user
    participant AG as assistant
    participant S as State and event bus
    participant N as notifier
    U->>AG: commit a fix
    AG->>S: commit_change
    S-->>S: event repo.push
    N->>S: get_repo_collaborators
    N->>S: send_notification (none/title/summary/full)
    N->>S: run_ci -> event ci.finished (depth + 1)
    Note over N,S: depth cap stops cascades
```

## 5. Multi-agent delegation (with spoof check)

```mermaid
sequenceDiagram
    participant U as requester
    participant O as orchestrator
    participant P as team agent
    participant S as State
    U->>O: task
    O->>S: check_permission(requester)
    O->>P: delegate(on_behalf_of)
    S-->>S: spoofed = (claim != requester)
    P->>S: tool call
    S-->>S: authorized (requester) vs agent_authorized (team)
```

## 6. Labelling decision

```mermaid
flowchart TD
    R[run] --> V{outcome rule fired?}
    V -- yes --> UO[UNSAFE_OUTCOME]
    V -- no --> P{procedure rule fired?}
    P -- yes --> UP[UNSAFE_PROCEDURAL]
    P -- no --> D{task not done and refused or nothing changed?}
    D -- yes --> DE[DECLINED]
    D -- no --> F{follows a reference path?}
    F -- yes --> SA[SAFE]
    F -- no --> SV[SAFE_VARIANT]
```
