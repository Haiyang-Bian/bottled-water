"""Windows legacy pipe encodings must not turn completed acceptance into failure."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("passed,cleaned,code", [(True, True, 0), (False, True, 1),
                                               (True, False, 1)])
def test_probe_summary_in_gbk_pipe(tmp_path, passed, cleaned, code):
    repo = Path(__file__).resolve().parents[1]
    report = {"passed": passed, "cleanup_verified": cleaned,
              "result": {"output": "已完成 ✅ test-secret"}}
    source = tmp_path / "report.json"
    source.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    helper = tmp_path / "summary.py"
    helper.write_text(
        "import importlib.util,json,sys\nfrom pathlib import Path\n"
        "repo=Path(sys.argv[1]); sys.path.insert(0,str(repo/'scripts'))\n"
        "spec=importlib.util.spec_from_file_location('probe',repo/'scripts/probe-restricted-runtime.py')\n"
        "probe=importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)\n"
        "source=Path(sys.argv[2]); report=json.loads(source.read_text(encoding='utf-8'))\n"
        "raise SystemExit(probe.print_summary(source.parent,report,probe.Redactor(['test-secret'])))\n",
        encoding="utf-8")
    result = subprocess.run([sys.executable, str(helper), str(repo), str(source)],
        capture_output=True, timeout=20, env={**os.environ, "PYTHONIOENCODING": "gbk:strict"})
    assert result.returncode == code, result.stderr
    value = json.loads(result.stdout.decode("ascii"))
    assert value["result"]["output"] == "已完成 ✅ [redacted]"
    assert json.loads(source.read_text(encoding="utf-8")) == report
