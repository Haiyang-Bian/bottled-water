"""Quiescent policy changes. Storage transactions do not span host communication."""

from agent_contracts.errors import OperationError
from .permission_ports import HostControl, StandingPolicyStore


async def change_policy(authority: StandingPolicyStore, target, expected_revision: int,
                        control: HostControl):
    identifier, target, affected = authority.begin(target, expected_revision)
    hosts = sorted({row["host"] for row in affected})
    frozen, retiring = [], False
    try:
        for host in hosts:
            await control(host, "freeze", identifier)
            frozen.append(host)
        authority.transition(identifier, "retiring", {"hosts": hosts})
        retiring = True
        for host in hosts:
            await control(host, "retire", identifier)
        result = authority.commit(identifier)
    except BaseException as exc:
        authority.transition(identifier, "repair_required" if retiring else "aborted",
                             {"reason": getattr(exc, "code", "permission_control_failed")})
        raise
    finally:
        errors = []
        for host in frozen:
            try:
                await control(host, "thaw", identifier)
            except Exception:
                errors.append(host)
        if errors and not retiring:
            raise OperationError("permission_control_failed", "空闲窗口尚未确认解除冻结。")
    return result
