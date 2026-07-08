from __future__ import annotations

import asyncio
import importlib
import tomllib
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import flet as ft

from fmodmaster import app as app_module
from fmodmaster.app import (
    APP_TITLE,
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    create_app,
    load_startup_settings,
)
from fmodmaster.config import Settings
from fmodmaster.main_view import MainViewController
from fmodmaster.modbus_comm import ModbusComm


class FakeWindow:
    def __init__(self) -> None:
        self.width = 0
        self.height = 0
        self.min_width = 0
        self.min_height = 0


class FakePage:
    def __init__(self) -> None:
        self.title = ""
        self.window = FakeWindow()
        self.appbar: ft.AppBar | None = None
        self.snack_bar: ft.SnackBar | None = None
        self.dialog: ft.AlertDialog | None = None
        self.overlay: list[ft.Control] = []
        self.controls: list[ft.Control] = []
        self.update_count = 0

    def add(self, *controls: ft.Control) -> None:
        self.controls.extend(controls)

    def run_thread(self, handler: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        handler(*args, **kwargs)

    def run_task(
        self,
        handler: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        coroutine = handler(*args, **kwargs)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coroutine)
        finally:
            loop.close()

    def update(self) -> None:
        self.update_count += 1


def test_load_startup_settings_missing_ini_uses_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    settings = load_startup_settings()

    assert settings.tcp_port == "502"
    assert settings.slave_ip == "127.000.000.001"
    assert settings.time_out == "0"


def test_load_startup_settings_restores_existing_ini(tmp_path) -> None:
    ini_path = tmp_path / "fModMaster.ini"
    saved = Settings()
    saved.tcp_port = "1502"
    saved.slave_ip = "010.000.000.001"
    saved.modbus_mode = 1
    saved.slave_id = 17
    saved.save_settings(str(ini_path))

    settings = load_startup_settings(ini_path)

    assert settings.tcp_port == "1502"
    assert settings.slave_ip == "010.000.000.001"
    assert settings.modbus_mode == 1
    assert settings.slave_id == 17


def test_load_startup_settings_corrupt_ini_warns_and_uses_defaults(
    tmp_path, monkeypatch
) -> None:
    ini_path = tmp_path / "fModMaster.ini"
    ini_path.write_text("not an ini\n[[[\n", encoding="utf-8")
    warnings: list[str] = []

    def capture_warning(message: str, *args: Any) -> None:
        warnings.append(message % args)

    monkeypatch.setattr(app_module.logger, "warning", capture_warning)

    settings = load_startup_settings(ini_path)

    assert settings.tcp_port == "502"
    assert settings.slave_ip == "127.000.000.001"
    assert warnings == [f"Settings file {ini_path} is corrupt; using defaults"]


def test_create_app_configures_desktop_page_and_main_view(tmp_path) -> None:
    ini_path = tmp_path / "fModMaster.ini"
    saved = Settings()
    saved.scan_rate = 250
    saved.save_settings(str(ini_path))
    page = FakePage()

    app = create_app(page, settings_path=ini_path)

    assert page.title == APP_TITLE
    assert page.window.width == DEFAULT_WINDOW_WIDTH
    assert page.window.height == DEFAULT_WINDOW_HEIGHT
    assert page.window.min_width == MIN_WINDOW_WIDTH
    assert page.window.min_height == MIN_WINDOW_HEIGHT
    assert page.controls == [app.root]
    assert app.settings.scan_rate == 250
    assert isinstance(app.root.data, MainViewController)
    assert app.root.data.settings is app.settings
    assert isinstance(app.root.data.comm, ModbusComm)


def test_project_script_entry_point_runs_flet_with_main_callback(monkeypatch) -> None:
    script_target = _project_script_target("fmodmaster")
    module_name, function_name = script_target.split(":", maxsplit=1)
    module = importlib.import_module(module_name)
    callbacks: list[Callable[[ft.Page], None]] = []

    def capture_run(callback: Callable[[ft.Page], None]) -> None:
        callbacks.append(callback)

    monkeypatch.setattr(module.ft, "run", capture_run)

    entry_point = getattr(module, function_name)
    entry_point()

    assert callbacks == [module.main]


def _project_script_target(script_name: str) -> str:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as config_file:
        config = tomllib.load(config_file)
    return config["project"]["scripts"][script_name]
