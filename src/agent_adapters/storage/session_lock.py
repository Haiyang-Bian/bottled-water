"""OS-held session locks release automatically on process death."""

import hashlib
import os


class SessionBusyError(RuntimeError):
    pass


class SessionLock:
    def __init__(self, directory, session_id):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / (hashlib.sha256(session_id.encode()).hexdigest() + ".lock")
        self.file = None

    def __enter__(self):
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
