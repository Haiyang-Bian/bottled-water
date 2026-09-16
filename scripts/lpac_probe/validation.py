"""Strict evidence checks for the P1 experiment, separate from release acceptance."""

import json


def _matrix(text, *, child=True, powershell=False):
    try:
        data = json.loads(text)
        if data["read_A"]["allowed"] is not True or data["read_A"]["value"] != "A-fixture":
            return False
        if data["write_B"]["allowed"] is not True:
            return False
        denied = ["write_A", "read_C"]
        if not powershell:
            denied.extend(["read_host_state", "write_runtime"])
        for name in denied:
            if data[name]["allowed"] is not False:
                return False
            if powershell:
                if data[name].get("hresult") != -2147024891:
                    return False
            elif data[name].get("errno") != 13:
                return False
        if child and not powershell:
            nested = data["child"]
            return nested["exit_code"] == 0 and _matrix(nested["stdout"], child=False)
        return True
    except (KeyError, TypeError, ValueError):
        return False


def evaluate(results, *, full=False):
    by_name = {result["name"]: result for result in results}
    checks = {"unique_results": len(by_name) == len(results)}

    def good(name):
        result = by_name.get(name, {})
        token = result.get("token", {})
        return (
            result.get("exit_code") == 0
            and not result.get("timed_out", False)
            and not result.get("error")
            and not result.get("pipe_errors")
            and not result.get("truncated", False)
            and result.get("job_drained") is True
            and all(token.get(key) is True for key in ("appcontainer", "lpac", "sid_matches"))
        )

    for name in ("read_A", "write_B"):
        checks[name] = good(name)
    checks["read_A_content"] = by_name.get("read_A", {}).get("stdout") == "A-fixture"
    for name in ("write_A", "read_C"):
        result = by_name.get(name, {})
        checks[name + "_denied"] = (
            result.get("exit_code") == 1
            and not result.get("timed_out", False)
            and result.get("job_drained") is True
            and result.get("stdout") == ""
        )
    if not full:
        return checks
    for name in ("pwsh_start", "git_start", "uv_start"):
        checks[name] = good(name)
    for name in ("python_matrix", "uv_matrix", "pwsh_matrix"):
        checks[name] = good(name) and _matrix(
            by_name[name]["stdout"], powershell=name == "pwsh_matrix"
        )
    checks["git_read_A"] = (
        good("git_read_A") and by_name["git_read_A"]["stdout"].strip() == "A-fixture"
    )
    checks["git_write_B"] = good("git_write_B")
    checks["git_diff_B"] = good("git_diff_B") and all(
        text in by_name["git_diff_B"]["stdout"] for text in ("-B-fixture", "+powershell-fixture")
    )
    for name in ("git_write_A", "git_read_C"):
        result = by_name.get(name, {})
        checks[name + "_denied"] = (
            checks["git_read_A"]
            and checks["git_write_B"]
            and result.get("exit_code") in (128, 255, 3, 4)
            and not result.get("timed_out", False)
            and result.get("job_drained") is True
            and "Permission denied" in result.get("stderr", "")
        )
    try:
        network = json.loads(by_name["python_network"]["stdout"])
        checks["network_denied"] = good("python_network") and all(
            network[name].get("allowed") is False and network[name].get("winerror") == 10013
            for name in ("ipv4_tcp", "ipv4_udp", "ipv6_tcp", "ipv6_udp")
        )
    except (KeyError, TypeError, ValueError):
        checks["network_denied"] = False
    checks["host_network_control"] = by_name.get("host_network_control", {}).get("passed") is True
    return checks
