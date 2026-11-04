"""
env/faults.py - seeded tool-fault injection.

A scenario may list faults, for example
    {"tool": "query_payments", "nth": 1, "type": "error"}
meaning "the first query_payments call fails". Real agents often improvise
after an error, and that is where many violations start. Faults are
deterministic (by call count), so a run can be repeated exactly.
"""
FAULT_RESULTS = {
    "error": {"status": "error", "error": "Service temporarily unavailable. Please try again."},
    "timeout": {"status": "error", "error": "Request timed out."},
    "empty": {"status": "ok", "result": "no data returned"},
}


def maybe_fault(state, tool):
    """Return a replacement result if a fault is due for this call, else None. The tool is not executed."""
    state.fault_counts[tool] = state.fault_counts.get(tool, 0) + 1
    for f in state.faults:
        if f["tool"] == tool and f.get("nth", 1) == state.fault_counts[tool]:
            state.last_fault = f["type"]
            return dict(FAULT_RESULTS[f["type"]])
    return None
