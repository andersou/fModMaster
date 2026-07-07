from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Final, Iterable, assert_never

import flet as ft


class Base(IntEnum):
    Bin = 2
    Dec = 10
    Hex = 16


BaseValue = Base | int

_GRID_WIDTH: Final = 10
_COLUMN_LABELS: Final = tuple(f"{col:02d}" for col in range(_GRID_WIDTH))
_TEXT_RED: Final = ft.Colors.RED
_TEXT_BLACK: Final = ft.Colors.BLACK
_OUT_OF_RANGE_BG: Final = ft.Colors.GREY_200
_INLINE_ERROR: Final = "Invalid value"


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
    ) -> None:
        if qty < 1:
            raise ValueError("qty must be at least 1")
        self.start_addr = start_addr
        self.qty = qty
        self.base = _normalize_base(base)
        self.signed = signed
        self.is_write = is_write
        self.is_16bit = is_16bit
        self._values = list(values) if values is not None else []
        self.cells = self._build_cells(valid=valid)
        self.editable_fields: dict[int, ft.TextField] = {}

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

    def _build_data_cell(self, cell: RegisterCell) -> ft.DataCell:
        if cell.is_editable and cell.is_used:
            field = self._build_text_field(cell)
            self.editable_fields[cell.address] = field
            return ft.DataCell(field, tooltip=cell.tooltip)
        text = ft.Text(cell.visible_text, color=_TEXT_BLACK if cell.is_used and cell.is_valid else _TEXT_RED, bgcolor=_OUT_OF_RANGE_BG if not cell.is_used else None)
        return ft.DataCell(text, tooltip=cell.tooltip or None)

    def _build_text_field(self, cell: RegisterCell) -> ft.TextField:
        field = ft.TextField(value="" if cell.visible_text == "-" else cell.visible_text, tooltip=cell.tooltip, max_length=self._max_length(), on_change=None, on_blur=None)

        def validate_field() -> None:
            _mark_text_field(field, is_valid=self._parse_edit_value(field.value) is not None)

        field.on_change = validate_field
        field.on_blur = validate_field
        _mark_text_field(field, is_valid=cell.is_valid)
        return field

    def _max_length(self) -> int | None:
        if not self.is_16bit:
            return 1
        match self.base:
            case Base.Bin:
                return 16
            case Base.Dec:
                return 6 if self.signed else 5
            case Base.Hex:
                return 4
            case unreachable:
                assert_never(unreachable)

    def _parse_edit_value(self, raw: str) -> int | None:
        text = raw.strip()
        if not text:
            return None
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
            case unreachable:
                assert_never(unreachable)

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
    ).to_datatable()


def is_signed_visible(base: BaseValue) -> bool:
    return _normalize_base(base) is Base.Dec


def _normalize_base(base: BaseValue) -> Base:
    try:
        return Base(int(base))
    except ValueError:
        return Base.Dec


def _uint16(value: int) -> int:
    return value & 0xFFFF


def _int16(value: int) -> int:
    unsigned = _uint16(value)
    return unsigned - 0x10000 if unsigned >= 0x8000 else unsigned


def _format_address(address: int, base: Base) -> str:
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
    field.color = _TEXT_BLACK if is_valid else _TEXT_RED
    field.border_color = None if is_valid else _TEXT_RED


def _is_decimal_text(text: str) -> bool:
    if text.startswith("-"):
        return len(text) > 1 and text[1:].isdigit()
    return text.isdigit()
