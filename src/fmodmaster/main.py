"""fModMaster application entry point.

Launches the Flet desktop window for the Modbus master application.
"""

import flet as ft

from fmodmaster.app import create_app


def main(page: ft.Page) -> None:
    """Configure and render the root Flet page.

    Args:
        page: The Flet page instance provided by ``ft.run``.
    """
    create_app(page)


def cli() -> None:
    ft.run(main)


if __name__ == "__main__":
    cli()
