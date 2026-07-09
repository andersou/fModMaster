"""Main window view and UI state machine for fModMaster.

The module owns the primary Flet control tree: communication controls, request
controls, toolbar, menu bar, registers grid, status bar, and the state
transitions around disconnected/connected/scanning modes.

allow: SIZE_OK -- task scope requires the full main-window composition in this
file until later wiring tasks split dialogs/tools into their own modules.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol, assert_never

import flet as ft
import serial.tools.list_ports  # type: ignore[import-untyped]

from .bus_monitor import BusMonitorController, build_bus_monitor_dialog
from .config import Settings
from .modbus_comm import (
    FC_READ_COILS,
    FC_READ_DISCRETE_INPUTS,
    FC_READ_HOLDING_REGISTERS,
    FC_READ_INPUT_REGISTERS,
    FC_WRITE_MULTIPLE_COILS,
    FC_WRITE_MULTIPLE_REGISTERS,
    FC_WRITE_SINGLE_COIL,
    FC_WRITE_SINGLE_REGISTER,
    ModbusComm,
)
from .registers import (
    Base,
    FloatEndian,
    RegistersModel,
    build_grid,
    is_signed_visible,
    validate_format_assignment,
)
from .tools_view import ToolsController, build_tools_dialog
from .logging_helper import get_logger

_logger = get_logger(__name__)

# Log records at this level or above are surfaced to the user as a snack bar.
_SNACKBAR_LOG_LEVEL = logging.INFO


class SnackbarLogHandler(logging.Handler):
    """Bridge log records to the UI snack bar.

    Registered on the ``fmodmaster`` logger so any module's INFO+ message pops
    a snack bar. Emits are marshalled to the Flet event loop via the page's
    ``run_task`` (logs may originate from worker threads in ModbusComm).
    """

    def __init__(self, controller: "MainViewController") -> None:
        super().__init__(level=_SNACKBAR_LOG_LEVEL)
        self._controller = controller

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            self._controller.show_log_snackbar(message)
        except Exception:  # pragma: no cover - defensive: never break logging
            pass


class PageLike(Protocol):
    """Subset of :class:`flet.Page` used by the main view."""

    appbar: ft.AppBar | None
    dialog: ft.AlertDialog | None
    services: list[Any]

    def run_thread(
        self, handler: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> None: ...

    def run_task(
        self,
        handler: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any: ...

    def update(self) -> None: ...

    def show_dialog(self, dialog: ft.AlertDialog | ft.SnackBar) -> None: ...

    def pop_dialog(self) -> None: ...


class SettingsLike(Protocol):
    tcp_port: str
    slave_ip: str
    serial_dev: str
    serial_port: str
    serial_port_name: str
    baud: str
    data_bits: str
    stop_bits: str
    parity: str
    rts: str
    max_no_of_lines: str
    slave_id: int
    scan_rate: int
    function_code: int
    start_addr: int
    no_of_regs: int
    base: int
    default_base: int
    float_endian: int
    register_formats: dict[int, int]
    register_float_endians: dict[int, int]
    modbus_mode: int
    time_out: str
    base_addr: str
    logging_level: int

    def save_settings(self, path: str | None = None) -> None: ...

    def load_session(self, path: str) -> None: ...

    def save_session(self, path: str) -> None: ...


class CommLike(Protocol):
    connected: bool
    mode: str | None
    slave: int
    function_code: int
    start_addr: int
    num_items: int
    scan_rate: int
    timeout: float
    packets: int
    errors: int
    scan_running: bool
    values: list[Any]
    write_values: list[Any]
    valid: bool
    on_raw: Callable[[str, bytes], None] | None
    bus_monitor_model: Any

    def connect_rtu(
        self,
        port: str,
        baud: int,
        parity_char: str,
        data_bits: int,
        stop_bits: int,
        rts: str,
        timeout: int | str | float,
    ) -> bool: ...

    def connect_tcp(self, ip: str, port: int, timeout: int | str | float) -> bool: ...

    def disconnect(self) -> None: ...

    def transaction(self) -> None: ...

    def start_scan(self) -> None: ...

    def stop_scan(self) -> None: ...

    def reset_counters(self) -> None: ...

    def report_slave_id(
        self, slave: int | None = None
    ) -> tuple[bool, int | None, bytes]: ...


@dataclass(frozen=True, slots=True)
class FunctionSpec:
    code: int
    name: str
    is_16bit: bool
    min_qty: int
    max_qty: int
    quantity_label: str
    locks_quantity: bool = False

    @property
    def is_write(self) -> bool:
        return self.code in _WRITE_FUNCTION_CODES


@dataclass(slots=True)  # noqa: MUTABLE_OK - owns mutable Flet controls.
class MainViewControls:
    mode_dropdown: ft.Dropdown
    slave_label: ft.Text
    slave_field: ft.TextField
    scan_rate_field: ft.TextField
    function_dropdown: ft.Dropdown
    start_addr_field: ft.TextField
    address_base_toggle: ft.SegmentedButton
    qty_label: ft.Text
    qty_field: ft.TextField
    data_format_dropdown: ft.Dropdown
    signed_checkbox: ft.Checkbox
    grid_host: ft.Container
    load_session_button: ft.OutlinedButton
    save_session_button: ft.OutlinedButton
    connect_button: ft.OutlinedButton
    read_write_button: ft.OutlinedButton
    scan_button: ft.OutlinedButton
    clear_table_button: ft.OutlinedButton
    reset_counters_button: ft.OutlinedButton
    log_file_button: ft.OutlinedButton
    bus_monitor_button: ft.OutlinedButton
    tools_button: ft.OutlinedButton
    settings_button: ft.OutlinedButton
    about_button: ft.OutlinedButton
    connection_status: ft.Text
    base_addr_status: ft.Text
    packets_status: ft.Text
    errors_status: ft.Text


@dataclass(frozen=True, slots=True)
class ConnectionRequest:
    mode: str
    slave_ip: str
    tcp_port: int
    timeout: int | str | float
    serial_port_name: str
    baud: int
    parity_char: str
    data_bits: int
    stop_bits: int
    rts: str


@dataclass(slots=True)  # noqa: MUTABLE_OK - exposes mutable dialog fields to tests.
class RtuSettingsDialogData:
    serial_port: ft.Dropdown
    baud: ft.TextField
    data_bits: ft.TextField
    stop_bits: ft.TextField
    parity: ft.TextField
    rts: ft.TextField


@dataclass(slots=True)  # noqa: MUTABLE_OK - exposes mutable dialog fields to tests.
class TcpSettingsDialogData:
    slave_ip: ft.TextField
    tcp_port: ft.TextField


@dataclass(slots=True)  # noqa: MUTABLE_OK - exposes mutable dialog fields to tests.
class GeneralSettingsDialogData:
    time_out: ft.TextField
    max_no_of_lines: ft.TextField
    base_addr: ft.TextField
    float_endian: ft.Dropdown


@dataclass(frozen=True, slots=True)
class ModalDialogSpec:
    title: str
    content: ft.Control
    data: RtuSettingsDialogData | TcpSettingsDialogData | GeneralSettingsDialogData
    save: Callable[..., None]


_MODE_RTU: Final = "RTU"
_MODE_TCP: Final = "TCP"
_FORMAT_BIN: Final = "Bin"
_FORMAT_DEC: Final = "Dec"
_FORMAT_HEX: Final = "Hex"
_FORMAT_FLOAT: Final = "Float"
_ADDR_DEC: Final = "Dec"
_ADDR_HEX: Final = "Hex"
_WRITE_FUNCTION_CODES: Final = frozenset(
    {
        FC_WRITE_SINGLE_COIL,
        FC_WRITE_SINGLE_REGISTER,
        FC_WRITE_MULTIPLE_COILS,
        FC_WRITE_MULTIPLE_REGISTERS,
    }
)
_FUNCTION_SPECS: Final = (
    FunctionSpec(FC_READ_COILS, "Read Coils", False, 1, 2000, "Number of Coils"),
    FunctionSpec(
        FC_READ_DISCRETE_INPUTS,
        "Read Discrete Inputs",
        False,
        1,
        2000,
        "Number of Coils",
    ),
    FunctionSpec(
        FC_READ_HOLDING_REGISTERS,
        "Read Holding Registers",
        True,
        1,
        125,
        "Number of Registers",
    ),
    FunctionSpec(
        FC_READ_INPUT_REGISTERS,
        "Read Input Registers",
        True,
        1,
        125,
        "Number of Registers",
    ),
    FunctionSpec(
        FC_WRITE_SINGLE_COIL,
        "Write Single Coil",
        False,
        1,
        1,
        "Number of Coils",
        locks_quantity=True,
    ),
    FunctionSpec(
        FC_WRITE_SINGLE_REGISTER,
        "Write Single Register",
        True,
        1,
        1,
        "Number of Registers",
        locks_quantity=True,
    ),
    FunctionSpec(
        FC_WRITE_MULTIPLE_COILS,
        "Write Multiple Coils",
        False,
        2,
        2000,
        "Number of Coils",
    ),
    FunctionSpec(
        FC_WRITE_MULTIPLE_REGISTERS,
        "Write Multiple Registers",
        True,
        2,
        125,
        "Number of Registers",
    ),
)
_SPECS_BY_CODE: Final = {spec.code: spec for spec in _FUNCTION_SPECS}


def _labeled_field(label: str | ft.Text, control: ft.Control) -> ft.Column:
    label_control = (
        label
        if isinstance(label, ft.Text)
        else ft.Text(label, size=12, weight=ft.FontWeight.W_500)
    )
    return ft.Column(
        controls=[
            label_control,
            control,
        ],
        spacing=4,
        horizontal_alignment=ft.CrossAxisAlignment.START,
    )


def _serial_port_options() -> list[ft.DropdownOption]:
    return [
        ft.DropdownOption(
            key=port.device,
            content=ft.Text(
                value=f"{port.device} — {port.description or 'unknown'}",
                no_wrap=False,
            ),
        )
        for port in serial.tools.list_ports.comports()
    ]


def _split_serial_port_name(port_name: str) -> tuple[str, str]:
    cleaned = port_name.removeprefix("\\\\.\\")
    digits = _trailing_digits(cleaned)
    prefix = cleaned[: -len(digits)] if digits else cleaned
    number = digits or "1"
    if prefix.startswith("/dev/ttyS") and digits:
        number = str(int(digits) + 1)
    return prefix, number


def _trailing_digits(value: str) -> str:
    length = len(value)
    while length > 0 and value[length - 1].isdigit():
        length -= 1
    return value[length:]


class MainViewController:
    def __init__(
        self,
        page: PageLike,
        settings: SettingsLike,
        comm: CommLike | None = None,
    ) -> None:
        self.page = page
        self.settings = settings
        self.comm: CommLike = (
            comm if comm is not None else ModbusComm(refresh_cb=self.schedule_refresh)
        )
        self.controls = self._build_controls()
        self.root = self._build_layout()
        self._bind_handlers()
        self._refresh_controls(rebuild_grid=True)
        # Function-switch checkpoint: preserves session choices (qty, format)
        # until a read/write is actually executed.
        self._fn_checkpoints: dict[int, dict[str, Any]] = {}
        # Surface INFO+ log records as snack bars.
        # Register on every fmodmaster.* logger (each has propagate=False per
        # logging_helper, so the handler must be attached to each module whose
        # logs we want to surface).
        self._snackbar_log_handler = SnackbarLogHandler(self)
        # Only surface logs from the modbus_comm module as snackbars.  Manual
        # snackbars (via _show_snackbar) remain the path for user-facing
        # messages from main_view itself, keeping them clean and friendly.
        logging.getLogger("fmodmaster.modbus_comm").addHandler(
            self._snackbar_log_handler
        )

    def schedule_refresh(self) -> None:
        self.page.run_task(self._refresh_async)

    async def _refresh_async(self) -> None:
        self._refresh_controls(rebuild_grid=True)
        self.page.update()

    def _build_controls(self) -> MainViewControls:
        mode = _MODE_TCP if int(self.settings.modbus_mode) == 1 else _MODE_RTU
        fc = _normalize_function_code(self.settings.function_code)
        data_format = _format_from_base(self.settings.default_base)
        qty = _clamp_quantity(self.settings.no_of_regs or 1, _SPECS_BY_CODE[fc])
        grid_host = ft.Container()
        return MainViewControls(
            mode_dropdown=ft.Dropdown(
                value=mode,
                width=160,
                height=48,
                options=[
                    ft.DropdownOption(key=_MODE_RTU, text="RTU"),
                    ft.DropdownOption(key=_MODE_TCP, text="TCP"),
                ],
            ),
            slave_label=ft.Text("Slave Addr"),
            slave_field=ft.TextField(
                value=str(self.settings.slave_id), width=120, height=48
            ),
            scan_rate_field=ft.TextField(
                value=str(self.settings.scan_rate), width=160, height=48
            ),
            function_dropdown=ft.Dropdown(
                value=str(fc),
                width=220,
                height=48,
                options=[
                    ft.DropdownOption(key=str(spec.code), text=spec.name)
                    for spec in _FUNCTION_SPECS
                ],
            ),
            start_addr_field=ft.TextField(
                value=str(self.settings.start_addr), width=120, height=48
            ),
            address_base_toggle=ft.SegmentedButton(
                segments=[
                    ft.Segment(value=_ADDR_DEC, label="Dec"),
                    ft.Segment(value=_ADDR_HEX, label="Hex"),
                ],
                selected=[_ADDR_DEC],
            ),
            qty_label=ft.Text(_SPECS_BY_CODE[fc].quantity_label),
            qty_field=ft.TextField(value=str(qty), width=110, height=48),
            data_format_dropdown=ft.Dropdown(
                value=data_format,
                width=110,
                height=48,
                options=[
                    ft.DropdownOption(key=_FORMAT_BIN, text="Bin"),
                    ft.DropdownOption(key=_FORMAT_DEC, text="Dec"),
                    ft.DropdownOption(key=_FORMAT_HEX, text="Hex"),
                    ft.DropdownOption(key=_FORMAT_FLOAT, text="Float"),
                ],
            ),
            signed_checkbox=ft.Checkbox(label="Signed", value=False),
            grid_host=grid_host,
            load_session_button=ft.OutlinedButton("Load Session"),
            save_session_button=ft.OutlinedButton("Save Session"),
            connect_button=ft.OutlinedButton("Connect"),
            read_write_button=ft.OutlinedButton("Read / Write"),
            scan_button=ft.OutlinedButton("Scan"),
            clear_table_button=ft.OutlinedButton("Clear Table"),
            reset_counters_button=ft.OutlinedButton("Reset Counters"),
            log_file_button=ft.OutlinedButton("Log File"),
            bus_monitor_button=ft.OutlinedButton("Bus Monitor"),
            tools_button=ft.OutlinedButton("Tools"),
            settings_button=ft.OutlinedButton("Settings"),
            about_button=ft.OutlinedButton("About"),
            connection_status=ft.Text(),
            base_addr_status=ft.Text(),
            packets_status=ft.Text(),
            errors_status=ft.Text(),
        )

    def _build_layout(self) -> ft.Control:
        main_content = ft.Column(
            controls=[
                self._build_menu_bar(),
                self._build_toolbar(),
                self._communication_area(),
                self._request_area(),
                self.controls.grid_host,
            ],
            spacing=12,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )
        content = ft.Column(
            controls=[main_content, self._status_bar()],
            spacing=12,
            expand=True,
        )
        content.data = self
        return content

    def _build_menu_bar(self) -> ft.Container:
        left_menus: list[ft.Control] = [
            self._submenu("File", ["New Session", "Load Session", "Save Session"]),
            self._submenu("Options", ["Modbus RTU", "Modbus TCP", "Settings"]),
            self._submenu("View", ["Log File", "Bus Monitor"]),
            self._submenu(
                "Commands",
                [
                    "Connect",
                    "Read / Write",
                    "Scan",
                    "Clear Table",
                    "Reset Counters",
                    "Tools",
                ],
            ),
        ]
        menu_style = ft.MenuStyle(
            alignment=ft.Alignment.TOP_LEFT,
            bgcolor=ft.Colors.TRANSPARENT,
            elevation=0,
        )
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Text(
                            "fModMaster",
                            color=ft.Colors.ON_SECONDARY,
                            weight=ft.FontWeight.W_600,
                        ),
                        bgcolor=ft.Colors.SECONDARY,
                        height=42,
                        padding=ft.Padding.symmetric(horizontal=12),
                        alignment=ft.Alignment.CENTER_LEFT,
                    ),
                    ft.MenuBar(
                        expand=True,
                        style=menu_style,
                        controls=left_menus,
                    ),
                    ft.MenuBar(
                        style=menu_style,
                        controls=[self._submenu("Help", ["About"])],
                    ),
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border_radius=6,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

    def _submenu(self, label: str, item_labels: Sequence[str]) -> ft.SubmenuButton:
        return ft.SubmenuButton(
            content=ft.Text(label, text_align=ft.TextAlign.CENTER),
            style=ft.ButtonStyle(alignment=ft.Alignment.CENTER),
            controls=[
                ft.MenuItemButton(
                    content=ft.Text(text),
                    on_click=self._menu_handler(text),
                )
                for text in item_labels
            ],
        )

    def _build_toolbar(self) -> ft.Row:
        c = self.controls
        return ft.Row(
            controls=[
                ft.Container(c.connect_button, height=40),
                ft.Container(c.read_write_button, height=40),
                ft.Container(c.scan_button, height=40),
                ft.Container(c.clear_table_button, height=40),
                ft.Container(c.log_file_button, height=40),
                ft.Container(c.bus_monitor_button, height=40),
                ft.Container(c.settings_button, height=40),
            ],
            wrap=True,
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _communication_area(self) -> ft.Control:
        c = self.controls
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Communication", weight=ft.FontWeight.W_500),
                    ft.Row(
                        controls=[
                            _labeled_field("Modbus Mode", c.mode_dropdown),
                            _labeled_field(c.slave_label, c.slave_field),
                            _labeled_field("Scan Rate (ms)", c.scan_rate_field),
                        ],
                        wrap=True,
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                ],
                spacing=8,
            ),
            padding=12,
            border_radius=8,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
        )

    def _request_area(self) -> ft.Control:
        c = self.controls
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Request", weight=ft.FontWeight.W_500),
                    ft.Row(
                        controls=[
                            _labeled_field("Function Code", c.function_dropdown),
                            _labeled_field("Start Address", c.start_addr_field),
                            _labeled_field("Addr Base", c.address_base_toggle),
                            _labeled_field(c.qty_label, c.qty_field),
                            _labeled_field("Default Format", c.data_format_dropdown),
                            ft.Container(
                                c.signed_checkbox,
                                height=48,
                                alignment=ft.Alignment.BOTTOM_LEFT,
                            ),
                        ],
                        wrap=True,
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                ],
                spacing=8,
            ),
            padding=12,
            border_radius=8,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
        )

    def _status_bar(self) -> ft.Row:
        c = self.controls
        return ft.Row(
            controls=[
                c.connection_status,
                c.base_addr_status,
                c.packets_status,
                c.errors_status,
                c.reset_counters_button,
            ],
            spacing=24,
        )

    def _bind_handlers(self) -> None:
        c = self.controls
        c.mode_dropdown.on_select = self._on_mode_change
        c.function_dropdown.on_select = self._on_function_change
        c.data_format_dropdown.on_select = self._on_format_change
        c.signed_checkbox.on_change = self._on_format_change
        c.address_base_toggle.on_change = self._on_request_change
        c.start_addr_field.on_change = self._on_request_change
        c.qty_field.on_change = self._on_request_change
        c.connect_button.on_click = self._on_connect_click
        c.read_write_button.on_click = self._on_read_write_click
        c.scan_button.on_click = self._on_scan_click
        c.clear_table_button.on_click = self._on_clear_table_click
        c.reset_counters_button.on_click = self._on_reset_counters_click
        c.load_session_button.on_click = self._load_session_clicked
        c.save_session_button.on_click = self._save_session_clicked
        c.log_file_button.on_click = self._open_log_file
        c.bus_monitor_button.on_click = self._show_bus_monitor
        c.tools_button.on_click = self._show_tools
        c.settings_button.on_click = self._show_general_settings
        c.about_button.on_click = self._show_about

    def _menu_handler(self, label: str) -> Callable[..., None]:
        handlers: dict[str, Callable[..., None]] = {
            "New Session": self._new_session_clicked,
            "Load Session": self._load_session_clicked,
            "Save Session": self._save_session_clicked,
            "Connect": self._on_connect_click,
            "Read / Write": self._on_read_write_click,
            "Scan": self._on_scan_click,
            "Clear Table": self._on_clear_table_click,
            "Reset Counters": self._on_reset_counters_click,
            "Modbus RTU": self._show_rtu_settings,
            "Modbus TCP": self._show_tcp_settings,
            "Settings": self._show_general_settings,
            "Log File": self._open_log_file,
            "Bus Monitor": self._show_bus_monitor,
            "Tools": self._show_tools,
            "Modbus Manual": self._open_modbus_manual,
            "About": self._show_about,
        }
        return handlers.get(label, self._stub_handler(label))

    def _stub_handler(self, label: str) -> Callable[..., None]:
        def show_stub(*_: Any) -> None:
            self._show_snackbar(f"{label} will be wired in a later task.")

        return show_stub

    def _on_mode_change(self) -> None:
        self.controls.slave_label.value = (
            "Unit ID" if self._mode() == _MODE_TCP else "Slave Addr"
        )
        self.schedule_refresh()

    def _snapshot_current_fn_state(self, code: int) -> None:
        """Save the current UI controls into the checkpoint for *code*.

        Called *before* the function-dropdown value changes so we save the
        outgoing function's state.
        """
        self._fn_checkpoints[code] = {
            "qty": _parse_int(self.controls.qty_field.value, 1),
            "start_addr": _parse_int(self.controls.start_addr_field.value, 0),
            "default_base": self.controls.data_format_dropdown.value,
            "format_map": dict(self.settings.register_formats),
            "float_endian_map": dict(self.settings.register_float_endians),
        }

    def _restore_checkpoint(self, spec: FunctionSpec) -> bool:
        """Restore controls from a saved checkpoint for *spec*.

        Returns True if a checkpoint was found and applied.
        """
        cp = self._fn_checkpoints.get(spec.code)
        if cp is None:
            return False
        self.controls.qty_field.value = str(cp["qty"])
        self.controls.start_addr_field.value = str(cp["start_addr"])
        self.controls.data_format_dropdown.value = str(cp["default_base"])
        # Restore per-address format maps so the grid is rebuilt with them.
        self.settings.register_formats.clear()
        self.settings.register_formats.update(cp["format_map"])  # type: ignore[call-overload]
        self.settings.register_float_endians.clear()
        self.settings.register_float_endians.update(cp["float_endian_map"])  # type: ignore[call-overload]
        return True

    def _on_function_change(self) -> None:
        # Before switching, snapshot the state of the *outgoing* function.
        # The dropdown value has already changed when on_select fires, so
        # read the saved code rather than _function_spec().
        prev_code = getattr(
            self, "_last_fn_code", _normalize_function_code(self.settings.function_code)
        )
        self._snapshot_current_fn_state(prev_code)
        spec = self._function_spec()
        self._last_fn_code = spec.code
        # Restore checkpoint for the incoming function, if one exists.
        if not self._restore_checkpoint(spec):
            # First time on this function: clamp qty to its valid range.
            qty = _clamp_quantity(
                _parse_int(self.controls.qty_field.value, spec.min_qty), spec
            )
            if spec.locks_quantity:
                qty = 1
            self.controls.qty_field.value = str(qty)
        # Single-register / single-coil writes have intrinsic limits that the
        # session may not respect — warn the user when switching in.
        if spec.code == FC_WRITE_SINGLE_COIL:
            self._show_snackbar("Write Single Coil suporta apenas 1 bobina por vez.")
        elif spec.code == FC_WRITE_SINGLE_REGISTER and (
            self._grid_has_float() or self._data_base() is Base.Float
        ):
            _logger.warning(
                "FC06 float block (function change): grid_has_float=%s, "
                "default_base_is_float=%s",
                self._grid_has_float(),
                self._data_base() is Base.Float,
            )
            self._show_snackbar(
                "Float requer Write Multiple Registers (FC 10) — "
                "o registrador selecionado é float (32 bits)."
            )
        self.schedule_refresh()

    def _on_format_change(self) -> None:
        self._sync_default_format_from_controls()
        self.schedule_refresh()

    def _on_request_change(self) -> None:
        self.schedule_refresh()

    def _on_connect_click(self) -> None:
        request: ConnectionRequest | None = None
        if not self.comm.connected:
            self._sync_comm_from_controls()
            try:
                request = self._connection_request()
            except ValueError as exc:
                self._show_snackbar(str(exc))
                return
        self._run_worker(
            lambda: self._toggle_and_persist_mode(request),
            on_value_error=self._show_connection_error,
        )

    def _toggle_and_persist_mode(self, request: ConnectionRequest | None) -> None:
        self._toggle_connection(request)
        if self.comm.connected:
            self._sync_settings_from_controls()
            self.settings.save_settings()

    def _on_read_write_click(self) -> None:
        if not self.comm.connected:
            return
        self._sync_comm_from_controls()
        if self._is_single_register_write() and self._grid_has_float():
            _logger.warning(
                "FC06 float block (write click): grid_has_float=%s",
                self._grid_has_float(),
            )
            self._show_snackbar(
                "Float requer Write Multiple Registers (FC 10) — "
                "o registrador selecionado é float (32 bits)."
            )
            self.schedule_refresh()
            return
        if self._function_spec().is_write and not self._collect_write_values():
            self._show_snackbar("Invalid write value in table.")
            self.schedule_refresh()
            return
        self._run_worker(self._transaction_and_persist_fc)

    def _transaction_and_persist_fc(self) -> None:
        self.comm.transaction()
        self._sync_settings_from_controls()
        self.settings.save_settings()

    def _on_scan_click(self) -> None:
        if not self.comm.connected and not self.comm.scan_running:
            return
        self._sync_comm_from_controls()
        self._run_worker(self._toggle_scan)

    def _on_clear_table_click(self) -> None:
        self.comm.values = []
        self.comm.valid = True
        self.schedule_refresh()

    def _on_reset_counters_click(self) -> None:
        self.comm.reset_counters()
        self.schedule_refresh()

    def _show_bus_monitor(self, *_: Any) -> None:
        dialog = build_bus_monitor_dialog(self.page, self.comm, self.settings)
        if isinstance(dialog.data, BusMonitorController):
            dialog.data.open()
            return
        self.page.show_dialog(dialog)

    def _show_tools(self, *_: Any) -> None:
        dialog = build_tools_dialog(self.page, self.comm, self.settings)
        if isinstance(dialog.data, ToolsController):
            dialog.data.open()
            return
        self.page.show_dialog(dialog)

    def _show_rtu_settings(self, *_: Any) -> None:
        data = RtuSettingsDialogData(
            serial_port=ft.Dropdown(
                value=self.settings.serial_port_name,
                editable=True,
                label="Serial Port",
                options=_serial_port_options(),
            ),
            baud=ft.TextField(value=self.settings.baud, label="Baud"),
            data_bits=ft.TextField(value=self.settings.data_bits, label="Data Bits"),
            stop_bits=ft.TextField(value=self.settings.stop_bits, label="Stop Bits"),
            parity=ft.TextField(value=self.settings.parity, label="Parity"),
            rts=ft.TextField(value=self.settings.rts, label="RTS"),
        )

        def save(*_: Any) -> None:
            port_name = data.serial_port.value or self.settings.serial_port_name
            self.settings.serial_port_name = port_name
            self.settings.serial_dev, self.settings.serial_port = (
                _split_serial_port_name(port_name)
            )
            self.settings.baud = data.baud.value or self.settings.baud
            self.settings.data_bits = data.data_bits.value or self.settings.data_bits
            self.settings.stop_bits = data.stop_bits.value or self.settings.stop_bits
            self.settings.parity = data.parity.value or self.settings.parity
            self.settings.rts = data.rts.value or self.settings.rts
            self.settings.save_settings()
            self._close_dialog()

        self._open_modal_dialog(
            ModalDialogSpec(
                "Modbus RTU Settings",
                ft.Column(
                    [
                        data.serial_port,
                        data.baud,
                        data.data_bits,
                        data.stop_bits,
                        data.parity,
                        data.rts,
                    ],
                    width=420,
                    spacing=8,
                ),
                data,
                save,
            )
        )

    def _show_tcp_settings(self, *_: Any) -> None:
        data = TcpSettingsDialogData(
            slave_ip=ft.TextField(value=self.settings.slave_ip, label="Slave IP"),
            tcp_port=ft.TextField(value=self.settings.tcp_port, label="TCP Port"),
        )

        def save(*_: Any) -> None:
            self.settings.slave_ip = data.slave_ip.value or self.settings.slave_ip
            self.settings.tcp_port = data.tcp_port.value or self.settings.tcp_port
            self.settings.save_settings()
            self._close_dialog()

        self._open_modal_dialog(
            ModalDialogSpec(
                "Modbus TCP Settings",
                ft.Column([data.slave_ip, data.tcp_port], width=420, spacing=8),
                data,
                save,
            )
        )

    def _show_general_settings(self, *_: Any) -> None:
        data = GeneralSettingsDialogData(
            time_out=ft.TextField(
                value=self.settings.time_out, label="Response Timeout (ms)"
            ),
            max_no_of_lines=ft.TextField(
                value=self.settings.max_no_of_lines,
                label="Max No Of Bus Monitor Lines",
            ),
            base_addr=ft.TextField(value=self.settings.base_addr, label="Base Addr"),
            float_endian=ft.Dropdown(
                value=str(self.settings.float_endian),
                width=220,
                options=[
                    ft.DropdownOption(key="0", text=FloatEndian.ABCD.label),
                    ft.DropdownOption(key="1", text=FloatEndian.DCBA.label),
                    ft.DropdownOption(key="2", text=FloatEndian.BADC.label),
                    ft.DropdownOption(key="3", text=FloatEndian.CDAB.label),
                ],
            ),
        )

        def save(*_: Any) -> None:
            self.settings.time_out = data.time_out.value or self.settings.time_out
            self.settings.max_no_of_lines = (
                data.max_no_of_lines.value or self.settings.max_no_of_lines
            )
            self.settings.base_addr = data.base_addr.value or self.settings.base_addr
            self.settings.float_endian = _parse_int(data.float_endian.value, 0)
            self.settings.save_settings()
            self._refresh_controls(rebuild_grid=True)
            self._close_dialog()

        self._open_modal_dialog(
            ModalDialogSpec(
                "Settings",
                ft.Column(
                    [
                        data.time_out,
                        data.max_no_of_lines,
                        data.base_addr,
                        data.float_endian,
                    ],
                    width=420,
                    spacing=8,
                ),
                data,
                save,
            )
        )

    def _open_modal_dialog(self, spec: ModalDialogSpec) -> None:
        dialog = ft.AlertDialog(
            modal=True,
            title=spec.title,
            content=spec.content,
            actions=[
                ft.TextButton("OK", on_click=spec.save),
                ft.TextButton("Cancel", on_click=self._close_dialog),
            ],
            open=True,
        )
        dialog.data = spec.data
        self.page.show_dialog(dialog)

    def _show_about(self, *_: Any) -> None:
        dialog = ft.AlertDialog(
            modal=True,
            title="About fModMaster",
            content=ft.Markdown(
                "fModMaster 0.1.0\n"
                "A Flet recreation of qModMaster.\n"
                "Responsible: Anderson Souza\n"
                "GitHub: [andersou/fModMaster](https://github.com/andersou/fModMaster)\n"
                "Credits: qModMaster/libmodbus/QsLog project references."
            ),
            actions=[ft.TextButton("OK", on_click=self._close_dialog)],
            open=True,
        )
        self.page.show_dialog(dialog)

    def _close_dialog(self, *_: Any) -> None:
        self.page.pop_dialog()

    def _open_log_file(self, *_: Any) -> None:
        log_path = Path.cwd() / "fModMaster.log"
        if not _open_local_path(log_path):
            self._show_snackbar(f"Could not open log file: {log_path}")

    def _open_modbus_manual(self, *_: Any) -> None:
        manual_path = _manual_path()
        if manual_path is None:
            self._show_snackbar("Modbus manual not found.")
            return
        if not _open_local_path(manual_path):
            self._show_snackbar(f"Could not open Modbus manual: {manual_path}")

    def _new_session_clicked(self, *_: Any) -> None:
        defaults = Settings()
        self.settings.modbus_mode = defaults.modbus_mode
        self.settings.slave_id = defaults.slave_id
        self.settings.scan_rate = defaults.scan_rate
        self.settings.function_code = defaults.function_code
        self.settings.start_addr = defaults.start_addr
        self.settings.no_of_regs = defaults.no_of_regs
        self.settings.base = defaults.base
        self.settings.default_base = defaults.default_base
        self.settings.float_endian = defaults.float_endian
        self.settings.register_formats.clear()
        self.settings.register_float_endians.clear()
        self.comm.values = []
        self.comm.valid = True
        self._fn_checkpoints.clear()
        self._load_main_fields_from_settings()
        self._refresh_controls(rebuild_grid=True)
        self.page.update()

    def _load_session_clicked(self, *_: Any) -> None:
        self.page.run_task(self._load_session_async)

    async def _load_session_async(self) -> None:
        picker = _file_picker_for_page(self.page)
        files = await picker.pick_files(
            dialog_title="Load Session",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["fmmsess"],
            allow_multiple=False,
        )
        if files:
            path = files[0].path
            if path:
                self._load_session_from_path(path)

    def _load_session_from_path(self, path: str) -> None:
        self.settings.load_session(path)
        self._fn_checkpoints.clear()
        self._load_main_fields_from_settings()
        self._refresh_controls(rebuild_grid=True)
        self.page.update()

    def _save_session_clicked(self, *_: Any) -> None:
        self.page.run_task(self._save_session_async)

    async def _save_session_async(self) -> None:
        picker = _file_picker_for_page(self.page)
        path = await picker.save_file(
            dialog_title="Save Session",
            file_name="session",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["fmmsess"],
        )
        if path is not None:
            self._save_session_to_path(path)

    def _save_session_to_path(self, path: str) -> None:
        self._sync_settings_from_controls()
        self.settings.save_session(path)

    def _load_main_fields_from_settings(self) -> None:
        self.controls.mode_dropdown.value = (
            _MODE_TCP if self.settings.modbus_mode == 1 else _MODE_RTU
        )
        self.controls.slave_field.value = str(self.settings.slave_id)
        self.controls.scan_rate_field.value = str(self.settings.scan_rate)
        self.controls.function_dropdown.value = str(
            _normalize_function_code(self.settings.function_code)
        )
        self.controls.start_addr_field.value = str(self.settings.start_addr)
        self.controls.qty_field.value = str(max(self.settings.no_of_regs, 1))
        self.controls.data_format_dropdown.value = _format_from_base(
            self.settings.default_base
        )

    def _sync_settings_from_controls(self) -> None:
        self.settings.modbus_mode = 1 if self._mode() == _MODE_TCP else 0
        self.settings.slave_id = _parse_int(
            self.controls.slave_field.value, self.settings.slave_id
        )
        self.settings.scan_rate = _parse_int(
            self.controls.scan_rate_field.value, self.settings.scan_rate
        )
        self.settings.function_code = _function_index(self._function_spec().code)
        self.settings.start_addr = self._start_address()
        self.settings.no_of_regs = _parse_int(
            self.controls.qty_field.value, self.settings.no_of_regs
        )
        self._sync_default_format_from_controls()

    def _sync_default_format_from_controls(self) -> None:
        value = _base_to_settings_value(self._data_base())
        self.settings.default_base = value
        self.settings.base = value

    def _run_worker(
        self,
        handler: Callable[[], None],
        *,
        on_value_error: Callable[[ValueError], None] | None = None,
    ) -> None:
        def worker() -> None:
            try:
                handler()
            except ValueError as exc:
                if on_value_error is None:
                    raise
                on_value_error(exc)
            finally:
                self.schedule_refresh()

        self.page.run_thread(worker)

    def _connection_request(self) -> ConnectionRequest:
        mode = self._mode()
        return ConnectionRequest(
            mode=mode,
            slave_ip=self.settings.slave_ip,
            tcp_port=_parse_tcp_port(self.settings.tcp_port)
            if mode == _MODE_TCP
            else 502,
            timeout=self.settings.time_out,
            serial_port_name=self.settings.serial_port_name,
            baud=_parse_int(self.settings.baud, 9600),
            parity_char=_parity_char(self.settings.parity),
            data_bits=_parse_int(self.settings.data_bits, 8),
            stop_bits=_parse_int(self.settings.stop_bits, 1),
            rts=self.settings.rts,
        )

    def _toggle_connection(self, request: ConnectionRequest | None) -> None:
        if self.comm.connected:
            self.comm.disconnect()
            return
        if request is None:
            return
        if request.mode == _MODE_TCP:
            self.comm.connect_tcp(
                request.slave_ip,
                request.tcp_port,
                request.timeout,
            )
            return
        self.comm.connect_rtu(
            request.serial_port_name,
            request.baud,
            request.parity_char,
            request.data_bits,
            request.stop_bits,
            request.rts,
            request.timeout,
        )

    def _toggle_scan(self) -> None:
        if self.comm.scan_running:
            self.comm.stop_scan()
            return
        self.comm.start_scan()

    def _sync_comm_from_controls(self) -> None:
        spec = self._function_spec()
        requested_start = self._start_address()
        base_addr = _parse_int(self.settings.base_addr, 0)
        self.comm.mode = self._mode()
        self.comm.slave = _parse_int(self.controls.slave_field.value, 1)
        self.comm.function_code = spec.code
        # Fixed base-address rule: manual reads/writes and scan both subtract
        # Base Addr before sending the request, correcting qModMaster's mismatch.
        self.comm.start_addr = max(0, requested_start - base_addr)
        self.comm.num_items = _clamp_quantity(
            _parse_int(self.controls.qty_field.value, spec.min_qty), spec
        )
        self.comm.scan_rate = max(
            _parse_int(self.controls.scan_rate_field.value, 1000), 1
        )
        self.controls.qty_field.value = str(self.comm.num_items)

    def _collect_write_values(self) -> bool:
        table = self.controls.grid_host.content
        if not isinstance(table, ft.DataTable):
            return False
        model = table.data
        if not isinstance(model, RegistersModel):
            return False
        values = model.collect_write_values()
        if values is None:
            return False
        self.comm.write_values = values
        return True

    def _refresh_controls(self, *, rebuild_grid: bool) -> None:
        c = self.controls
        spec = self._function_spec()
        scanning = self.comm.scan_running
        connected = self.comm.connected
        c.connect_button.content = "Disconnect" if connected else "Connect"
        c.read_write_button.disabled = (not connected) or scanning
        c.scan_button.disabled = not connected
        c.scan_button.content = "Stop" if scanning else "Scan"
        c.mode_dropdown.disabled = connected or scanning
        c.slave_field.disabled = connected or scanning
        c.scan_rate_field.disabled = connected or scanning
        c.function_dropdown.disabled = scanning
        c.start_addr_field.disabled = scanning
        c.address_base_toggle.disabled = scanning
        c.data_format_dropdown.disabled = scanning
        c.signed_checkbox.disabled = scanning
        # Write Single Register (FC 06) writes one 16-bit register, so a 32-bit
        # Float is impossible there. Disable the Float option and, if it was
        # selected, fall back to Dec to avoid a stuck/invalid state.
        single_reg_write = self._is_single_register_write()
        for option in c.data_format_dropdown.options:
            if option.key == _FORMAT_FLOAT:
                option.disabled = single_reg_write
        if single_reg_write and c.data_format_dropdown.value == _FORMAT_FLOAT:
            c.data_format_dropdown.value = _FORMAT_DEC
        for option in c.data_format_dropdown.options:
            if option.key == _FORMAT_FLOAT:
                option.disabled = single_reg_write
                option.tooltip = (
                    "Float requer Write Multiple Registers (FC 10)."
                    if single_reg_write
                    else None
                )
        c.qty_field.disabled = scanning or spec.locks_quantity
        c.qty_label.value = spec.quantity_label
        if spec.locks_quantity:
            c.qty_field.value = "1"
        data_base = self._data_base()
        c.signed_checkbox.visible = is_signed_visible(data_base)
        c.slave_label.value = "Unit ID" if self._mode() == _MODE_TCP else "Slave Addr"
        c.connection_status.value = _connection_text(connected, self.comm.mode)
        c.base_addr_status.value = f"Base Addr: {self.settings.base_addr}"
        c.packets_status.value = f"Packets: {self.comm.packets}"
        c.errors_status.value = f"Errors: {self.comm.errors}"
        if rebuild_grid:
            c.grid_host.content = self._build_grid()

    def _build_grid(self) -> ft.DataTable:
        spec = self._function_spec()
        qty = _clamp_quantity(
            _parse_int(self.controls.qty_field.value, spec.min_qty), spec
        )
        self.controls.qty_field.value = str(qty)
        return build_grid(
            self._start_address(),
            qty,
            self._data_base(),
            bool(self.controls.signed_checkbox.value),
            spec.is_write,
            is_16bit=spec.is_16bit,
            values=self.comm.values,
            valid=self.comm.valid,
            float_endian=self._float_endian(),
            default_base=self._data_base(),
            format_map=self._format_map(),
            float_endian_map=self._float_endian_map(),
            cell_wrapper=self._wrap_register_cell,
        )

    def _format_map(self) -> dict[int, Base]:
        result: dict[int, Base] = {}
        for address, raw_base in self.settings.register_formats.items():
            try:
                result[address] = Base(raw_base)
            except ValueError:
                continue
        return result

    def _float_endian_map(self) -> dict[int, FloatEndian]:
        result: dict[int, FloatEndian] = {}
        for address, raw_endian in self.settings.register_float_endians.items():
            try:
                result[address] = FloatEndian(raw_endian)
            except ValueError:
                continue
        return result

    def _apply_register_format(
        self,
        address: int,
        base: Base,
        endian: FloatEndian | None = None,
    ) -> None:
        message = validate_format_assignment(address, base, self._format_map())
        if message is not None:
            self._show_snackbar(message)
            return
        self.settings.register_formats[address] = int(base)
        if base is Base.Float:
            self.settings.register_float_endians[address] = int(
                endian if endian is not None else self._float_endian()
            )
        else:
            self.settings.register_float_endians.pop(address, None)
        self.schedule_refresh()

    def _reset_register_format(self, address: int) -> None:
        self.settings.register_formats.pop(address, None)
        self.settings.register_float_endians.pop(address, None)
        self.schedule_refresh()

    def _wrap_register_cell(self, address: int, control: ft.Control) -> ft.Control:
        def on_select(event: ft.ContextMenuSelectEvent) -> None:
            item = event.item
            item_data = getattr(item, "data", None) if item is not None else None
            action = str(item_data or event.data or "")
            # FC 06 writes a single 16-bit register; a 32-bit Float is invalid.
            if self._is_single_register_write() and action.startswith("float:"):
                self._show_snackbar("Float requer Write Multiple Registers (FC 10).")
                return
            match action:
                case "base:bin":
                    self._apply_register_format(address, Base.Bin)
                case "base:dec":
                    self._apply_register_format(address, Base.Dec)
                case "base:hex":
                    self._apply_register_format(address, Base.Hex)
                case "float:abcd":
                    self._apply_register_format(address, Base.Float, FloatEndian.ABCD)
                case "float:dcba":
                    self._apply_register_format(address, Base.Float, FloatEndian.DCBA)
                case "float:badc":
                    self._apply_register_format(address, Base.Float, FloatEndian.BADC)
                case "float:cdab":
                    self._apply_register_format(address, Base.Float, FloatEndian.CDAB)
                case "reset":
                    self._reset_register_format(address)

        single_reg_write = self._is_single_register_write()
        float_tooltip = (
            "Float requer Write Multiple Registers (FC 10)."
            if single_reg_write
            else None
        )
        return ft.ContextMenu(
            content=control,
            secondary_items=[
                ft.PopupMenuItem(content="Dec", data="base:dec"),
                ft.PopupMenuItem(content="Bin", data="base:bin"),
                ft.PopupMenuItem(content="Hex", data="base:hex"),
                ft.PopupMenuItem(
                    content="Float ABCD",
                    data="float:abcd",
                    disabled=single_reg_write,
                    tooltip=float_tooltip,
                ),
                ft.PopupMenuItem(
                    content="Float DCBA",
                    data="float:dcba",
                    disabled=single_reg_write,
                    tooltip=float_tooltip,
                ),
                ft.PopupMenuItem(
                    content="Float BADC",
                    data="float:badc",
                    disabled=single_reg_write,
                    tooltip=float_tooltip,
                ),
                ft.PopupMenuItem(
                    content="Float CDAB",
                    data="float:cdab",
                    disabled=single_reg_write,
                    tooltip=float_tooltip,
                ),
                ft.PopupMenuItem(content="Reset to default", data="reset"),
            ],
            on_select=on_select,
        )

    def _show_snackbar(self, message: str) -> None:
        # Flet 0.85.x has no page.snack_bar slot; a SnackBar is shown by
        # passing it to page.show_dialog (SnackBar is a DialogControl).
        self.page.show_dialog(ft.SnackBar(ft.Text(message)))
        self.schedule_refresh()

    def show_log_snackbar(self, message: str) -> None:
        """Sink for SnackbarLogHandler: show a log record as a snack bar.

        Marshalled to the Flet event loop because log records may be emitted
        from ModbusComm worker threads.
        """
        self.page.run_task(self._show_snackbar_async, message)

    async def _show_snackbar_async(self, message: str) -> None:
        self.page.show_dialog(ft.SnackBar(ft.Text(message)))
        self._refresh_controls(rebuild_grid=True)
        self.page.update()

    def _show_connection_error(self, exc: ValueError) -> None:
        # Log the error for the file, then show a friendly snack bar directly
        # (modbus_comm logs are surfaced as snack bars automatically).
        _logger.error("Connection failed: %s", exc)
        self._show_snackbar(str(exc))

    def _mode(self) -> str:
        return self.controls.mode_dropdown.value or _MODE_RTU

    def _function_spec(self) -> FunctionSpec:
        code = _parse_int(self.controls.function_dropdown.value, FC_READ_COILS)
        return _SPECS_BY_CODE.get(code, _SPECS_BY_CODE[FC_READ_COILS])

    def _is_single_register_write(self) -> bool:
        """True when the active function is Write Single Register (FC 06).

        FC 06 writes exactly one 16-bit register, so a 32-bit Float (which
        needs two registers) is physically impossible. Float formatting is
        blocked in that mode.
        """
        return self._function_spec().code == FC_WRITE_SINGLE_REGISTER

    def _grid_has_float(self) -> bool:
        """True when the built grid would write any register as a 32-bit Float.

        Delegates to :meth:`RegistersModel.has_float_in_range`, which uses the
        exact same per-address resolution as the actual write — so the check
        cannot drift from what gets encoded. Catches a register read/set as
        Float (per-address override or legacy float mode), not just the default
        format dropdown.
        """
        table = self.controls.grid_host.content
        if not isinstance(table, ft.DataTable):
            return False
        model = table.data
        if not isinstance(model, RegistersModel):
            return False
        return model.has_float_in_range()

    def _data_base(self) -> Base:
        raw = self.controls.data_format_dropdown.value or _FORMAT_DEC
        match raw:
            case "Bin":
                return Base.Bin
            case "Dec":
                return Base.Dec
            case "Hex":
                return Base.Hex
            case "Float":
                return Base.Float
            case _:
                return Base.Dec

    def _float_endian(self) -> FloatEndian:
        try:
            return FloatEndian(self.settings.float_endian)
        except ValueError:
            return FloatEndian.ABCD

    def _start_address(self) -> int:
        raw = self.controls.start_addr_field.value or "0"
        selected = self.controls.address_base_toggle.selected
        base = 16 if selected and selected[0] == _ADDR_HEX else 10
        try:
            parsed = int(raw, base)
        except ValueError:
            parsed = 0
        return max(0, min(parsed, 65535))


def build_main_view(
    page: PageLike,
    settings: SettingsLike | None = None,
    comm: CommLike | None = None,
) -> ft.Control:
    """Build the main fModMaster window content.

    Args:
        page: Flet page or a test double exposing ``run_thread``/``run_task``.
        settings: Optional settings object. Defaults to qModMaster-compatible
            :class:`Settings` values.
        comm: Optional communication object. Defaults to :class:`ModbusComm`.

    Returns:
        The root Flet control. Its ``data`` points to ``MainViewController`` for
        smoke tests and later app wiring.
    """
    controller = MainViewController(
        page, settings if settings is not None else Settings(), comm
    )
    return controller.root


def _normalize_function_code(raw: int) -> int:
    if raw in _SPECS_BY_CODE and raw > len(_FUNCTION_SPECS) - 1:
        return raw
    if 0 <= raw < len(_FUNCTION_SPECS):
        return _FUNCTION_SPECS[raw].code
    return FC_READ_COILS


def _format_from_base(raw: int) -> str:
    if raw == 1:
        return _FORMAT_DEC
    try:
        base = Base(raw)
    except ValueError:
        return _FORMAT_DEC
    match base:
        case Base.Bin:
            return _FORMAT_BIN
        case Base.Dec:
            return _FORMAT_DEC
        case Base.Hex:
            return _FORMAT_HEX
        case Base.Float:
            return _FORMAT_FLOAT
        case unreachable:
            assert_never(unreachable)


def _base_to_settings_value(base: Base) -> int:
    match base:
        case Base.Bin:
            return 2
        case Base.Dec:
            return 1
        case Base.Hex:
            return 0
        case Base.Float:
            return 3
        case unreachable:
            assert_never(unreachable)


def _function_index(code: int) -> int:
    for index, spec in enumerate(_FUNCTION_SPECS):
        if spec.code == code:
            return index
    return 0


def _clamp_quantity(qty: int, spec: FunctionSpec) -> int:
    return min(max(qty, spec.min_qty), spec.max_qty)


def _parse_int(raw: str | int | float | None, default: int) -> int:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return default


def _parse_tcp_port(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            "Connection failed: TCP port must be a number (1..65535)."
        ) from exc


def _parity_char(raw: str) -> str:
    cleaned = raw.strip().upper()
    if cleaned in {"EVEN", "E"}:
        return "E"
    if cleaned in {"ODD", "O"}:
        return "O"
    return "N"


def _connection_text(connected: bool, mode: str | None) -> str:
    if not connected:
        return "Disconnected"
    return f"Connected ({mode or 'unknown'})"


def _file_picker_for_page(page: PageLike) -> ft.FilePicker:
    for service in page.services:
        if isinstance(service, ft.FilePicker):
            return service
    picker = ft.FilePicker()
    page.services.append(picker)
    return picker


def _open_local_path(path: Path) -> bool:
    if webbrowser.open(path.resolve().as_uri()):
        return True
    try:
        if sys.platform.startswith("win"):
            startfile = getattr(os, "startfile", None)
            if callable(startfile):
                startfile(str(path))
                return True
            return False
        if sys.platform == "darwin":
            completed = subprocess.run(["open", str(path)], check=False)
        else:
            completed = subprocess.run(["xdg-open", str(path)], check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _manual_path() -> Path | None:
    project_root = Path(__file__).resolve().parents[2]
    candidates = (
        project_root / "docs" / "ManModbus" / "index.html",
        project_root
        / "docs"
        / "qmodmaster"
        / "sourcecode-ref"
        / "qModMaster"
        / "ManModbus"
        / "index.html",
    )
    return next((candidate for candidate in candidates if candidate.exists()), None)
