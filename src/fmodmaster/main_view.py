"""Main window view and UI state machine for fModMaster.

The module owns the primary Flet control tree: communication controls, request
controls, toolbar, menu bar, registers grid, status bar, and the state
transitions around disconnected/connected/scanning modes.

allow: SIZE_OK -- task scope requires the full main-window composition in this
file until later wiring tasks split dialogs/tools into their own modules.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol, assert_never

import flet as ft

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
from .registers import Base, RegistersModel, build_grid, is_signed_visible


class PageLike(Protocol):
    """Subset of :class:`flet.Page` used by the main view."""

    appbar: ft.AppBar | None
    snack_bar: ft.SnackBar | None
    dialog: ft.AlertDialog | None

    def run_thread(self, handler: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        ...

    def run_task(
        self,
        handler: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        ...

    def update(self) -> None:
        ...


class SettingsLike(Protocol):
    tcp_port: str
    slave_ip: str
    serial_port_name: str
    baud: str
    data_bits: str
    stop_bits: str
    parity: str
    rts: str
    slave_id: int
    scan_rate: int
    function_code: int
    start_addr: int
    no_of_regs: int
    base: int
    modbus_mode: int
    time_out: str
    base_addr: str


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

    def connect_rtu(
        self,
        port: str,
        baud: int,
        parity_char: str,
        data_bits: int,
        stop_bits: int,
        rts: str,
        timeout: int | str | float,
    ) -> bool:
        ...

    def connect_tcp(self, ip: str, port: int, timeout: int | str | float) -> bool:
        ...

    def disconnect(self) -> None:
        ...

    def transaction(self) -> None:
        ...

    def start_scan(self) -> None:
        ...

    def stop_scan(self) -> None:
        ...

    def reset_counters(self) -> None:
        ...



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
    load_session_button: ft.Button
    save_session_button: ft.Button
    connect_button: ft.Button
    read_write_button: ft.Button
    scan_button: ft.Button
    clear_table_button: ft.Button
    reset_counters_button: ft.Button
    log_file_button: ft.Button
    bus_monitor_button: ft.Button
    tools_button: ft.Button
    settings_button: ft.Button
    about_button: ft.Button
    exit_button: ft.Button
    connection_status: ft.Text
    base_addr_status: ft.Text
    packets_status: ft.Text
    errors_status: ft.Text


_MODE_RTU: Final = "RTU"
_MODE_TCP: Final = "TCP"
_FORMAT_BIN: Final = "Bin"
_FORMAT_DEC: Final = "Dec"
_FORMAT_HEX: Final = "Hex"
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


class MainViewController:
    def __init__(
        self,
        page: PageLike,
        settings: SettingsLike,
        comm: CommLike | None = None,
    ) -> None:
        self.page = page
        self.settings = settings
        self.comm: CommLike = comm if comm is not None else ModbusComm(refresh_cb=self.schedule_refresh)
        self.controls = self._build_controls()
        self.root = self._build_layout()
        self._bind_handlers()
        self._refresh_controls(rebuild_grid=True)

    def schedule_refresh(self) -> None:
        self.page.run_task(self._refresh_async)

    async def _refresh_async(self) -> None:
        self._refresh_controls(rebuild_grid=True)
        self.page.update()

    def _build_controls(self) -> MainViewControls:
        mode = _MODE_TCP if int(self.settings.modbus_mode) == 1 else _MODE_RTU
        fc = _normalize_function_code(self.settings.function_code)
        data_format = _format_from_base(self.settings.base)
        qty = _clamp_quantity(self.settings.no_of_regs or 1, _SPECS_BY_CODE[fc])
        grid_host = ft.Container()
        return MainViewControls(
            mode_dropdown=ft.Dropdown(
                value=mode,
                label="Modbus Mode",
                width=160,
                options=[
                    ft.DropdownOption(key=_MODE_RTU, text="RTU"),
                    ft.DropdownOption(key=_MODE_TCP, text="TCP"),
                ],
            ),
            slave_label=ft.Text("Slave Addr"),
            slave_field=ft.TextField(value=str(self.settings.slave_id), width=120),
            scan_rate_field=ft.TextField(
                value=str(self.settings.scan_rate), label="Scan Rate (ms)", width=160
            ),
            function_dropdown=ft.Dropdown(
                value=str(fc),
                label="Function Code",
                width=260,
                options=[
                    ft.DropdownOption(key=str(spec.code), text=spec.name)
                    for spec in _FUNCTION_SPECS
                ],
            ),
            start_addr_field=ft.TextField(
                value=str(self.settings.start_addr), label="Start Address", width=160
            ),
            address_base_toggle=ft.SegmentedButton(
                segments=[
                    ft.Segment(value=_ADDR_DEC, label="Dec"),
                    ft.Segment(value=_ADDR_HEX, label="Hex"),
                ],
                selected=[_ADDR_DEC],
            ),
            qty_label=ft.Text(_SPECS_BY_CODE[fc].quantity_label),
            qty_field=ft.TextField(value=str(qty), width=140),
            data_format_dropdown=ft.Dropdown(
                value=data_format,
                label="Data Format",
                width=150,
                options=[
                    ft.DropdownOption(key=_FORMAT_BIN, text="Bin"),
                    ft.DropdownOption(key=_FORMAT_DEC, text="Dec"),
                    ft.DropdownOption(key=_FORMAT_HEX, text="Hex"),
                ],
            ),
            signed_checkbox=ft.Checkbox(label="Signed", value=False),
            grid_host=grid_host,
            load_session_button=ft.Button("Load Session"),
            save_session_button=ft.Button("Save Session"),
            connect_button=ft.Button("Connect"),
            read_write_button=ft.Button("Read / Write"),
            scan_button=ft.Button("Scan"),
            clear_table_button=ft.Button("Clear Table"),
            reset_counters_button=ft.Button("Reset Counters"),
            log_file_button=ft.Button("Log File"),
            bus_monitor_button=ft.Button("Bus Monitor"),
            tools_button=ft.Button("Tools"),
            settings_button=ft.Button("Settings"),
            about_button=ft.Button("About"),
            exit_button=ft.Button("Exit"),
            connection_status=ft.Text(),
            base_addr_status=ft.Text(),
            packets_status=ft.Text(),
            errors_status=ft.Text(),
        )

    def _build_layout(self) -> ft.Control:
        self.page.appbar = ft.AppBar(title="fModMaster")
        content = ft.Column(
            controls=[
                self._build_menu_bar(),
                self._build_toolbar(),
                self._communication_area(),
                self._request_area(),
                self.controls.grid_host,
                self._status_bar(),
            ],
            spacing=12,
            expand=True,
        )
        content.data = self
        return content

    def _build_menu_bar(self) -> ft.MenuBar:
        return ft.MenuBar(
            controls=[
                self._submenu("File", ["Load Session", "Save Session", "Exit"]),
                self._submenu("Options", ["Modbus RTU", "Modbus TCP", "Settings"]),
                self._submenu("View", ["Log File", "Bus Monitor"]),
                self._submenu("Commands", ["Connect", "Read / Write", "Scan", "Clear Table", "Reset Counters", "Tools"]),
                self._submenu("Help", ["Modbus Manual", "About"]),
            ]
        )

    def _submenu(self, label: str, item_labels: Sequence[str]) -> ft.SubmenuButton:
        return ft.SubmenuButton(
            content=label,
            controls=[ft.TextButton(text, on_click=self._menu_handler(text)) for text in item_labels],
        )

    def _build_toolbar(self) -> ft.Row:
        c = self.controls
        return ft.Row(
            controls=[
                c.load_session_button,
                c.save_session_button,
                c.connect_button,
                c.read_write_button,
                c.scan_button,
                c.clear_table_button,
                c.reset_counters_button,
                c.log_file_button,
                c.bus_monitor_button,
                c.tools_button,
                c.settings_button,
                c.about_button,
                c.exit_button,
            ],
            wrap=True,
            spacing=8,
        )

    def _communication_area(self) -> ft.Column:
        c = self.controls
        return ft.Column(
            controls=[
                ft.Text("Communication"),
                ft.Row(
                    controls=[
                        c.mode_dropdown,
                        ft.Column([c.slave_label, c.slave_field], spacing=2),
                        c.scan_rate_field,
                    ],
                    wrap=True,
                ),
            ],
            spacing=6,
        )

    def _request_area(self) -> ft.Column:
        c = self.controls
        return ft.Column(
            controls=[
                ft.Text("Request"),
                ft.Row(
                    controls=[
                        c.function_dropdown,
                        c.start_addr_field,
                        c.address_base_toggle,
                        ft.Column([c.qty_label, c.qty_field], spacing=2),
                        c.data_format_dropdown,
                        c.signed_checkbox,
                    ],
                    wrap=True,
                ),
            ],
            spacing=6,
        )

    def _status_bar(self) -> ft.Row:
        c = self.controls
        return ft.Row(
            controls=[
                c.connection_status,
                c.base_addr_status,
                c.packets_status,
                c.errors_status,
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
        for button, label in (
            (c.load_session_button, "Load Session"),
            (c.save_session_button, "Save Session"),
            (c.log_file_button, "Log File"),
            (c.bus_monitor_button, "Bus Monitor"),
            (c.tools_button, "Tools"),
            (c.settings_button, "Settings"),
            (c.exit_button, "Exit"),
        ):
            button.on_click = self._stub_handler(label)
        c.about_button.on_click = self._show_about

    def _menu_handler(self, label: str) -> Callable[[], None]:
        handlers: dict[str, Callable[[], None]] = {
            "Connect": self._on_connect_click,
            "Read / Write": self._on_read_write_click,
            "Scan": self._on_scan_click,
            "Clear Table": self._on_clear_table_click,
            "Reset Counters": self._on_reset_counters_click,
            "About": self._show_about,
        }
        return handlers.get(label, self._stub_handler(label))

    def _stub_handler(self, label: str) -> Callable[[], None]:
        def show_stub() -> None:
            self._show_snackbar(f"{label} will be wired in a later task.")

        return show_stub

    def _on_mode_change(self) -> None:
        self.controls.slave_label.value = "Unit ID" if self._mode() == _MODE_TCP else "Slave Addr"
        self.schedule_refresh()

    def _on_function_change(self) -> None:
        spec = self._function_spec()
        qty = _clamp_quantity(_parse_int(self.controls.qty_field.value, spec.min_qty), spec)
        if spec.locks_quantity:
            qty = 1
        self.controls.qty_field.value = str(qty)
        self.schedule_refresh()

    def _on_format_change(self) -> None:
        self.schedule_refresh()

    def _on_request_change(self) -> None:
        self.schedule_refresh()

    def _on_connect_click(self) -> None:
        self._run_worker(self._toggle_connection)

    def _on_read_write_click(self) -> None:
        if not self.comm.connected:
            return
        self._sync_comm_from_controls()
        if self._function_spec().is_write and not self._collect_write_values():
            self._show_snackbar("Invalid write value in table.")
            self.schedule_refresh()
            return
        self._run_worker(self.comm.transaction)

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

    def _show_about(self) -> None:
        self.page.dialog = ft.AlertDialog(
            modal=True,
            title="About fModMaster",
            content=ft.Text("fModMaster Modbus master interface."),
            actions=[ft.TextButton("OK", on_click=self._close_dialog)],
            open=True,
        )
        self.schedule_refresh()

    def _close_dialog(self) -> None:
        if self.page.dialog is not None:
            self.page.dialog.open = False
        self.schedule_refresh()

    def _run_worker(self, handler: Callable[[], None]) -> None:
        def worker() -> None:
            handler()
            self.schedule_refresh()

        self.page.run_thread(worker)

    def _toggle_connection(self) -> None:
        if self.comm.connected:
            self.comm.disconnect()
            return
        self._sync_comm_from_controls()
        mode = self._mode()
        if mode == _MODE_TCP:
            self.comm.connect_tcp(
                self.settings.slave_ip,
                _parse_int(self.settings.tcp_port, 502),
                self.settings.time_out,
            )
            return
        self.comm.connect_rtu(
            self.settings.serial_port_name,
            _parse_int(self.settings.baud, 9600),
            _parity_char(self.settings.parity),
            _parse_int(self.settings.data_bits, 8),
            _parse_int(self.settings.stop_bits, 1),
            self.settings.rts,
            self.settings.time_out,
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
        self.comm.num_items = _clamp_quantity(_parse_int(self.controls.qty_field.value, spec.min_qty), spec)
        self.comm.scan_rate = max(_parse_int(self.controls.scan_rate_field.value, 1000), 1)
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
        c.qty_field.disabled = scanning or spec.locks_quantity
        c.qty_label.value = spec.quantity_label
        if spec.locks_quantity:
            c.qty_field.value = "1"
        c.signed_checkbox.visible = is_signed_visible(self._data_base())
        c.slave_label.value = "Unit ID" if self._mode() == _MODE_TCP else "Slave Addr"
        c.connection_status.value = _connection_text(connected, self.comm.mode)
        c.base_addr_status.value = f"Base Addr: {self.settings.base_addr}"
        c.packets_status.value = f"Packets: {self.comm.packets}"
        c.errors_status.value = f"Errors: {self.comm.errors}"
        if rebuild_grid:
            c.grid_host.content = self._build_grid()

    def _build_grid(self) -> ft.DataTable:
        spec = self._function_spec()
        qty = _clamp_quantity(_parse_int(self.controls.qty_field.value, spec.min_qty), spec)
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
        )

    def _show_snackbar(self, message: str) -> None:
        self.page.snack_bar = ft.SnackBar(ft.Text(message), open=True)
        self.schedule_refresh()

    def _mode(self) -> str:
        return self.controls.mode_dropdown.value or _MODE_RTU

    def _function_spec(self) -> FunctionSpec:
        code = _parse_int(self.controls.function_dropdown.value, FC_READ_COILS)
        return _SPECS_BY_CODE.get(code, _SPECS_BY_CODE[FC_READ_COILS])

    def _data_base(self) -> Base:
        raw = self.controls.data_format_dropdown.value or _FORMAT_DEC
        match raw:
            case "Bin":
                return Base.Bin
            case "Dec":
                return Base.Dec
            case "Hex":
                return Base.Hex
            case _:
                return Base.Dec

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
    controller = MainViewController(page, settings if settings is not None else Settings(), comm)
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
        case unreachable:
            assert_never(unreachable)


def _clamp_quantity(qty: int, spec: FunctionSpec) -> int:
    return min(max(qty, spec.min_qty), spec.max_qty)


def _parse_int(raw: str | int | float | None, default: int) -> int:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return default


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
