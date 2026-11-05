# Architecture Diagram

SafeTask is a Python and Streamlit simulation for evaluating tool-using LLM agents. It runs synthetic ShopSphere scenarios with either a deterministic mock agent or Groq, records every action, and applies Objective 2 safety rules to produce explainable labels.

## Application Architecture

<!-- mermaid-checked: no \n, no em-dash/en-dash, no {} in labels, subgraphs are id["label"], arrows are -->|"label"|, all subgraphs closed by end, ids unique -->
~~~mermaid
flowchart TD
    subgraph ClientLayer["Client Layer"]
        User["Researcher or user"]
        RunUI["app.py Streamlit runner"]
        AnalysisUI["app_analysis.py dashboard"]
    end
    subgraph AgentLayer["Agent Layer"]
        Runner["runner.py"]
        Loop["agents.loop agent loop"]
        LLM["agents.llm"]
        Orchestrator["agents.orchestrator"]
    end
    subgraph SimulationLayer["Simulation Layer"]
        Tools["env.tools tool registry"]
        State["env.state per-run state"]
        Events["Events and notifications"]
        Goals["Goal and state diff checks"]
    end
    subgraph EvaluationLayer["Objective 2 Layer"]
        Rules["objective2.rules R1 to R18"]
        Labeler["objective2.labeler"]
        Reports["stage4.py report pipeline"]
        Patterns["Risk patterns and procedural analysis"]
    end
    subgraph DataLayer["Synthetic Data and Reports"]
        Company[("data/company JSON")]
        Scenarios[("data/scenarios JSON")]
        Prompts[("data/prompts text")]
        Runs[("data/runs JSONL")]
        ReportFiles[("reports JSON and CSV")]
    end
    subgraph ExternalLayer["External Service"]
        Groq["Groq OpenAI compatible API"]
    end

    User -->|"selects task and settings"| RunUI
    RunUI -->|"starts scenario runs"| Runner
    Runner -->|"creates agents"| LLM
    Runner -->|"runs scenario"| Orchestrator
    Orchestrator -->|"invokes"| Loop
    Loop -->|"requests next decision"| LLM
    LLM -->|"real model calls"| Groq
    Loop -->|"dispatches tool calls"| Tools
    Tools -->|"reads or mutates"| State
    State -->|"loads synthetic records"| Company
    State -->|"loads scenario seeds"| Scenarios
    LLM -->|"loads safety prompt"| Prompts
    Orchestrator -->|"creates events"| Events
    Goals -->|"checks completion"| State
    Loop -->|"writes trajectory rows"| Runs
    Runs -->|"evaluated by"| Rules
    Rules -->|"violations"| Labeler
    Labeler -->|"labels and reasons"| Reports
    Reports -->|"writes"| ReportFiles
    Patterns -->|"mines from labels"| ReportFiles
    AnalysisUI -->|"reads"| ReportFiles
    Labeler -->|"immediate result"| RunUI
~~~

### Technology Stack Summary

| Layer | Technology | Version | Purpose |
|---|---|---|---|
| UI | Streamlit | >=1.35.0 | Runs scenarios and displays safety results |
| Runtime | Python | 3.10 or newer | Executes the simulation and analysis |
| LLM client | OpenAI Python client | >=1.30.0 | Calls the Groq compatible endpoint |
| LLM service | Groq API | Runtime selected | Chooses the next tool or final answer |
| Local agent | Python mock agents | Project code | Provides repeatable offline behavior |
| Data format | JSON and JSONL | Standard format | Stores company data, scenarios, runs, and reports |
| Testing | pytest | >=7.0 | Validates environment, agents, tools, and Objective 2 |

### Data Storage and External Services

SafeTask has no database, cache, message broker, or production filesystem service. Each run creates a fresh in-memory `State` from synthetic JSON files under `data/company/`, scenario definitions, and prompt files. Trajectories are persisted as JSONL under `data/runs/`, and Objective 2 writes JSON, JSONL, CSV, and Markdown reports under `reports/`. Groq is the only external service and is optional when the mock backend is selected.

### Key Architectural Decisions

- The environment runs in open mode: tools record actions and data flows instead of blocking them, allowing Objective 2 to evaluate unsafe behavior afterward.
- Authority is tracked separately for the original requester and delegated team agents, enabling confused-deputy detection.
- Objective 2 uses deterministic rules for labels while keeping the LLM responsible only for action selection.

## Component Relationships

<!-- mermaid-checked: no \n, no em-dash/en-dash, no {} in labels, subgraphs are id["label"], arrows are -->|"label"|, all subgraphs closed by end, ids unique -->
~~~mermaid
flowchart LR
    subgraph cPresentation["Presentation"]
        cRunUI["app.py"]
        cAnalysisUI["app_analysis.py"]
    end
    subgraph cExecution["Execution"]
        cRunner["runner.py"]
        cLoop["agent_loop"]
        cFactory["make_factory"]
        cSystem["run_system"]
    end
    subgraph cAgents["Agents"]
        cMock["MockAgent"]
        cGroq["GroqAgent"]
        cTeam["Team agents"]
        cNotifier["Notifier agent"]
        cApprover["Approver agent"]
    end
    subgraph cEnvironment["Environment"]
        cMenus["Role menus"]
        cRegistry["TOOLS registry"]
        cState["State"]
        cFlows["Data flow tracker"]
        cEvents["Event store"]
        cGoals["Goal evaluator"]
    end
    subgraph cEvaluation["Objective 2"]
        cRules["Rule catalogue"]
        cEvaluate["Rule evaluator"]
        cLabel["Labeler"]
        cStage["stage4.py"]
        cPattern["Pattern miner"]
    end

    cRunUI -->|"starts"| cRunner
    cAnalysisUI -->|"reads reports"| cStage
    cRunner -->|"builds"| cFactory
    cRunner -->|"starts"| cSystem
    cSystem -->|"runs"| cLoop
    cFactory -->|"creates"| cMock
    cFactory -->|"creates"| cGroq
    cSystem -->|"delegates"| cTeam
    cSystem -->|"reacts to events"| cNotifier
    cSystem -->|"handles approvals"| cApprover
    cLoop -->|"gets allowed tools"| cMenus
    cLoop -->|"dispatches"| cRegistry
    cRegistry -->|"uses"| cState
    cRegistry -->|"records"| cFlows
    cRegistry -->|"publishes"| cEvents
    cSystem -->|"checks completion"| cGoals
    cLoop -->|"writes rows"| cLabel
    cLabel -->|"calls"| cEvaluate
    cEvaluate -->|"applies"| cRules
    cStage -->|"loads runs"| cLabel
    cStage -->|"mines"| cPattern
    cStage -->|"writes reports"| cAnalysisUI
~~~

### Component Inventory

| Component | Layer | Type | Responsibility |
|---|---|---|---|
| `app.py` | Presentation | Streamlit page | Selects scenarios, backend, prompt strength, and run count |
| `app_analysis.py` | Presentation | Streamlit page | Displays Objective 2 summaries, violations, patterns, and run details |
| `runner.py` | Execution | CLI and adapter | Loads scenarios and saves trajectory and summary JSONL |
| `agent_loop` | Execution | Control loop | Requests one decision, executes one tool, records one row, and repeats |
| `run_system` | Execution | Multi-agent runtime | Coordinates entry agents, delegation, approvals, notifications, and shared state |
| `make_factory` | Execution | Agent factory | Creates mock or Groq agents for each invocation |
| `MockAgent` | Agents | Offline decision agent | Follows scenario scripts with controlled safety variation |
| `GroqAgent` | Agents | External LLM adapter | Requests tool calls and final answers from Groq |
| Team agents | Agents | Delegated workers | Perform department-specific work with restricted menus |
| Notifier agent | Agents | Event worker | Decides notification audience and detail after events |
| Approver agent | Agents | Approval worker | Verifies and records approval decisions |
| `TOOLS` | Environment | Tool registry | Maps tool names to state operations |
| `State` | Environment | In-memory domain model | Holds users, teams, records, permissions, changes, events, and flows |
| Role menus | Environment | Authorization surface | Defines tools visible to each agent role |
| Data flow tracker | Environment | Security telemetry | Tracks tagged data moving between resources and destinations |
| Event store | Environment | In-memory event log | Supports notifier reactions and bounded cascades |
| Goal evaluator | Environment | Scenario oracle | Determines task success and state differences |
| Rule catalogue | Objective 2 | Safety definitions | Defines R1 through R18 with kind, severity, weight, and meaning |
| Rule evaluator | Objective 2 | Deterministic analyzer | Finds rule violations in a completed run |
| Labeler | Objective 2 | Classifier | Produces SAFE, SAFE_VARIANT, DECLINED, or unsafe labels |
| `stage4.py` | Objective 2 | Analysis CLI | Builds labels, risk patterns, procedural metrics, and reports |
| Pattern miner | Objective 2 | Generalization analyzer | Mines unsafe sequences from known runs and tests other splits |
