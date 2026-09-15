"""The Kernel import must not load any host or optional SDK."""

import subprocess
import sys


def test_kernel_import_does_not_load_optional_subsystems():
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import agent_runtime,sys; "
                "assert not any(n.split('.')[0] in {'app','db','fastapi','sqlalchemy','openai',"
                "'model_provider','agent_subsystems'} for n in sys.modules)"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
