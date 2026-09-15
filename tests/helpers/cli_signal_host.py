"""Invoke the installed CLI in an outer Job; deliver SIGINT without touching other consoles."""

import signal
import sys
import threading
import time
from pathlib import Path

import win32api
import win32job

trigger = Path(sys.argv.pop(1))
outer_job = win32job.CreateJobObject(None, "")
win32job.AssignProcessToJobObject(outer_job, win32api.GetCurrentProcess())


def interrupt_when_requested():
    while not trigger.exists():
        time.sleep(0.03)
    signal.raise_signal(signal.SIGINT)


threading.Thread(target=interrupt_when_requested, daemon=True).start()
from agent_cli.main import main  # noqa: E402

raise SystemExit(main())
