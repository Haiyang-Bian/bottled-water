"""One fixed operation, executed entirely inside an LPAC process."""

import hashlib
from pathlib import Path
import subprocess
import sys
import threading
import time

from agent_contracts.errors import OperationError
from agent_contracts.execution import ExecutionLocation, ResourceGrant, WorkspaceSpec
from agent_subsystems.workspaces.paths import resolve_resource
from .files import LocalFiles
from .file_probes import probe
from .worker_protocol import OPERATIONS, RESPONSE_LIMIT, VERSION, encode, read_request


class WorkerContext:
    def __init__(self, config):
        self.location = ExecutionLocation(Path(config["cwd"]), config["location_version"])
        self.permissions = config["permissions"]
        self.grant = ResourceGrant(WorkspaceSpec(tuple(Path(r["path"]) for r in self.permissions)),
                                   frozenset({"files", "process"}))
        self.deadline = time.monotonic() + config["timeout"]

    def check(self):
        if time.monotonic() >= self.deadline:
            raise OperationError("process_timeout", "Operation deadline reached")

    def resolve(self, value, operation="read", *, directory=False):
        path = resolve_resource(self.grant.workspace, self.location, value, directory=directory)
        allowed = any(path.is_relative_to(Path(rule["path"])) and
                      (operation == "read" or rule["access"] == "modify")
                      for rule in self.permissions)
        if not allowed:
            raise OperationError("permission_denied", "Frozen policy denies this operation")
        return path


def command(config, context, args):
    argv = args["argv"]
    tool = next((item for item in config["executables"].values()
                 if item["path"] == argv[0]), None)
    if tool is None:
        raise OperationError("software_incompatible", "Executable is not in the isolated bundle")
    path = Path(tool["path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != tool["sha256"]:
        raise OperationError("software_changed", "Selected executable fingerprint changed")
    cwd = context.resolve(args["cwd"], directory=True)
    output, sizes = [bytearray(), bytearray()], [0, 0]
    failures = []

    def collect(stream, i):
        try:
            while block := stream.read(4096):
                sizes[i] += len(block)
                output[i].extend(block[:max(0, 32768 - len(output[i]))])
        except OSError as exc:
            failures.append(type(exc).__name__)

    started = time.monotonic()
    # The OS supplies the already-verified token and nested command Job to descendants.
    child = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, close_fds=True, creationflags=0x08000000)
    threads = [threading.Thread(target=collect, args=(stream, i), daemon=True)
               for i, stream in enumerate((child.stdout, child.stderr))]
    for thread in threads:
        thread.start()
    code = child.wait()  # Host deadline/cancel kills this entire command group, including us.
    for thread in threads:
        thread.join(timeout=2)
    if failures or any(thread.is_alive() for thread in threads):
        raise RuntimeError("Child output completion unconfirmed")
    return {"exit_code": code, "stdout": output[0].decode("utf-8", "replace"),
            "stderr": output[1].decode("utf-8", "replace"), "pid": child.pid,
            "truncated": any(sizes[i] > len(output[i]) for i in range(2)),
            "elapsed": time.monotonic() - started,
            "execution": {"cwd": str(cwd), "default_cwd": str(context.location.cwd),
                          "workspace_version": context.location.version}}


async def operate(request):
    operation, args, config = request["operation"], request["parameters"], request["config"]
    if operation not in OPERATIONS:
        raise OperationError("invalid_operation", "Unknown worker operation")
    context = WorkerContext(config)

    async def checkpoint():
        context.check()  # One worker per operation; host Job supplies cancellation.
    # Relative paths never depend on the full-privilege host's current directory.
    context.resolve(str(context.location.cwd), directory=True)
    if operation == "command":
        return command(config, context, args)
    value = args.get("value", ".") if operation == "resolve" else args.get("path", ".")
    actual = context.resolve(value, "modify" if operation in {"write", "edit"} else "read",
                             directory=args.get("directory", False))
    if operation == "resolve":
        return {"path": str(actual)}
    if operation == "probe":
        return {"path": str(actual), **await probe(actual, context, checkpoint=checkpoint, **{
            key: val for key, val in args.items() if key != "path"})}

    async def read_index(directory):
        git = config["executables"].get("git")
        if git is None:
            return [], "unavailable"
        result = command(config, context, {"argv": [git["path"], "ls-files", "--cached", "-z"],
                                            "cwd": str(directory)})
        if result["exit_code"] != 0:
            return [], "not_repository" if "not a git repository" in result["stderr"].lower() else "failed"
        return result["stdout"].split("\0")[:-1], "truncated" if result["truncated"] else "complete"

    files = LocalFiles(context.grant.workspace, context.location, index_reader=read_index,
                       checkpoint=checkpoint)
    result = await getattr(files, operation)(**args)
    result["execution"] = {"path": str(actual), "default_cwd": str(context.location.cwd),
                           "workspace_version": context.location.version}
    return result


def main():
    request = read_request(sys.stdin.buffer)
    if set(request) != {"version", "request_id", "operation", "parameters", "config"}:
        raise ValueError("Invalid request envelope")
    response = {"version": VERSION, "request_id": request["request_id"]}
    try:
        operation = operate(request)
        try:
            operation.send(None)
        except StopIteration as done:
            response.update(ok=True, result=done.value)
        else:
            operation.close()
            raise RuntimeError("Worker unexpectedly requested asynchronous host I/O")
    except (OSError, OperationError, ValueError) as exc:
        code = "permission_denied" if isinstance(exc, PermissionError) else getattr(exc, "code", "file_error")
        if code == "outside_workspace":
            code = "permission_denied"
        response.update(ok=False, error_code=code, error=str(exc))
    try:
        data = encode(response, RESPONSE_LIMIT)
    except ValueError:
        data = encode({"version": VERSION, "request_id": request["request_id"], "ok": False,
                       "error_code": "response_too_large", "error": "Worker response exceeds 1 MiB"},
                      RESPONSE_LIMIT)
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
