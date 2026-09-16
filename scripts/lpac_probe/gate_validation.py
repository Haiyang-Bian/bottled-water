"""Final gate includes administrator cleanup, not just successful child probes."""

REQUIRED = frozenset({"policies", "isolation", "objects", "toolchain",
                      "desktop_job_inherited", "run_jobs_closed", "symbolic_link"})


def assess(report, initialization):
    checks = report.get("checks", {})
    errors = []
    if (not report.get("namespace_experiment")
            or report["namespace_experiment"] != initialization.get("experiment")):
        errors.append("initialization_experiment_mismatch")
    if set(checks) != REQUIRED or any(value is not True for value in checks.values()):
        errors.append("required_native_check_missing_or_failed")
    if report.get("not_executed") or report.get("coverage_limits") or report.get("error"):
        errors.append("native_coverage_incomplete")
    targets = initialization.get("targets", [])
    if (initialization.get("status") != "cleaned" or initialization.get("cleanup_errors") != []
            or initialization.get("error") or len(targets) != 5
            or any(target.get("state") != "removed" for target in targets)):
        errors.append("system_initialization_cleanup_unconfirmed")
    if initialization.get("symbolic_fixture", {}).get("created") is not True:
        errors.append("symbolic_fixture_not_created")
    return {"gate": "not_passed" if errors else "passed", "gate_errors": errors}
