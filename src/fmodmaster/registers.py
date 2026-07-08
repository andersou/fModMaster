from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Final, Iterable, assert_never

import flet as ft


class Base(IntEnum):
    Bin = 2
    Dec = 10
    Hex = 16
    Float = 3


class FloatEndian(IntEnum):
    """Byte and word order for interpreting two 16-bit registers as IEEE 754 float32."""

    ABCD = 0  # Big Endian, Big Endian   (BE_BE)
    DCBA = 1  # Little Endian, Little Endian (LE_LE)
    BADC = 2  # Big Endian, Little Endian  (BE_LE)
    CDAB = 3  # Little Endian, Big Endian  (LE_BE)

    @property
    def label(self) -> str:
        _labels: dict[FloatEndian, str] = {
            FloatEndian.ABCD: "ABCD (BE_BE)",
            FloatEndian.DCBA: "DCBA (LE_LE)",
            FloatEndian.BADC: "BADC (BE_LE)",
            FloatEndian.CDAB: "CDAB (LE_BE)",
        }
        return _labels[self]


BaseValue = Base | int

_GRID_WIDTH: Final = 10
_COLUMN_LABELS: Final = tuple(f"{col:02d}" for col in range(_GRID_WIDTH))
_COLOR_TEXT: Final = ft.Colors.ON_SURFACE
_COLOR_ERROR: Final = ft.Colors.ERROR
_COLOR_OUT_OF_RANGE_BG: Final = ft.Colors.SURFACE_CONTAINER_HIGHEST
_INLINE_ERROR: Final = "Invalid value"


def float_from_regs(reg0: int, reg1: int, endian: FloatEndian) -> float:
    """Combine two 16-bit register values into an IEEE 754 float32.

    Mirrors the libmodbus ``modbus_get_float_*`` family.
    """
    match endian:
        case FloatEndian.ABCD:
            return struct.unpack(">f", struct.pack(">HH", reg0, reg1))[0]
        case FloatEndian.DCBA:
            return struct.unpack(">f", struct.pack(">HH", reg0, reg1)[::-1])[0]
        case FloatEndian.BADC:
            return struct.unpack(">f", struct.pack("<HH", reg0, reg1))[0]
        case FloatEndian.CDAB:
            return struct.unpack(">f", struct.pack(">HH", reg1, reg0))[0]
        case unreachable:
            assert_never(unreachable)


def float_to_regs(value: float, endian: FloatEndian) -> tuple[int, int]:
    """Convert an IEEE 754 float32 into two 16-bit register values."""
    packed = struct.pack(">f", value)
    match endian:
        case FloatEndian.ABCD:
            return struct.unpack(">HH", packed)  # type: ignore[return-value]
        case FloatEndian.DCBA:
            return struct.unpack(">HH", packed[::-1])  # type: ignore[return-value]
        case FloatEndian.BADC:
            return struct.unpack("<HH", packed)  # type: ignore[return-value]
        case FloatEndian.CDAB:
            hi, lo = struct.unpack(">HH", packed)
            return lo, hi
        case unreachable:
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class RegisterCell:
    address: int
    value: int | None
    visible_text: str
    is_used: bool
    is_editable: bool
    is_valid: bool
    tooltip: str


def format_value(value: int, base: BaseValue, is_16bit: bool, signed: bool) -> str:
    normalized = _normalize_base(base)
    if normalized is Base.Float:
        return _format_int_fallback(value)
    match normalized:
        case Base.Bin:
            if is_16bit:
                return format(_uint16(value), "016b")
            return format(value, "b")
        case Base.Dec:
            if signed and is_16bit:
                return str(_int16(value))
            if is_16bit:
                return str(_uint16(value))
            return str(value)
        case Base.Hex:
            if is_16bit:
                return format(_uint16(value), "04x")
            return format(value, "x")
        case unreachable:
            assert_never(unreachable)


def _format_int_fallback(value: int) -> str:
    """Fallback integer display when a register has no float pair."""
    return str(_uint16(value))


class RegistersModel:
    def __init__(
        self,
        start_addr: int,
        qty: int,
        base: BaseValue = Base.Dec,
        signed: bool = False,
        is_write: bool = False,
        *,
        is_16bit: bool = True,
        values: Iterable[int | None] | None = None,
        valid: bool = True,
        float_endian: FloatEndian = FloatEndian.ABCD,
    ) -> None:
        if qty < 1:
            raise ValueError("qty must be at least 1")
        self.start_addr = start_addr
        self.qty = qty
        self.base = _normalize_base(base)
        self.signed = signed
        self.is_write = is_write
        self.is_16bit = is_16bit
        self.float_endian = float_endian
        self._values = list(values) if values is not None else []
        self.cells = self._build_cells(valid=valid)
        self.editable_fields: dict[int, ft.TextField] = {}

    @property
    def is_float_mode(self) -> bool:
        return self.base is Base.Float

    def to_datatable(self) -> ft.DataTable:
        self.editable_fields.clear()
        labels = (_COLUMN_LABELS[0],) if self.qty == 1 else _COLUMN_LABELS
        columns = [ft.DataColumn(label=label) for label in labels]
        rows = [
            ft.DataRow(
                cells=[self._build_data_cell(cell) for cell in row],
                data=self.row_headers[index],
            )
            for index, row in enumerate(self.cells)
        ]
        return ft.DataTable(columns=columns, rows=rows, data=self)

    @property
    def row_headers(self) -> tuple[str, ...]:
        if self.qty == 1:
            return (_format_address(self.start_addr, self.base),)
        first_row = self.start_addr // _GRID_WIDTH
        last_row = (self.start_addr + self.qty - 1) // _GRID_WIDTH
        return tuple(
            _format_address(row * _GRID_WIDTH, self.base)
            for row in range(first_row, last_row + 1)
        )

    def collect_write_values(self) -> list[int] | None:
        if self.is_float_mode:
            return self._collect_float_write_values()
        collected: list[int] = []
        for addr in range(self.start_addr, self.start_addr + self.qty):
            field = self.editable_fields.get(addr)
            if field is None:
                continue
            parsed = self._parse_edit_value(field.value or "")
            if parsed is None:
                _mark_text_field(field, is_valid=False)
                return None
            _mark_text_field(field, is_valid=True)
            collected.append(parsed)
        return collected

    def _collect_float_write_values(self) -> list[int] | None:
        collected: list[int] = []
        for addr in range(self.start_addr, self.start_addr + self.qty, 2):
            field = self.editable_fields.get(addr)
            if field is None:
                # Odd register of a pair — value already handled by even side.
                continue
            parsed = self._parse_edit_float(field.value or "")
            if parsed is None:
                _mark_text_field(field, is_valid=False)
                return None
            _mark_text_field(field, is_valid=True)
            reg0, reg1 = float_to_regs(parsed, self.float_endian)
            collected.append(reg0)
            collected.append(reg1)
        return collected

    def _build_cells(self, *, valid: bool) -> list[list[RegisterCell]]:
        if self.qty == 1:
            return [[self._used_cell(self.start_addr, 0, valid=valid)]]

        offset = self.start_addr % _GRID_WIDTH
        row_count = (offset + self.qty - 1) // _GRID_WIDTH + 1
        rows: list[list[RegisterCell]] = []
        for row_index in range(row_count):
            row: list[RegisterCell] = []
            for col in range(_GRID_WIDTH):
                flat_index = row_index * _GRID_WIDTH + col
                address = self.start_addr - offset + flat_index
                if offset <= flat_index < offset + self.qty:
                    row.append(
                        self._used_cell(address, flat_index - offset, valid=valid)
                    )
                else:
                    row.append(_unused_cell(address))
            rows.append(row)
        return rows

    def _used_cell(self, address: int, value_index: int, *, valid: bool) -> RegisterCell:
        cell = self._used_cell_core(address, value_index, valid=valid)
        if not self.is_float_mode:
            return cell
        return self._wrap_float_cell(cell, address, value_index, valid=valid)

    def _used_cell_core(
        self, address: int, value_index: int, *, valid: bool
    ) -> RegisterCell:
        value = self._values[value_index] if value_index < len(self._values) else None
        if not valid:
            text = "-/-"
        elif value is None:
            text = "-"
        else:
            text = format_value(value, self.base, self.is_16bit, self.signed)
        return RegisterCell(
            address,
            value,
            text,
            True,
            self.is_write,
            valid,
            f"Address : {_format_address(address, self.base)}",
        )

    def _wrap_float_cell(
        self, cell: RegisterCell, address: int, value_index: int, *, valid: bool
    ) -> RegisterCell:
        """For float mode: even-index cells show float; odd-index cells show '—'."""
        if value_index % 2 == 1:
            # Continuation cell — part of the float pair above.
            return RegisterCell(
                address,
                cell.value,
                "—",
                True,
                False,  # not editable
                valid,
                cell.tooltip,
            )
        # Even index: try to form a float from this register and the next.
        next_value = (
            self._values[value_index + 1]
            if value_index + 1 < len(self._values)
            else None
        )
        # If this is the last register of an odd-length qty, show integer fallback.
        if (
            next_value is None
            and self.qty % 2 == 1
            and value_index == self.qty - 1
        ):
            return cell
        if not valid:
            return RegisterCell(
                address,
                cell.value,
                "-/-",
                True,
                self.is_write,
                valid,
                f"Address : {_format_address(address, self.base)} → "
                f"{_format_address(address + 1, self.base)}",
            )
        if cell.value is None or next_value is None:
            # One or both registers missing — show dash.
            return RegisterCell(
                address,
                cell.value,
                "-",
                True,
                self.is_write,
                valid,
                f"Address : {_format_address(address, self.base)} → "
                f"{_format_address(address + 1, self.base)}",
            )
        float_val = float_from_regs(cell.value, next_value, self.float_endian)
        return RegisterCell(
            address,
            cell.value,
            _format_float_display(float_val),
            True,
            self.is_write,
            valid,
            f"Address : {_format_address(address, self.base)} → "
            f"{_format_address(address + 1, self.base)}",
        )

    def _build_data_cell(self, cell: RegisterCell) -> ft.DataCell:
        if cell.is_editable and cell.is_used:
            field = self._build_text_field(cell)
            self.editable_fields[cell.address] = field
            return ft.DataCell(field, tooltip=cell.tooltip)
        text = ft.Text(
            cell.visible_text,
            color=_COLOR_TEXT if cell.is_used and cell.is_valid else _COLOR_ERROR,
            bgcolor=_COLOR_OUT_OF_RANGE_BG if not cell.is_used else None,
        )
        return ft.DataCell(text, tooltip=cell.tooltip or None)

    def _build_text_field(self, cell: RegisterCell) -> ft.TextField:
        field = ft.TextField(
            value="" if cell.visible_text == "-" else cell.visible_text,
            tooltip=cell.tooltip,
            max_length=self._max_length(),
            on_change=None,
            on_blur=None,
        )

        if self.is_float_mode:

            def validate_field() -> None:
                _mark_text_field(
                    field, is_valid=self._parse_edit_float(field.value) is not None
                )
        else:

            def validate_field() -> None:
                _mark_text_field(
                    field, is_valid=self._parse_edit_value(field.value) is not None
                )

        field.on_change = validate_field
        field.on_blur = validate_field
        _mark_text_field(field, is_valid=cell.is_valid)
        return field

    def _max_length(self) -> int | None:
        if self.is_float_mode:
            return 20
        if not self.is_16bit:
            return 1
        match self.base:
            case Base.Bin:
                return 16
            case Base.Dec:
                return 6 if self.signed else 5
            case Base.Hex:
                return 4
            case Base.Float:
                return 20
            case unreachable:
                assert_never(unreachable)

    def _parse_edit_value(self, raw: str) -> int | None:
        text = raw.strip()
        if not text:
            return None
        if self.is_float_mode:
            return None  # Float validation uses _parse_edit_float instead
        if not self.is_16bit:
            return int(text) if text in {"0", "1"} else None
        match self.base:
            case Base.Bin:
                if len(text) > 16 or any(char not in {"0", "1"} for char in text):
                    return None
                return int(text, 2)
            case Base.Dec:
                if not _is_decimal_text(text):
                    return None
                parsed = int(text, 10)
                if self.signed:
                    return parsed if -32768 <= parsed <= 32767 else None
                return parsed if 0 <= parsed <= 65535 else None
            case Base.Hex:
                if len(text) > 4 or any(char not in _HEX_DIGITS for char in text):
                    return None
                return int(text, 16)
            case Base.Float:
                return None  # Float validation uses _parse_edit_float instead
            case unreachable:
                assert_never(unreachable)

    def _parse_edit_float(self, raw: str) -> float | None:
        """Parse a float string and validate it fits in IEEE 754 float32."""
        text = raw.strip()
        if not text:
            return None
        try:
            val = float(text)
        except (ValueError, OverflowError):
            return None
        # Check float32 range (finite only — NaN/Inf rejected here).
        if math.isfinite(val) and -3.4e38 <= val <= 3.4e38:
            return val
        return None

_HEX_DIGITS: Final = frozenset("0123456789abcdefABCDEF")


def build_grid(
    start_addr: int,
    qty: int,
    base: BaseValue,
    signed: bool,
    is_write: bool,
    *,
    is_16bit: bool = True,
    values: Iterable[int | None] | None = None,
    valid: bool = True,
    float_endian: FloatEndian = FloatEndian.ABCD,
) -> ft.DataTable:
    return RegistersModel(
        start_addr,
        qty,
        base,
        signed,
        is_write,
        is_16bit=is_16bit,
        values=values,
        valid=valid,
        float_endian=float_endian,
    ).to_datatable()


def is_signed_visible(base: BaseValue) -> bool:
    return _normalize_base(base) is Base.Dec


def _normalize_base(base: BaseValue) -> Base:
    try:
        return Base(int(base))
    except ValueError:
        return Base.Dec


def _format_float_display(value: float) -> str:
    """Format a float for display in the register table.

    Uses compact notation: up to 6 significant digits, trailing zeros
    stripped, avoiding exponential notation for reasonable ranges.
    """
    return f"{value:.6g}"


def _uint16(value: int) -> int:
    return value & 0xFFFF


def _int16(value: int) -> int:
    unsigned = _uint16(value)
    return unsigned - 0x10000 if unsigned >= 0x8000 else unsigned


def _format_address(address: int, base: Base) -> str:
    if base is Base.Float:
        return f"{address:02d}"
    match base:
        case Base.Bin:
            return format(address, "b")
        case Base.Dec:
            return f"{address:02d}"
        case Base.Hex:
            return format(address, "x")
        case unreachable:
            assert_never(unreachable)


def _unused_cell(address: int) -> RegisterCell:
    return RegisterCell(address, None, "x", False, False, False, "")


def _mark_text_field(field: ft.TextField, *, is_valid: bool) -> None:
    field.error = None if is_valid else _INLINE_ERROR
    field.color = _COLOR_TEXT if is_valid else _COLOR_ERROR
    field.border_color = None if is_valid else _COLOR_ERROR


def _is_decimal_text(text: str) -> bool:
    if text.startswith("-"):
        return len(text) > 1 and text[1:].isdigit()
    return text.isdigit()
