"""Modbus communication layer for fModMaster.

Wraps pymodbus client connections (serial RTU / TCP) and exposes read/write
helpers. Placeholder stub.
"""

from __future__ import annotations

from typing import Any, Optional


class ModbusComm:
    """Manages a pymodbus client connection and request helpers.

    Placeholder stub — actual connect/read/write logic to be implemented.
    """

    def __init__(self, connection: Optional[Any] = None) -> None:
        """Initialize the Modbus communication handler.

        Args:
            connection: Optional pre-built pymodbus client instance.
        """
        ...
