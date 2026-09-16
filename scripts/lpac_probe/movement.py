"""Pure evidence checks for the movement experiment, not an authorization adapter."""

import json


def evaluate(outcome, policy_sid, *, moved=False):
    token = outcome.get("token", {})
    if not isinstance(token, dict):
        token = {}
    checks = {
        "launcher": (
            outcome.get("exit_code") == 0
            and outcome.get("timed_out") is False
            and outcome.get("cancelled") is False
            and outcome.get("truncated") is False
            and outcome.get("job_drained") is True
            and outcome.get("pipe_errors") == []
        ),
        "identity": all(token.get(key) is True for key in ("appcontainer", "lpac", "sid_matches"))
        and isinstance(token.get("capabilities"), list)
        and policy_sid in token["capabilities"],
    }
    try:
        data = json.loads(outcome.get("stdout", ""))
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    positive = {"read_work": "work", "read_archive": "archive", "write_work": 7}
    negative = {"write_archive", "read_private", "write_private"}
    if moved:
        positive.update({"read_moved_archive": "archive-move", "read_copied_archive": "copy"})
        negative |= {
            "write_moved_archive", "write_copied_archive", "read_moved_private",
            "write_moved_private", "read_copied_private", "write_copied_private",
            "read_detached_root", "write_detached_root", "read_moved_directory",
            "write_moved_directory",
        }
    for name, expected in positive.items():
        item = data.get(name, {})
        checks[name] = isinstance(item, dict) and (
            item.get("allowed") is True and item.get("value") == expected
        )
    for name in sorted(negative):
        item = data.get(name, {})
        checks[name] = isinstance(item, dict) and item.get("allowed") is False and (
            item.get("winerror") == 5 or item.get("errno") == 13
        )
    return checks


def bypasses(outcome, checks):
    """Separate actual unauthorized access from a missing file or failed launcher."""
    if not checks.get("launcher") or not checks.get("identity"):
        return []
    try:
        data = json.loads(outcome.get("stdout", ""))
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    forbidden = {
        "write_archive", "read_private", "write_private", "write_moved_archive",
        "write_copied_archive", "read_moved_private", "write_moved_private",
        "read_copied_private", "write_copied_private", "read_detached_root",
        "write_detached_root", "read_moved_directory", "write_moved_directory",
    }
    return sorted(name for name in forbidden if isinstance(data.get(name), dict)
                  and data[name].get("allowed") is True)


def subset_passed(report):
    """Require every phase, cleanup and original failure evidence, including stale reuse."""
    phases = {"baseline", "reused_after_children_moved", "reused_after_root_replaced",
              "fresh_generation"}
    checks = report.get("checks", {})
    return (
        not report.get("error") and not report.get("cleanup_error")
        and report.get("cleanup_verified") is True
        and report.get("unrelated_acl_preserved") is True
        and set(checks) == phases
        and all(items and all(value is True for value in items.values())
                for items in checks.values())
        and set(report.get("bypasses", {})) == phases
        and not any(report["bypasses"].values())
    )
