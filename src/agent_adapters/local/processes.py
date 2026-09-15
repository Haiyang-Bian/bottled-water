"""Non-interactive processes with bounded output and owned process trees."""

import asyncio
import contextlib
import os
import shutil
import signal
import subprocess
import threading
import time
import math
from pathlib import Path
from uuid import uuid4

from agent_contracts.errors import ConfigurationError, OperationError

LIMIT = 65536


def executable(name, override=None):
    value = override or shutil.which(name)
    if not value or not Path(value).is_file():
        raise ConfigurationError(f"Required executable is unavailable: {name}")
    return str(Path(value).resolve())


def powershell_executable(override=None):
    return executable("pwsh", override or shutil.which("pwsh") or shutil.which("powershell"))


def filtered_environment(redactor, overrides=None):
    environment = dict(os.environ)
    environment.update(overrides or {})
    parts = ("api_key", "apikey", "secret", "password", "credential", "access_token", "auth_token")
    return {
        key: value
        for key, value in environment.items()
        if not any(part in key.lower() for part in parts)
        and not any(secret in value for secret in redactor.secrets)
    }


class LocalProcessDriver:
    capabilities = {
        "filesystem_isolation": False,
        "network_isolation": False,
        "process_tree_control": True,
    }

    def __init__(self, redactor):
        self.redactor = redactor
        self.jobs = set()
        self.processes = set()

    async def run(self, argv, cwd, *, timeout, context, env=None):
        context.check()
        deadline = min(context.deadline, time.monotonic() + timeout)
        if (
            not math.isfinite(timeout)
            or not math.isfinite(deadline)
            or timeout <= 0
            or deadline <= time.monotonic()
        ):
            raise OperationError("process_timeout", "Command deadline has expired")
        environment = filtered_environment(self.redactor, env)
        if os.name == "nt":
            return await self._windows(argv, cwd, deadline, context, environment)
        return await self._posix(argv, cwd, deadline, context, environment)

    async def _windows(self, argv, cwd, deadline, context, environment):
        import win32api
        import win32con
        import win32event
        import win32file
        import win32job
        import win32pipe
        import win32process
        import win32security
        import pywintypes

        job = win32job.CreateJobObject(None, "Local\\AgentHub-" + uuid4().hex)
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        handles = []
        threads = []
        output = [bytearray(), bytearray()]
        sizes = [0, 0]
        read_errors = []
        process = None
        assigned = False
        self.jobs.add(job)

        def read_pipe(handle, index):
            try:
                while True:
                    _, data = win32file.ReadFile(handle, 4096)
                    if not data:
                        break
                    sizes[index] += len(data)
                    output[index].extend(data[: max(0, LIMIT // 2 - len(output[index]))])
            except (OSError, pywintypes.error) as exc:
                if getattr(exc, "winerror", None) not in {109, 232, 995}:
                    read_errors.append(type(exc).__name__)

        try:
            security = win32security.SECURITY_ATTRIBUTES()
            security.bInheritHandle = True
            out_read, out_write = win32pipe.CreatePipe(security, 0)
            err_read, err_write = win32pipe.CreatePipe(security, 0)
            handles.extend((out_read, out_write, err_read, err_write))
            win32api.SetHandleInformation(out_read, win32con.HANDLE_FLAG_INHERIT, 0)
            win32api.SetHandleInformation(err_read, win32con.HANDLE_FLAG_INHERIT, 0)
            stdin = win32file.CreateFile(
                "NUL",
                win32con.GENERIC_READ,
                win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
                security,
                win32con.OPEN_EXISTING,
                0,
                None,
            )
            handles.append(stdin)
            startup = win32process.STARTUPINFO()
            startup.dwFlags = win32process.STARTF_USESTDHANDLES | win32process.STARTF_USESHOWWINDOW
            startup.wShowWindow = win32con.SW_HIDE
            startup.hStdInput, startup.hStdOutput, startup.hStdError = stdin, out_write, err_write
            flags = (
                win32process.CREATE_SUSPENDED
                | win32process.CREATE_NO_WINDOW
                | win32process.CREATE_UNICODE_ENVIRONMENT
            )
            process, thread, pid, _ = win32process.CreateProcess(
                argv[0],
                subprocess.list2cmdline(argv),
                None,
                None,
                True,
                flags,
                environment,
                str(cwd),
                startup,
            )
            handles.extend((process, thread))
            win32job.AssignProcessToJobObject(job, process)
            assigned = True
            win32process.ResumeThread(thread)
            # Parent copies must close before readers can observe EOF.
            for handle in (out_write, err_write, stdin):
                handle.Close()
                handles.remove(handle)
            for index, handle in enumerate((out_read, err_read)):
                worker = threading.Thread(target=read_pipe, args=(handle, index), daemon=True)
                worker.start()
                threads.append(worker)
            while win32event.WaitForSingleObject(process, 0) == win32con.WAIT_TIMEOUT:
                context.check()
                if time.monotonic() >= deadline:
                    raise OperationError("process_timeout", "Command exceeded its deadline")
                await asyncio.sleep(0.03)
            exit_code = win32process.GetExitCodeProcess(process)
        finally:
            if process is not None and not assigned:
                with contextlib.suppress(Exception):
                    win32process.TerminateProcess(process, 1)
            with contextlib.suppress(Exception):
                win32job.TerminateJobObject(job, 1)
            self.jobs.discard(job)
            job.Close()
            for worker in threads:
                worker.join(timeout=2)
            for handle in handles:
                with contextlib.suppress(Exception):
                    handle.Close()
        if read_errors:
            raise OperationError("pipe_read_error", "Could not collect complete process output")
        return {
            "exit_code": exit_code,
            "pid": pid,
            "stdout": self.redactor.text(output[0].decode("utf-8", errors="replace")),
            "stderr": self.redactor.text(output[1].decode("utf-8", errors="replace")),
            "truncated": any(sizes[i] > len(output[i]) for i in range(2)),
        }

    async def _posix(self, argv, cwd, deadline, context, environment):
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self.processes.add(process)
        outputs = [bytearray(), bytearray()]
        sizes = [0, 0]

        async def drain(stream, index):
            while data := await stream.read(4096):
                sizes[index] += len(data)
                outputs[index].extend(data[: max(0, LIMIT // 2 - len(outputs[index]))])

        readers = [
            asyncio.create_task(drain(process.stdout, 0)),
            asyncio.create_task(drain(process.stderr, 1)),
        ]
        try:
            while process.returncode is None:
                context.check()
                if time.monotonic() >= deadline:
                    raise OperationError("process_timeout", "Command exceeded its deadline")
                await asyncio.sleep(0.03)
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            await asyncio.gather(*readers, return_exceptions=True)
            self.processes.discard(process)
        return {
            "exit_code": process.returncode,
            "pid": process.pid,
            "stdout": self.redactor.text(outputs[0].decode("utf-8", errors="replace")),
            "stderr": self.redactor.text(outputs[1].decode("utf-8", errors="replace")),
            "truncated": any(sizes[i] > len(outputs[i]) for i in range(2)),
        }

    async def aclose(self):
        if os.name == "nt":
            import win32job

            for job in list(self.jobs):
                with contextlib.suppress(Exception):
                    win32job.TerminateJobObject(job, 1)
        else:
            for process in list(self.processes):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
