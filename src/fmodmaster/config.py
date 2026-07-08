"""Application configuration and session persistence for fModMaster.

Mirrors qModMaster's ``ModbusCommSettings`` (modbuscommsettings.cpp): the same
INI groups/keys and default values are used so that ``fModMaster.ini`` and
``.ses`` session files stay compatible with the C++ version.

INI layout (keys are EXACT, do not rename — needed for .ses compatibility):

    [TCP]    TCPPort, SlaveIP
    [RTU]    SerialDev, SerialPort, SerialPortName, Baud, DataBits,
             StopBits, Parity, RTS
    [Var]    MaxNoOfLines, BaseAddr, TimeOut, LoggingLevel
    [Session] ModBusMode, SlaveID, ScanRate, FunctionCode,
              StartAddr, NoOfRegs, Base

``TimeOut`` is stored as a STRING (per C++) — use ``timeout_seconds()`` for
the int coercion. The >=1s minimum correction lives in ModbusComm (task C2),
not here.
"""

from __future__ import annotations

import configparser
import os
import sys
from typing import Optional

_INI_FILE_NAME = "fModMaster.ini"


def _is_windows() -> bool:
    """Return True when running on a Windows platform."""
    return sys.platform.startswith("win")


def _ini_path() -> str:
    """Return the absolute path of ``fModMaster.ini`` in the current dir."""
    return os.path.join(os.getcwd(), _INI_FILE_NAME)


class Settings:
    """Persist Modbus connection / session settings to ``fModMaster.ini``.

    Attributes mirror qModMaster's ``ModbusCommSettings`` fields. String-typed
    C++ fields are Python ``str``; ``int``-typed C++ fields are Python ``int``.
    On construction the object holds the C++ defaults; call ``load_settings()``
    to overlay values from ``fModMaster.ini`` (missing keys/sections fall back
    to the defaults without raising).
    """

    # INI groups.
    _TCP = "TCP"
    _RTU = "RTU"
    _VAR = "Var"
    _SESSION = "Session"

    def __init__(self) -> None:
        """Initialise the settings with qModMaster defaults (no file load)."""
        win = _is_windows()

        # [TCP]
        self.tcp_port: str = "502"
        self.slave_ip: str = "127.000.000.001"

        # [RTU]
        self.serial_dev: str = "COM" if win else "/dev/ttyS"
        self.serial_port: str = "1"
        # Default SerialPortName mirrors C++ load() when SerialPort is null:
        #   Win  -> "COM" + serial_port            -> "COM1"
        #   Unix -> serial_dev + (port.toInt()-1)  -> "/dev/ttyS0"
        self.serial_port_name: str = self._compute_serial_port_name(
            self.serial_dev, self.serial_port
        )
        self.baud: str = "9600"
        self.data_bits: str = "8"
        self.stop_bits: str = "1"
        self.parity: str = "None"
        self.rts: str = "Disable" if win else "None"

        # [Var]
        self.max_no_of_lines: str = "60"
        self.base_addr: str = "0"
        self.time_out: str = "0"  # STRING per C++
        self.logging_level: int = 3  # WarnLevel (QsLog)

        # [Session]
        self.modbus_mode: int = 0  # RTU
        self.slave_id: int = 1
        self.scan_rate: int = 1000
        self.function_code: int = 0  # Read Coils
        self.start_addr: int = 0
        self.no_of_regs: int = 0
        self.base: int = 1  # Dec
        self.float_endian: int = 0  # ABCD

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _compute_serial_port_name(serial_dev: str, serial_port: str) -> str:
        """Compute ``SerialPortName`` from ``SerialDev`` + ``SerialPort``.

        Replicates ``ModbusCommSettings::setSerialPort``:
            Win  -> ``\\\\.\\COM<n>`` when n > 9 else ``COM<n>``
            Unix -> ``serial_dev + str(int(serial_port) - 1)``
        """
        if _is_windows():
            try:
                n = int(serial_port)
            except ValueError:
                return "COM" + serial_port
            if n > 9:
                return "\\\\.\\COM" + serial_port
            return "COM" + serial_port
        try:
            n = int(serial_port)
        except ValueError:
            return serial_dev + serial_port
        return serial_dev + str(n - 1)

    @staticmethod
    def strip_ip(ip: str) -> str:
        """Strip leading zeros from each octet of an IPv4 string.

        Identical to ``ModbusAdapter::stripIP``: splits on ``.``, and if there
        are exactly 4 octets returns ``int(octet)`` joined by ``.``, else ``""``.
        e.g. ``"127.000.000.001"`` -> ``"127.0.0.1"``.
        """
        octets = ip.split(".")
        if len(octets) != 4:
            return ""
        try:
            return ".".join(str(int(o)) for o in octets)
        except ValueError:
            return ""

    def timeout_seconds(self) -> int:
        """Coerce the string ``TimeOut`` value to int (0 for ``"0"``).

        The >=1s minimum correction is applied in ModbusComm (task C2), not
        here — this only exposes the raw configured int.
        """
        try:
            return int(self.time_out)
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def load_settings(self, path: Optional[str] = None) -> None:
        """Load settings from ``fModMaster.ini`` (or ``path``), keeping defaults
        for any missing/corrupt keys/sections."""
        self._load_from_file(path if path is not None else _ini_path())

    def save_settings(self, path: Optional[str] = None) -> None:
        """Save settings to ``fModMaster.ini`` (or ``path``)."""
        self._save_to_file(path if path is not None else _ini_path())

    def load_session(self, path: str) -> None:
        """Load a ``.ses`` session file, overlaying its values on the defaults."""
        self._load_from_file(path)

    def save_session(self, path: str) -> None:
        """Save the current state to a ``.ses`` session file."""
        self._save_to_file(path)

    # ------------------------------------------------------------------ #
    # Internal load / save
    # ------------------------------------------------------------------ #

    def _load_from_file(self, path: str) -> None:
        """Overlay values from ``path`` onto the current (default) state.

        Missing sections/keys are silently ignored (defaults remain). A
        corrupt/unreadable file leaves the defaults untouched.
        """
        parser = configparser.ConfigParser()
        try:
            if not os.path.exists(path):
                return  # nothing to load — keep defaults
            with open(path, "r", encoding="utf-8") as fh:
                parser.read_file(fh)
        except (OSError, configparser.Error):
            return  # corrupt / unreadable — keep defaults

        # [TCP]
        if parser.has_section(self._TCP):
            if parser.has_option(self._TCP, "TCPPort"):
                self.tcp_port = parser.get(self._TCP, "TCPPort")
            if parser.has_option(self._TCP, "SlaveIP"):
                self.slave_ip = parser.get(self._TCP, "SlaveIP")

        # [RTU]
        if parser.has_section(self._RTU):
            if parser.has_option(self._RTU, "SerialDev"):
                # On Windows C++ forces "COM" regardless of stored value.
                if not _is_windows():
                    self.serial_dev = parser.get(self._RTU, "SerialDev")
            if parser.has_option(self._RTU, "SerialPort"):
                self.serial_port = parser.get(self._RTU, "SerialPort")
            if parser.has_option(self._RTU, "SerialPortName"):
                self.serial_port_name = parser.get(self._RTU, "SerialPortName")
            if parser.has_option(self._RTU, "Baud"):
                self.baud = parser.get(self._RTU, "Baud")
            if parser.has_option(self._RTU, "DataBits"):
                self.data_bits = parser.get(self._RTU, "DataBits")
            if parser.has_option(self._RTU, "StopBits"):
                self.stop_bits = parser.get(self._RTU, "StopBits")
            if parser.has_option(self._RTU, "Parity"):
                self.parity = parser.get(self._RTU, "Parity")
            if parser.has_option(self._RTU, "RTS"):
                self.rts = parser.get(self._RTU, "RTS")

        # [Var]
        if parser.has_section(self._VAR):
            # MaxNoOfLines: C++ falls back to "60" when value is 0 OR null.
            if parser.has_option(self._VAR, "MaxNoOfLines"):
                raw = parser.get(self._VAR, "MaxNoOfLines")
                try:
                    if int(raw) != 0:
                        self.max_no_of_lines = raw
                except ValueError:
                    self.max_no_of_lines = raw
            if parser.has_option(self._VAR, "BaseAddr"):
                self.base_addr = parser.get(self._VAR, "BaseAddr")
            if parser.has_option(self._VAR, "TimeOut"):
                self.time_out = parser.get(self._VAR, "TimeOut")
            if parser.has_option(self._VAR, "LoggingLevel"):
                try:
                    self.logging_level = int(parser.get(self._VAR, "LoggingLevel"))
                except ValueError:
                    pass

        # [Session]
        if parser.has_section(self._SESSION):
            if parser.has_option(self._SESSION, "ModBusMode"):
                try:
                    self.modbus_mode = int(parser.get(self._SESSION, "ModBusMode"))
                except ValueError:
                    pass
            if parser.has_option(self._SESSION, "SlaveID"):
                try:
                    self.slave_id = int(parser.get(self._SESSION, "SlaveID"))
                except ValueError:
                    pass
            if parser.has_option(self._SESSION, "ScanRate"):
                try:
                    self.scan_rate = int(parser.get(self._SESSION, "ScanRate"))
                except ValueError:
                    pass
            if parser.has_option(self._SESSION, "FunctionCode"):
                try:
                    self.function_code = int(parser.get(self._SESSION, "FunctionCode"))
                except ValueError:
                    pass
            if parser.has_option(self._SESSION, "StartAddr"):
                try:
                    self.start_addr = int(parser.get(self._SESSION, "StartAddr"))
                except ValueError:
                    pass
            if parser.has_option(self._SESSION, "NoOfRegs"):
                try:
                    self.no_of_regs = int(parser.get(self._SESSION, "NoOfRegs"))
                except ValueError:
                    pass
            if parser.has_option(self._SESSION, "Base"):
                try:
                    self.base = int(parser.get(self._SESSION, "Base"))
                except ValueError:
                    pass
            if parser.has_option(self._SESSION, "FloatEndian"):
                try:
                    self.float_endian = int(parser.get(self._SESSION, "FloatEndian"))
                except ValueError:
                    pass

    def _save_to_file(self, path: str) -> None:
        """Write the current state to ``path`` in INI format."""
        parser = configparser.ConfigParser()

        parser[self._TCP] = {
            "TCPPort": self.tcp_port,
            "SlaveIP": self.slave_ip,
        }
        parser[self._RTU] = {
            "SerialDev": self.serial_dev,
            "SerialPort": self.serial_port,
            "SerialPortName": self.serial_port_name,
            "Baud": self.baud,
            "DataBits": self.data_bits,
            "StopBits": self.stop_bits,
            "Parity": self.parity,
            "RTS": self.rts,
        }
        parser[self._VAR] = {
            "MaxNoOfLines": self.max_no_of_lines,
            "BaseAddr": self.base_addr,
            "TimeOut": self.time_out,
            "LoggingLevel": str(self.logging_level),
        }
        parser[self._SESSION] = {
            "ModBusMode": str(self.modbus_mode),
            "SlaveID": str(self.slave_id),
            "ScanRate": str(self.scan_rate),
            "FunctionCode": str(self.function_code),
            "StartAddr": str(self.start_addr),
            "NoOfRegs": str(self.no_of_regs),
            "Base": str(self.base),
            "FloatEndian": str(self.float_endian),
        }

        with open(path, "w", encoding="utf-8") as fh:
            parser.write(fh)