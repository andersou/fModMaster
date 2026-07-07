from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import flet as ft

from fmodmaster.bus_monitor import (
    BusMonitorController,
    BusMonitorModel,
    build_bus_monitor_dialog,
    parse_raw_line,
)
from fmodmaster.config import Settings


class FakePage:
    def __init__(self) -> None:
        self.dialog: ft.AlertDialog | None = None
        self.overlay: list[ft.Control] = []
        self.update_count = 0

    def run_task(
        self,
        handler: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        coro = handler(*args, **kwargs)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def update(self) -> None:
        self.update_count += 1


class FakeComm:
    def __init__(self) -> None:
        self.mode: str | None = "RTU"
        self.on_raw: Callable[[str, bytes], None] | None = None


def _settings(max_lines: int = 60) -> Settings:
    settings = Settings()
    settings.max_no_of_lines = str(max_lines)
    return settings


def test_parse_rtu_tx_read_request_fields() -> None:
    line = "Tx 2026-07-07T12:00:00.000 RTU 0103010000027105"

    parsed = parse_raw_line(line)

    assert parsed.unparseable_reason is None
    assert parsed.line_type == "Tx"
    assert parsed.timestamp == "2026-07-07T12:00:00.000"
    assert parsed.mode == "RTU"
    assert parsed.slave_or_unit == 1
    assert parsed.function_code == 0x03
    assert parsed.start_addr == 0x0100
    assert parsed.quantity == 2
    assert parsed.crc == 0x0571


def test_parse_tcp_tx_read_request_fields() -> None:
    line = "Tx 2026-07-07T12:00:00.000 TCP 000100000006010301000002"

    parsed = parse_raw_line(line)

    assert parsed.unparseable_reason is None
    assert parsed.mode == "TCP"
    assert parsed.transaction_id == 1
    assert parsed.protocol_id == 0
    assert parsed.length == 6
    assert parsed.slave_or_unit == 1
    assert parsed.function_code == 0x03
    assert parsed.start_addr == 0x0100
    assert parsed.quantity == 2


def test_parse_rtu_read_response_values_and_byte_count() -> None:
    line = "Rx 2026-07-07T12:00:00.000 RTU 010304000A0014DA3E"

    parsed = parse_raw_line(line)

    assert parsed.unparseable_reason is None
    assert parsed.byte_count == 4
    assert parsed.values == (10, 20)


def test_parse_exception_response_code() -> None:
    line = "Rx 2026-07-07T12:00:00.000 RTU 018302C0F1"

    parsed = parse_raw_line(line)

    assert parsed.function_code == 0x83
    assert parsed.exception_code == 2


def test_malformed_short_hex_is_unparseable_not_crash() -> None:
    parsed = parse_raw_line("Rx 2026-07-07T12:00:00.000 RTU 01")


    assert parsed.unparseable_reason is not None
    assert "unparseable" in parsed.to_detail_text().lower()


def test_line_cap_evicts_oldest_beyond_limit() -> None:
    model = BusMonitorModel(max_lines=3)
    model.enable_capture(True)

    for index in range(5):
        model.add_raw("tx", bytes([1, 3, 0, index, 0, 1, 0, 0]), mode="RTU")

    assert len(model.lines) == 3
    assert [line.raw_hex for line in model.lines] == [
        "0103000200010000",
        "0103000300010000",
        "0103000400010000",
    ]


def test_selecting_line_populates_detail_panel() -> None:
    page = FakePage()
    comm = FakeComm()
    controller = BusMonitorController(page, comm, _settings())
    controller.open()
    assert comm.on_raw is not None
    comm.on_raw("tx", bytes.fromhex("0103010000027105"))

    controller.select_line(0)

    assert "Type: Tx" in controller.controls.detail_text.value
    assert "Slave Address: 1" in controller.controls.detail_text.value
    assert "Function Code: 0x03" in controller.controls.detail_text.value
    assert "Start Address: 256" in controller.controls.detail_text.value
    assert "Quantity: 2" in controller.controls.detail_text.value


def test_save_writes_raw_lines_to_file(tmp_path: Path) -> None:
    model = BusMonitorModel(max_lines=10)
    model.enable_capture(True)
    model.add_raw("tx", bytes.fromhex("0103010000027105"), mode="RTU")
    destination = tmp_path / "bus-monitor.txt"

    model.save_to_file(destination)

    assert destination.read_text(encoding="utf-8").startswith("Tx ")
    assert "0103010000027105" in destination.read_text(encoding="utf-8")


def test_clear_empties_model_and_list_view() -> None:
    page = FakePage()
    comm = FakeComm()
    controller = BusMonitorController(page, comm, _settings())
    controller.open()
    assert comm.on_raw is not None
    comm.on_raw("tx", bytes.fromhex("0103010000027105"))

    controller.clear()

    assert controller.model.lines == []
    assert controller.controls.raw_list.controls == []
    assert controller.controls.detail_text.value == "Select a raw line to decode ADU/PDU fields."


def test_sys_line_can_be_generated_internally() -> None:
    page = FakePage()
    comm = FakeComm()
    controller = BusMonitorController(page, comm, _settings())
    controller.open()

    controller.add_sys("Connected")

    assert controller.model.lines[0].line_type == "Sys"
    assert controller.model.lines[0].raw_hex == "Connected"


def test_open_and_close_subscribe_and_restore_on_raw() -> None:
    page = FakePage()
    comm = FakeComm()
    previous_calls: list[tuple[str, bytes]] = []
    comm.on_raw = lambda direction, data: previous_calls.append((direction, data))
    dialog = build_bus_monitor_dialog(page, comm, _settings(max_lines=3))
    assert isinstance(dialog.data, BusMonitorController)
    controller = dialog.data

    controller.open()
    assert comm.on_raw is not None
    comm.on_raw("rx", bytes.fromhex("010302000A3843"))
    controller.close()

    assert comm.on_raw is not None
    comm.on_raw("tx", b"abc")
    assert previous_calls == [("tx", b"abc")]
    assert controller.model.capture_enabled is False
    assert dialog.open is False
