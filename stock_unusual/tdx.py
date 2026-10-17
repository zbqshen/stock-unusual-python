from __future__ import annotations

import math
import socket
import struct
import time
import zlib
from dataclasses import dataclass
from threading import Lock
from typing import Iterable

DEFAULT_HOSTS = (
    "110.41.147.114:7709", "110.41.2.72:7709", "110.41.4.4:7709", "47.113.94.204:7709",
    "8.129.174.169:7709", "110.41.154.219:7709", "124.70.176.52:7709", "47.100.236.28:7709",
    "123.60.186.45:7709", "123.60.164.122:7709", "47.116.105.28:7709", "124.70.199.56:7709",
    "121.36.54.217:7709", "121.36.81.195:7709", "123.249.15.60:7709", "124.71.85.110:7709",
    "139.9.51.18:7709", "139.159.239.163:7709", "106.14.201.131:7709", "106.14.190.242:7709",
    "121.36.225.169:7709", "123.60.70.228:7709", "123.60.73.44:7709", "124.70.133.119:7709",
    "124.71.187.72:7709", "124.71.187.122:7709", "119.97.185.59:7709", "47.107.64.168:7709",
    "124.70.75.113:7709", "124.71.9.153:7709", "123.60.84.66:7709", "47.107.228.47:7719",
    "120.46.186.223:7709", "124.70.22.210:7709", "139.9.133.247:7709", "116.205.163.254:7709",
    "116.205.171.132:7709", "116.205.183.150:7709",
)
METHOD_HELLO = 0x000D
METHOD_SECURITY_COUNT = 0x044E
METHOD_SECURITY_LIST = 0x044D
METHOD_UNUSUAL = 0x0563


@dataclass(frozen=True)
class Unusual:
    index: int
    market: int
    code: str
    time: str
    desc: str
    value: str
    unusual_type: int


@dataclass(frozen=True)
class Security:
    code: str
    name: str


def _clean_text(raw: bytes) -> str:
    return raw.replace(b"\x00", b"").decode("gbk", errors="replace").strip()


def _u16(data: bytes) -> int:
    return struct.unpack_from("<H", data)[0]


def _u32(data: bytes) -> int:
    return struct.unpack_from("<I", data)[0]


def _f32(data: bytes) -> float:
    return struct.unpack_from("<f", data)[0]


def unusual_text(event_type: int, data: bytes, hour: int, minute: int) -> tuple[str, str]:
    if len(data) < 13:
        return "", ""
    v1 = data[0]
    v2, v3, v4 = _f32(data[1:5]), _f32(data[5:9]), _f32(data[9:13])
    if event_type == 0x03: return ("主力买入" if v1 == 0 else "主力卖出", f"{v2:.2f}/{v3:.2f}")
    if event_type == 0x04: return "加速拉升", f"{v2 * 100:.2f}%"
    if event_type == 0x05: return "加速下跌", ""
    if event_type == 0x06: return "低位反弹", f"{v2 * 100:.2f}%"
    if event_type == 0x07: return "高位回落", f"{v2 * 100:.2f}%"
    if event_type == 0x08: return "撑杆跳高", f"{v2 * 100:.2f}%"
    if event_type == 0x09: return "平台跳水", f"{v2 * 100:.2f}%"
    if event_type == 0x0A: return ("单笔冲跌" if v2 < 0 else "单笔冲涨", f"{v2 * 100:.2f}%")
    if event_type == 0x0B:
        if v3 == 0: return "区间放量平", f"{v2:.1f}倍"
        return ("区间放量跌" if v3 < 0 else "区间放量涨", f"{v2:.1f}倍{v3 * 100:.2f}%")
    if event_type == 0x0C: return "区间缩量", ""
    if event_type == 0x10: return "大单托盘", f"{v4:.2f}/{v3:.2f}"
    if event_type == 0x11: return "大单压盘", f"{v2:.2f}/{v3:.2f}"
    if event_type == 0x12: return "大单锁盘", ""
    if event_type == 0x13: return ("竞价试买" if v1 == 0 else "竞价试卖", f"{v2:.2f}/{v3:.2f}")
    if event_type == 0x14:
        if len(data) < 10: return "", ""
        direction = "涨" if v1 == 0 else "跌"
        desc = {1: f"逼近{direction}停", 2: f"封{direction}停板", 4: f"封{direction}大减", 5: f"打开{direction}停"}.get(data[1], "")
        return desc, f"{_f32(data[2:6]):.2f}/{_f32(data[6:10]):.2f}"
    if event_type == 0x15:
        prefix = "竞价" if hour == 9 and minute <= 30 else "尾盘"
        desc = {0: "打压", 1: "对倒", 2: "拉升"}.get(v1, "打压")
        return prefix + desc, f"{v2 * 100:.2f}%/{v3:.2f}"
    if event_type == 0x16: return ("盘中弱势" if v2 < 0 else "盘中强势", f"{v2 * 100:.2f}%")
    if event_type == 0x1D: return "急速拉升", f"{v2 * 100:.2f}%"
    if event_type == 0x1E: return "急速下跌", f"{v2 * 100:.2f}%"
    return "", ""


def parse_unusual(body: bytes) -> list[Unusual]:
    if len(body) < 2:
        raise ValueError(f"unusual response too short: {len(body)}")
    count = _u16(body[:2])
    if count > (len(body) - 2) // 32:
        raise ValueError(f"truncated unusual response: count={count} bytes={len(body)}")
    result: list[Unusual] = []
    for i in range(count):
        row = body[2 + i * 32: 2 + (i + 1) * 32]
        market, code = _u16(row[:2]), _clean_text(row[2:8])
        event_type, index = row[9], _u16(row[11:13])
        hour, minute_second = row[29], _u16(row[30:32])
        minute, second = minute_second // 100, minute_second % 100
        desc, value = unusual_text(event_type, row[15:28], hour, minute)
        result.append(Unusual(index, market, code, f"{hour:02d}:{minute:02d}:{second:02d}", desc, value, event_type))
    return result


def parse_security_list(body: bytes) -> list[Security]:
    if len(body) < 2:
        raise ValueError("security list response too short")
    count = _u16(body[:2])
    record_size = 37
    if count > (len(body) - 2) // record_size:
        raise ValueError(f"truncated security list: count={count} bytes={len(body)}")
    return [Security(_clean_text(body[2 + i * record_size:8 + i * record_size]),
                     _clean_text(body[10 + i * record_size:26 + i * record_size])) for i in range(count)]


class TDXClient:
    def __init__(self, hosts: Iterable[str] = (), timeout: float = 6.0):
        self.hosts = tuple(hosts) or DEFAULT_HOSTS
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._lock = Lock()
        self._seq = 0

    def connect(self) -> str:
        self.close()
        candidates = []
        for host in self.hosts:
            started = time.monotonic()
            try:
                sock = socket.create_connection(_split_host(host), timeout=min(self.timeout, 0.8))
                sock.close()
                candidates.append((time.monotonic() - started, host))
            except OSError:
                continue
        ordered = [h for _, h in sorted(candidates)] + [h for h in self.hosts if h not in {x[1] for x in candidates}]
        last: Exception | None = None
        for host in ordered:
            try:
                self._sock = socket.create_connection(_split_host(host), timeout=self.timeout)
                self._exchange(METHOD_HELLO, 0x01, b"\x01")
                return host
            except Exception as exc:
                last = exc
                self.close()
        raise ConnectionError(f"no TDX host available: {last}")

    def close(self) -> None:
        if self._sock is not None:
            try: self._sock.close()
            finally: self._sock = None

    def get_unusual(self, market: int, start: int, count: int) -> list[Unusual]:
        payload = struct.pack("<HII", market, start, count)
        return parse_unusual(self._exchange(METHOD_UNUSUAL, 0x00, payload))

    def get_security_count(self, market: int) -> int:
        body = self._exchange(METHOD_SECURITY_COUNT, 0x01, struct.pack("<HI", market, 0))
        if len(body) < 2: raise ValueError("security count response too short")
        return _u16(body[:2])

    def get_security_list(self, market: int, start: int, count: int) -> list[Security]:
        body = self._exchange(METHOD_SECURITY_LIST, 0x01, struct.pack("<HIII", market, start, count, 0))
        return parse_security_list(body)

    def _exchange(self, method: int, packet_type: int, payload: bytes) -> bytes:
        with self._lock:
            if self._sock is None: raise ConnectionError("TDX connection is not established")
            self._seq += 1
            packet = struct.pack("<BIBHHH", 0x0C, self._seq, packet_type, 2 + len(payload), 2 + len(payload), method) + payload
            self._sock.settimeout(self.timeout)
            self._sock.sendall(packet)
            raw = _read_exact(self._sock, 16)
            _i1, _i2, seq, _i3, response_method, zip_size, unzip_size = struct.unpack("<IBIBHHH", raw)
            if seq != self._seq: raise ValueError(f"TDX response sequence mismatch: got {seq} want {self._seq}")
            if response_method != method: raise ValueError(f"TDX response method mismatch: got {response_method:#x}")
            if zip_size > 65536 or unzip_size > 1048576: raise ValueError("TDX response too large")
            body = _read_exact(self._sock, zip_size)
            if zip_size == unzip_size: return body
            body = zlib.decompress(body)
            if len(body) != unzip_size: raise ValueError("invalid expanded TDX response size")
            return body


def _split_host(value: str) -> tuple[str, int]:
    host, port = value.rsplit(":", 1)
    return host, int(port)


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        part = sock.recv(size - len(chunks))
        if not part: raise ConnectionError("TDX connection closed")
        chunks.extend(part)
    return bytes(chunks)
