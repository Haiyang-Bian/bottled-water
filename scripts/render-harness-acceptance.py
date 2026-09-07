"""Render selected observed acceptance facts; retain failed attempts separately."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    first = json.loads((ROOT / "var/live-015-deepseek-final.json").read_text(encoding="utf-8"))
    recovery = json.loads(
        (ROOT / "var/live-015-deepseek-repair-recovery.json").read_text(encoding="utf-8")
    )
    assert recovery["status"] == "passed"
    selected = first["records"][:6] + recovery["records"]
    wheel = ROOT / "dist/agenthub_system-0.1.5-py3-none-any.whl"
    checksum = hashlib.sha256(wheel.read_bytes()).hexdigest()
    lines = [
        "# AgentHub 0.1.5 安装与真实服务验收",
        "",
        "2026-09-07；源码标签 `agenthub-v0.1.5`，最终源码提交与逐版 SHA 见 `dist/harness-releases.json`。",
        f"验收 wheel：`{wheel.name}`；SHA-256：`{checksum}`。",
        "",
        f"实际安装：`{recovery['installed_version']}`；Provider `{recovery['provider']}`；模型 `{recovery['model']}`。",
        "使用现有显式 `default` profile，凭据只解析使用。所有项目、Git 仓库和状态目录均为独立临时目录。",
        "",
        "| 任务 | Run ID | 终态 / 原因 | 退出码 | 秒 | 已知输入 / 输出 token |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in selected:
        final = item.get("result") or {}
        terminal = item.get("recovered_terminal", {}).get("payload", {})
        run_id = final.get("run_id") or item.get("run_id")
        state = final.get("state") or ("failed" if terminal else "unknown")
        reason = final.get("reason_code") or terminal.get("reason_code", "unknown")
        usage = final.get("usage")
        if usage is None:
            usage = (
                next(
                    (
                        e["payload"].get("run_usage")
                        for e in reversed(item["events"])
                        if e["type"] == "execution.usage"
                    ),
                    {},
                )
                or {}
            )
        counters = final.get("counters")
        item["accepted_usage"] = usage
        if item["task"] not in {"cancel", "crash", "injected_transport_outage"}:
            assert item["exit_code"] == 0 and state == "completed"
        if item["task"] in {"cancel", "crash"}:
            assert item["process_cleanup_verified"] and item["no_replay_verified"]
        lines.append(
            f"| {item['task']} | `{run_id}` | `{state}/{reason}` | {item['exit_code']} | "
            f"{item['elapsed_seconds']:.2f} | {usage.get('prompt_tokens', '未知')} / "
            f"{usage.get('completion_tokens', '未知')} |"
        )
        item["accepted_run_id"] = run_id
        item["accepted_counters"] = counters
    for task in (
        "repair_and_test",
        "restart_followup",
        "cancel_continuation",
        "crash_continuation",
    ):
        item = next(r for r in selected if r["task"] == task)
        invocations = {}
        for event in item["events"]:
            if event["type"] == "agent.tool_call":
                for call in event["payload"].get("calls", []):
                    invocations[call["id"]] = call["function"]
        tests = [
            e
            for e in item["events"]
            if e["type"] == "agent.tool_result"
            and e["payload"].get("tool") == "powershell.run"
            and e["payload"].get("result", {}).get("exit_code") == 0
            and "unittest" in invocations.get(e["payload"].get("call_id"), {}).get("arguments", "")
        ]
        assert tests, f"No observed successful test command: {task}"
        item["actual_test_commands_verified"] = True
    lines += [
        "",
        "工具开始、结果、退出码、计数和完整用量细分保存在 `var/harness-015-accepted.json`。",
        "崩溃行用量来自最后一条已确认请求事件，仅为已知小计；取消或连接中断后的未报告消耗不能视为零。缓存 token 是输入的细分，不重复求和，也未估算费用。",
        "",
        "## 确定性与安装证据",
        "",
        "- 共享测试：53 通过、3 按独立入口跳过，`var/harness-015-final.xml`。",
        "- 已安装 CLI：7 通过，`var/install-015-final.log`。",
        "- Web / Runtime / 桌面入口：131 通过、1 缺少凭据跳过，`var/web-015.xml`。",
        "- 从真实 0.1.0 wheel 创建旧状态并升级：1 通过，`var/upgrade-015.xml`；配置、DPAPI 引用、信任、历史保持，旧二进制拒绝 v2。",
        "- 桌面 sidecar 构建、实际 Alembic 迁移及启动通过，`var/sidecar-015-final-build.log`、`var/sidecar-015-final-smoke.log`。",
        "",
        "## 负结果与未执行项",
        "",
        "- 首轮 DeepSeek 取消记录报告被模型自报告为 blocked；提示明确当前任务后，取消报告通过。另一次单纯描述崩溃记录仍被自报告为 failed。原始失败保留在 `var/live-015-deepseek.json` 和 `var/live-015-deepseek-recovery.json`，不改写为成功。最终通过的是带明确文件修复和测试目标的续接；不宣称所有描述性任务的语义判定已经可靠。",
        "- 第二轮验收脚本把只读 PowerShell 核实误判为重放；已检查实际参数，仅执行读取与 Get-Process。修正后通过 PID 文件修改时间、命令内容和实际进程句柄验证，允许只读核实。前六项成功来自 `var/live-015-deepseek-final.json`，恢复与故障来自 `var/live-015-deepseek-repair-recovery.json`，均使用上述同一 wheel。",
        "- 全共享测试首次有一项超过脚本 45 秒外层等待；未确认根因。外层期限改为覆盖产品 120 秒请求期限，最终独立安装复验通过；产品请求超时未放宽。",
        "- 0.1.4 的 Web 迁移接在较早 revision 导致多 head，最终桌面验收发现后在 0.1.5 修正，并增加单 head 检查。0.1.4 的 SQL 功能回归不等同于部署迁移验收。",
        "- 注入连接故障使用真实适配器和不可连接的本地地址，属于受控故障，不是 DeepSeek 服务发生中断的证据。",
        "- OpenAI-compatible：无显式 profile，真实验收未执行；兼容 SDK 的本地 HTTP 替身测试另列，不能代替真实服务。",
        "- Docker：客户端存在，但 Docker Desktop Linux engine 未运行，未执行构建或启动。",
        "",
    ]
    target = ROOT / "docs/acceptance/harness-0.1.5.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    (ROOT / "var/harness-015-accepted.json").write_text(
        json.dumps(
            {
                "version": "0.1.5",
                "tag": "agenthub-v0.1.5",
                "wheel_sha256": checksum,
                "provider": recovery["provider"],
                "model": recovery["model"],
                "records": selected,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(target)


if __name__ == "__main__":
    main()
