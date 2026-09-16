"""Explicit recovery of dead hosts; never kill a guessed PID or replay a full ACL."""

import json
import re
from pathlib import Path

from agent_contracts.errors import OperationError
from .windows_lpac import LpacProfile
from .windows_run_acl import RunAcl


def recover_runs(store, host, dependencies):
    import win32job
    from agent_adapters.storage.permissions import SQLitePermissions
    authority = SQLitePermissions(store)
    for row in store.db.execute(
        "SELECT run FROM permission_leases WHERE host=? AND state!='closed'", (host,)
    ).fetchall():
        run = row["run"]
        records = [json.loads(event[0]) for event in store.db.execute(
            "SELECT body FROM permission_events WHERE operation='run.isolation' ORDER BY sequence"
        )]
        records = [value for value in records if value.get("run") == run and value.get("host") == host]
        if not records:
            raise OperationError("permission_repair_required", "Run initialization evidence missing")
        path = Path(records[-1]["audit_path"])
        # The trusted recorded path must stay in this environment's managed area.
        if not path.resolve().is_relative_to((store.path.parent / "sandbox" / "runs").resolve()):
            raise OperationError("permission_repair_required", "Invalid isolation audit path")
        audit = json.loads(path.read_text(encoding="utf-8"))
        if audit.get("host_id") != host or audit.get("run_id") != run:
            raise OperationError("permission_repair_required", "Isolation audit identity mismatch")
        name = audit.get("job_name")
        if name:
            if not re.fullmatch(r"Local\\AgentHub-Run-[0-9a-f]{32}", name):
                raise OperationError("permission_repair_required", "Invalid owned Job name")
            try:
                job = win32job.OpenJobObject(4, False, name)
            except Exception as exc:
                if getattr(exc, "winerror", None) != 2:
                    raise
            else:
                try:
                    if win32job.QueryInformationJobObject(
                        job, win32job.JobObjectBasicAccountingInformation
                    )["ActiveProcesses"]:
                        raise OperationError("permission_repair_required", "Run Job is still active")
                finally:
                    job.Close()
        elif audit.get("state") not in {"creating_profile", "profile_created"}:
            raise OperationError("permission_repair_required", "Job evidence missing")
        if audit.get("grants"):
            expected_private = path.with_name(path.name.removesuffix("-acl.json"))
            if Path(audit["private"]) != expected_private:
                raise OperationError("permission_repair_required", "Run directory mismatch")
            allowed = {dependencies.root, expected_private}
            if any(Path(item["root"]) not in allowed or item["sid"] != audit["sid"]
                   for item in audit["grants"]):
                raise OperationError("permission_repair_required", "Run grant ownership mismatch")
            acl = RunAcl(audit["sid"], lambda _: None)
            acl.grants = audit["grants"]
            acl.close()
        if audit.get("profile_name") and audit.get("state") != "closed":
            profile = LpacProfile(audit["profile_name"])
            try:
                profile.delete()
            except OSError:
                raise OperationError("permission_repair_required", "Profile deletion unconfirmed")
        authority.finish(run, cleaned=True)
