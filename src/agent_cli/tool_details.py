"""Read saved tool results through the same scoped reader used by the harness."""

import json

from agent_subsystems.observability.redaction import Redactor
from .presentation import ToolView
from .selection import Choice, Selector, pager
from .sessions import SessionHistoryReader, short


async def browse_tools(controller, *, color=True):
    reader = SessionHistoryReader(controller.home, controller.redactor)
    choices, cursor = [], None
    while True:
        turns, cursor = reader.page(controller.session["id"], before=cursor, limit=30)
        if not turns:
            break
        choices.extend(Choice(t.run_id, f"{short(t.request, 60)} · {t.created} · {t.state}")
                       for t in turns)
    selected = await Selector(choices, "选择运行 · 只读工具记录", color=color).run()
    if selected is None:
        return
    tools = {}
    with reader.queries() as queries:
        for event in queries.events(controller.session["id"], selected):
            p = controller.redactor.value(event.get("payload", {}))
            if event.get("type") == "agent.tool_call":
                for call in p.get("calls", []):
                    fn = call.get("function", {})
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except ValueError:
                            args = {"unparsed": args}
                    tools[call.get("id")] = ToolView(call.get("id"), fn.get("name", "未知"), args)
            elif event.get("type") in {"agent.tool_started", "agent.tool_result"}:
                tool = tools.setdefault(p.get("call_id"), ToolView(p.get("call_id")))
                tool.name = p.get("tool", tool.name)
                if event["type"] == "agent.tool_result":
                    tool.success, tool.result = p.get("success"), p.get("result")
                    tool.error = p.get("error") or ""
    chosen = await Selector([Choice(k, v.summary()) for k, v in tools.items()],
                            "选择工具调用 · 不会重新执行", color=color).run()
    if chosen is None:
        return
    tool = tools[chosen]
    redactor = controller.redactor or Redactor()
    await pager(redactor.dumps({"tool": tool.name, "arguments": tool.arguments,
                               "success": tool.success, "error": tool.error}))
    if tool.success is None:
        await pager("该操作有开始记录，但未保存结果；结果未知。")
        return
    from agent_adapters.storage.sqlite import SQLiteStore
    from agent_subsystems.tools.history import JournalResultReader
    store = SQLiteStore(controller.home / "state.sqlite3", readonly=True)
    try:
        results = JournalResultReader(store, controller.session["id"])
        offset = 0
        while True:
            page = await results.read(selected, chosen, offset=offset)
            heading = f"保存记录 {page['offset']}—{page['offset'] + len(page['result_json'])} 字符"
            if page.get("record_truncated"):
                heading += " · 原结果已截断，未保存部分不可恢复"
            text = page["result_json"]
            if page["offset"] == 0 and page["next_offset"] is None:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            if not await pager(heading + "\n" + redactor.text(text)):
                break
            offset = page.get("next_offset")
            if offset is None:
                break
    finally:
        store.close()
