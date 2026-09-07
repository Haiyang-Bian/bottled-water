"""Exercise the installed entry point with prompt_toolkit keyboard input (not visual QA)."""

import sys
from pathlib import Path

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from agent_cli.main import main

with Path(sys.argv.pop(1)).open(encoding="utf-8", newline="") as stream:
    keys = stream.read()
sys.stdin.isatty = lambda: True
sys.stdout.isatty = lambda: True
with create_pipe_input() as pipe:
    with create_app_session(input=pipe, output=DummyOutput()):
        pipe.send_text(keys)
        raise SystemExit(main())
