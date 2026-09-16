"""Owned parent Jobs; no breakaway or privilege changes."""

import time
from uuid import uuid4


class OwnedJob:
    def __init__(self):
        import win32job

        self.handle = win32job.CreateJobObject(None, "Local\\AgentHub-Probe-" + uuid4().hex)
        value = win32job.QueryInformationJobObject(
            self.handle, win32job.JobObjectExtendedLimitInformation)
        value["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(
            self.handle, win32job.JobObjectExtendedLimitInformation, value)

    def terminate(self):
        import win32job

        win32job.TerminateJobObject(self.handle, 1)

    def active(self):
        import win32job

        return win32job.QueryInformationJobObject(
            self.handle, win32job.JobObjectBasicAccountingInformation)["ActiveProcesses"]

    def close(self):
        if self.handle is None:
            return
        self.terminate()
        deadline = time.monotonic() + 5
        while self.active():
            if time.monotonic() >= deadline:
                raise RuntimeError("Parent Job exit was not confirmed")
            time.sleep(0.01)
        self.handle.Close()
        self.handle = None
