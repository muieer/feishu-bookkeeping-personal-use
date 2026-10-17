"""Create a movable private test bundle with no production target or live backend."""

import argparse
import json
import shutil
from pathlib import Path


def prepare(source, target):
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".runtime", "*.pyc", ".DS_Store"),
    )
    private = target / "private"
    (private / "config.json").write_text(
        json.dumps(
            {
                "bitable_base_token": "isolated-no-credentials",
                "bitable_table_id": "tblIsolatedBookkeeping",
            }
        )
    )
    categories = private / "knowledges/categories.json"
    data = json.loads(categories.read_text())
    data["table_id"] = "tblIsolatedBookkeeping"
    categories.write_text(json.dumps(data, ensure_ascii=False))
    (private / "knowledges/schema.json").write_text('{"tables":[]}\n')
    shutil.copy2(
        Path(__file__).with_name("evaluation_backend.py"), target / "scripts/evaluation_backend.py"
    )
    # The production transport is physically absent as a second layer of isolation.
    (target / "scripts/bookkeeping_core/cli.py").unlink()
    for p in private.rglob("*"):
        if p.is_file():
            p.chmod(0o600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    prepare(args.source.resolve(), args.target.resolve())
