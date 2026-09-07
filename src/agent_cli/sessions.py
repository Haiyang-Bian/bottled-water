"""Human-facing session read models and lock-owning session controller."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from agent_adapters.storage.session_queries import SessionQueries, decode
from agent_adapters.storage.session_lock import SessionLock
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.errors import ConfigurationError
from agent_subsystems.observability.redaction import Redactor
from agent_subsystems.workspaces.paths import canonical_directory


def short(text, length=100):
    text = " ".join(str(text or "未保存请求").split())
    return text[:length] + ("…" if len(text) > length else "")


@dataclass(frozen=True)
class SessionSummary:
    id: str
    root: str
    title: str
    preview: str
    last_active: str
    run_count: int
    state: str
    reason_code: str
    last_run_id: str

    def label(self):
        try:
            date = datetime.fromisoformat(self.last_active).astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            date = self.last_active
        state = {"completed": "已完成", "failed": "失败", "cancelled": "已取消",
                 "running": "上次运行未结束"}.get(self.state, self.state)
        return f"{self.title} · {date} · {self.run_count} 轮 · {state}"


class SessionCatalogReader:
    def __init__(self, home, redactor=None):
        self.path = home / "state.sqlite3"
        self.redactor = redactor or Redactor()

    @contextmanager
    def queries(self):
        if not self.path.exists():
            yield None
            return
        store = SQLiteStore(self.path, readonly=True)
        try:
            yield SessionQueries(store.db)
        finally:
            store.close()

    def list(self, root=None):
        with self.queries() as queries:
            rows = queries.catalog(root) if queries else []
        return [SessionSummary(
            id=r["id"], root=r["root"],
            title=short(self.redactor.text(str(decode(r["first_request"]).get("input", ""))), 60),
            preview=short(self.redactor.text(str(decode(r["last_request"]).get("input", "")))),
            last_active=r["last_active"], run_count=r["run_count"], state=r["state"],
            reason_code=decode(r["result"]).get("reason_code", "未知"),
            last_run_id=r["last_run_id"],
        ) for r in rows]

    def resolve(self, identifier, root):
        with self.queries() as queries:
            session = queries.session(identifier) if queries else None
            if session is None:
                scope = queries.run_scope(identifier) if queries else None
                if scope:
                    raise ConfigurationError(
                        f"这是 Run ID，不是会话 ID。所属会话：{scope}；请用 agenthub -r 从列表选择。"
                    )
                raise ConfigurationError("会话不存在。请用 agenthub -r 从列表选择。")
        if session["root"] != str(root):
            raise ConfigurationError(
                f"会话属于其他目录：{session['root']}。请切换到该目录后运行 agenthub -r。"
            )
        return session


@dataclass
class HistoryTurn:
    run_id: str
    created: str
    request: str
    output: str
    state: str
    reason_code: str
    tools: list

    def text(self):
        partial = "（未完成输出）" if self.state != "completed" else ""
        lines = [f"{self.created} · {self.state} / {self.reason_code}",
                 f"你：{self.request}", f"AgentHub{partial}：{self.output or '未保存答复'}"]
        for tool in self.tools:
            status = "结果未知" if tool.get("success") is None else (
                "成功" if tool["success"] else "失败"
            )
            lines.append(f"  工具 {tool.get('tool', '未知')} · {status}")
        return "\n".join(lines)


class SessionHistoryReader(SessionCatalogReader):
    def page(self, scope, *, before=None, limit=3):
        with self.queries() as queries:
            rows = queries.runs(scope, before=before, limit=limit) if queries else []
            turns = []
            for row in rows:
                result = decode(row["result"])
                texts, tools, seen = {}, {}, set()
                for event in queries.events(scope, row["id"]):
                    sequence = event.get("sequence")
                    if sequence in seen:
                        continue
                    seen.add(sequence)
                    payload = event.get("payload", {})
                    kind = event.get("type")
                    if kind == "agent.token":
                        key = payload.get("agent_message_id", "legacy")
                        texts[key] = texts.get(key, "") + payload.get("token", "")
                    elif kind == "agent.tool_started":
                        tools.setdefault(payload.get("call_id"), payload)
                    elif kind == "agent.tool_result":
                        tools[payload.get("call_id")] = payload
                turns.append(HistoryTurn(
                    row["id"], row["created"],
                    self.redactor.text(str(decode(row["request"]).get("input", "未保存请求"))),
                    self.redactor.text(result.get("output") or "\n\n".join(texts.values())),
                    row["state"], result.get("reason_code", "未知"), list(tools.values()),
                ))
        cursor = (rows[-1]["created"], rows[-1]["id"]) if rows else None
        return turns, cursor


class SessionController:
    def __init__(self, home, root, ensure_trusted, redactor=None):
        self.home, self.root = home, root
        self.ensure_trusted = ensure_trusted
        self.redactor = redactor or Redactor()
        self.catalog = SessionCatalogReader(home, self.redactor)
        self.store = None
        self.lock = None
        self.session = {"id": None, "root": str(root), "dirs": []}

    def writable(self):
        if self.store is None:
            self.store = SQLiteStore(self.home / "state.sqlite3", self.redactor)
        return self.store

    def new(self):
        if self.lock:
            self.lock.__exit__()
        self.lock = None
        self.session = {"id": None, "root": str(self.root), "dirs": []}

    def directories(self, session, additional=()):
        store = self.writable()
        self.ensure_trusted(store, self.root)
        directories = []
        for name in [*session["dirs"], *additional]:
            path = canonical_directory(name)
            self.ensure_trusted(store, path)
            if path != self.root and str(path) not in directories:
                directories.append(str(path))
        return directories

    async def activate(self, identifier, additional=()):
        session = self.catalog.resolve(identifier, self.root)
        if self.session["id"] == identifier:
            return
        store = self.writable()
        target = SessionLock(self.home / "locks", identifier)
        target.__enter__()
        try:
            session = self.catalog.resolve(identifier, self.root)
            directories = self.directories(session, additional)
            await store.recover_session(identifier)
            if directories != session["dirs"]:
                store.set_directories(identifier, directories)
            session["dirs"] = directories
        except BaseException:
            target.__exit__()
            raise
        previous = self.lock
        self.session, self.lock = session, target
        if previous:
            previous.__exit__()

    def materialize(self):
        if self.session["id"] is not None:
            return self.session
        directories = self.directories(self.session)
        session = self.writable().new_session(self.root)
        target = SessionLock(self.home / "locks", session["id"])
        target.__enter__()
        try:
            if directories:
                self.store.set_directories(session["id"], directories)
            session["dirs"] = directories
        except BaseException:
            target.__exit__()
            raise
        self.session, self.lock = session, target
        return session

    def close(self):
        if self.lock:
            self.lock.__exit__()
        if self.store:
            self.store.close()
