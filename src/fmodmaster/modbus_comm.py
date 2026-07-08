"""Modbus communication layer for fModMaster.

Wraps :mod:`pymodbus` synchronous clients (serial RTU and TCP) and exposes a
small, UI-friendly API modelled on the qModMaster ``ModbusAdapter`` (see
``docs/qmodmaster/sourcecode-ref/qModMaster/src/modbusadapter.cpp``).

Threading contract
-------------------
All blocking pymodbus calls run inside ``page.run_thread`` (a fire-and-forget
worker thread provided by Flet). UI refresh is marshalled back to the Flet
event loop via ``page.run_task``. The HARD RULE is: never call
``page.update()`` directly inside ``run_thread`` -- only inside ``run_task`` or
let Flet auto-update at the event-handler end.

The scan loop is long-lived and needs a responsive stop mechanism, so it runs
in its own managed daemon thread (started from ``run_thread``) and marshals
each refresh via ``run_task``. It never touches ``page.update()`` directly.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional, Union

from pymodbus import ModbusException
from pymodbus.client import ModbusSerialClient, ModbusTcpClient

from .logging_helper import get_logger

_logger = get_logger(__name__)

# Modbus function codes supported by the transaction switch (0x01-0x10).
FC_READ_COILS = 0x01
FC_READ_DISCRETE_INPUTS = 0x02
FC_READ_HOLDING_REGISTERS = 0x03
FC_READ_INPUT_REGISTERS = 0x04
FC_WRITE_SINGLE_COIL = 0x05
FC_WRITE_SINGLE_REGISTER = 0x06
FC_WRITE_MULTIPLE_COILS = 0x0F
FC_WRITE_MULTIPLE_REGISTERS = 0x10
FC_REPORT_SLAVE_ID = 0x11

_READ_FCS = frozenset(
    {
        FC_READ_COILS,
        FC_READ_DISCRETE_INPUTS,
        FC_READ_HOLDING_REGISTERS,
        FC_READ_INPUT_REGISTERS,
    }
)
_WRITE_COIL_FCS = frozenset({FC_WRITE_SINGLE_COIL, FC_WRITE_MULTIPLE_COILS})
_WRITE_REG_FCS = frozenset(
    {FC_WRITE_SINGLE_REGISTER, FC_WRITE_MULTIPLE_REGISTERS}
)

# Raw capture direction labels (match qModMaster Bus Monitor prefixes).
_DIR_TX = "tx"
_DIR_RX = "rx"

# Callback type for raw byte capture: (direction: str, data: bytes) -> None.
OnRaw = Callable[[str, bytes], None]
# Callback type for UI refresh (sync or async).
RefreshCb = Callable[[], Any]


def strip_ip(ip: str) -> str:
    """Strip leading zeros from each octet of an IPv4 string.

    Mirrors ``ModbusAdapter::stripIP`` (modbusadapter.cpp:493-513):
    ``"127.000.000.001"`` -> ``"127.0.0.1"``. Returns ``""`` when the input
    does not contain exactly four dot-separated octets.
    """
    if not ip:
        return ""
    parts = ip.split(".")
    if len(parts) != 4:
        return ""
    out: list[str] = []
    for part in parts:
        try:
            out.append(str(int(part)))
        except ValueError:
            return ""
    return ".".join(out)


def _coerce_timeout_ms(timeout: Union[int, str, float]) -> float:
    """Coerce a timeout value (milliseconds) to seconds, enforcing >= 1s.

    The original qModMaster stores ``TimeOut`` as the string ``"0"`` in
    Settings, which is unsafe (zero-length response timeout). We CORRECT it
    by clamping to a 1000 ms minimum (refs QMODMASTER.md §10.2). The value
    may arrive as int, float, or numeric string.
    """
    try:
        ms = int(float(timeout))
    except (TypeError, ValueError):
        ms = 1000
    # Enforce at least 1 second (QMODMASTER.md §10.2 timeout correction).
    return max(ms, 1000) / 1000.0


class ModbusComm:
    """Manages a pymodbus client connection and request helpers.

    Holds the transaction state (slave, function code, start address, number
    of items, scan rate, timeout) and the packet/error counters, mirroring the
    qModMaster ``ModbusAdapter`` surface area. The UI layer (task 5) binds
    itself to ``refresh_cb`` and the counters; the Bus Monitor (task 6)
    subscribes to ``on_raw``.
    """

    def __init__(
        self,
        page: Optional[Any] = None,
        refresh_cb: Optional[RefreshCb] = None,
        on_raw: Optional[OnRaw] = None,
    ) -> None:
        """Initialize the Modbus communication handler.

        Args:
            page: Flet ``Page`` (or a test double) exposing ``run_thread``
                and ``run_task``. When ``None``, blocking calls run
                synchronously in the current thread (used by tests and the
                Tools ``report_slave_id`` helper).
            refresh_cb: Callback invoked (via ``page.run_task``) after each
                transaction so the UI can refresh counters/table. May be sync
                or async.
            on_raw: Raw Tx/Rx capture hook invoked as
                ``on_raw(direction, data)`` where ``direction`` is ``"tx"`` or
                ``"rx"`` and ``data`` is the raw frame bytes. Subscribed to by
                the Bus Monitor (task 6).
        """
        self._page = page
        self._refresh_cb: Optional[RefreshCb] = refresh_cb
        self.on_raw: Optional[OnRaw] = on_raw

        # Connection state.
        self.connected: bool = False
        self.mode: Optional[str] = None  # "RTU" | "TCP" | None
        self._client: Union[ModbusSerialClient, ModbusTcpClient, None] = None

        # Transaction parameters (set by the UI before each transaction).
        self.slave: int = 1
        self.function_code: int = FC_READ_COILS
        self.start_addr: int = 0
        self.num_items: int = 1
        self.scan_rate: int = 1000  # milliseconds
        self.timeout: float = 1.0  # seconds (coerced, >= 1.0)

        # Counters.
        self.packets: int = 0
        self.errors: int = 0

        # Last transaction result (list of bool for coils, list of int for
        # registers). Cleared on error.
        self.values: list[Any] = []
        # Values to write (set by the UI table before a write transaction).
        self.write_values: list[Any] = []
        # ``True`` when the last read produced valid data; ``False`` after an
        # error (mirrors ``RegistersModel::setNoValidValues``).
        self.valid: bool = False

        # Scan loop control.
        self.scan_running: bool = False
        self._scan_thread: Optional[threading.Thread] = None
        self._scan_stop = threading.Event()

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #

    def connect_rtu(
        self,
        port: str,
        baud: int,
        parity_char: str,
        data_bits: int,
        stop_bits: int,
        rts: str,
        timeout: Union[int, str, float],
    ) -> bool:
        """Connect a Modbus RTU serial client.

        Args:
            port: Serial device path (e.g. ``"/dev/ttyUSB0"`` or ``"COM3"``).
            baud: Baud rate.
            parity_char: Single-char parity: ``"N"``/``"E"``/``"O"``.
            data_bits: 7 or 8.
            stop_bits: 1 or 2.
            rts: RTS mode label. When not ``"None"``/``"Disable"`` the RTS
                line is driven via pyserial after connect (best-effort, refs
                modbusadapter.cpp:48).
            timeout: Response timeout in milliseconds (coerced to >= 1000).

        Returns:
            ``True`` on success, ``False`` on failure.
        """
        self.disconnect()
        self.timeout = _coerce_timeout_ms(timeout)
        parity = (parity_char or "N").strip()[:1].upper() or "N"
        try:
            client = ModbusSerialClient(
                port=port,
                baudrate=int(baud),
                bytesize=int(data_bits),
                parity=parity,
                stopbits=int(stop_bits),
                timeout=self.timeout,
                trace_packet=self._trace_packet,
            )
        except Exception as exc:  # pragma: no cover - defensive
            _logger.error("RTU context creation failed: %s", exc)
            self.connected = False
            self.mode = None
            return False

        if not client.connect():
            _logger.error("RTU connect failed on port %s", port)
            try:
                client.close()
            except Exception:  # pragma: no cover - defensive
                pass
            self.connected = False
            self.mode = None
            self._client = None
            return False

        # Apply RTS via pyserial when requested. pymodbus' ModbusSerialClient
        # has no RTS constructor arg; the underlying serial socket exposes
        # setRTS(). Not all backends support it, so wrap defensively
        # (refs modbusadapter.cpp:48).
        if rts and rts not in ("None", "Disable", "none", "disable"):
            try:
                level = rts in ("Enable", "enable", "True", "true", "1")
                if hasattr(client, "socket") and client.socket is not None:
                    client.socket.setRTS(level)
            except Exception as exc:  # pragma: no cover - backend-dependent
                _logger.warn("RTS setRTS failed (ignored): %s", exc)

        self._client = client
        self.connected = True
        self.mode = "RTU"
        _logger.info("RTU connected on %s @ %d baud", port, baud)
        return True

    def connect_tcp(
        self,
        ip: str,
        port: int,
        timeout: Union[int, str, float],
    ) -> bool:
        """Connect a Modbus TCP client.

        Strips leading zeros from the IP (``strip_ip``), validates that it
        yields four octets each ``<= 255`` and that ``port`` is in
        ``1..65535``. Raises ``ValueError`` with a friendly message on bad
        input (mirrors QMODMASTER.md §5.2).

        Args:
            ip: IPv4 address, possibly zero-padded (``"127.000.000.001"``).
            port: TCP port (1..65535).
            timeout: Response timeout in milliseconds (coerced to >= 1000).

        Returns:
            ``True`` on success, ``False`` on failure.

        Raises:
            ValueError: If the IP or port is invalid.
        """
        stripped = strip_ip(ip) if ip else ""
        if not stripped:
            raise ValueError("Connection failed: blank or invalid IP address.")
        octets = stripped.split(".")
        if len(octets) != 4 or any(not o.isdigit() or int(o) > 255 for o in octets):
            raise ValueError(
                "Connection failed: invalid IP address '%s' "
                "(each octet must be 0..255)." % ip
            )
        try:
            port_i = int(port)
        except (TypeError, ValueError):
            raise ValueError(
                "Connection failed: TCP port must be a number (1..65535)."
            )
        if not (1 <= port_i <= 65535):
            raise ValueError(
                "Connection failed: TCP port %d out of range (1..65535)." % port_i
            )

        self.disconnect()
        self.timeout = _coerce_timeout_ms(timeout)

        try:
            client = ModbusTcpClient(
                host=stripped,
                port=port_i,
                timeout=self.timeout,
                trace_packet=self._trace_packet,
            )
        except Exception as exc:  # pragma: no cover - defensive
            _logger.error("TCP context creation failed: %s", exc)
            self.connected = False
            self.mode = None
            return False

        if not client.connect():
            _logger.error("TCP connect failed to %s:%d", stripped, port_i)
            try:
                client.close()
            except Exception:  # pragma: no cover - defensive
                pass
            self.connected = False
            self.mode = None
            self._client = None
            return False

        self._client = client
        self.connected = True
        self.mode = "TCP"
        _logger.info("TCP connected to %s:%d", stripped, port_i)
        return True

    def disconnect(self) -> None:
        """Close and release the current client, if any."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:  # pragma: no cover - defensive
                _logger.warn("disconnect close failed (ignored): %s", exc)
        self._client = None
        self.connected = False
        self.mode = None
        # Stop the scan loop if it is running.
        if self.scan_running:
            self.stop_scan()

    # ------------------------------------------------------------------ #
    # Transaction
    # ------------------------------------------------------------------ #

    def transaction(self) -> None:
        """Execute one Modbus transaction (read or write) for the current
        function code, slave, start address and item count.

        Dispatches to :meth:`_do_transaction` inside ``page.run_thread`` (or
        synchronously when no page is attached). On success ``packets`` is
        incremented and ``values`` is populated; on error ``errors`` is
        incremented, ``valid`` is cleared and a UI refresh is emitted.
        """
        if self._page is not None and hasattr(self._page, "run_thread"):
            self._page.run_thread(self._do_transaction)
        else:
            self._do_transaction()

    def _do_transaction(self) -> None:
        """Run a single transaction against ``self._client``.

        This is the inner blocking body. It must only be called from a worker
        thread (via :meth:`transaction` or the scan loop). It never calls
        ``page.update()`` directly; UI refresh is marshalled via
        :meth:`_emit_refresh`.
        """
        if self._client is None or not self.connected:
            # No client set: no-op, not a crash (mirrors modbusReadData's
            # ``if(m_modbus == NULL) return;`` guard).
            return

        fc = self.function_code
        try:
            if fc == FC_READ_COILS:
                self._read_bits(self._client.read_coils)
            elif fc == FC_READ_DISCRETE_INPUTS:
                self._read_bits(self._client.read_discrete_inputs)
            elif fc == FC_READ_HOLDING_REGISTERS:
                self._read_regs(self._client.read_holding_registers)
            elif fc == FC_READ_INPUT_REGISTERS:
                self._read_regs(self._client.read_input_registers)
            elif fc == FC_WRITE_SINGLE_COIL:
                self._write_single_coil()
            elif fc == FC_WRITE_SINGLE_REGISTER:
                self._write_single_register()
            elif fc == FC_WRITE_MULTIPLE_COILS:
                self._write_multiple_coils()
            elif fc == FC_WRITE_MULTIPLE_REGISTERS:
                self._write_multiple_registers()
            else:
                # Unknown FC: no-op.
                return
        except ModbusException as exc:
            self._handle_error(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self._handle_error(str(exc))

    def _read_bits(self, fn: Callable[..., Any]) -> None:
        """Execute a bit-read (coils/discrete inputs) and store results."""
        rr = fn(self.start_addr, count=self.num_items, device_id=self.slave)
        if rr.isError():
            self._handle_error("read bits: device error", response=rr)
            return
        bits = list(rr.bits[: self.num_items])
        self.values = bits
        self.valid = True
        self.packets += 1
        self._emit_refresh()

    def _read_regs(self, fn: Callable[..., Any]) -> None:
        """Execute a register-read (holding/input) and store results."""
        rr = fn(self.start_addr, count=self.num_items, device_id=self.slave)
        if rr.isError():
            self._handle_error("read registers: device error", response=rr)
            return
        regs = list(rr.registers[: self.num_items])
        self.values = regs
        self.valid = True
        self.packets += 1
        self._emit_refresh()

    def _write_single_coil(self) -> None:
        """Write one coil (FC 0x05) using ``write_values[0]``."""
        client = self._client
        if client is None:
            return
        value = bool(self.write_values[0]) if self.write_values else False
        rr = client.write_coil(
            self.start_addr, value, device_id=self.slave
        )
        if rr.isError():
            self._handle_error("write single coil: device error", response=rr)
            return
        self.values = [value]
        self.valid = True
        self.packets += 1
        self._emit_refresh()

    def _write_single_register(self) -> None:
        """Write one holding register (FC 0x06) using ``write_values[0]``."""
        client = self._client
        if client is None:
            return
        value = int(self.write_values[0]) if self.write_values else 0
        rr = client.write_register(
            self.start_addr, value, device_id=self.slave
        )
        if rr.isError():
            self._handle_error("write single register: device error", response=rr)
            return
        self.values = [value]
        self.valid = True
        self.packets += 1
        self._emit_refresh()

    def _write_multiple_coils(self) -> None:
        """Write multiple coils (FC 0x0F) using ``write_values``."""
        client = self._client
        if client is None:
            return
        values = [bool(v) for v in self.write_values[: self.num_items]]
        if not values:
            values = [False] * self.num_items
        rr = client.write_coils(
            self.start_addr, values, device_id=self.slave
        )
        if rr.isError():
            self._handle_error("write multiple coils: device error", response=rr)
            return
        self.values = values
        self.valid = True
        self.packets += 1
        self._emit_refresh()

    def _write_multiple_registers(self) -> None:
        """Write multiple holding registers (FC 0x10) using ``write_values``."""
        client = self._client
        if client is None:
            return
        values = [int(v) for v in self.write_values[: self.num_items]]
        if not values:
            values = [0] * self.num_items
        rr = client.write_registers(
            self.start_addr, values, device_id=self.slave
        )
        if rr.isError():
            self._handle_error(
                "write multiple registers: device error", response=rr
            )
            return
        self.values = values
        self.valid = True
        self.packets += 1
        self._emit_refresh()

    def _handle_error(
        self, message: str, response: Optional[Any] = None
    ) -> None:
        """Record a transaction error: bump counter, mark invalid, refresh."""
        self.errors += 1
        self.valid = False
        self.values = []
        detail = message
        if response is not None:
            try:
                detail = "%s (code=%s)" % (message, response.exception_code)
            except Exception:  # pragma: no cover - defensive
                pass
        _logger.error("Modbus transaction error: %s", detail)
        self._emit_refresh()

    # ------------------------------------------------------------------ #
    # Report Slave ID (FC 0x11, Tools-only)
    # ------------------------------------------------------------------ #

    def report_slave_id(
        self, slave: Optional[int] = None
    ) -> tuple[bool, Optional[int], bytes]:
        """Execute FC 0x11 Report Slave/Device ID (Tools-only).

        Runs synchronously (Tools is a user-triggered diagnostic, not part of
        the scan loop). Returns ``(status, slave_id, data)`` where ``status``
        is the device run status (``True``/``False``), ``slave_id`` is the
        first byte of the identifier when available, and ``data`` is the raw
        identifier bytes.

        Args:
            slave: Override device ID; defaults to :attr:`slave`.

        Returns:
            ``(status, slave_id, data)``. On error returns
            ``(False, None, b"")``.
        """
        if self._client is None or not self.connected:
            return (False, None, b"")
        dev_id = slave if slave is not None else self.slave
        try:
            # pymodbus 3.x renamed report_slave_id -> report_device_id.
            rr = self._client.report_device_id(device_id=dev_id)
        except ModbusException as exc:
            _logger.error("report_slave_id failed: %s", exc)
            return (False, None, b"")
        except AttributeError:
            # Fallback for older/newer naming.
            try:
                fallback = getattr(self._client, "report_slave_id")
                rr = fallback(device_id=dev_id)
            except Exception as exc:  # pragma: no cover - defensive
                _logger.error("report_slave_id fallback failed: %s", exc)
                return (False, None, b"")
        if rr.isError():
            _logger.error("report_slave_id device error")
            return (False, None, b"")
        status = bool(getattr(rr, "status", False))
        identifier = bytes(getattr(rr, "identifier", b"") or b"")
        slave_id = identifier[0] if identifier else None
        return (status, slave_id, identifier)

    # ------------------------------------------------------------------ #
    # Pre-read on write-function selection (mirrors addItems())
    # ------------------------------------------------------------------ #

    def add_items(self) -> None:
        """Pre-read current coils/holding regs when a write function is
        selected while connected (mirrors ``ModbusAdapter::addItems()``
        modbusadapter.cpp:443-450).

        Populates :attr:`values` so the UI table can show current values
        before the user edits them. Runs the read inside
        ``page.run_thread``.
        """
        if not self.connected or self._client is None:
            return
        if self.function_code in _WRITE_COIL_FCS:
            self._pre_read(FC_READ_COILS, self._client.read_coils)
        elif self.function_code in _WRITE_REG_FCS:
            self._pre_read(FC_READ_HOLDING_REGISTERS, self._client.read_holding_registers)

    def _pre_read(self, fc: int, fn: Callable[..., Any]) -> None:
        """Run a pre-read for ``add_items`` in a worker thread."""

        def _do() -> None:
            try:
                rr = fn(self.start_addr, count=self.num_items, device_id=self.slave)
                if not rr.isError():
                    if fc == FC_READ_COILS:
                        self.values = list(rr.bits[: self.num_items])
                    else:
                        self.values = list(rr.registers[: self.num_items])
                    self.valid = True
                    self._emit_refresh()
            except Exception as exc:  # pragma: no cover - defensive
                _logger.warn("add_items pre-read failed: %s", exc)

        if self._page is not None and hasattr(self._page, "run_thread"):
            self._page.run_thread(_do)
        else:
            _do()

    # ------------------------------------------------------------------ #
    # Counters
    # ------------------------------------------------------------------ #

    def reset_counters(self) -> None:
        """Zero the packet and error counters (mirrors resetCounters)."""
        self.packets = 0
        self.errors = 0
        self._emit_refresh()

    # ------------------------------------------------------------------ #
    # Scan loop
    # ------------------------------------------------------------------ #

    def start_scan(self) -> None:
        """Start the periodic scan loop.

        Sets :attr:`scan_running` and launches the loop in a daemon thread.
        Each iteration calls :meth:`_do_transaction` then marshals a UI
        refresh via ``page.run_task``. The loop sleeps for ``scan_rate``
        milliseconds between iterations and stops promptly on
        :meth:`stop_scan`.
        """
        if self.scan_running:
            return
        self.scan_running = True
        self._scan_stop.clear()
        self._scan_thread = threading.Thread(
            target=self._scan_loop, name="fModMaster-scan", daemon=True
        )
        self._scan_thread.start()

    def stop_scan(self) -> None:
        """Stop the periodic scan loop and wait briefly for it to finish."""
        if not self.scan_running:
            return
        self.scan_running = False
        self._scan_stop.set()
        thread = self._scan_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._scan_thread = None

    def _scan_loop(self) -> None:
        """Run transactions every ``scan_rate`` ms until ``stop_scan``."""
        interval = max(self.scan_rate, 1) / 1000.0
        while self.scan_running and not self._scan_stop.is_set():
            self._do_transaction()
            # Responsive sleep: wakes immediately when stop is set.
            self._scan_stop.wait(interval)

    # ------------------------------------------------------------------ #
    # UI refresh marshalling + raw capture
    # ------------------------------------------------------------------ #

    def _emit_refresh(self) -> None:
        """Marshal the UI refresh callback to the Flet event loop.

        Uses ``page.run_task`` so the refresh (which may call
        ``page.update()``) runs on the event-loop thread, never inside the
        worker thread. When no page is attached (tests, Tools), the callback
        is invoked synchronously.
        """
        if self._refresh_cb is None:
            return
        if self._page is not None and hasattr(self._page, "run_task"):
            self._page.run_task(self._refresh_async)
        else:
            # No page: invoke directly (tests / Tools).
            try:
                self._refresh_cb()
            except Exception as exc:  # pragma: no cover - defensive
                _logger.warn("refresh_cb failed: %s", exc)

    async def _refresh_async(self) -> None:
        """Async wrapper so ``run_task`` can schedule the refresh callback."""
        if self._refresh_cb is None:
            return
        try:
            result = self._refresh_cb()
            # If the callback itself returns a coroutine, await it.
            if hasattr(result, "__await__"):
                await result  # type: ignore[misc]
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warn("refresh_cb failed: %s", exc)

    def _trace_packet(self, sending: bool, data: bytes) -> bytes:
        """pymodbus ``trace_packet`` callback: tee raw Tx/Rx bytes.

        pymodbus calls this with ``(sending, data)`` where ``sending`` is
        ``True`` for outgoing frames and ``False`` for incoming frames. We
        forward to the ``on_raw`` hook (subscribed by the Bus Monitor, task
        6) and return ``data`` unchanged. Failures here must never break
        comms, so the whole body is defensive.

        This replaces the libmodbus ``busMonitorRawResponseData`` /
        ``busMonitorRequestData`` C callbacks (modbusadapter.cpp:378-415)
        with pymodbus' built-in packet trace hook.
        """
        if self.on_raw is not None:
            try:
                self.on_raw(_DIR_TX if sending else _DIR_RX, bytes(data))
            except Exception as exc:  # pragma: no cover - defensive
                _logger.warn("on_raw hook failed (ignored): %s", exc)
        return data
