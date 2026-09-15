"""Experimental native LPAC primitives. Not connected to production execution.

No fallback to a normal token. Caller owns the explicit ACL grants and recovery
ledger; this module does not grant access to any host directory automatically.
"""

import ctypes
from ctypes import wintypes as w
import os
import subprocess
import threading
import time
import re
from uuid import uuid4


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", w.LPVOID), ("Attributes", w.DWORD)]


class SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", w.LPVOID),
        ("Capabilities", ctypes.POINTER(SID_AND_ATTRIBUTES)),
        ("CapabilityCount", w.DWORD),
        ("Reserved", w.DWORD),
    ]


class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("lpReserved", w.LPWSTR),
        ("lpDesktop", w.LPWSTR),
        ("lpTitle", w.LPWSTR),
        ("dwX", w.DWORD),
        ("dwY", w.DWORD),
        ("dwXSize", w.DWORD),
        ("dwYSize", w.DWORD),
        ("dwXCountChars", w.DWORD),
        ("dwYCountChars", w.DWORD),
        ("dwFillAttribute", w.DWORD),
        ("dwFlags", w.DWORD),
        ("wShowWindow", w.WORD),
        ("cbReserved2", w.WORD),
        ("lpReserved2", ctypes.POINTER(w.BYTE)),
        ("hStdInput", w.HANDLE),
        ("hStdOutput", w.HANDLE),
        ("hStdError", w.HANDLE),
    ]


class STARTUPINFOEX(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFO), ("lpAttributeList", w.LPVOID)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", w.HANDLE),
        ("hThread", w.HANDLE),
        ("dwProcessId", w.DWORD),
        ("dwThreadId", w.DWORD),
    ]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", w.WORD), ("MaximumLength", w.WORD), ("Buffer", w.LPVOID)]


class TOKEN_SECURITY_ATTRIBUTE(ctypes.Structure):
    _fields_ = [
        ("Name", UNICODE_STRING),
        ("ValueType", w.WORD),
        ("Reserved", w.WORD),
        ("Flags", w.DWORD),
        ("ValueCount", w.DWORD),
        ("Values", w.LPVOID),
    ]


class TOKEN_SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Version", w.WORD),
        ("Reserved", w.WORD),
        ("AttributeCount", w.DWORD),
        ("Attributes", ctypes.POINTER(TOKEN_SECURITY_ATTRIBUTE)),
    ]


def _lpac_attribute(token):
    # The LPAC launch attribute materializes as this kernel security attribute.
    # Google Project Zero's NtToken uses the same affirmative UInt64 check.
    query = _function(
        _dll("ntdll"),
        "NtQueryInformationToken",
        ctypes.c_long,
        w.HANDLE,
        ctypes.c_int,
        w.LPVOID,
        w.DWORD,
        ctypes.POINTER(w.DWORD),
    )
    size = w.DWORD()
    query(int(token), 39, None, 0, ctypes.byref(size))
    if not ctypes.sizeof(TOKEN_SECURITY_ATTRIBUTES) <= size.value <= 1024 * 1024:
        raise RuntimeError("Invalid token security attributes size")
    buffer = ctypes.create_string_buffer(size.value)
    status = query(int(token), 39, buffer, size, ctypes.byref(size))
    if status < 0:
        raise OSError(f"Token security attributes NTSTATUS 0x{status & 0xFFFFFFFF:08x}")
    attributes = TOKEN_SECURITY_ATTRIBUTES.from_buffer(buffer)
    if attributes.Version != 1 or attributes.AttributeCount > 1024:
        raise RuntimeError("Unsupported token security attributes")
    for index in range(attributes.AttributeCount):
        item = attributes.Attributes[index]
        name = ctypes.wstring_at(item.Name.Buffer, item.Name.Length // 2)
        if name == "WIN://NOALLAPPPKG":
            return (
                item.ValueType == 2
                and item.ValueCount == 1
                and not item.Flags & 0x10
                and ctypes.cast(item.Values, ctypes.POINTER(ctypes.c_uint64))[0] != 0
            )
    return False


def _dll(name):
    if os.name != "nt":
        raise RuntimeError("LPAC requires Windows")
    return ctypes.WinDLL(name, use_last_error=True)


def _function(dll, name, result, *args):
    function = getattr(dll, name)
    function.restype, function.argtypes = result, list(args)
    return function


def _check(success):
    if not success:
        raise ctypes.WinError(ctypes.get_last_error())


def token_flag(process, information_class):
    import win32con
    import win32security

    token = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
    try:
        value, size = w.DWORD(), w.DWORD()
        query = _function(
            _dll("advapi32"),
            "GetTokenInformation",
            w.BOOL,
            w.HANDLE,
            ctypes.c_int,
            w.LPVOID,
            w.DWORD,
            ctypes.POINTER(w.DWORD),
        )
        if information_class == 46:
            return _lpac_attribute(token)
        else:
            _check(
                query(
                    int(token),
                    information_class,
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                    ctypes.byref(size),
                )
            )
        return bool(value.value)
    finally:
        token.Close()


def token_sid(token):
    query = _function(
        _dll("advapi32"),
        "GetTokenInformation",
        w.BOOL,
        w.HANDLE,
        ctypes.c_int,
        w.LPVOID,
        w.DWORD,
        ctypes.POINTER(w.DWORD),
    )
    size = w.DWORD()
    query(int(token), 31, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    _check(query(int(token), 31, buffer, size, ctypes.byref(size)))
    pointer = ctypes.cast(buffer, ctypes.POINTER(w.LPVOID))[0]
    string = w.LPWSTR()
    convert = _function(
        _dll("advapi32"), "ConvertSidToStringSidW", w.BOOL, w.LPVOID, ctypes.POINTER(w.LPWSTR)
    )
    _check(convert(pointer, ctypes.byref(string)))
    try:
        return string.value
    finally:
        _function(_dll("kernel32"), "LocalFree", w.LPVOID, w.LPVOID)(string)


def system_capability_sids(name):
    """Derive only explicitly investigated runtime capabilities, never network access."""
    if name not in ("registryRead", "lpacInstrumentation"):
        raise ValueError("Capability is outside the P1 experiment allowlist")
    groups, capabilities = ctypes.POINTER(w.LPVOID)(), ctypes.POINTER(w.LPVOID)()
    group_count, capability_count = w.DWORD(), w.DWORD()
    derive = _function(
        _dll("kernelbase"),
        "DeriveCapabilitySidsFromName",
        w.BOOL,
        w.LPCWSTR,
        ctypes.POINTER(ctypes.POINTER(w.LPVOID)),
        ctypes.POINTER(w.DWORD),
        ctypes.POINTER(ctypes.POINTER(w.LPVOID)),
        ctypes.POINTER(w.DWORD),
    )
    _check(
        derive(
            name,
            ctypes.byref(groups),
            ctypes.byref(group_count),
            ctypes.byref(capabilities),
            ctypes.byref(capability_count),
        )
    )
    convert = _function(
        _dll("advapi32"), "ConvertSidToStringSidW", w.BOOL, w.LPVOID, ctypes.POINTER(w.LPWSTR)
    )
    free = _function(_dll("kernel32"), "LocalFree", w.LPVOID, w.LPVOID)
    values = []
    try:
        for index in range(capability_count.value):
            value = w.LPWSTR()
            _check(convert(capabilities[index], ctypes.byref(value)))
            try:
                values.append(value.value)
            finally:
                free(value)
    finally:
        for array, count in ((groups, group_count.value), (capabilities, capability_count.value)):
            for index in range(count):
                free(array[index])
            free(array)
    return values


class LpacProfile:
    """A unique profile with no network or broad file capabilities."""

    def __init__(self, name=None):
        self.name = name or "AgentHub.Probe." + uuid4().hex
        if not re.fullmatch(r"AgentHub\.Probe\.[0-9a-f]{32}", self.name):
            raise ValueError("Only fresh AgentHub native experiment profiles are supported")
        self.sid = None
        self.cleanup_blocked = False

    def create(self):
        import win32security

        sid = w.LPVOID()
        create = _function(
            _dll("userenv"),
            "CreateAppContainerProfile",
            ctypes.c_long,
            w.LPCWSTR,
            w.LPCWSTR,
            w.LPCWSTR,
            w.LPVOID,
            w.DWORD,
            ctypes.POINTER(w.LPVOID),
        )
        result = create(
            self.name,
            "AgentHub LPAC probe",
            "Isolated native acceptance",
            None,
            0,
            ctypes.byref(sid),
        )
        if result < 0:
            raise OSError(f"CreateAppContainerProfile HRESULT 0x{result & 0xFFFFFFFF:08x}")
        try:
            string = w.LPWSTR()
            convert = _function(
                _dll("advapi32"),
                "ConvertSidToStringSidW",
                w.BOOL,
                w.LPVOID,
                ctypes.POINTER(w.LPWSTR),
            )
            _check(convert(sid, ctypes.byref(string)))
            try:
                self.sid = string.value
                # Validate before allowing use in any ACL.
                win32security.ConvertStringSidToSid(self.sid)
            finally:
                _function(_dll("kernel32"), "LocalFree", w.LPVOID, w.LPVOID)(string)
        finally:
            _function(_dll("advapi32"), "FreeSid", w.LPVOID, w.LPVOID)(sid)
        return self

    def delete(self):
        if self.cleanup_blocked:
            raise RuntimeError(
                "Process cleanup was not confirmed; retain profile for investigation"
            )
        delete = _function(_dll("userenv"), "DeleteAppContainerProfile", ctypes.c_long, w.LPCWSTR)
        result = delete(self.name)
        if result < 0:
            raise OSError(f"DeleteAppContainerProfile HRESULT 0x{result & 0xFFFFFFFF:08x}")

    def run(
        self, argv, cwd, *, environment, timeout=15, registry_read=False, instrumentation=False
    ):
        """Run with an LPAC token, explicit stdio handles and a kill-on-close Job."""
        import pywintypes
        import win32api
        import win32con
        import win32event
        import win32file
        import win32job
        import win32pipe
        import win32process
        import win32security

        if not self.sid:
            raise RuntimeError("Profile is not created")
        if self.cleanup_blocked:
            raise RuntimeError("A previous launch has unconfirmed cleanup")
        kernel, advapi = _dll("kernel32"), _dll("advapi32")
        handles, readers, sizes = [], [], [0, 0]
        output, errors = [bytearray(), bytearray()], []
        job = win32job.CreateJobObject(None, "Local\\AgentHub-LPAC-" + uuid4().hex)
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        sid, attributes = w.LPVOID(), None
        capability_pointers = []
        process, assigned, initialized = None, False, False
        timed_out = False
        started = time.monotonic()

        def drain(handle, index):
            try:
                while True:
                    _, data = win32file.ReadFile(handle, 4096)
                    sizes[index] += len(data)
                    output[index].extend(data[: max(0, 32768 - len(output[index]))])
            except pywintypes.error as exc:
                if exc.winerror not in (109, 232, 995):
                    errors.append(str(exc))

        try:
            security = win32security.SECURITY_ATTRIBUTES()
            security.bInheritHandle = True
            out_read, out_write = win32pipe.CreatePipe(security, 0)
            err_read, err_write = win32pipe.CreatePipe(security, 0)
            handles.extend((out_read, out_write, err_read, err_write))
            for handle in (out_read, err_read):
                win32api.SetHandleInformation(handle, win32con.HANDLE_FLAG_INHERIT, 0)
            stdin = win32file.CreateFile(
                "NUL", win32con.GENERIC_READ, 3, security, win32con.OPEN_EXISTING, 0, None
            )
            handles.append(stdin)
            convert = _function(
                advapi, "ConvertStringSidToSidW", w.BOOL, w.LPCWSTR, ctypes.POINTER(w.LPVOID)
            )
            _check(convert(self.sid, ctypes.byref(sid)))
            capability_names = system_capability_sids("registryRead") if registry_read else []
            if instrumentation:
                capability_names.extend(system_capability_sids("lpacInstrumentation"))
            capability_array = (SID_AND_ATTRIBUTES * len(capability_names))()
            for index, name in enumerate(capability_names):
                pointer = w.LPVOID()
                _check(convert(name, ctypes.byref(pointer)))
                capability_pointers.append(pointer)
                capability_array[index] = SID_AND_ATTRIBUTES(pointer, 4)
            capabilities = SECURITY_CAPABILITIES(sid, capability_array, len(capability_names), 0)
            opt_out = w.DWORD(1)
            inherit = (w.HANDLE * 3)(int(stdin), int(out_write), int(err_write))
            size = ctypes.c_size_t()
            initialize = _function(
                kernel,
                "InitializeProcThreadAttributeList",
                w.BOOL,
                w.LPVOID,
                w.DWORD,
                w.DWORD,
                ctypes.POINTER(ctypes.c_size_t),
            )
            initialize(None, 3, 0, ctypes.byref(size))
            if not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            attributes = ctypes.create_string_buffer(size.value)
            _check(initialize(attributes, 3, 0, ctypes.byref(size)))
            initialized = True
            update = _function(
                kernel,
                "UpdateProcThreadAttribute",
                w.BOOL,
                w.LPVOID,
                w.DWORD,
                ctypes.c_size_t,
                w.LPVOID,
                ctypes.c_size_t,
                w.LPVOID,
                w.LPVOID,
            )
            for attribute, value in (
                (0x20009, capabilities),
                (0x2000F, opt_out),
                (0x20002, inherit),
            ):
                _check(
                    update(
                        attributes,
                        0,
                        attribute,
                        ctypes.byref(value),
                        ctypes.sizeof(value),
                        None,
                        None,
                    )
                )
            startup = STARTUPINFOEX()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = 0x101  # USESTDHANDLES | USESHOWWINDOW
            startup.StartupInfo.wShowWindow = 0
            startup.StartupInfo.hStdInput = int(stdin)
            startup.StartupInfo.hStdOutput = int(out_write)
            startup.StartupInfo.hStdError = int(err_write)
            startup.lpAttributeList = ctypes.cast(attributes, w.LPVOID)
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            env = ctypes.create_unicode_buffer(
                "\0".join(f"{key}={value}" for key, value in sorted(environment.items())) + "\0\0"
            )
            pi = PROCESS_INFORMATION()
            create = _function(
                kernel,
                "CreateProcessW",
                w.BOOL,
                w.LPCWSTR,
                w.LPWSTR,
                w.LPVOID,
                w.LPVOID,
                w.BOOL,
                w.DWORD,
                w.LPVOID,
                w.LPCWSTR,
                ctypes.POINTER(STARTUPINFOEX),
                ctypes.POINTER(PROCESS_INFORMATION),
            )
            self.cleanup_blocked = True
            _check(
                create(
                    argv[0],
                    command,
                    None,
                    None,
                    True,
                    0x08080404,
                    env,
                    str(cwd),
                    ctypes.byref(startup),
                    ctypes.byref(pi),
                )
            )
            process = pywintypes.HANDLE(pi.hProcess)
            thread = pywintypes.HANDLE(pi.hThread)
            handles.extend((process, thread))
            win32job.AssignProcessToJobObject(job, process)
            assigned = True
            token = {"appcontainer": token_flag(process, 29), "lpac": token_flag(process, 46)}
            if not all(token.values()):
                raise RuntimeError("Created process does not have the required LPAC token")
            query_token = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
            try:
                token["sid_matches"] = token_sid(query_token) == self.sid
                token["privileges"] = [
                    [win32security.LookupPrivilegeName(None, luid), flags]
                    for luid, flags in win32security.GetTokenInformation(query_token, 3)
                ]
            finally:
                query_token.Close()
            if not token["sid_matches"]:
                raise RuntimeError("AppContainer SID differs from the prepared profile")
            token["registry_read_requested"] = registry_read
            token["instrumentation_requested"] = instrumentation
            win32process.ResumeThread(thread)
            for handle in (stdin, out_write, err_write):
                handle.Close()
                handles.remove(handle)
            for index, handle in enumerate((out_read, err_read)):
                worker = threading.Thread(target=drain, args=(handle, index), daemon=True)
                worker.start()
                readers.append(worker)
            while win32event.WaitForSingleObject(process, 30) == win32con.WAIT_TIMEOUT:
                if time.monotonic() - started > timeout:
                    timed_out = True
                    break
            code = None if timed_out else win32process.GetExitCodeProcess(process)
        finally:
            if process is not None and not assigned:
                win32process.TerminateProcess(process, 1)
                if win32event.WaitForSingleObject(process, 5000) != win32con.WAIT_OBJECT_0:
                    raise RuntimeError("Suspended process cleanup was not confirmed")
            win32job.TerminateJobObject(job, 1)
            cleanup_deadline = time.monotonic() + 5
            while win32job.QueryInformationJobObject(
                job,
                win32job.JobObjectBasicAccountingInformation,
            )["ActiveProcesses"]:
                if time.monotonic() >= cleanup_deadline:
                    break
                time.sleep(0.01)
            else:
                self.cleanup_blocked = False
            job.Close()
            for worker in readers:
                worker.join(timeout=2)
            for handle in handles:
                handle.Close()
            if initialized:
                _function(kernel, "DeleteProcThreadAttributeList", None, w.LPVOID)(attributes)
            if sid:
                _function(kernel, "LocalFree", w.LPVOID, w.LPVOID)(sid)
            for pointer in capability_pointers:
                _function(kernel, "LocalFree", w.LPVOID, w.LPVOID)(pointer)
        if self.cleanup_blocked:
            raise RuntimeError("LPAC Job did not drain; profile cleanup is blocked")
        return {
            "exit_code": code,
            "timed_out": timed_out,
            "pid": pi.dwProcessId,
            "token": token,
            "job_drained": True,
            "elapsed": time.monotonic() - started,
            "stdout": bytes(output[0]).decode("utf-8", "replace"),
            "stderr": bytes(output[1]).decode("utf-8", "replace"),
            "truncated": any(sizes[i] > len(output[i]) for i in range(2)),
            "pipe_errors": errors,
        }
