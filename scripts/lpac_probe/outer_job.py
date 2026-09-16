"""Launch a fixed diagnostic host inside a desktop-like kill-on-close Job."""

import subprocess
import time

from .jobs import OwnedJob


def run(argv, cwd, *, timeout=240, log_path=None):
    import win32api
    import win32con
    import win32event
    import win32job
    import win32process
    import win32file
    import win32security

    outer = OwnedJob()
    process = thread = None
    log = stdin = None
    try:
        startup = win32process.STARTUPINFO()
        if log_path is not None:
            attributes = win32security.SECURITY_ATTRIBUTES()
            attributes.bInheritHandle = True
            log = win32file.CreateFile(str(log_path), 0x40000000, 1, attributes, 1, 0, None)
            stdin = win32file.CreateFile("NUL", 0x80000000, 3, attributes, 3, 0, None)
            startup.dwFlags |= win32con.STARTF_USESTDHANDLES
            startup.hStdInput, startup.hStdOutput, startup.hStdError = stdin, log, log
        process, thread, _, _ = win32process.CreateProcess(
            argv[0], subprocess.list2cmdline(argv), None, None, log_path is not None,
            win32con.CREATE_SUSPENDED | win32con.CREATE_NO_WINDOW,
            None, str(cwd), startup)
        win32job.AssignProcessToJobObject(outer.handle, process)
        if not win32job.IsProcessInJob(process, outer.handle):
            raise RuntimeError("Diagnostic host is not in its outer Job")
        win32process.ResumeThread(thread)
        started = time.monotonic()
        while win32event.WaitForSingleObject(process, 50) == 258:
            if time.monotonic() - started > timeout:
                raise TimeoutError("Diagnostic host exceeded the bounded gate time")
        return {"exit_code": win32process.GetExitCodeProcess(process),
                "outer_host_membership_verified": True}
    finally:
        if process is not None and win32event.WaitForSingleObject(process, 0) == 258:
            win32api.TerminateProcess(process, 1)
        outer.close()
        if process is not None:
            process.Close()
        if thread is not None:
            thread.Close()
        if log is not None:
            log.Close()
        if stdin is not None:
            stdin.Close()
