"""OS-held session locks release automatically on process death."""

import hashlib
import os


class SessionBusyError(RuntimeError):
    pass


class SessionLock:
    def __init__(self, directory, session_id, *, guard=True):
        self.directory = directory
        self.guard = guard
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / (hashlib.sha256(session_id.encode()).hexdigest() + ".lock")
        self.file = None

    def __enter__(self):
        if self.guard:
            with SessionLock(self.directory, "__migration__", guard=False):
                return self._acquire()
        return self._acquire()

    def _acquire(self):
        handle = self.path.open("a+b")
        try:
            if self.path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise SessionBusyError("This session is already open in another process") from exc
        self.file = handle
        return self

    def __exit__(self, *args):
        if self.file is not None:
            self.file.close()
            self.file = None

    def owns(self, directory, session_id):
        expected = directory / (hashlib.sha256(session_id.encode()).hexdigest() + ".lock")
        return self.file is not None and not self.file.closed and self.path == expected
