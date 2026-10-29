# Architecture Diagram

SafeTask is a Streamlit UI over a single-process Python simulation. The
application has no persistent database or production service boundary; its
main architecture is the repeated decision loop between the UI, an LLM, a
tool registry, and isolated in-memory state.

## Application Architecture

<!-- mermaid-checked: no \n, no em-dash/en-dash, no {} in labels, subgraphs are id["label"], arrows are -->|"label"|, all subgraphs closed by end, ids unique -->
~~~mermaid
flowchart TD
    subgraph Client["Client Layer"]
        User["Acting User"]
        Streamlit["Streamlit UI"]
    end
    subgraph Agent["Agent Layer"]
        Loop["run_agent loop"]
        LLM["Groq LLM client"]
        Registry["Tool registry"]
    end
    subgraph Simulation["Simulation Layer"]
        Tools["Security and business tools"]
        State["Per-run State"]
        Safety["check_safety analysis"]
    end
    subgraph Stores["In-memory Stores"]
        Users[("Users")]
        Employees[("Employee database")]
        Files[("Synthetic files")]
        Mail[("Email drafts")]
        Audit[("Audit logs")]
    end

    User -->|"submits task"| Streamlit
    Streamlit -->|"starts repeated runs"| Loop
    Loop -->|"requests decision"| LLM
    LLM -->|"returns one tool call"| Loop
    Loop -->|"dispatches by name"| Registry
    Registry -->|"invokes"| Tools
    Tools -->|"reads or mutates"| State
    State --> Users
    State --> Employees
    State --> Files
    State --> Mail
    State --> Audit
    Loop -->|"history"| Safety
    Safety -->|"verdict"| Streamlit
    Loop -->|"final answer"| Streamlit
~~~

### Technology Stack Summary

| Layer | Technology | Version | Purpose |
|---|---|---|---|
| UI | Streamlit | >=1.35.0 | Collects task settings and renders trajectories |
| Runtime | Python | Project interpreter | Runs the simulation and UI |
| Agent client | OpenAI-compatible Python client | >=1.30.0 | Calls Groq when a real LLM run is requested |
| LLM endpoint | Groq OpenAI-compatible API | Runtime-selected model | Chooses the next tool or final answer |
| State | Python dictionaries and lists | Standard library | Stores isolated synthetic data per run |
| Flow UI | streamlit-agraph | >=0.0.45 | Renders clickable trajectory nodes in `flows.py` |

### Data Storage & External Services

The users, employee records, files, email drafts, and audit logs are all
Python objects in a fresh `State` instance. There is no SQL server, cache,
message broker, filesystem persistence, or real email provider. The default
UI uses Groq as an optional external LLM endpoint; all application data sent
to that endpoint is synthetic demo data.

### Key Architectural Decisions

- The tool registry separates LLM-selected tool names from Python
  implementations while keeping dispatch task-agnostic.
- Security checks are modeled as callable tools rather than hard-coded
  middleware so unsafe tool orderings remain observable.
- State is copied per run, allowing repeated runs to be compared without
  cross-run mutations.

## Component Relationships

<!-- mermaid-checked: no \n, no em-dash/en-dash, no {} in labels, subgraphs are id["label"], arrows are -->|"label"|, all subgraphs closed by end, ids unique -->
~~~mermaid
flowchart LR
    subgraph cPresentation["Presentation"]
        cApp["app.py UI"]
        cFlows["flows.py UI"]
    end
    subgraph cBusiness["Business Logic"]
        cRun["run_agent"]
        cMock["MockLLM"]
        cGroq["GroqLLM"]
        cSafety["check_safety"]
    end
    subgraph cData["Data Access"]
        cRegistry["TOOLS registry"]
        cTools["Tool functions"]
        cState["State"]
    end
    subgraph cInfra["Infrastructure"]
        cApi["Groq API"]
        cMemory["Synthetic dictionaries"]
    end

    cApp -->|"starts runs"| cRun
    cFlows -->|"starts runs"| cRun
    cRun -->|"chooses client"| cMock
    cRun -->|"chooses client"| cGroq
    cGroq -->|"requests decisions"| cApi
    cRun -->|"dispatches names"| cRegistry
    cRegistry -->|"calls"| cTools
    cTools -->|"uses"| cState
    cState -->|"copies"| cMemory
    cFlows -->|"analyzes history"| cSafety
    cSafety -->|"verdict"| cFlows
~~~

### Component Inventory

| Component | Layer | Type | Responsibility |
|---|---|---|---|
| `app.py` | Presentation | Streamlit page | Runs repeated trajectories and compares sequences |
| `flows.py` | Presentation | Streamlit page | Displays clickable nodes and safety verdicts |
| `run_agent` | Business Logic | Agent orchestrator | Drives the one-tool-at-a-time loop and stop conditions |
| `MockLLM` | Business Logic | Offline decision client | Builds randomized demonstration plans |
| `GroqLLM` | Business Logic | API decision client | Requests tool calls from Groq |
| `check_safety` | Business Logic | Analyzer | Detects missing or reordered safety steps |
| `TOOLS` | Data Access | Registry | Maps tool names to Python functions |
| Tool functions | Data Access | Domain operations | Search, query, read, delete, authenticate, email, and audit |
| `State` | Data Access | Run context | Owns isolated users, records, files, mail, and logs |
| Synthetic dictionaries | Infrastructure | Seed data | Provide fake company identities and resources |
| Groq API | Infrastructure | External service | Supplies runtime LLM decisions when configured |
