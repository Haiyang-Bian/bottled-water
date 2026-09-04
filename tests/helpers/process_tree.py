"""Owned test workload: a parent and a child that wait until their Job is closed."""

import os
import subprocess
import sys
import time
from pathlib import Path

if len(sys.argv) > 1 and sys.argv[1] == "child":
    time.sleep(90)
else:
    child = subprocess.Popen([sys.executable, __file__, "child"])
    Path("tree-pids.txt").write_text(f"{os.getpid()} {child.pid}", encoding="ascii")
    child.wait()
