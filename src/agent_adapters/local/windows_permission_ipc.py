"""Event-driven local control pipe. Never an RPC bridge for tool processes."""

import ctypes
from ctypes import wintypes
import json
import os
import threading
from uuid import uuid4

from agent_contracts.errors import OperationError

LIMIT = 32768
OPERATIONS = {"status", "freeze", "retire", "thaw"}


def process_identity(pid=None):
    import win32api
    import win32con
    import win32process
    import win32security as sec
    import pywintypes
    pid = pid or os.getpid()
    try:
        process = win32api.OpenProcess(0x1000, False, pid)
    except pywintypes.error as exc:
        raise ctypes.WinError(exc.winerror) from exc
    try:
        token = sec.OpenProcessToken(process, win32con.TOKEN_QUERY)
        try:
            query = ctypes.WinDLL("advapi32", use_last_error=True).GetTokenInformation
            query.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                              wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            query.restype = wintypes.BOOL
            flag, length = wintypes.DWORD(), wintypes.DWORD()
            if not query(int(token), 29, ctypes.byref(flag), ctypes.sizeof(flag), ctypes.byref(length)):
                raise ctypes.WinError(ctypes.get_last_error())
            if flag.value:  # TokenIsAppContainer; not yet exposed by pywin32.
                raise OperationError("permission_control_denied", "AppContainer control is denied")
            owner = sec.ConvertSidToStringSid(sec.GetTokenInformation(token, sec.TokenUser)[0])
        finally:
            token.Close()
        created = win32process.GetProcessTimes(process)["CreationTime"].isoformat()
        return {"pid": pid, "created": created, "owner": owner}
    finally:
        process.Close()


def peer_identity(handle, *, server=False):
    function = getattr(ctypes.WinDLL("kernel32", use_last_error=True),
                       "GetNamedPipeServerProcessId" if server else "GetNamedPipeClientProcessId")
    function.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    function.restype = wintypes.BOOL
    pid = wintypes.ULONG()
    if not function(int(handle), ctypes.byref(pid)):
        raise ctypes.WinError(ctypes.get_last_error())
    return process_identity(pid.value)


def pipe_security():
    import win32security as sec
    owner = process_identity()["owner"]
    attributes = sec.SECURITY_ATTRIBUTES()
    attributes.bInheritHandle = False
    attributes.SECURITY_DESCRIPTOR = sec.ConvertStringSecurityDescriptorToSecurityDescriptor(
        f"D:P(A;;GA;;;SY)(A;;GA;;;{owner})", 1,
    )
    return attributes


def wait_io(handle, overlapped, stop=None, timeout=5000):
    import win32event
    import win32file
    handles = [overlapped.hEvent] + ([stop] if stop is not None else [])
    result = win32event.WaitForMultipleObjects(handles, False, timeout)
    if result != win32event.WAIT_OBJECT_0:
        win32file.CancelIoEx(handle, overlapped)
        win32event.WaitForSingleObject(overlapped.hEvent, 5000)
        raise OperationError("permission_control_timeout", "权限控制通道未响应；未推断宿主已退出。")
    return win32file.GetOverlappedResult(handle, overlapped, False)


def transfer(handle, value=None, stop=None, timeout=5000):
    import pywintypes
    import win32event
    import win32file
    operation = pywintypes.OVERLAPPED()
    operation.hEvent = win32event.CreateEvent(None, True, False, None)
    try:
        if value is None:
            buffer = win32file.AllocateReadBuffer(LIMIT + 1)
            win32file.ReadFile(handle, buffer, operation)
            count = wait_io(handle, operation, stop, timeout)
            if not 0 < count <= LIMIT:
                raise ValueError("Invalid control message size")
            return json.loads(bytes(buffer[:count]).decode("utf-8"))
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if len(encoded) > LIMIT:
            raise ValueError("Control message too large")
        win32file.WriteFile(handle, encoded, operation)
        wait_io(handle, operation, stop, timeout)
    finally:
        operation.hEvent.Close()


class PermissionControlServer:
    def __init__(self, environment, host, handler):
        import win32event
        self.environment, self.host, self.handler = environment, host, handler
        self.nonce = uuid4().hex
        self.endpoint = "\\\\.\\pipe\\AgentHub-" + host + "-" + self.nonce
        self.identity = process_identity()
        self.stop = win32event.CreateEvent(None, True, False, None)
        self.ready = threading.Event()
        self.error = None
        self.closed = False
        self.thread = threading.Thread(target=self._serve, name="agenthub-permission-control",
                                       daemon=True)

    def start(self):
        self.thread.start()
        if not self.ready.wait(5) or self.error:
            self.close()
            raise OperationError("permission_control_failed", "Cannot open permission control pipe")
        return {**self.identity, "endpoint": self.endpoint, "nonce": self.nonce}

    def _serve(self):
        import pywintypes
        import win32event
        import win32pipe
        try:
            pipe = win32pipe.CreateNamedPipe(
                self.endpoint, 3 | 0x40000000 | 0x80000,
                4 | 2 | 8, 1, LIMIT + 1, LIMIT + 1, 5000, pipe_security(),
            )
        except BaseException as exc:
            self.error = exc
            self.ready.set()
            return
        # Keep the named object alive between clients. Recreating it leaves a gap
        # where WaitNamedPipe reports FILE_NOT_FOUND instead of waiting.
        while win32event.WaitForSingleObject(self.stop, 0) != win32event.WAIT_OBJECT_0:
            connect = None
            try:
                connect = pywintypes.OVERLAPPED()
                connect.hEvent = win32event.CreateEvent(None, True, False, None)
                self.ready.set()
                connected = False
                try:
                    win32pipe.ConnectNamedPipe(pipe, connect)
                except pywintypes.error as exc:
                    if exc.winerror != 535:  # ERROR_PIPE_CONNECTED
                        raise
                    connected = True
                if not connected:
                    wait_io(pipe, connect, self.stop, win32event.INFINITE)
                peer = peer_identity(pipe)
                if peer["owner"] != self.identity["owner"]:
                    raise ValueError("Foreign owner")
                request = transfer(pipe, stop=self.stop)
                if (set(request) != {"version", "id", "environment", "host", "nonce", "operation",
                                     "transition"}
                        or request["version"] != 1 or request["environment"] != self.environment
                        or request["host"] != self.host or request["nonce"] != self.nonce
                        or request["operation"] not in OPERATIONS
                        or not isinstance(request["id"], str) or len(request["id"]) != 32):
                    raise ValueError("Invalid control request")
                try:
                    result = self.handler(request, peer)
                    response = {"id": request["id"], "ok": True, "result": result}
                except Exception as exc:
                    response = {"id": request["id"], "ok": False,
                                "code": getattr(exc, "code", "permission_control_failed"),
                                "message": str(exc)}
                transfer(pipe, response, self.stop)
                if transfer(pipe, stop=self.stop) != {"ack": request["id"]}:
                    raise ValueError("Missing response acknowledgement")
            except Exception:
                # A rejected peer never changes authority or terminates the listener.
                pass
            finally:
                if connect:
                    connect.hEvent.Close()
                try:
                    win32pipe.DisconnectNamedPipe(pipe)
                except pywintypes.error:
                    pass
        pipe.Close()

    def close(self):
        import win32event
        if self.closed:
            return
        win32event.SetEvent(self.stop)
        if self.thread.is_alive():
            self.thread.join(6)
        if self.thread.is_alive():
            raise OperationError("permission_control_failed", "Control thread did not stop")
        self.stop.Close()
        self.closed = True


def request_control(record, environment, host, operation, transition=None):
    import win32file
    import win32pipe
    if operation not in OPERATIONS:
        raise ValueError("Invalid control operation")
    win32pipe.WaitNamedPipe(record["endpoint"], 5000)
    pipe = win32file.CreateFile(record["endpoint"], 0x12019B, 0, None, 3, 0x40000000, None)
    try:
        peer = peer_identity(pipe, server=True)
        if peer != {key: record[key] for key in ("pid", "created", "owner")}:
            raise OperationError("permission_control_denied", "Control peer identity changed")
        win32pipe.SetNamedPipeHandleState(pipe, 2, None, None)
        identifier = uuid4().hex
        transfer(pipe, {"version": 1, "id": identifier, "environment": environment,
                        "host": host, "nonce": record["nonce"], "operation": operation,
                        "transition": transition})
        response = transfer(pipe, timeout=120000 if operation == "retire" else 5000)
        if response.get("id") != identifier:
            raise OperationError("permission_control_denied", "Control response ID mismatch")
        transfer(pipe, {"ack": identifier})
        if not response.get("ok"):
            raise OperationError(response.get("code", "permission_control_failed"),
                                 response.get("message", "Control operation failed"))
        return response["result"]
    finally:
        pipe.Close()
