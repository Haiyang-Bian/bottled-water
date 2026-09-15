"""异步多 Agent runtime 事件协议常量。"""

CONTROL_ASSIGN = "control.assign"
CONTROL_PAUSE = "control.pause"
CONTROL_RESUME = "control.resume"
CONTROL_CANCEL = "control.cancel"
CONTROL_COMPLETE = "control.complete"
CONTROL_SHUTDOWN = "control.shutdown"

AGENT_REPORT = "agent.report"
AGENT_TOKEN = "agent.token"
AGENT_TOOL_CALL = "agent.tool_call"
AGENT_TOOL_RESULT = "agent.tool_result"
AGENT_STATE_CHANGED = "agent.state_changed"
AGENT_FAILED = "agent.failed"

BLACKBOARD_UPDATED = "blackboard.updated"
USER_INPUT = "user.input"
SCHEDULER_DECISION = "scheduler.decision"
SCHEDULER_PLAN = "scheduler.plan"
SCHEDULER_SUMMARY = "scheduler.summary"

SYSTEM_SESSION_STARTED = "system.session_started"
SYSTEM_SESSION_COMPLETED = "system.session_completed"
SYSTEM_SESSION_CANCELLED = "system.session_cancelled"

CONTROL_EVENTS = {
    CONTROL_ASSIGN,
    CONTROL_PAUSE,
    CONTROL_RESUME,
    CONTROL_CANCEL,
    CONTROL_COMPLETE,
    CONTROL_SHUTDOWN,
}
