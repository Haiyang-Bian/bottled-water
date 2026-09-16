"""Single-operation framing; never deserialize executable Python objects."""

import json
import struct

VERSION = 1
REQUEST_LIMIT = 32 * 1024 * 1024
RESPONSE_LIMIT = 1024 * 1024
OPERATIONS = frozenset({"read", "write", "edit", "list", "search", "resolve", "probe", "command"})


class ProtocolError(ValueError):
    pass


def unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ProtocolError("Duplicate JSON field")
        result[key] = value
    return result


def encode(value, limit):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(data) > limit:
        raise ProtocolError("Frame exceeds its byte limit")
    return struct.pack("!I", len(data)) + data


def decode(data, limit):
    if len(data) < 4:
        raise ProtocolError("Incomplete frame header")
    size = struct.unpack("!I", data[:4])[0]
    if size > limit or size != len(data) - 4:
        raise ProtocolError("Invalid frame length")
    try:
        result = json.loads(data[4:].decode("utf-8"), object_pairs_hook=unique_object,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeError, ValueError) as exc:
        raise ProtocolError("Invalid UTF-8 JSON") from exc
    if (not isinstance(result, dict) or type(result.get("version")) is not int
            or result["version"] != VERSION):
        raise ProtocolError("Unsupported protocol envelope")
    return result


def response_result(data, request_id):
    result = decode(data, RESPONSE_LIMIT)
    if result.get("request_id") != request_id or type(result.get("ok")) is not bool:
        raise ProtocolError("Unexpected response identity")
    fields = {"version", "request_id", "ok"}
    if result["ok"]:
        if set(result) != fields | {"result"} or not isinstance(result["result"], dict):
            raise ProtocolError("Invalid result envelope")
    elif (set(result) != fields | {"error", "error_code"}
          or not all(isinstance(result[key], str) and result[key] for key in ("error", "error_code"))):
        raise ProtocolError("Invalid error envelope")
    return result


def read_request(stream):
    header = stream.read(4)
    if len(header) != 4:
        raise ProtocolError("Incomplete request header")
    size = struct.unpack("!I", header)[0]
    if size > REQUEST_LIMIT:
        raise ProtocolError("Request too large")
    data = bytearray(header)
    while len(data) < size + 4:
        block = stream.read(min(65536, size + 4 - len(data)))
        if not block:
            raise ProtocolError("Incomplete request body")
        data.extend(block)
    return decode(data, REQUEST_LIMIT)
