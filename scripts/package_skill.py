"""Private release bundle, without venv, caches, logs, test hooks or credentials."""

import argparse
import hashlib
import json
import stat
import zipfile
from pathlib import Path


def files(root):
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and not any(
            part in {".venv", "__pycache__", ".runtime", ".DS_Store", ".evaluation-only"}
            for part in p.relative_to(root).parts
        )
        and p.suffix != ".pyc"
    )


def hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files(root)
    }


def package(root, output):
    if (root / ".evaluation-only").exists() or (root / "scripts/evaluation_backend.py").exists():
        raise ValueError("Refusing to release an evaluation bundle")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files(root):
            info = zipfile.ZipInfo.from_file(path, str(Path(root.name) / path.relative_to(root)))
            if "private" in path.relative_to(root).parts:
                info.external_attr = (stat.S_IFREG | 0o600) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    output.chmod(0o600)
    manifest = output.with_suffix(".manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "archive_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "files": hashes(root),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    manifest.chmod(0o600)
    print(
        json.dumps(
            {
                "archive": str(output.resolve()),
                "files": len(files(root)),
                "bytes": output.stat().st_size,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    package(args.source.resolve(), args.output.resolve())
