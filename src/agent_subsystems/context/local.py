"""Build local model input from committed history, bounded by whole turns."""

from agent_runtime.core.interfaces import AgentContextBuildResult


class LocalContextProvider:
    def __init__(self, workspace, max_history_chars=64000):
        self.workspace = workspace
        self.max_history_chars = max_history_chars

    async def build_agent_context(self, request):
        history = list(request.context_snapshot.messages) if request.context_snapshot else []
        original = len(history)
        budget = max(0, self.max_history_chars - len(request.base_user_prompt))
        while history and sum(len(m.get("content", "")) for m in history) > budget:
            history.pop(0)
            while history and history[0].get("role") != "user":
                history.pop(0)
        roots = "\n".join(str(p) for p in self.workspace.roots)
        system = request.base_system_prompt + (
            f"\nCurrent working directory: {self.workspace.root}\nAccessible file tool roots:\n{roots}\n"
            "PowerShell uses the current Windows user's permissions. No OS sandbox is provided. "
            "Use file.read hashes for edits, inspect actual command exit codes, and never claim "
            "tests passed without executing them. Do not commit, push or reset unless requested."
        )
        return AgentContextBuildResult(
            messages=[*history, {"role": "user", "content": request.base_user_prompt}],
            system_prompt=system,
            diagnostics={
                "history_messages": len(history),
                "dropped_messages": original - len(history),
            },
        )
