"""fModMaster application entry point.

Launches the Flet desktop window for the Modbus master application.
"""

import flet as ft
from typing import cast

from fmodmaster.logging_helper import get_logger
from fmodmaster.main_view import PageLike, build_main_view

logger = get_logger(__name__)


def main(page: ft.Page) -> None:
    """Configure and render the root Flet page.

    Args:
        page: The Flet page instance provided by ``ft.run``.
    """
    page.title = "fModMaster"
    page.add(build_main_view(cast(PageLike, page)))
    logger.info("fModMaster window initialized")


if __name__ == "__main__":
    ft.run(main)
