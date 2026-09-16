"""Human-facing session read models and lock-owning session controller."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agent_adapters.storage.session_queries import decode
from agent_adapters.storage.session_lock import SessionLock
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.errors import ConfigurationError, OperationError
from agent_contracts.execution import ExecutionLocation, WorkspaceSpec
from agent_subsystems.observability.redaction import Redactor
from agent_subsystems.workspaces.paths import canonical_directory, effective_roots, resolve_resource


def short(text, length=100):
    text = " ".join(str(text or "未保存请求").split())
    return text[:length] + ("…" if len(text) > length else "")


@dataclass(frozen=True)
class SessionSummary:
    id: str
    origin_root: str
    cwd: str
    workspace_version: int
    environment_id: str | None
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
            yield store.queries()
        finally:
            store.close()

    def list(self, root=None, query="", *, since=None, until=None):
        with self.queries() as queries:
            rows = queries.catalog(root) if queries else []
        if query or since or until:
            from agent_adapters.storage.tasks import TaskCatalog
            if self.path.exists():
                store = SQLiteStore(self.path, self.redactor, readonly=True)
                try:
                    rows = TaskCatalog(store).rows(query, root=root, since=since, until=until)
                finally:
                    store.close()
        return [SessionSummary(
            id=r["id"], origin_root=r["origin_root"], cwd=r["cwd"],
            workspace_version=r["workspace_version"], environment_id=r["environment_id"],
            title=short(self.redactor.text(str(decode(r["first_request"]).get("input", ""))), 60),
            preview=short(self.redactor.text(str(decode(r["last_request"]).get("input", "")))),
            last_active=r["last_active"], run_count=r["run_count"], state=r["state"],
            reason_code=decode(r["result"]).get("reason_code", "未知"),
            last_run_id=r["last_run_id"],
        ) for r in rows]

    def resolve(self, identifier):
        with self.queries() as queries:
            session = queries.session(identifier) if queries else None
            if session is None:
                scope = queries.run_scope(identifier) if queries else None
                if scope:
                    raise ConfigurationError(
                        f"这是 Run ID，不是会话 ID。所属会话：{scope}；请用 agenthub -r 从列表选择。"
                    )
                raise ConfigurationError("会话不存在。请用 agenthub -r 从列表选择。")
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
    cwd: str = "未保存"

    def text(self):
        partial = "（未完成输出）" if self.state != "completed" else ""
        lines = [f"{self.created} · {self.state} / {self.reason_code} · 位置：{self.cwd}",
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
                    decode(row["request"]).get("metadata", {}).get(
                        "execution_location", {}
                    ).get("cwd", "未保存"),
                ))
        cursor = (rows[-1]["created"], rows[-1]["id"]) if rows else None
        return turns, cursor


class SessionController:
    def __init__(self, home, root, ensure_trusted, redactor=None):
        self.home, self.startup_root = home, root
        self.ensure_trusted = ensure_trusted
        self.redactor = redactor or Redactor()
        self.catalog = SessionCatalogReader(home, self.redactor)
        self.store = None
        self.lock = None
        self.running = False
        self.session = self._draft(root, [str(root)])

    @staticmethod
    def _draft(cwd, roots):
        return {"id": None, "environment_id": None, "origin_root": str(cwd), "cwd": str(cwd),
                "granted_roots": list(roots), "workspace_version": 0,
                "execution_mode": "current_user",
                "permission_selection": {"mode": "inherit", "roots": []}}

    def _idle(self):
        if self.running:
            raise ConfigurationError("任务运行期间不能切换任务、位置或授权。请先取消当前 Run。")

    def writable(self):
        if self.store is None:
            self.store = SQLiteStore(self.home / "state.sqlite3", self.redactor)
        return self.store

    def new(self):
        self._idle()
        if self.lock:
            self.lock.__exit__()
        self.lock = None
        previous = self.session
        self.session = self._draft(previous["cwd"], previous["granted_roots"])
        for name in ("execution_mode", "permission_selection"):
            self.session[name] = previous[name]

    def permission_snapshot(self, session=None):
        from dataclasses import replace
        from agent_adapters.storage.permissions import SQLitePermissions
        from agent_subsystems.workspaces.permissions import freeze_policy
        from agent_subsystems.workspaces.permission_records import selection_from_dict
        session = session or self.session
        from .permissions import mandatory_rules
        policy = SQLitePermissions(self.writable()).load()
        mandatory = {rule.path: rule for rule in (*policy.mandatory, *mandatory_rules(self.home))}
        policy = replace(policy, mandatory=tuple(mandatory.values()))
        return freeze_policy(policy,
                             selection_from_dict(session["permission_selection"]))

    def default_mode(self):
        path = self.home / "state.sqlite3"
        if path.exists():
            from agent_adapters.storage.permissions import SQLitePermissions
            store = SQLiteStore(path, readonly=True)
            try:
                if store.schema_version >= 3 and SQLitePermissions(store).windows_default():
                    self.session["execution_mode"] = "windows_lpac"
            finally:
                store.close()

    def _validate(self, session, *, trusted=True):
        cwd = canonical_directory(session["cwd"])
        if str(cwd) != session["cwd"]:
            raise ConfigurationError("保存位置的实际路径已改变，请显式重新选择位置。")
        if session.get("execution_mode") == "windows_lpac":
            from agent_subsystems.workspaces.permissions import authorize_path
            if not authorize_path(self.permission_snapshot(session), cwd, "read").allowed:
                raise ConfigurationError("保存位置不在长期权限范围内；使用 --cwd 选择已授权位置。")
            return []
        check = self.writable().is_trusted if trusted else lambda _: True
        roots, inactive = effective_roots(session["granted_roots"], check)
        resolve_resource(WorkspaceSpec(roots), ExecutionLocation(cwd), ".", directory=True)
        return inactive

    def _prepare(self, session, additional, cwd, base, execution_options=None, access="read"):
        candidate = {**session, "granted_roots": list(session["granted_roots"])}
        if execution_options:
            from agent_subsystems.workspaces.permission_records import document
            from agent_contracts.permissions import TaskPermissionSelection, PathPermission
            import json
            mode = execution_options.get("sandbox")
            if mode:
                candidate["execution_mode"] = (
                    "windows_lpac" if mode == "windows" else "current_user"
                )
                if mode == "windows" and session.get("execution_mode") != "windows_lpac":
                    candidate["permission_selection"] = {"mode": "inherit", "roots": []}
            selection = execution_options.get("permissions")
            reads, writes = execution_options.get("read_dir", []), execution_options.get("write_dir", [])
            if reads or writes:
                if selection != "custom":
                    raise ConfigurationError("--read-dir/--write-dir 需要 --permissions custom。")
            if selection:
                if candidate["execution_mode"] != "windows_lpac":
                    raise ConfigurationError("权限选择需要 --sandbox windows。")
                rules = {canonical_directory(p, base=base): level
                         for level, paths in (("read", reads), ("modify", writes)) for p in paths}
                candidate["permission_selection"] = json.loads(document(TaskPermissionSelection(
                    selection, tuple(PathPermission(p, level) for p, level in rules.items()),
                )))
        for name in additional:
            path = canonical_directory(name, base=base)
            if candidate.get("execution_mode") == "windows_lpac":
                from agent_subsystems.workspaces.permissions import authorize_path, freeze_policy
                from agent_adapters.storage.permissions import SQLitePermissions
                if not authorize_path(freeze_policy(SQLitePermissions(self.writable()).load()),
                                      path, "modify" if access == "modify" else "read").allowed:
                    raise ConfigurationError("目录未获长期授权；请使用 permissions grant。")
                selected = candidate["permission_selection"]
                if selected["mode"] == "custom":
                    roots = [r for r in selected["roots"] if r["path"] != str(path)]
                    candidate["permission_selection"] = {"mode": "custom", "roots": [
                        *roots, {"path": str(path), "access": access},
                    ]}
                continue
            self.ensure_trusted(self.writable(), path)
            if str(path) not in candidate["granted_roots"]:
                candidate["granted_roots"].append(str(path))
        if cwd is not None:
            candidate["cwd"] = str(canonical_directory(cwd, base=base))
        self._validate(candidate, trusted=session["id"] is not None)
        return candidate

    def _save(self, candidate):
        if candidate["id"] is None:
            self.session = candidate
        else:
            self.session = self.writable().update_workspace(
                candidate["id"], cwd=candidate["cwd"], granted_roots=candidate["granted_roots"],
                expected_version=candidate["workspace_version"], lock=self.lock,
                execution_mode=candidate["execution_mode"],
                permission_selection=candidate["permission_selection"],
            )

    def configure(self, additional=(), cwd=None, *, startup=False,
                  execution_options=None, access="read"):
        self._idle()
        base = self.startup_root if startup else Path(self.session["cwd"])
        self._save(self._prepare(self.session, additional, cwd, base, execution_options, access))

    async def activate(self, identifier, additional=(), cwd=None, *, execution_options=None):
        self._idle()
        self.catalog.resolve(identifier)
        store = self.writable()
        same = self.session["id"] == identifier
        target = self.lock if same else SessionLock(self.home / "locks", identifier)
        if not same:
            target.__enter__()
        try:
            session = self.catalog.resolve(identifier)
            try:
                candidate = self._prepare(session, additional, cwd, self.startup_root, execution_options)
            except (OSError, ValueError, ConfigurationError, OperationError) as exc:
                raise ConfigurationError(
                    f"无法恢复保存位置：{session['cwd']}。"
                    f"只读查看：agenthub history {identifier}；修复：agenthub --resume {identifier} "
                    '--add-dir "有效目录" --cwd "有效目录"。' + str(exc)
                ) from exc
            await store.recover_session(identifier)
            session = store.update_workspace(
                identifier, cwd=candidate["cwd"], granted_roots=candidate["granted_roots"],
                expected_version=session["workspace_version"], lock=target,
                execution_mode=candidate["execution_mode"],
                permission_selection=candidate["permission_selection"],
            )
        except BaseException:
            if not same:
                target.__exit__()
            raise
        previous = self.lock
        self.session, self.lock = session, target
        if previous and not same:
            previous.__exit__()

    def materialize(self):
        self._idle()
        if self.session["id"] is not None:
            self._validate(self.session)
            return self.session
        cwd = canonical_directory(self.session["cwd"])
        for name in (self.session["granted_roots"]
                     if self.session["execution_mode"] == "current_user" else []):
            if cwd.is_relative_to(Path(name)):
                self.ensure_trusted(self.writable(), canonical_directory(name))
                break
        self._validate(self.session)
        session = self.writable().new_session(
            self.session["origin_root"], cwd=cwd, granted_roots=self.session["granted_roots"],
            execution_mode=self.session["execution_mode"],
            permission_selection=self.session["permission_selection"],
        )
        target = SessionLock(self.home / "locks", session["id"])
        target.__enter__()
        self.session, self.lock = session, target
        return session

    @contextmanager
    def execution(self):
        session = self.materialize()
        self.running = True
        try:
            yield {**session, "granted_roots": list(session["granted_roots"])}
        finally:
            self.running = False

    def close(self):
        if self.lock:
            self.lock.__exit__()
        if self.store:
            self.store.close()
