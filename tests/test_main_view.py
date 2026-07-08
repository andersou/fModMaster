from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import flet as ft
import serial.tools.list_ports  # type: ignore[import-untyped]

from fmodmaster.config import Settings
from fmodmaster.main_view import MainViewController, build_main_view
import fmodmaster.main_view as main_view
from fmodmaster.main_view import _serial_port_options, _split_serial_port_name
from fmodmaster.registers import Base, FloatEndian, RegistersModel


class FakePage:
    def __init__(self) -> None:
        self.appbar: ft.AppBar | None = None
        self.snack_bar: ft.SnackBar | None = None
        self.dialog: ft.AlertDialog | None = None
        self.show_dialog_count = 0
        self.pop_dialog_count = 0
        self.update_count = 0

    def run_thread(self, handler: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        handler(*args, **kwargs)

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

    def show_dialog(self, dialog: ft.AlertDialog) -> None:
        # In Flet 0.85.x a SnackBar is shown via page.show_dialog (it is a
        # DialogControl). Mirror that so snackbar assertions stay valid.
        if isinstance(dialog, ft.SnackBar):
            self.snack_bar = dialog
        else:
            self.dialog = dialog
        self.show_dialog_count += 1

    def pop_dialog(self) -> None:
        self.pop_dialog_count += 1
        if self.dialog is not None:
            self.dialog.open = False

    def launch_url(self, url: str) -> None:
        self.launched_url = url


class FakeComm:
    def __init__(self) -> None:
        self.connected = False
        self.mode: str | None = None
        self.slave = 1
        self.function_code = 0x01
        self.start_addr = 0
        self.num_items = 1
        self.scan_rate = 1000
        self.timeout = 1.0
        self.packets = 0
        self.errors = 0
        self.scan_running = False
        self.values: list[int] = []
        self.write_values: list[int] = []
        self.valid = True
        self.rtu_args: tuple[Any, ...] | None = None
        self.tcp_args: tuple[Any, ...] | None = None

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
        self.connected = True
        self.mode = "RTU"
        self.rtu_args = (port, baud, parity_char, data_bits, stop_bits, rts, timeout)
        return True

    def connect_tcp(self, ip: str, port: int, timeout: int | str | float) -> bool:
        self.connected = True
        self.mode = "TCP"
        self.tcp_args = (ip, port, timeout)
        return True

    def disconnect(self) -> None:
        self.connected = False
        self.mode = None
        self.scan_running = False

    def transaction(self) -> None:
        self.packets += 1
        self.errors += 2
        self.values = [7]
        self.valid = True

    def start_scan(self) -> None:
        self.scan_running = True

    def stop_scan(self) -> None:
        self.scan_running = False

    def reset_counters(self) -> None:
        self.packets = 0
        self.errors = 0


class RejectingTcpComm(FakeComm):
    def connect_tcp(self, ip: str, port: int, timeout: int | str | float) -> bool:
        self.tcp_args = (ip, port, timeout)
        raise ValueError("Connection failed: TCP port 70000 out of range (1..65535).")


class FakeSettings(Settings):
    """Settings subclass that tracks ``save_settings`` calls for test assertions.

    Redirects path-less saves to a temp file so tests never overwrite the
    repository's ``fModMaster.ini``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.save_count = 0
        self.last_save_path: str | None = None
        self._default_save_path = os.path.join(
            tempfile.gettempdir(), f"fModMaster-test-{os.getpid()}.ini"
        )

    def save_settings(self, path: str | None = None) -> None:
        self.save_count += 1
        self.last_save_path = path
        super().save_settings(path if path is not None else self._default_save_path)


def _build_controller() -> tuple[MainViewController, FakeComm, FakePage]:
    return _build_controller_with(FakeComm(), FakeSettings())


def _build_controller_with(
    comm: FakeComm, settings: Settings | FakeSettings
) -> tuple[MainViewController, FakeComm, FakePage]:
    page = FakePage()
    view = build_main_view(page, settings=settings, comm=comm)
    assert isinstance(view.data, MainViewController)
    return view.data, comm, page


def test_build_starts_disconnected_with_transactions_disabled() -> None:
    controller, _, _ = _build_controller()
    controls = controller.controls

    assert controls.read_write_button.disabled is True
    assert controls.scan_button.disabled is True
    assert controls.mode_dropdown.disabled is False
    assert controls.scan_rate_field.disabled is False


def test_status_bar_is_fixed_below_expanding_content() -> None:
    view = build_main_view(FakePage(), settings=Settings(), comm=FakeComm())

    assert isinstance(view, ft.Column)
    assert view.expand is True
    assert len(view.controls) == 2
    content = view.controls[0]
    status_bar = view.controls[1]
    assert isinstance(content, ft.Column)
    assert content.expand is True
    assert content.scroll == ft.ScrollMode.AUTO
    toolbar = content.controls[1]
    assert isinstance(toolbar, ft.Row)
    assert isinstance(status_bar, ft.Row)
    assert status_bar.controls == [
        view.data.controls.connection_status,
        view.data.controls.base_addr_status,
        view.data.controls.packets_status,
        view.data.controls.errors_status,
        view.data.controls.reset_counters_button,
    ]
    toolbar_controls = [container.content for container in toolbar.controls]
    assert view.data.controls.reset_counters_button not in toolbar_controls
    assert content.controls[-1] is view.data.controls.grid_host


def test_mode_switch_flips_slave_label() -> None:
    controller, _, _ = _build_controller()
    controls = controller.controls

    controls.mode_dropdown.value = "TCP"
    assert controls.mode_dropdown.on_select is not None
    controls.mode_dropdown.on_select()

    assert controls.slave_label.value == "Unit ID"


def test_connect_enables_transactions_and_locks_comm_settings() -> None:
    controller, comm, _ = _build_controller()
    controls = controller.controls

    assert controls.connect_button.on_click is not None
    controls.connect_button.on_click()

    assert comm.connected is True
    assert controls.read_write_button.disabled is False
    assert controls.scan_button.disabled is False
    assert controls.mode_dropdown.disabled is True
    assert controls.slave_field.disabled is True
    assert controls.scan_rate_field.disabled is True


def test_invalid_tcp_connect_shows_error_and_refreshes() -> None:
    page = FakePage()
    settings = Settings()
    settings.modbus_mode = 1
    settings.tcp_port = "70000"
    comm = RejectingTcpComm()
    view = build_main_view(page, settings=settings, comm=comm)
    assert isinstance(view.data, MainViewController)
    controls = view.data.controls
    assert controls.connect_button.on_click is not None

    controls.connect_button.on_click()

    assert comm.connected is False
    assert comm.tcp_args == (settings.slave_ip, 70000, settings.time_out)
    assert page.snack_bar is not None
    assert isinstance(page.snack_bar.content, ft.Text)
    assert "TCP port 70000 out of range" in page.snack_bar.content.value
    assert controls.read_write_button.disabled is True
    assert page.update_count >= 1


def test_write_single_coil_locks_quantity_to_one() -> None:
    controller, _, _ = _build_controller()
    controls = controller.controls

    controls.qty_field.value = "25"
    controls.function_dropdown.value = "5"
    assert controls.function_dropdown.on_select is not None
    controls.function_dropdown.on_select()

    assert controls.qty_field.value == "1"
    assert controls.qty_field.disabled is True
    assert controls.qty_label.value == "Number of Coils"


class TestFloatBlockedOnWriteSingleRegister:
    """FC 06 writes a single 16-bit register, so a 32-bit Float must be blocked."""

    def test_float_dropdown_option_disabled_in_fc06(self) -> None:
        controller, _, _ = _build_controller()
        controls = controller.controls

        controls.function_dropdown.value = "6"  # FC_WRITE_SINGLE_REGISTER
        controls.function_dropdown.on_select()

        float_option = next(
            o for o in controls.data_format_dropdown.options if o.key == "Float"
        )
        assert float_option.disabled is True
        assert controller._is_single_register_write() is True

    def test_float_dropdown_option_enabled_in_fc10(self) -> None:
        controller, _, _ = _build_controller()
        controls = controller.controls

        controls.function_dropdown.value = "16"  # FC_WRITE_MULTIPLE_REGISTERS
        controls.function_dropdown.on_select()

        float_option = next(
            o for o in controls.data_format_dropdown.options if o.key == "Float"
        )
        assert float_option.disabled is False
        assert controller._is_single_register_write() is False

    def test_float_selected_falls_back_to_dec_in_fc06(self) -> None:
        controller, _, page = _build_controller()
        controls = controller.controls

        controls.data_format_dropdown.value = "Float"
        controls.function_dropdown.value = "6"
        controls.function_dropdown.on_select()

        assert controls.data_format_dropdown.value == "Dec"
        # Switching into FC 06 with Float active must warn the user.
        assert page.snack_bar is not None
        assert "FC 10" in page.snack_bar.content.value

    def test_no_float_snackbar_when_not_float_in_fc06(self) -> None:
        controller, _, page = _build_controller()
        controls = controller.controls

        controls.data_format_dropdown.value = "Dec"
        controls.function_dropdown.value = "6"
        controls.function_dropdown.on_select()

        assert page.snack_bar is None

    def test_snackbar_when_per_address_float_in_grid_switched_to_fc06(self) -> None:
        # Read holding registers with addr 0 set to Float and addr 1 to Bin
        # (default format is Dec). The float is a per-address override, so the
        # default-only check would miss it — switching to FC 06 must still warn.
        from fmodmaster.config import Settings

        settings = Settings()
        settings.register_formats = {0: 3, 1: 2}  # 3 == Float, 2 == Bin
        settings.register_float_endians = {0: 0}
        controller, comm, page = _build_controller_with(FakeComm(), settings)
        controls = controller.controls
        comm.connected = True

        # Start in Read Holding Registers (FC 03) and build the grid.
        controls.function_dropdown.value = "3"
        controls.function_dropdown.on_select()
        controller._refresh_controls(rebuild_grid=True)

        assert controls.data_format_dropdown.value != "Float"

        controls.function_dropdown.value = "6"
        controls.function_dropdown.on_select()

        assert page.snack_bar is not None
        assert "FC 10" in page.snack_bar.content.value

    def test_context_menu_float_items_disabled_in_fc06(self) -> None:
        controller, _, _ = _build_controller()
        controls = controller.controls

        controls.function_dropdown.value = "6"
        controls.function_dropdown.on_select()

        # Rebuild the grid so the context menu reflects the new function.
        controller._refresh_controls(rebuild_grid=True)
        table = controls.grid_host.content
        from fmodmaster.registers import RegistersModel

        assert isinstance(table, ft.DataTable)
        model = table.data
        assert isinstance(model, RegistersModel)

        # Inspect the context menu built for the first used cell.
        from fmodmaster.main_view import MainViewController

        cell_control = table.rows[0].cells[0].content
        # The cell is wrapped by _wrap_register_cell -> ContextMenu.
        cm = cell_control
        # Walk: Container(text) wrapped by ContextMenu.
        if isinstance(cm, ft.Container):
            cm = cm.content
        assert isinstance(cm, ft.ContextMenu)
        float_items = [i for i in cm.secondary_items if str(i.data).startswith("float:")]
        assert float_items
        assert all(i.disabled for i in float_items)

    def test_write_rejected_when_float_override_in_fc06_range(self) -> None:
        from fmodmaster.config import Settings

        settings = Settings()
        settings.register_formats = {0: 3}  # 3 == Base.Float
        controller, comm, page = _build_controller_with(FakeComm(), settings)
        controls = controller.controls
        comm.connected = True

        controls.function_dropdown.value = "6"
        controls.function_dropdown.on_select()
        controls.read_write_button.on_click()

        assert page.snack_bar is not None
        assert "FC 10" in page.snack_bar.content.value
        # FC 06 must not have been issued.
        assert comm.write_values == []

    def test_write_allowed_with_float_override_in_fc10(self) -> None:
        from fmodmaster.config import Settings

        settings = Settings()
        settings.register_formats = {0: 3}  # 3 == Base.Float
        controller, comm, page = _build_controller_with(FakeComm(), settings)
        controls = controller.controls
        comm.connected = True

        controls.function_dropdown.value = "16"
        controls.function_dropdown.on_select()
        controls.read_write_button.on_click()

        # No rejection snackbar; transaction proceeds (write_values collected).
        assert page.snack_bar is None or "FC 10" not in (
            page.snack_bar.content.value if page.snack_bar else ""
        )

    def test_write_rejected_for_per_register_float_not_default(self) -> None:
        # Default format is Dec, but the writable register (addr 0) is
        # overridden to Float. The rejection must fire off that register's
        # effective format, not the default dropdown value.
        from fmodmaster.config import Settings

        settings = Settings()
        settings.register_formats = {0: 3}  # 3 == Base.Float, only reg 0
        controller, comm, page = _build_controller_with(FakeComm(), settings)
        controls = controller.controls
        comm.connected = True

        # Default format stays Dec; only reg 0 is float.
        assert controls.data_format_dropdown.value != "Float"

        controls.function_dropdown.value = "6"  # FC 06
        controls.function_dropdown.on_select()
        controls.read_write_button.on_click()

        assert page.snack_bar is not None
        assert "FC 10" in page.snack_bar.content.value
        assert comm.write_values == []


def test_scan_start_disables_transaction_controls_and_stop_restores() -> None:
    controller, comm, _ = _build_controller()
    controls = controller.controls
    assert controls.connect_button.on_click is not None
    assert controls.scan_button.on_click is not None
    controls.connect_button.on_click()

    controls.scan_button.on_click()

    assert comm.scan_running is True
    assert controls.scan_button.content == "Stop"
    assert controls.read_write_button.disabled is True
    assert controls.function_dropdown.disabled is True
    assert controls.start_addr_field.disabled is True

    controls.scan_button.on_click()

    assert comm.scan_running is False
    assert controls.scan_button.content == "Scan"
    assert controls.read_write_button.disabled is False
    assert controls.function_dropdown.disabled is False


def test_transaction_refreshes_packets_and_errors_status() -> None:
    controller, _, _ = _build_controller()
    controls = controller.controls
    assert controls.connect_button.on_click is not None
    assert controls.read_write_button.on_click is not None
    controls.connect_button.on_click()

    controls.read_write_button.on_click()

    assert controls.packets_status.value == "Packets: 1"
    assert controls.errors_status.value == "Errors: 2"


def test_menu_settings_opens_dialog_with_current_flet_api() -> None:
    controller, _, page = _build_controller()

    controller._menu_handler("Settings")()

    assert page.show_dialog_count == 1
    assert page.dialog is not None
    assert page.dialog.open is True
    assert page.dialog.title == "Settings"


def test_menu_about_opens_dialog_with_current_flet_api() -> None:
    controller, _, page = _build_controller()

    controller._menu_handler("About")()

    assert page.show_dialog_count == 1
    assert page.dialog is not None
    assert page.dialog.open is True
    assert page.dialog.title == "About fModMaster"


class FakePort:
    def __init__(self, device: str, description: str) -> None:
        self.device = device
        self.description = description


def test_menu_modbus_rtu_opens_dialog_with_serial_dropdown(monkeypatch) -> None:
    monkeypatch.setattr(
        serial.tools.list_ports,
        "comports",
        lambda: [FakePort("/dev/ttyUSB0", "USB Serial")],
    )
    controller, _, page = _build_controller()

    controller._menu_handler("Modbus RTU")()

    assert page.show_dialog_count == 1
    assert page.dialog is not None
    assert page.dialog.title == "Modbus RTU Settings"
    dialog_data = page.dialog.data
    assert isinstance(dialog_data.serial_port, ft.Dropdown)
    assert dialog_data.serial_port.editable is True
    assert any(option.key == "/dev/ttyUSB0" for option in dialog_data.serial_port.options)


def test_rtu_settings_save_updates_serial_port_name(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        serial.tools.list_ports,
        "comports",
        lambda: [FakePort("/dev/ttyUSB0", "USB Serial")],
    )
    settings = FakeSettings()
    settings.serial_port_name = "/dev/ttyS0"
    controller, _, page = _build_controller_with(FakeComm(), settings)

    controller._menu_handler("Modbus RTU")()
    dialog_data = page.dialog.data
    dialog_data.serial_port.value = "/dev/ttyUSB0"
    ok_button = page.dialog.actions[0]
    ok_button.on_click(None)

    assert settings.serial_port_name == "/dev/ttyUSB0"
    assert settings.serial_dev == "/dev/ttyUSB"
    assert settings.serial_port == "0"
    assert settings.save_count >= 1


def test_split_serial_port_name_maps_common_ports() -> None:
    assert _split_serial_port_name("COM1") == ("COM", "1")
    assert _split_serial_port_name("COM10") == ("COM", "10")
    assert _split_serial_port_name("/dev/ttyS0") == ("/dev/ttyS", "1")
    assert _split_serial_port_name("/dev/ttyUSB0") == ("/dev/ttyUSB", "0")
    assert _split_serial_port_name("\\\\.\\COM10") == ("COM", "10")


def test_serial_port_options_uses_pyserial(monkeypatch) -> None:
    monkeypatch.setattr(
        serial.tools.list_ports,
        "comports",
        lambda: [FakePort("/dev/ttyTEST1", "Test Device")],
    )

    options = _serial_port_options()

    assert len(options) == 1
    assert options[0].key == "/dev/ttyTEST1"


def test_file_menu_contains_new_session() -> None:
    view = build_main_view(FakePage(), settings=Settings(), comm=FakeComm())

    assert isinstance(view, ft.Column)
    main_content = view.controls[0]
    assert isinstance(main_content, ft.Column)
    menu_bar = main_content.controls[0]
    assert isinstance(menu_bar, ft.MenuBar)
    file_menu = menu_bar.controls[0]
    assert isinstance(file_menu, ft.SubmenuButton)

    assert [button.content for button in file_menu.controls] == [
        "New Session",
        "Load Session",
        "Save Session",
    ]


def test_new_session_resets_session_fields_and_preserves_connection_settings() -> None:
    settings = Settings()
    settings.slave_ip = "10.6.6.1"
    settings.tcp_port = "1502"
    settings.modbus_mode = 1
    settings.slave_id = 7
    settings.scan_rate = 250
    settings.function_code = 3
    settings.start_addr = 99
    settings.no_of_regs = 10
    settings.base = 16
    settings.default_base = 16
    settings.float_endian = 2
    settings.register_formats = {0: 3, 2: 16}
    settings.register_float_endians = {0: 1}
    comm = FakeComm()
    comm.values = [1, 2, 3]
    comm.valid = False
    controller, _, page = _build_controller_with(comm, settings)

    controller._menu_handler("New Session")()

    assert settings.slave_ip == "10.6.6.1"
    assert settings.tcp_port == "1502"
    assert settings.modbus_mode == 0
    assert settings.slave_id == 1
    assert settings.scan_rate == 1000
    assert settings.function_code == 0
    assert settings.start_addr == 0
    assert settings.no_of_regs == 0
    assert settings.base == 1
    assert settings.default_base == 1
    assert settings.float_endian == 0
    assert settings.register_formats == {}
    assert settings.register_float_endians == {}
    assert comm.values == []
    assert comm.valid is True
    assert controller.controls.mode_dropdown.value == "RTU"
    assert controller.controls.slave_field.value == "1"
    assert controller.controls.scan_rate_field.value == "1000"
    assert controller.controls.function_dropdown.value == "1"
    assert controller.controls.start_addr_field.value == "0"
    assert controller.controls.qty_field.value == "1"
    assert controller.controls.data_format_dropdown.value == "Dec"
    assert page.update_count >= 1


def test_log_file_uses_file_uri_when_opening(monkeypatch, tmp_path) -> None:
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(main_view.webbrowser, "open", fake_open)

    log_path = tmp_path / "fModMaster.log"
    log_path.write_text("log\n", encoding="utf-8")

    assert main_view._open_local_path(log_path) is True
    assert opened == [log_path.as_uri()]


# --------------------------------------------------------------------------- #
# Persistence on connect / read-write
# --------------------------------------------------------------------------- #


def test_connect_persists_modbus_mode_to_settings() -> None:
    """After a successful connect, modbus_mode is saved to INI."""
    s = FakeSettings()
    controller, comm, _ = _build_controller_with(FakeComm(), s)
    controls = controller.controls

    controls.mode_dropdown.value = "TCP"
    controls.mode_dropdown.on_select()

    controls.connect_button.on_click()

    assert comm.connected is True
    assert comm.mode == "TCP"
    assert s.modbus_mode == 1
    assert s.save_count >= 1


def test_connect_persists_rtu_mode() -> None:
    """RTU connect persists modbus_mode=0."""
    s = FakeSettings()
    controller, comm, _ = _build_controller_with(FakeComm(), s)
    controls = controller.controls

    controls.connect_button.on_click()

    assert comm.connected is True
    assert comm.mode == "RTU"
    assert s.modbus_mode == 0
    assert s.save_count >= 1


def test_read_write_persists_function_code_to_settings() -> None:
    """After read/write, function_code is saved to INI."""
    s = FakeSettings()
    controller, comm, _ = _build_controller_with(FakeComm(), s)
    controls = controller.controls

    controls.connect_button.on_click()
    s.save_count = 0

    controls.function_dropdown.value = "3"
    controls.function_dropdown.on_select()

    controls.read_write_button.on_click()

    assert comm.packets >= 1
    assert s.function_code == _function_index(0x03)
    assert s.save_count >= 1


def test_disconnect_does_not_persist_mode_as_connected() -> None:
    """Disconnect should not flag modbus_mode as TCP/RTU of a connected state."""
    s = FakeSettings()
    controller, comm, _ = _build_controller_with(FakeComm(), s)
    controls = controller.controls

    controls.mode_dropdown.value = "TCP"
    controls.mode_dropdown.on_select()
    controls.connect_button.on_click()
    assert comm.connected is True
    s.save_count = 0

    # Disconnect
    controls.connect_button.on_click()
    assert comm.connected is False
    assert s.save_count == 0


def test_default_format_dropdown_syncs_default_base_and_legacy_base() -> None:
    settings = Settings()
    controller, _, _ = _build_controller_with(FakeComm(), settings)

    controller.controls.data_format_dropdown.value = "Hex"
    controller.controls.data_format_dropdown.on_select()

    assert settings.default_base == 0
    assert settings.base == 0


def test_build_grid_passes_default_and_register_format_maps() -> None:
    settings = Settings()
    settings.default_base = 1
    settings.register_formats = {0: 3, 2: 2}
    settings.register_float_endians = {0: 1}
    comm = FakeComm()
    comm.values = [0x0000, 0x803F, 5]
    controller, _, _ = _build_controller_with(comm, settings)
    controller.controls.qty_field.value = "3"

    table = controller._build_grid()
    model = table.data

    assert isinstance(model, RegistersModel)
    assert model.default_base is Base.Dec
    assert model.format_map == {0: Base.Float, 2: Base.Bin}
    assert model.float_endian_map == {0: FloatEndian.DCBA}
    assert isinstance(table.rows[0].cells[0].content, ft.ContextMenu)


def test_register_format_helper_sets_float_endian_and_rebuilds_grid() -> None:
    settings = Settings()
    controller, _, page = _build_controller_with(FakeComm(), settings)

    controller._apply_register_format(4, Base.Float, FloatEndian.CDAB)

    assert settings.register_formats == {4: 3}
    assert settings.register_float_endians == {4: 3}
    assert page.update_count >= 1


def test_context_menu_selection_uses_selected_item_data() -> None:
    class SelectedItem:
        data = "base:hex"

    class MenuEvent:
        data = None
        item = SelectedItem()

    settings = Settings()
    controller, _, _ = _build_controller_with(FakeComm(), settings)
    menu = controller._wrap_register_cell(2, ft.Text("cell"))

    assert isinstance(menu, ft.ContextMenu)
    assert menu.on_select is not None
    menu.on_select(MenuEvent())

    assert settings.register_formats == {2: 16}


def test_register_format_helper_rejects_consumed_continuation() -> None:
    settings = Settings()
    settings.register_formats = {0: 3}
    controller, _, page = _build_controller_with(FakeComm(), settings)

    controller._apply_register_format(1, Base.Hex)

    assert settings.register_formats == {0: 3}
    assert page.snack_bar is not None
    assert isinstance(page.snack_bar.content, ft.Text)
    assert page.snack_bar.content.value == "Register 1 is consumed by float at address 0"


def test_reset_register_format_removes_map_entries() -> None:
    settings = Settings()
    settings.register_formats = {4: 3}
    settings.register_float_endians = {4: 2}
    controller, _, page = _build_controller_with(FakeComm(), settings)

    controller._reset_register_format(4)

    assert settings.register_formats == {}
    assert settings.register_float_endians == {}
    assert page.update_count >= 1


def test_register_format_maps_survive_session_save_load(tmp_path) -> None:
    path = tmp_path / "format-session.ses"
    settings = Settings()
    settings.default_base = 16
    settings.register_formats = {0: 3, 2: 2, 3: 16}
    settings.register_float_endians = {0: 0}

    settings.save_session(str(path))
    loaded = Settings()
    loaded.load_session(str(path))

    assert loaded.default_base == 16
    assert loaded.base == 16
    assert loaded.register_formats == {0: 3, 2: 2, 3: 16}
    assert loaded.register_float_endians == {0: 0}


from fmodmaster.main_view import _function_index
