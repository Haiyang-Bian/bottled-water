"""Deployment uses `alembic upgrade head`, so the migration graph must have one head."""

import ast
from pathlib import Path


def test_web_migrations_have_one_head_and_no_missing_parent():
    directory = Path(__file__).resolve().parents[1] / "backend" / "alembic" / "versions"
    revisions = {}
    for path in directory.glob("*.py"):
        fields = {}
        for node in ast.parse(path.read_text(encoding="utf-8-sig")).body:
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    fields[target.id] = ast.literal_eval(node.value)
        if "revision" in fields:
            revisions[fields["revision"]] = fields["down_revision"]
    parents = {
        parent
        for value in revisions.values()
        for parent in (value if isinstance(value, tuple) else (value,))
        if parent is not None
    }
    assert parents <= revisions.keys()
    assert len(revisions.keys() - parents) == 1
