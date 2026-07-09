"""Bus Monitor dialog, raw-line model, and Modbus ADU/PDU decoder.

allow: SIZE_OK -- this task is constrained to a single bus_monitor.py module that
must contain the Flet controller, capture model, and RTU/TCP parser until later
feature waves are allowed to split dialogs and protocol decoding.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Protocol

import flet as ft

_DEFAULT_MAX_LINES: Final = 60
_READ_FUNCTIONS: Final = frozenset({0x01, 0x02, 0x03, 0x04})
_SINGLE_WRITE_FUNCTIONS: Final = frozenset({0x05, 0x06})
_MULTI_WRITE_FUNCTIONS: Final = frozenset({0x0F, 0x10})
_EMPTY_DETAIL: Final = "Select a raw line to decode ADU/PDU fields."


def _file_picker_for_page(page: PageLike) -> ft.FilePicker:
    for service in page.services:
        if isinstance(service, ft.FilePicker):
            return service
    picker = ft.FilePicker()
    page.services.append(picker)
    return picker


class PageLike(Protocol):
    dialog: ft.AlertDialog | None
    services: list[Any]

    def run_task(
        self,
        handler: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        ...

    def update(self) -> None:
        ...

    def show_dialog(self, dialog: ft.AlertDialog) -> None:
        ...

    def pop_dialog(self) -> None:
        ...


class CommLike(Protocol):
    mode: str | None
    on_raw: Callable[[str, bytes], None] | None
    bus_monitor_model: Any


class SettingsLike(Protocol):
    max_no_of_lines: str



@dataclass(frozen=True, slots=True)
class RawLine:
    line_type: str
    timestamp: str
    mode: str
    raw_hex: str

    @property
    def text(self) -> str:
        return f"{self.line_type} {self.timestamp} {self.mode} {self.raw_hex}"


@dataclass(frozen=True, slots=True)
class ParsedFrame:
    line_type: str
    timestamp: str
    mode: str
    raw_hex: str
    slave_or_unit: int | None = None
    function_code: int | None = None
    start_addr: int | None = None
    quantity: int | None = None
    byte_count: int | None = None
    values: tuple[int, ...] = ()
    exception_code: int | None = None
    crc: int | None = None
    transaction_id: int | None = None
    protocol_id: int | None = None
    length: int | None = None
    unparseable_reason: str | None = None

    def to_detail_text(self) -> str:
        rows = [
            f"Type: {self.line_type}",
            f"Timestamp: {self.timestamp}",
            f"Mode: {self.mode}",
        ]
        if self.unparseable_reason is not None:
            rows.append(f"Unparseable: {self.unparseable_reason}")
            rows.append(f"Raw: {self.raw_hex}")
            return "\n".join(rows)
        if self.transaction_id is not None:
            rows.extend(
                [
                    f"Transaction ID: {self.transaction_id}",
                    f"Protocol ID: {self.protocol_id}",
                    f"Length: {self.length}",
                ]
            )
        if self.slave_or_unit is not None:
            label = "Unit ID" if self.mode.upper() == "TCP" else "Slave Address"
            rows.append(f"{label}: {self.slave_or_unit}")
        if self.function_code is not None:
            rows.append(f"Function Code: 0x{self.function_code:02X}")
        if self.start_addr is not None:
            rows.append(f"Start Address: {self.start_addr}")
        if self.quantity is not None:
            rows.append(f"Quantity: {self.quantity}")
        if self.byte_count is not None:
            rows.append(f"Byte Count: {self.byte_count}")
        if self.values:
            rows.append("Values: " + ", ".join(str(value) for value in self.values))
        if self.exception_code is not None:
            rows.append(f"Exception Code: {self.exception_code}")
        if self.crc is not None:
            rows.append(f"CRC: 0x{self.crc:04X}")
        rows.append(f"Raw: {self.raw_hex}")
        return "\n".join(rows)


@dataclass(slots=True)  # noqa: MUTABLE_OK - owns mutable Flet controls.
class BusMonitorControls:
    raw_list: ft.ListView
    detail_text: ft.Text
    dialog: ft.AlertDialog


class BusMonitorModel:
    def __init__(self, max_lines: int = _DEFAULT_MAX_LINES) -> None:
        self.max_lines = max(max_lines, 1)
        self.capture_enabled = False
        self.lines: list[RawLine] = []

    def enable_capture(self, enabled: bool) -> None:
        self.capture_enabled = enabled

    def add_raw(self, direction: str, data: bytes, *, mode: str | None) -> RawLine | None:
        line_type = _line_type(direction)
        line = RawLine(line_type, _timestamp(), mode or "-", data.hex().upper())
        self._append(line)
        return line

    def add_sys(self, message: str, *, mode: str | None) -> RawLine | None:
        line = RawLine("Sys", _timestamp(), mode or "-", message)
        self._append(line)
        return line

    def set_max_lines(self, max_lines: int) -> None:
        self.max_lines = max(max_lines, 1)
        while len(self.lines) > self.max_lines:
            self.lines.pop(0)

    def clear(self) -> None:
        self.lines.clear()

    def save_to_file(self, path: str | Path) -> None:
        with Path(path).open("w", encoding="utf-8") as handle:
            handle.write("\n".join(line.text for line in self.lines))
            if self.lines:
                handle.write("\n")

    def _append(self, line: RawLine) -> None:
        if len(self.lines) == self.max_lines:
            self.lines.pop(0)
        self.lines.append(line)


class BusMonitorController:
    def __init__(self, page: PageLike, comm: CommLike, settings: SettingsLike) -> None:
        self.page = page
        self.comm = comm
        self.settings = settings
        self.model = getattr(
            comm, "bus_monitor_model", BusMonitorModel(_max_lines_from_settings(settings))
        )
        self._previous_on_raw: Callable[[str, bytes], None] | None = None
        self.controls = self._build_controls()

    def open(self) -> ft.AlertDialog:
        self.model.set_max_lines(_max_lines_from_settings(self.settings))
        self.model.enable_capture(True)
        self._previous_on_raw = self.comm.on_raw
        self.comm.on_raw = self._on_raw
        self.controls.dialog.open = True
        self._refresh_controls()
        self.page.show_dialog(self.controls.dialog)
        return self.controls.dialog

    def close(self, _: Any = None) -> None:
        self.model.enable_capture(False)
        self.comm.on_raw = self._previous_on_raw
        self._previous_on_raw = None
        self.controls.dialog.open = False
        self._refresh_controls()
        self.page.pop_dialog()

    def clear(self, _: Any = None) -> None:
        self.model.clear()
        self._refresh_controls()
        self.page.update()

    def add_sys(self, message: str) -> None:
        self.page.run_task(self._append_sys_async, message, self.comm.mode)

    def select_line(self, index: int) -> None:
        if 0 <= index < len(self.model.lines):
            self.controls.detail_text.value = parse_raw_line(
                self.model.lines[index].text
            ).to_detail_text()
        self.page.update()

    def save_to_path(self, path: str | Path) -> None:
        self.model.save_to_file(path)

    def _build_controls(self) -> BusMonitorControls:
        raw_list = ft.ListView(expand=True, height=260, spacing=2, auto_scroll=True)
        detail_text = ft.Text(_EMPTY_DETAIL, selectable=True)
        dialog = ft.AlertDialog(
            modal=False,
            title="Bus Monitor",
            content=ft.Column(
                controls=[
                    ft.Text("Raw Data"),
                    raw_list,
                    ft.Text("ADU / PDU"),
                    ft.Container(content=detail_text, padding=8),
                ],
                width=760,
                height=520,
                spacing=8,
            ),
            actions=[
                ft.TextButton("Save", on_click=self._save_clicked),
                ft.TextButton("Clear", on_click=self.clear),
                ft.TextButton("Exit", on_click=self.close),
            ],
            open=False,
        )
        dialog.data = self
        return BusMonitorControls(raw_list, detail_text, dialog)

    def _on_raw(self, direction: str, data: bytes) -> None:
        # When the comm owns a persistent bus_monitor_model (production),
        # _trace_packet already appended the line there, and self.model IS
        # that model. When the comm has no such attribute (tests with
        # FakeComm), self.model is a standalone model we must feed here.
        comm_model = getattr(self.comm, "bus_monitor_model", None)
        if comm_model is not self.model:
            self.model.add_raw(direction, bytes(data), mode=self.comm.mode)
        self.page.run_task(self._append_raw_async)

    async def _append_raw_async(self) -> None:
        self._refresh_controls()
        self.page.update()

    async def _append_sys_async(self, message: str, mode: str | None) -> None:
        self.model.add_sys(message, mode=mode)
        self._refresh_controls()
        self.page.update()

    def _refresh_controls(self) -> None:
        self.controls.raw_list.controls = [
            self._line_button(index, line) for index, line in enumerate(self.model.lines)
        ]
        if not self.model.lines:
            self.controls.detail_text.value = _EMPTY_DETAIL

    def _line_button(self, index: int, line: RawLine) -> ft.TextButton:
        def choose(_: Any = None) -> None:
            self.select_line(index)

        return ft.TextButton(line.text, on_click=choose)

    def _save_clicked(self, _: Any = None) -> None:
        self.page.run_task(self._save_clicked_async)

    async def _save_clicked_async(self) -> None:
        picker = _file_picker_for_page(self.page)
        path = await picker.save_file(
            "Save Bus Monitor Raw Data",
            "bus-monitor.txt",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["txt"],
        )
        if path is not None:
            self.save_to_path(path)


def build_bus_monitor_dialog(
    page: PageLike, comm: CommLike, settings: SettingsLike
) -> ft.AlertDialog:
    controller = BusMonitorController(page, comm, settings)
    return controller.controls.dialog


def parse_raw_line(line: str) -> ParsedFrame:
    parts = line.split(" ", 3)
    if len(parts) != 4:
        return _unparseable("-", "-", "-", line, "line does not match raw format")
    line_type, timestamp, mode, raw_hex = parts
    if line_type == "Sys":
        return ParsedFrame(line_type, timestamp, mode, raw_hex)
    try:
        data = bytes.fromhex(raw_hex)
    except ValueError:
        return _unparseable(line_type, timestamp, mode, raw_hex, "hex bytes are invalid")
    normalized_mode = mode.upper()
    if normalized_mode == "TCP":
        return _parse_tcp(line_type, timestamp, mode, raw_hex, data)
    return _parse_rtu(line_type, timestamp, mode, raw_hex, data)


def _parse_rtu(
    line_type: str, timestamp: str, mode: str, raw_hex: str, data: bytes
) -> ParsedFrame:
    if len(data) < 4:
        return _unparseable(line_type, timestamp, mode, raw_hex, "RTU ADU is too short")
    pdu = data[1:-2]
    crc = int.from_bytes(data[-2:], "little")
    return _parse_pdu(
        line_type,
        timestamp,
        mode,
        raw_hex,
        pdu,
        slave_or_unit=data[0],
        crc=crc,
    )


def _parse_tcp(
    line_type: str, timestamp: str, mode: str, raw_hex: str, data: bytes
) -> ParsedFrame:
    if len(data) < 8:
        return _unparseable(line_type, timestamp, mode, raw_hex, "TCP ADU is too short")
    transaction_id = int.from_bytes(data[0:2], "big")
    protocol_id = int.from_bytes(data[2:4], "big")
    length = int.from_bytes(data[4:6], "big")
    unit_id = data[6]
    parsed = _parse_pdu(
        line_type,
        timestamp,
        mode,
        raw_hex,
        data[7:],
        slave_or_unit=unit_id,
    )
    return ParsedFrame(
        parsed.line_type,
        parsed.timestamp,
        parsed.mode,
        parsed.raw_hex,
        slave_or_unit=parsed.slave_or_unit,
        function_code=parsed.function_code,
        start_addr=parsed.start_addr,
        quantity=parsed.quantity,
        byte_count=parsed.byte_count,
        values=parsed.values,
        exception_code=parsed.exception_code,
        transaction_id=transaction_id,
        protocol_id=protocol_id,
        length=length,
        unparseable_reason=parsed.unparseable_reason,
    )


def _parse_pdu(
    line_type: str,
    timestamp: str,
    mode: str,
    raw_hex: str,
    pdu: bytes,
    *,
    slave_or_unit: int,
    crc: int | None = None,
) -> ParsedFrame:
    if not pdu:
        return _unparseable(line_type, timestamp, mode, raw_hex, "PDU is empty")
    function_code = pdu[0]
    payload = pdu[1:]
    if function_code & 0x80:
        exception_code = payload[0] if payload else None
        return ParsedFrame(
            line_type,
            timestamp,
            mode,
            raw_hex,
            slave_or_unit=slave_or_unit,
            function_code=function_code,
            exception_code=exception_code,
            crc=crc,
        )
    start_addr = _start_addr(function_code, payload, line_type)
    quantity = _quantity(function_code, payload, line_type)
    byte_count = _byte_count(function_code, payload, line_type)
    values = _values(function_code, payload, line_type)
    return ParsedFrame(
        line_type,
        timestamp,
        mode,
        raw_hex,
        slave_or_unit=slave_or_unit,
        function_code=function_code,
        start_addr=start_addr,
        quantity=quantity,
        byte_count=byte_count,
        values=values,
        crc=crc,
    )


def _start_addr(function_code: int, payload: bytes, line_type: str) -> int | None:
    if function_code in _SINGLE_WRITE_FUNCTIONS and len(payload) >= 4:
        return int.from_bytes(payload[0:2], "big")
    if function_code in _MULTI_WRITE_FUNCTIONS and len(payload) >= 4:
        return int.from_bytes(payload[0:2], "big")
    if function_code in _READ_FUNCTIONS and line_type == "Tx" and len(payload) >= 4:
        return int.from_bytes(payload[0:2], "big")
    return None


def _quantity(function_code: int, payload: bytes, line_type: str) -> int | None:
    if function_code in _MULTI_WRITE_FUNCTIONS and len(payload) >= 4:
        return int.from_bytes(payload[2:4], "big")
    if function_code in _READ_FUNCTIONS and line_type == "Tx" and len(payload) >= 4:
        return int.from_bytes(payload[2:4], "big")
    return None


def _byte_count(function_code: int, payload: bytes, line_type: str) -> int | None:
    if function_code in _MULTI_WRITE_FUNCTIONS and len(payload) >= 5:
        return payload[4]
    if function_code in _READ_FUNCTIONS and line_type == "Rx" and payload:
        return payload[0]
    return None


def _values(function_code: int, payload: bytes, line_type: str) -> tuple[int, ...]:
    if function_code in _SINGLE_WRITE_FUNCTIONS and len(payload) >= 4:
        return (int.from_bytes(payload[2:4], "big"),)
    if function_code in _MULTI_WRITE_FUNCTIONS and line_type == "Tx" and len(payload) >= 5:
        return _payload_values(payload[5 : 5 + payload[4]])
    if function_code in _READ_FUNCTIONS and line_type == "Rx" and payload:
        return _payload_values(payload[1 : 1 + payload[0]])
    return ()


def _payload_values(raw: bytes) -> tuple[int, ...]:
    if len(raw) >= 2 and len(raw) % 2 == 0:
        return tuple(int.from_bytes(raw[index : index + 2], "big") for index in range(0, len(raw), 2))
    return tuple(raw)


def _unparseable(
    line_type: str, timestamp: str, mode: str, raw_hex: str, reason: str
) -> ParsedFrame:
    return ParsedFrame(line_type, timestamp, mode, raw_hex, unparseable_reason=reason)


def _line_type(direction: str) -> str:
    if direction == "tx":
        return "Tx"
    if direction == "rx":
        return "Rx"
    return "Sys"


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _max_lines_from_settings(settings: SettingsLike) -> int:
    try:
        value = int(settings.max_no_of_lines)
    except ValueError:
        return _DEFAULT_MAX_LINES
    return value if value > 0 else _DEFAULT_MAX_LINES


BusMonitor = BusMonitorController
