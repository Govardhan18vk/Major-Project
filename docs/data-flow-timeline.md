# SafeTask Data-Flow Timeline

This timeline follows one complete task execution from the acting user to the
final answer. The concrete tools and their order can change because the LLM
chooses the next tool after every result.

## End-to-end timeline

| Step | Layer | Data movement | State or result |
|---:|---|---|---|
| 1 | User | The acting user selects a preset or enters a task in Streamlit. | Task text and acting user are ready. |
| 2 | UI | `app.py` or `flows.py` calls `run_agent(task, llm, acting_user)`. | A fresh `State` is created for this run. |
| 3 | State initialization | `State` copies `USERS`, `EMPLOYEES`, and `FILES`. | Users, employee records, files, authenticated users, emails, and audit logs are isolated to the run. |
| 4 | Agent loop | The LLM receives the task plus the previous tool history. | It returns either one tool call or a final answer. |
| 5 | Tool dispatch | `run_agent` looks up the selected name in `TOOLS`. | The selected function receives the same `State` object. |
| 6 | Lookup path | Search tools read employee, user, or file dictionaries. | A structured result is returned, such as a match list or record. |
| 7 | Data path | `query_database` reads an employee field from the in-memory employee database. | Salary, department, role, or a not-found status is returned. |
| 8 | File path | `read_file` reads content and sensitivity; `delete_file` mutates the file dictionary. | Content is returned or the file is deleted. |
| 9 | Security path | `authenticate`, `check_permission`, and `check_recipient` inspect context. | Authentication, ALLOW/DENY, or INTERNAL/EXTERNAL is returned. These checks are tools, not automatic gates. |
| 10 | Communication path | `create_email` appends a draft and allocates an email ID. | The next LLM step can use that ID through `$LAST_EMAIL`. |
| 11 | External-action simulation | `send_email` changes a matching draft from `draft` to `sent`. | No real email is sent; the change exists only in the current run. |
| 12 | Audit path | `audit_log` appends action, user, and resource metadata. | The run has an audit record if the LLM selected this tool. |
| 13 | Feedback loop | The tool result is appended to `history` and sent back to the LLM. | The LLM chooses the next action using the updated trajectory. |
| 14 | Stop condition | The LLM returns `final_answer`, or the loop reaches `max_steps` (12). | A final answer or step-limit message is produced. |
| 15 | Safety analysis | `flows.py` may call `check_safety(history)`. | The history is classified SAFE or UNSAFE based on ordering and presence of checks. |
| 16 | UI output | The UI renders the task, trajectory, tool arguments, results, and final answer. | The user can compare different runs of the same task. |

## Branches in the timeline

### Successful or allowed path

`authenticate` -> `check_permission` -> lookup or sensitive action ->
`check_recipient` when sending -> `send_email` -> `audit_log` -> final answer.

### Unsafe path

The LLM may call `query_database`, `read_file`, `delete_file`, or
`send_email` before the corresponding check, or omit a check completely.
The tool itself can still perform the simulated action because the environment
intentionally demonstrates unsafe trajectories instead of enforcing them.

### Error path

If the LLM/API raises an exception, `run_agent` returns a `Stopped: LLM/API
error` final answer. If an individual tool raises an exception, the exception
is recorded as a result with an `error` field and the loop continues. If no
final answer arrives within 12 steps, the loop returns
`Stopped: reached step limit.`

## Data ownership and persistence

All data is process memory only. `State()` is recreated for every call to
`run_agent`; there is no disk database, network database, email provider, file
system access, or persistent audit store in this demo. The only network call
in the default UI is the optional Groq API request used to obtain an LLM
decision.
