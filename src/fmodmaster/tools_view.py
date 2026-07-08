from __future__ import annotations

import socket
import subprocess
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Final, Protocol

import flet as ft

from .modbus_comm import strip_ip


class PageLike(Protocol):
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

    def show_dialog(self, dialog: ft.AlertDialog) -> None:
        ...

    def pop_dialog(self) -> None:
        ...


class CommLike(Protocol):
    slave: int

    def report_slave_id(self, slave: int | None = None) -> tuple[bool, int | None, bytes]:
        ...


class SettingsLike(Protocol):
    modbus_mode: int
    slave_ip: str
    tcp_port: str


@dataclass(slots=True)  # noqa: MUTABLE_OK - owns mutable Flet controls.
class ToolsControls:
    mode_dropdown: ft.Dropdown
    command_dropdown: ft.Dropdown
    output_text: ft.Text
    dialog: ft.AlertDialog


_MODE_RTU_TCP: Final = "RTU/TCP"
_MODE_TCP: Final = "TCP"
_CMD_REPORT_SLAVE_ID: Final = "Report Slave ID"
_CMD_PING: Final = "Ping"
_CMD_PORT_STATUS: Final = "Port Status"
_TCP_COMMANDS: Final = (_CMD_REPORT_SLAVE_ID, _CMD_PING, _CMD_PORT_STATUS)
_RTU_TCP_COMMANDS: Final = (_CMD_REPORT_SLAVE_ID,)
_TIMEOUT_SECONDS: Final = 5


class ToolsController:
    def __init__(self, page: PageLike, comm: CommLike, settings: SettingsLike) -> None:
        self.page = page
        self.comm = comm
        self.settings = settings
        self.controls = self._build_controls()
        self._refresh_commands()

    def open(self) -> ft.AlertDialog:
        self.controls.dialog.open = True
        self._refresh_commands()
        self.page.show_dialog(self.controls.dialog)
        return self.controls.dialog

    def close(self) -> None:
        self.controls.dialog.open = False
        self.page.pop_dialog()

    def clear(self) -> None:
        self.controls.output_text.value = ""
        self.page.update()

    def exec_selected(self) -> None:
        self.controls.output_text.value = "Running..."
        self.page.update()
        self.page.run_thread(self._run_selected_command)

    def _build_controls(self) -> ToolsControls:
        mode = _MODE_TCP if self.settings.modbus_mode == 1 else _MODE_RTU_TCP
        mode_dropdown = ft.Dropdown(
            value=mode,
            label="Mode",
            width=160,
            options=[
                ft.DropdownOption(key=_MODE_RTU_TCP, text=_MODE_RTU_TCP),
                ft.DropdownOption(key=_MODE_TCP, text=_MODE_TCP),
            ],
        )
        command_dropdown = ft.Dropdown(
            value=_CMD_REPORT_SLAVE_ID,
            label="Command",
            width=220,
        )
        output_text = ft.Text("", selectable=True)
        dialog = ft.AlertDialog(
            modal=False,
            title="Tools",
            content=ft.Column(
                controls=[
                    ft.Row([mode_dropdown, command_dropdown], wrap=True, spacing=8),
                    ft.Container(content=output_text, padding=8),
                ],
                width=620,
                height=360,
                spacing=8,
            ),
            actions=[
                ft.TextButton("Exec", on_click=self.exec_selected),
                ft.TextButton("Clear", on_click=self.clear),
                ft.TextButton("Exit", on_click=self.close),
            ],
            open=False,
        )
        dialog.data = self
        mode_dropdown.on_select = self._on_mode_change
        return ToolsControls(mode_dropdown, command_dropdown, output_text, dialog)

    def _on_mode_change(self) -> None:
        self._refresh_commands()
        self.page.update()

    def _refresh_commands(self) -> None:
        commands = _TCP_COMMANDS if self._mode() == _MODE_TCP else _RTU_TCP_COMMANDS
        self.controls.command_dropdown.options = [
            ft.DropdownOption(key=command, text=command) for command in commands
        ]
        if self.controls.command_dropdown.value not in commands:
            self.controls.command_dropdown.value = _CMD_REPORT_SLAVE_ID

    def _run_selected_command(self) -> None:
        command = self.controls.command_dropdown.value or _CMD_REPORT_SLAVE_ID
        match command:
            case "Report Slave ID":
                result = self._report_slave_id_text()
            case "Ping":
                result = ping_text(self.settings.slave_ip)
            case "Port Status":
                result = port_status_text(self.settings.slave_ip, self.settings.tcp_port)
            case _:
                result = f"Unknown command: {command}"
        self.page.run_task(self._set_output_async, result)

    async def _set_output_async(self, text: str) -> None:
        self.controls.output_text.value = text
        self.page.update()

    def _report_slave_id_text(self) -> str:
        status, slave_id, data = self.comm.report_slave_id()
        rows = [
            "Report Slave ID",
            f"Run Status: {'ON' if status else 'OFF'}",
            f"Slave ID: {slave_id if slave_id is not None else '-'}",
        ]
        if data:
            rows.append("Data: " + data.hex(" ").upper())
        return "\n".join(rows)

    def _mode(self) -> str:
        return self.controls.mode_dropdown.value or _MODE_RTU_TCP


def build_tools_dialog(
    page: PageLike, comm: CommLike, settings: SettingsLike
) -> ft.AlertDialog:
    controller = ToolsController(page, comm, settings)
    return controller.controls.dialog


def build_tools_view(page: PageLike, comm: CommLike, settings: SettingsLike) -> ft.AlertDialog:
    return build_tools_dialog(page, comm, settings)


def ping_text(ip: str) -> str:
    target = _normalized_ip(ip)
    args = _ping_args(target)
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Ping {target}: timeout after {_TIMEOUT_SECONDS}s"
    except OSError as exc:
        return f"Ping {target}: error: {exc}"

    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    status = "success" if completed.returncode == 0 else "error"
    if output:
        return f"Ping {target}: {status}\n{output}"
    return f"Ping {target}: {status} (exit {completed.returncode})"


def port_status_text(ip: str, port: str | int) -> str:
    target = _normalized_ip(ip)
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        return f"Port Status {target}:{port}: closed (invalid port)"
    try:
        with socket.create_connection((target, port_i), timeout=_TIMEOUT_SECONDS):
            return f"Port Status {target}:{port_i}: open"
    except OSError as exc:
        return f"Port Status {target}:{port_i}: closed ({exc})"


def _ping_args(ip: str) -> list[str]:
    if sys.platform.startswith("win"):
        return ["ping", "-n", "1", "-w", "5", ip]
    return ["ping", "-c", "1", "-W", "5", ip]


def _normalized_ip(ip: str) -> str:
    stripped = strip_ip(ip)
    return stripped if stripped else ip
