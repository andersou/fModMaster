from __future__ import annotations

import math
import struct
from collections.abc import Callable, Mapping
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
CellWrapper = Callable[[int, ft.Control], ft.Control]

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


def float_owner_for(address: int, format_map: dict[int, Base]) -> int | None:
    """Return the address N whose float consumes ``address`` as its continuation.

    A float at N consumes N+1. If ``address`` is the continuation register of a
    float stored at ``address - 1`` (and that earlier address is within the map
    and set to ``Base.Float``), return ``address - 1``. Otherwise return None.
    """
    if address < 1:
        return None
    owner = address - 1
    if format_map.get(owner) is Base.Float:
        return owner
    return None


def validate_format_assignment(
    address: int,
    base: Base,
    format_map: dict[int, Base],
) -> str | None:
    """Return an error message if assigning ``base`` at ``address`` is invalid.

    Cases:
    - Assigning *any* format to a register already consumed as a float
      continuation (address N+1 of an existing float at N) is rejected.
    - Assigning ``Base.Float`` at N when N+1 already has an explicit format is
      rejected (would silently overwrite the user's choice).
    Returns None when the assignment is allowed.
    """
    owner = float_owner_for(address, format_map)
    if owner is not None:
        return f"Register {address} is consumed by float at address {owner}"
    if base is Base.Float:
        next_addr = address + 1
        if next_addr in format_map and format_map[next_addr] is not Base.Float:
            return (
                f"Register {next_addr} already has an explicit format; "
                f"cannot extend float at address {address}"
            )
    return None


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
        default_base: BaseValue | None = None,
        format_map: Mapping[int, BaseValue] | None = None,
        float_endian_map: Mapping[int, FloatEndian | int] | None = None,
        cell_wrapper: CellWrapper | None = None,
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

        # Per-address format configuration. When format_map is empty/None,
        # behaviour is identical to the legacy single-base model. When present,
        # per-address logic takes over for the addresses listed and falls back
        # to default_base for unlisted addresses.
        if default_base is None:
            self.default_base = self.base
        else:
            self.default_base = _normalize_base(default_base)
        self.format_map: dict[int, Base] = {
            addr: _normalize_base(v) for addr, v in (format_map or {}).items()
        }
        self.float_endian_map: dict[int, FloatEndian] = {
            addr: _coerce_endian(v)
            for addr, v in (float_endian_map or {}).items()
        }
        self._cell_wrapper = cell_wrapper
        self._per_address_mode = bool(self.format_map)

        self._values = list(values) if values is not None else []
        self.cells = self._build_cells(valid=valid)
        self.editable_fields: dict[int, ft.TextField] = {}

    # ------------------------------------------------------------------ #
    # Per-address selection
    # ------------------------------------------------------------------ #

    @property
    def is_float_mode(self) -> bool:
        """Legacy single-base float mode (whole grid is float)."""
        return self.base is Base.Float and not self._per_address_mode

    def _format_for(self, address: int) -> Base:
        if not self._per_address_mode:
            return self.base
        return self.format_map.get(address, self.default_base)

    def _float_endian_for(self, address: int) -> FloatEndian:
        return self.float_endian_map.get(address, self.float_endian)

    def _is_float_at(self, address: int, value_index: int) -> bool:
        """True if a register at ``address`` should be displayed as a float.

        - Per-address mode: ``format_map[address] is Base.Float`` and the paired
          register exists in the configured range.
        - Legacy mode: ``self.base is Base.Float``.
        A float at the last register of an odd-qty range falls back to int
        (handled by the cell builder).
        """
        if self._per_address_mode:
            if self.format_map.get(address) is not Base.Float:
                return False
            # Float at the last register of an odd-qty range -> no pair -> int.
            if value_index == self.qty - 1:
                return False
            return True
        return self.base is Base.Float

    def _is_continuation_at(self, address: int, value_index: int) -> bool:
        """True if ``address`` is the N+1 continuation of a float at N."""
        if value_index % 2 == 1 and self._is_float_at(address - 1, value_index - 1):
            return True
        if self._per_address_mode and float_owner_for(address, self.format_map) is not None:
            return True
        return False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

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
        return ft.DataTable(
            columns=columns,
            rows=rows,
            data=self,
            column_spacing=8 if self.is_write else 24,
            horizontal_margin=8 if self.is_write else 16,
            data_row_min_height=36 if self.is_write else None,
            heading_row_height=36,
        )

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
        if self._per_address_mode:
            return self._collect_per_address_write_values()
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

    def _collect_per_address_write_values(self) -> list[int] | None:
        collected: list[int] = []
        addr = self.start_addr
        end = self.start_addr + self.qty
        while addr < end:
            field = self.editable_fields.get(addr)
            if field is None:
                # Continuation cell of a float pair above - value already emitted.
                addr += 1
                continue
            if self._format_for(addr) is Base.Float:
                parsed = self._parse_edit_float(field.value or "")
                if parsed is None:
                    _mark_text_field(field, is_valid=False)
                    return None
                _mark_text_field(field, is_valid=True)
                reg0, reg1 = float_to_regs(parsed, self._float_endian_for(addr))
                collected.append(reg0)
                collected.append(reg1)
                addr += 2
            else:
                parsed = self._parse_edit_value_for(field.value or "", addr)
                if parsed is None or not isinstance(parsed, int):
                    _mark_text_field(field, is_valid=False)
                    return None
                _mark_text_field(field, is_valid=True)
                collected.append(parsed)
                addr += 1
        return collected

    def _collect_float_write_values(self) -> list[int] | None:
        collected: list[int] = []
        for addr in range(self.start_addr, self.start_addr + self.qty, 2):
            field = self.editable_fields.get(addr)
            if field is None:
                # Odd register of a pair - value already handled by even side.
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

    # ------------------------------------------------------------------ #
    # Cell construction
    # ------------------------------------------------------------------ #

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
        if not self._per_address_mode:
            if not self.is_float_mode:
                return cell
            return self._wrap_float_cell(cell, address, value_index, valid=valid)
        # Per-address path. Two sub-cases:
        # 1. Continuation cell of a float at address-1 → render as "—",
        #    non-editable, even though it is itself unlisted in the format_map.
        # 2. Float-owning address → render as float; everything else stays int.
        if self._is_continuation_at(address, value_index):
            return RegisterCell(
                address,
                cell.value,
                "—",
                True,
                False,  # not editable
                valid,
                cell.tooltip,
            )
        fmt = self._format_for(address)
        if fmt is not Base.Float:
            return cell
        return self._wrap_float_cell_per_address(
            cell, address, value_index, valid=valid
        )

    def _used_cell_core(
        self, address: int, value_index: int, *, valid: bool
    ) -> RegisterCell:
        value = self._values[value_index] if value_index < len(self._values) else None
        fmt = self._format_for(address) if self._per_address_mode else self.base
        if not valid:
            text = "-/-"
        elif value is None:
            text = "-"
        else:
            text = format_value(value, fmt, self.is_16bit, self.signed)
        return RegisterCell(
            address,
            value,
            text,
            True,
            self.is_write,
            valid,
            f"Address : {_format_address(address, self.base)}",
        )

    # Legacy all-grid float path ------------------------------------------- #

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

    # Per-address float path ----------------------------------------------- #

    def _wrap_float_cell_per_address(
        self, cell: RegisterCell, address: int, value_index: int, *, valid: bool
    ) -> RegisterCell:
        """Float for a single configured address: N displays, N+1 is continuation."""
        # Continuation cell — part of a float pair above.
        if self._is_continuation_at(address, value_index):
            return RegisterCell(
                address,
                cell.value,
                "—",
                True,
                False,  # not editable
                valid,
                cell.tooltip,
            )
        # Float owns this row: try to form a float from this register and next.
        next_index = value_index + 1
        next_value = (
            self._values[next_index] if next_index < len(self._values) else None
        )
        # Last register of an odd qty with no pair -> integer fallback.
        if next_value is None and value_index == self.qty - 1:
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
        float_val = float_from_regs(
            cell.value, next_value, self._float_endian_for(address)
        )
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
            content = self._wrap_cell_content(cell, field)
            return ft.DataCell(content, tooltip=cell.tooltip)
        text = ft.Text(
            cell.visible_text,
            size=14,
            color=_COLOR_TEXT if cell.is_used and cell.is_valid else _COLOR_ERROR,
        )
        content = ft.Container(
            text,
            width=75,
            height=36,
            alignment=ft.Alignment.CENTER_LEFT,
            bgcolor=_COLOR_OUT_OF_RANGE_BG if not cell.is_used else None,
        )
        return ft.DataCell(
            self._wrap_cell_content(cell, content),
            tooltip=cell.tooltip or None,
        )

    def _wrap_cell_content(self, cell: RegisterCell, control: ft.Control) -> ft.Control:
        if not cell.is_used or self._cell_wrapper is None:
            return control
        return self._cell_wrapper(cell.address, control)

    def _build_text_field(self, cell: RegisterCell) -> ft.TextField:
        field = ft.TextField(
            value="" if cell.visible_text == "-" else cell.visible_text,
            tooltip=cell.tooltip,
            max_length=self._max_length_for(cell.address),
            counter="",
            width=75,
            height=36,
            text_size=14,
            content_padding=ft.Padding(4, 2, 4, 2),
            border_radius=3,
            on_change=None,
            on_blur=None,
        )

        if self._per_address_mode:
            addr = cell.address

            def validate_field() -> None:
                if self._format_for(addr) is Base.Float:
                    _mark_text_field(
                        field, is_valid=self._parse_edit_float(field.value) is not None
                    )
                else:
                    _mark_text_field(
                        field,
                        is_valid=self._parse_edit_value_for(field.value, addr) is not None,
                    )
        elif self.is_float_mode:

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

    def _max_length_for(self, address: int) -> int | None:
        if not self._per_address_mode:
            return self._max_length()
        fmt = self._format_for(address)
        if fmt is Base.Float:
            return 20
        if not self.is_16bit:
            return 1
        match fmt:
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

    def _parse_edit_value_for(self, raw: str, address: int) -> int | None:
        """Per-address version of :meth:`_parse_edit_value`.

        Float parsing is handled separately by :meth:`_parse_edit_float`; this
        method is only called for non-float formats.
        """
        text = raw.strip()
        if not text:
            return None
        fmt = self._format_for(address)
        if fmt is Base.Float:
            return None
        if not self.is_16bit:
            return int(text) if text in {"0", "1"} else None
        match fmt:
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
    default_base: BaseValue | None = None,
    format_map: Mapping[int, BaseValue] | None = None,
    float_endian_map: Mapping[int, FloatEndian | int] | None = None,
    cell_wrapper: CellWrapper | None = None,
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
        default_base=default_base,
        format_map=format_map,
        float_endian_map=float_endian_map,
        cell_wrapper=cell_wrapper,
    ).to_datatable()


def is_signed_visible(base: BaseValue) -> bool:
    return _normalize_base(base) is Base.Dec


def _normalize_base(base: BaseValue) -> Base:
    try:
        return Base(int(base))
    except ValueError:
        return Base.Dec


def _coerce_endian(value: FloatEndian | int) -> FloatEndian:
    try:
        return FloatEndian(int(value))
    except ValueError:
        return FloatEndian.ABCD


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
