import struct

from stock_unusual.store import events_from_tdx, is_a_stock
from stock_unusual.tdx import parse_unusual, unusual_text


def test_market_code_filter():
    assert is_a_stock(0, "000001")
    assert is_a_stock(0, "300750")
    assert is_a_stock(1, "600000")
    assert is_a_stock(2, "920001")
    assert not is_a_stock(0, "399001")
    assert not is_a_stock(1, "510300")
    assert not is_a_stock(2, "899001")


def test_unusual_parser_and_text():
    row = bytearray(32)
    struct.pack_into("<H", row, 0, 1)
    row[2:8] = b"600000"
    row[9] = 0x04
    struct.pack_into("<H", row, 11, 7)
    row[29] = 10
    struct.pack_into("<H", row, 30, 2030)
    struct.pack_into("<f", row, 16, 0.1234)
    data = struct.pack("<H", 1) + row
    parsed = parse_unusual(data)
    assert parsed[0].code == "600000"
    assert parsed[0].index == 7
    assert parsed[0].time == "10:20:30"
    assert parsed[0].desc == "加速拉升"
    assert parsed[0].value == "12.34%"


def test_events_from_tdx_filters_non_a_shares():
    from stock_unusual.tdx import Unusual
    rows = [Unusual(1, 0, "399001", "10:00:00", "", "", 1), Unusual(2, 1, "600000", "10:00:01", "x", "y", 2)]
    events = events_from_tdx("2026-10-17", 0, rows)
    assert [x.code for x in events] == ["600000"]
