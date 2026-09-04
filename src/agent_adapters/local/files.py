"""Bounded text file operations with optimistic concurrency checks."""

import codecs
import fnmatch
import hashlib
import os
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from agent_contracts.errors import OperationError
from agent_subsystems.workspaces.paths import resolve_resource

OUTPUT_LIMIT = 65536
MAX_TEXT_BYTES = 8 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decode(data):
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16-le" if data.startswith(codecs.BOM_UTF16_LE) else "utf-16-be"
        return data[2:].decode(encoding), encoding
    if b"\0" in data:
        raise OperationError("binary_file", "Binary files are not supported by text tools")
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError as exc:
        raise OperationError("unsupported_encoding", "Expected UTF-8 or BOM-marked UTF-16") from exc


def encode(text, encoding):
    if encoding in {"utf-16-le", "utf-16-be"}:
        bom = codecs.BOM_UTF16_LE if encoding == "utf-16-le" else codecs.BOM_UTF16_BE
        return bom + text.encode(encoding)
    return text.encode(encoding)


def bounded(text):
    data = text.encode("utf-8")
    return data[:OUTPUT_LIMIT].decode("utf-8", errors="ignore"), len(data) > OUTPUT_LIMIT


class LocalFiles:
    def __init__(self, workspace):
        self.workspace = workspace

    def _read(self, path):
        if path.stat().st_size > MAX_TEXT_BYTES:
            raise OperationError("file_too_large", "Text file exceeds the 8 MiB limit")
        data = path.read_bytes()
        text, encoding = decode(data)
        return data, text, encoding

    async def read(self, path, start_line=1, limit=400):
        if start_line < 1 or not 1 <= limit <= 10000:
            raise OperationError("invalid_range", "Invalid line range")
        target = resolve_resource(self.workspace, path)
        data, text, encoding = self._read(target)
        lines = text.splitlines(keepends=True)
        selected = "".join(lines[start_line - 1 : start_line - 1 + limit])
        content, truncated = bounded(selected)
        return {
            "path": str(target),
            "sha256": digest(data),
            "encoding": encoding,
            "start_line": start_line,
            "total_lines": len(lines),
            "content": content,
            "truncated": truncated or start_line - 1 + limit < len(lines),
        }

    def _replace(self, target, content, expected_hash):
        existed = target.exists()
        if existed:
            original, old_text, encoding = self._read(target)
            if expected_hash != digest(original):
                raise OperationError(
                    "file_conflict", "Read the current file and provide its sha256"
                )
            if "\r\n" in old_text:
                content = content.replace("\r\n", "\n").replace("\n", "\r\n")
        else:
            if expected_hash != "new":
                raise OperationError("file_conflict", "Use expected_hash='new' to create a file")
            original, encoding = b"", "utf-8"
        data = encode(content, encoding)
        if len(data) > MAX_TEXT_BYTES:
            raise OperationError("file_too_large", "Text file exceeds the 8 MiB limit")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / (".agenthub-" + uuid4().hex + ".tmp")
        try:
            temporary.write_bytes(data)
            # Check immediately before replacement; this is optimistic concurrency, not an OS lock.
            if (
                existed and (not target.exists() or digest(target.read_bytes()) != expected_hash)
            ) or (not existed and target.exists()):
                raise OperationError("file_conflict", "File changed during this operation")
            if existed:
                temporary.replace(target)
            else:
                # Exclusive creation prevents silently overwriting a concurrently created file.
                with target.open("xb") as stream:
                    stream.write(data)
            return {"path": str(target), "sha256": digest(data), "bytes_written": len(data)}
        finally:
            temporary.unlink(missing_ok=True)

    async def write(self, path, content, expected_hash):
        target = resolve_resource(self.workspace, path)
        return self._replace(target, content, expected_hash)

    async def edit(self, path, old_text, new_text, expected_hash):
        target = resolve_resource(self.workspace, path)
        data, text, _ = self._read(target)
        if digest(data) != expected_hash:
            raise OperationError("file_conflict", "Read the current file and provide its sha256")
        if not old_text or text.count(old_text) != 1:
            raise OperationError("ambiguous_edit", "old_text must match exactly once")
        return self._replace(target, text.replace(old_text, new_text, 1), expected_hash)

    async def _walk(self, directory):
        for root, dirs, files in os.walk(directory, followlinks=False):
            await asyncio.sleep(0)
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in {".git", ".venv", "node_modules", "__pycache__"}
                and not (Path(root) / d).is_symlink()
                and not (getattr(os.lstat(Path(root) / d), "st_file_attributes", 0) & 0x400)
            )
            for name in sorted(files):
                await asyncio.sleep(0)
                path = Path(root) / name
                try:
                    yield resolve_resource(self.workspace, str(path))
                except OperationError:
                    continue

    async def list(self, path=".", pattern="*", offset=0, limit=200):
        if offset < 0 or not 1 <= limit <= 1000:
            raise OperationError("invalid_range", "Invalid pagination")
        directory = resolve_resource(self.workspace, path, directory=True)
        results = []
        seen = 0
        output_bytes = 0
        async for item in self._walk(directory):
            name = item.relative_to(directory).as_posix()
            if fnmatch.fnmatch(name, pattern):
                if seen >= offset:
                    size = len(json.dumps(name, ensure_ascii=False).encode("utf-8")) + 2
                    if len(results) == limit or output_bytes + size > OUTPUT_LIMIT - 1024:
                        return {"files": results, "next_offset": seen, "truncated": True}
                    results.append(name)
                    output_bytes += size
                seen += 1
        return {"files": results, "next_offset": None, "truncated": False}

    async def search(self, query, path=".", pattern="*", offset=0, limit=100):
        if not query or offset < 0 or not 1 <= limit <= 1000:
            raise OperationError("invalid_range", "Invalid search or pagination")
        directory = resolve_resource(self.workspace, path, directory=True)
        results = []
        seen = 0
        output_bytes = 0
        async for item in self._walk(directory):
            name = item.relative_to(directory).as_posix()
            if not fnmatch.fnmatch(name, pattern):
                continue
            try:
                _, text, _ = self._read(item)
            except (OperationError, OSError, UnicodeError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if query in line:
                    if seen >= offset:
                        row = {
                            "path": name,
                            "line": number,
                            "text": line[:1000],
                            "line_truncated": len(line) > 1000,
                        }
                        size = len(json.dumps(row, ensure_ascii=False).encode("utf-8")) + 2
                        if len(results) == limit or output_bytes + size > OUTPUT_LIMIT - 1024:
                            return {"matches": results, "next_offset": seen, "truncated": True}
                        results.append(row)
                        output_bytes += size
                    seen += 1
        return {"matches": results, "next_offset": None, "truncated": False}
