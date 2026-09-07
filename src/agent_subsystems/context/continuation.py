"""Rebuild one bounded fragment from durable observations; never replay operations."""

import json


class JournalContinuationReader:
    def __init__(self, journal, max_chars=8000):
        self.journal = journal
        self.max_chars = max_chars

    async def read(self, snapshot):
        cursors = dict(snapshot.continuation.get("cursors", {}))
        fragments = []
        omitted = 0
        for run in await self.journal.list_scope_runs(snapshot.scope_id):
            if run.scope_id != snapshot.scope_id or run.state not in {"failed", "cancelled"}:
                continue
            if cursors.get(run.run_id, -1) >= run.sequence:
                continue
            cursor, calls = 0, {}
            while True:
                page = await self.journal.read_events(run.run_id, after_sequence=cursor)
                for event in page.items:
                    if event.context_scope_id != snapshot.scope_id:
                        raise ValueError("Continuation event scope mismatch")
                    payload = event.payload
                    call_id = payload.get("call_id")
                    if event.type == "agent.tool_call":
                        for call in payload.get("calls", []):
                            identifier = call.get("id") or call.get("call_id")
                            if identifier:
                                calls.setdefault(
                                    identifier,
                                    {
                                        "call_id": identifier,
                                        "tool": call.get("function", {}).get("name"),
                                        "status": "unknown",
                                    },
                                )
                    if call_id and event.type == "agent.tool_started":
                        calls[call_id] = {
                            "call_id": call_id,
                            "tool": payload.get("tool"),
                            "status": "unknown",
                            "started": True,
                        }
                    if call_id and event.type == "agent.tool_result":
                        result = payload.get("result") or {}
                        if not isinstance(result, dict):
                            result = {"result": str(result)[:200]}
                        data = result
                        if not isinstance(data, dict):
                            data = {}
                        calls[call_id] = {
                            "call_id": call_id,
                            "tool": payload.get("tool"),
                            "status": (
                                "completed"
                                if payload.get("success") is True
                                else "failed"
                                if payload.get("success") is False
                                else "unknown"
                            ),
                            "facts": {
                                key: data[key]
                                for key in (
                                    "path",
                                "hash",
                                "sha256",
                                "bytes_written",
                                "sha256",
                                "bytes_written",
                                    "exit_code",
                                    "created",
                                    "replaced",
                                    "truncated",
                                    "timed_out",
                                    "cancelled",
                                )
                                if key in data
                            },
                            "error": str(payload.get("error") or "")[:200],
                            "result_ref": {"run_id": run.run_id, "call_id": call_id},
                        }
                cursor = page.next_sequence
                if cursor >= page.last_sequence or not page.items:
                    break
            fragment = {
                "run_id": run.run_id,
                "through_sequence": run.sequence,
                "request": run.request[:1200],
                "request_truncated": len(run.request) > 1200,
                "stop": run.reason_code or "unknown",
                "state": run.state,
                "history_complete": run.history_complete,
                "operations": list(calls.values()),
            }
            # Prioritize recent Runs and recent operation metadata within an explicit bound.
            while (
                len(json.dumps(fragment, ensure_ascii=False)) > self.max_chars - 500
                and fragment["operations"]
            ):
                fragment["operations"].pop(0)
                fragment["operations_omitted"] = fragment.get("operations_omitted", 0) + 1
            fragments.append(fragment)
            cursors[run.run_id] = run.sequence
            while len(json.dumps(fragments, ensure_ascii=False)) > self.max_chars - 300:
                fragments.pop(0)
                omitted += 1
        summary = (
            json.dumps(
                {"observations": fragments, "older_runs_omitted": omitted}, ensure_ascii=False
            )
            if fragments
            else ""
        )
        return {"cursors": cursors, "summary": summary}
