from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import flet as ft

from fmodmaster.bus_monitor import BusMonitorController
from fmodmaster.config import Settings
from fmodmaster.main_view import (
    GeneralSettingsDialogData,
    MainViewController,
    RtuSettingsDialogData,
    TcpSettingsDialogData,
    build_main_view,
)
from fmodmaster.tools_view import ToolsController, build_tools_dialog, ping_text, port_status_text


class FakePage:
    def __init__(self) -> None:
        self.appbar: ft.AppBar | None = None
        self.snack_bar: ft.SnackBar | None = None
        self.dialog: ft.AlertDialog | None = None
        self.overlay: list[ft.Control] = []
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
        self.dialog = dialog
        self.show_dialog_count += 1

    def pop_dialog(self) -> None:
        self.pop_dialog_count += 1
        if self.dialog is not None:
            self.dialog.open = False


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
        self.on_raw: Callable[[str, bytes], None] | None = None

    def report_slave_id(self, slave: int | None = None) -> tuple[bool, int | None, bytes]:
        return (True, 42, b"DEV")

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
        return True

    def connect_tcp(self, ip: str, port: int, timeout: int | str | float) -> bool:
        self.connected = True
        self.mode = "TCP"
        return True

    def disconnect(self) -> None:
        self.connected = False
        self.mode = None
        self.scan_running = False

    def transaction(self) -> None:
        self.packets += 1

    def start_scan(self) -> None:
        self.scan_running = True

    def stop_scan(self) -> None:
        self.scan_running = False

    def reset_counters(self) -> None:
        self.packets = 0
        self.errors = 0


def _controller(settings: Settings | None = None) -> tuple[MainViewController, Settings, FakePage]:
    page = FakePage()
    active_settings = settings if settings is not None else Settings()
    view = build_main_view(page, settings=active_settings, comm=FakeComm())
    assert isinstance(view.data, MainViewController)
    return view.data, active_settings, page


def _click_first_dialog_action(page: FakePage) -> None:
    assert page.dialog is not None
    assert page.dialog.actions is not None
    action = page.dialog.actions[0]
    assert isinstance(action, ft.TextButton)
    _invoke_click(action.on_click)


def _invoke_click(handler: Callable[..., Any] | None) -> None:
    assert handler is not None
    handler()


def test_report_slave_id_with_mock_returns_status_and_id() -> None:
    page = FakePage()
    settings = Settings()
    dialog = build_tools_dialog(page, FakeComm(), settings)
    assert isinstance(dialog.data, ToolsController)
    controller = dialog.data

    controller.open()
    controller.controls.command_dropdown.value = "Report Slave ID"
    controller.exec_selected()

    assert "Run Status: ON" in controller.controls.output_text.value
    assert "Slave ID: 42" in controller.controls.output_text.value
    assert "44 45 56" in controller.controls.output_text.value


def test_toolbar_tools_button_opens_real_tools_dialog() -> None:
    controller, _, page = _controller()

    _invoke_click(controller.controls.tools_button.on_click)

    assert page.dialog is not None
    assert page.dialog.open is True
    assert isinstance(page.dialog.data, ToolsController)
    assert page.snack_bar is None


def test_menu_tools_action_opens_real_tools_dialog() -> None:
    controller, _, page = _controller()

    controller._menu_handler("Tools")()

    assert page.dialog is not None
    assert page.dialog.open is True
    assert isinstance(page.dialog.data, ToolsController)
    assert page.snack_bar is None


def test_menu_tools_action_uses_current_flet_dialog_api() -> None:
    controller, _, page = _controller()

    controller._menu_handler("Tools")()

    assert page.show_dialog_count == 1
    assert page.dialog is not None
    assert page.dialog.open is True
    assert isinstance(page.dialog.data, ToolsController)


def test_toolbar_bus_monitor_button_opens_real_bus_monitor_dialog() -> None:
    controller, _, page = _controller()

    _invoke_click(controller.controls.bus_monitor_button.on_click)

    assert page.dialog is not None
    assert page.dialog.open is True
    assert isinstance(page.dialog.data, BusMonitorController)
    assert page.snack_bar is None


def test_menu_bus_monitor_action_opens_real_bus_monitor_dialog() -> None:
    controller, _, page = _controller()

    controller._menu_handler("Bus Monitor")()

    assert page.dialog is not None
    assert page.dialog.open is True
    assert isinstance(page.dialog.data, BusMonitorController)
    assert page.snack_bar is None


def test_menu_bus_monitor_action_uses_current_flet_dialog_api() -> None:
    controller, _, page = _controller()

    controller._menu_handler("Bus Monitor")()

    assert page.show_dialog_count == 1
    assert page.dialog is not None
    assert page.dialog.open is True
    assert isinstance(page.dialog.data, BusMonitorController)


def test_ping_to_localhost_returns_success_text() -> None:
    result = ping_text("127.0.0.1")

    assert "Ping 127.0.0.1: success" in result


def test_ping_to_unreachable_host_returns_error_without_hanging() -> None:
    started = time.monotonic()

    result = ping_text("192.0.2.1")

    assert time.monotonic() - started < 6.5
    assert "Ping 192.0.2.1:" in result
    assert "error" in result or "timeout" in result


def test_port_status_to_open_local_port_returns_open() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        result = port_status_text("127.0.0.1", port)

    assert f"Port Status 127.0.0.1:{port}: open" in result


def test_port_status_to_closed_local_port_reports_closed() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    result = port_status_text("127.0.0.1", port)

    assert f"Port Status 127.0.0.1:{port}: closed" in result


def test_tcp_settings_dialog_saves_values_back_to_settings_and_ini(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    controller, settings, page = _controller()

    controller._show_tcp_settings()
    assert page.dialog is not None
    assert isinstance(page.dialog.data, TcpSettingsDialogData)
    page.dialog.data.slave_ip.value = "010.000.000.001"
    page.dialog.data.tcp_port.value = "1502"
    _click_first_dialog_action(page)

    loaded = Settings()
    loaded.load_settings()
    assert settings.slave_ip == "010.000.000.001"
    assert settings.tcp_port == "1502"
    assert loaded.slave_ip == "010.000.000.001"
    assert loaded.tcp_port == "1502"


def test_rtu_settings_dialog_saves_values_back_to_settings_and_ini(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    controller, settings, page = _controller()

    controller._show_rtu_settings()
    assert page.dialog is not None
    assert isinstance(page.dialog.data, RtuSettingsDialogData)
    page.dialog.data.serial_port.value = "/dev/ttyUSB2"
    page.dialog.data.baud.value = "19200"
    page.dialog.data.data_bits.value = "7"
    page.dialog.data.stop_bits.value = "2"
    page.dialog.data.parity.value = "Even"
    page.dialog.data.rts.value = "Enable"
    _click_first_dialog_action(page)

    loaded = Settings()
    loaded.load_settings()
    assert settings.serial_port_name == "/dev/ttyUSB2"
    assert settings.serial_dev == "/dev/ttyUSB"
    assert settings.serial_port == "2"
    assert settings.baud == "19200"
    assert loaded.baud == "19200"
    assert loaded.parity == "Even"


def test_general_settings_dialog_saves_values_back_to_settings_and_ini(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    controller, settings, page = _controller()

    controller._show_general_settings()
    assert page.dialog is not None
    assert isinstance(page.dialog.data, GeneralSettingsDialogData)
    page.dialog.data.time_out.value = "2500"
    page.dialog.data.max_no_of_lines.value = "99"
    page.dialog.data.base_addr.value = "10"
    _click_first_dialog_action(page)

    loaded = Settings()
    loaded.load_settings()
    assert settings.time_out == "2500"
    assert settings.max_no_of_lines == "99"
    assert settings.base_addr == "10"
    assert loaded.time_out == "2500"
    assert loaded.max_no_of_lines == "99"
    assert loaded.base_addr == "10"


def test_load_session_populates_main_fields(tmp_path: Path) -> None:
    session = Settings()
    session.modbus_mode = 1
    session.slave_id = 17
    session.scan_rate = 750
    session.function_code = 2
    session.start_addr = 123
    session.no_of_regs = 12
    session.base = 10
    path = tmp_path / "load.ses"
    session.save_session(str(path))
    controller, _, _ = _controller()

    controller._load_session_from_path(str(path))

    assert controller.controls.mode_dropdown.value == "TCP"
    assert controller.controls.slave_field.value == "17"
    assert controller.controls.scan_rate_field.value == "750"
    assert controller.controls.function_dropdown.value == "3"
    assert controller.controls.start_addr_field.value == "123"
    assert controller.controls.qty_field.value == "12"
