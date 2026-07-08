"""Application startup orchestration for fModMaster.

This module keeps the Flet entry point small while exposing deterministic seams
for tests: settings can be loaded from an explicit path, and the page wiring can
be exercised with a fake page instead of a real desktop window.
"""

from __future__ import annotations

import configparser
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import flet as ft

from fmodmaster.config import Settings
from fmodmaster.logging_helper import get_logger
from fmodmaster.main_view import PageLike, build_main_view

APP_TITLE = "fModMaster"
DEFAULT_SETTINGS_FILE = "fModMaster.ini"
DEFAULT_WINDOW_WIDTH = 1024
DEFAULT_WINDOW_HEIGHT = 800
MIN_WINDOW_WIDTH = 900
MIN_WINDOW_HEIGHT = 700

logger = get_logger(__name__)

SettingsFileState = Literal["missing", "readable", "corrupt"]


@dataclass
class App:
    """Runtime objects created during application startup."""

    page: Any
    settings: Settings
    root: ft.Control


def create_app(page: Any, settings_path: str | Path | None = None) -> App:
    """Configure ``page`` and add the fModMaster main view.

    Args:
        page: Flet page or a test double exposing the page methods used by the
            main view.
        settings_path: Optional explicit INI path for deterministic tests.

    Returns:
        The runtime app container with the loaded settings and root control.
    """
    configure_desktop_page(page)
    settings = load_startup_settings(settings_path)
    root = build_main_view(cast(PageLike, page), settings=settings)
    page.add(root)
    _center_desktop_window(page)
    logger.info("fModMaster window initialized")
    return App(page=page, settings=settings, root=root)


def configure_desktop_page(page: Any) -> None:
    """Apply desktop window defaults to a Flet page or test double."""
    page.title = APP_TITLE
    window = getattr(page, "window", None)
    if window is not None:
        _safe_setattr(window, "width", DEFAULT_WINDOW_WIDTH)
        _safe_setattr(window, "height", DEFAULT_WINDOW_HEIGHT)
        _safe_setattr(window, "min_width", MIN_WINDOW_WIDTH)
        _safe_setattr(window, "min_height", MIN_WINDOW_HEIGHT)
        return

    # Flet historically exposed these directly on Page. Keep the fallback for
    # compatibility with older runtimes and simple test doubles.
    _safe_setattr(page, "window_width", DEFAULT_WINDOW_WIDTH)
    _safe_setattr(page, "window_height", DEFAULT_WINDOW_HEIGHT)
    _safe_setattr(page, "window_min_width", MIN_WINDOW_WIDTH)
    _safe_setattr(page, "window_min_height", MIN_WINDOW_HEIGHT)


def load_startup_settings(settings_path: str | Path | None = None) -> Settings:
    """Load launch settings, falling back to defaults on missing/corrupt INI.

    ``Settings`` intentionally does not auto-load. Startup owns the explicit
    load call so tests and future launch modes can choose the settings path.
    """
    path = _resolve_settings_path(settings_path)
    state = _settings_file_state(path)
    settings = Settings()

    if state == "missing":
        logger.info("Settings file %s not found; using defaults", path)
    elif state == "corrupt":
        logger.warning("Settings file %s is corrupt; using defaults", path)
    else:
        logger.info("Loading settings from %s", path)

    settings.load_settings(str(path))
    return settings


def _resolve_settings_path(settings_path: str | Path | None) -> Path:
    if settings_path is None:
        return Path.cwd() / DEFAULT_SETTINGS_FILE
    return Path(settings_path)


def _settings_file_state(path: Path) -> SettingsFileState:
    if not path.exists():
        return "missing"

    parser = configparser.ConfigParser()
    try:
        with path.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error):
        return "corrupt"
    return "readable"


def _safe_setattr(target: Any, name: str, value: int) -> None:
    try:
        setattr(target, name, value)
    except (AttributeError, TypeError):
        return


def _center_desktop_window(page: Any) -> None:
    window = getattr(page, "window", None)
    if window is None or not callable(getattr(window, "center", None)):
        return
    run_task = getattr(page, "run_task", None)
    if callable(run_task):
        run_task(_center_desktop_window_async, window)


async def _center_desktop_window_async(window: Any) -> None:
    wait_until_ready = getattr(window, "wait_until_ready_to_show", None)
    if callable(wait_until_ready):
        result = wait_until_ready()
        if inspect.isawaitable(result):
            await result
    center = getattr(window, "center")
    result = center()
    if inspect.isawaitable(result):
        await result
