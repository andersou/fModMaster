"""Offline tests for :mod:`fmodmaster.modbus_comm`.

These tests do NOT touch the network or any serial port. They cover:

* ``strip_ip`` zero-stripping and validation.
* ``connect_tcp`` IP/port validation (``ValueError`` with friendly message).
* ``transaction()`` with no client set is a no-op, not a crash.
* ``start_scan``/``stop_scan`` toggle ``scan_running`` and the scan loop
  calls ``transaction()`` each interval, stopping on ``stop_scan``.

Run with: ``uv run python -m pytest tests/test_modbus_comm.py -k offline``
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Callable

import pytest

from fmodmaster.modbus_comm import ModbusComm, strip_ip


class FakePage:
    """Test double for a Flet ``Page``.

    ``run_thread`` runs the handler synchronously in the current thread by
    default (so one-shot transactions are deterministic). Pass
    ``threaded=True`` to run handlers in a daemon thread (used by the scan
    test so the loop does not block). ``run_task`` runs the coroutine
    synchronously via a fresh event loop.
    """

    def __init__(self, threaded: bool = False) -> None:
        self.threaded = threaded
        self.threads: list[threading.Thread] = []

    def run_thread(self, handler: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        if self.threaded:
            t = threading.Thread(
                target=handler, args=args, kwargs=kwargs, daemon=True
            )
            self.threads.append(t)
            t.start()
        else:
            handler(*args, **kwargs)

    def run_task(self, handler: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        coro = handler(*args, **kwargs)
        # Drive the coroutine to completion on a private event loop.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


# --------------------------------------------------------------------------- #
# strip_ip
# --------------------------------------------------------------------------- #


class TestOfflineStripIp:
    """``strip_ip`` mirrors ``ModbusAdapter::stripIP``."""

    def test_strips_leading_zeros(self) -> None:
        assert strip_ip("127.000.000.001") == "127.0.0.1"

    def test_already_clean(self) -> None:
        assert strip_ip("192.168.1.10") == "192.168.1.10"

    def test_all_zeros(self) -> None:
        assert strip_ip("000.000.000.000") == "0.0.0.0"

    def test_wrong_octet_count_returns_empty(self) -> None:
        assert strip_ip("1.2.3") == ""
        assert strip_ip("1.2.3.4.5") == ""

    def test_empty_input(self) -> None:
        assert strip_ip("") == ""

    def test_non_numeric_octet_returns_empty(self) -> None:
        assert strip_ip("a.b.c.d") == ""


# --------------------------------------------------------------------------- #
# connect_tcp validation
# --------------------------------------------------------------------------- #


class TestOfflineConnectTcpValidation:
    """``connect_tcp`` rejects bad IP/port with a friendly ``ValueError``."""

    def test_blank_ip_raises(self) -> None:
        comm = ModbusComm(page=FakePage())
        with pytest.raises(ValueError, match="blank or invalid IP"):
            comm.connect_tcp("", 502, timeout=1000)

    def test_wrong_octet_count_raises(self) -> None:
        comm = ModbusComm(page=FakePage())
        with pytest.raises(ValueError, match="invalid IP address"):
            comm.connect_tcp("1.2.3", 502, timeout=1000)

    def test_octet_above_255_raises(self) -> None:
        comm = ModbusComm(page=FakePage())
        with pytest.raises(ValueError, match="invalid IP address"):
            comm.connect_tcp("127.0.0.256", 502, timeout=1000)

    def test_port_out_of_range_raises(self) -> None:
        comm = ModbusComm(page=FakePage())
        with pytest.raises(ValueError, match="out of range"):
            comm.connect_tcp("127.0.0.1", 0, timeout=1000)
        with pytest.raises(ValueError, match="out of range"):
            comm.connect_tcp("127.0.0.1", 70000, timeout=1000)

    def test_port_non_numeric_raises(self) -> None:
        comm = ModbusComm(page=FakePage())
        with pytest.raises(ValueError, match="port must be a number"):
            comm.connect_tcp("127.0.0.1", "abc", timeout=1000)

    def test_strips_then_validates(self) -> None:
        """Zero-padded IP passes validation (stripped before octet check)."""
        comm = ModbusComm(page=FakePage())
        # Will fail at connect (no server) but should NOT raise ValueError.
        ok = comm.connect_tcp("127.000.000.001", 50201, timeout=1000)
        assert ok is False
        assert comm.connected is False


# --------------------------------------------------------------------------- #
# transaction() no-op without client
# --------------------------------------------------------------------------- #


class TestOfflineTransactionNoClient:
    """``transaction()`` with no client set is a no-op, not a crash."""

    def test_no_client_no_crash(self) -> None:
        comm = ModbusComm(page=FakePage())
        comm.function_code = 0x01
        comm.start_addr = 0
        comm.num_items = 5
        # Should not raise and should not change counters.
        comm.transaction()
        assert comm.packets == 0
        assert comm.errors == 0
        assert comm.values == []

    def test_no_page_no_client_no_crash(self) -> None:
        """Without a page, transaction still runs sync and is a no-op."""
        comm = ModbusComm()
        comm.function_code = 0x03
        comm.transaction()
        assert comm.packets == 0
        assert comm.errors == 0

    def test_unknown_function_code_no_op(self) -> None:
        """An unsupported FC is a no-op even with a client set."""
        comm = ModbusComm(page=FakePage())
        # Inject a dummy non-None client so the guard passes, then rely on
        # the FC switch default branch.
        comm._client = object()  # type: ignore[assignment]
        comm.connected = True
        comm.function_code = 0x99  # unsupported
        comm.transaction()
        assert comm.packets == 0
        assert comm.errors == 0


# --------------------------------------------------------------------------- #
# scan toggle
# --------------------------------------------------------------------------- #


class TestOfflineScanToggle:
    """``start_scan``/``stop_scan`` toggle ``scan_running`` and the loop calls
    ``transaction()`` each interval, stopping on ``stop_scan``."""

    def test_start_stop_toggles_flag(self) -> None:
        comm = ModbusComm(page=FakePage(threaded=True))
        comm.scan_rate = 20  # 20 ms interval
        assert comm.scan_running is False
        comm.start_scan()
        assert comm.scan_running is True
        comm.stop_scan()
        assert comm.scan_running is False

    def test_scan_loop_calls_transaction_and_stops(self) -> None:
        """The scan loop should call ``_do_transaction`` repeatedly and stop
        promptly on ``stop_scan``."""
        comm = ModbusComm(page=FakePage(threaded=True))
        comm.scan_rate = 15
        calls = {"n": 0}
        orig = comm._do_transaction

        def counting_do() -> None:
            calls["n"] += 1
            # Don't actually run pymodbus; just count.

        comm._do_transaction = counting_do  # type: ignore[assignment]
        comm.start_scan()
        # Let it tick a few times.
        time.sleep(0.1)
        assert comm.scan_running is True
        assert calls["n"] >= 2
        comm.stop_scan()
        # After stop, the flag is clear and the loop exits.
        assert comm.scan_running is False
        snapshot = calls["n"]
        time.sleep(0.05)
        # No more calls after stop (allow a single in-flight tail).
        assert calls["n"] - snapshot <= 1
        # Restore to keep the object tidy.
        comm._do_transaction = orig  # type: ignore[assignment]

    def test_start_scan_idempotent(self) -> None:
        comm = ModbusComm(page=FakePage(threaded=True))
        comm.scan_rate = 50
        comm.start_scan()
        first_thread = comm._scan_thread
        comm.start_scan()  # second call should not spawn a second loop
        assert comm._scan_thread is first_thread
        comm.stop_scan()

    def test_scan_loop_no_client_still_runs(self) -> None:
        """With no client, ``_do_transaction`` is a no-op but the loop keeps
        ticking until ``stop_scan``."""
        comm = ModbusComm(page=FakePage(threaded=True))
        comm.scan_rate = 10
        comm.start_scan()
        time.sleep(0.06)
        assert comm.scan_running is True
        # No counters bumped because there is no client.
        assert comm.packets == 0
        assert comm.errors == 0
        comm.stop_scan()
        assert comm.scan_running is False


# --------------------------------------------------------------------------- #
# refresh marshalling
# --------------------------------------------------------------------------- #


class TestOfflineRefreshMarshalling:
    """The refresh callback is invoked (sync or via run_task) after a
    transaction, and ``page.update()`` is never called inside the worker."""

    def test_refresh_called_via_run_task(self) -> None:
        refreshed = {"n": 0}

        def cb() -> None:
            refreshed["n"] += 1

        page = FakePage()
        comm = ModbusComm(page=page, refresh_cb=cb)
        # No client -> _do_transaction returns early, but it still calls
        # _emit_refresh only on success/error paths. With no client it returns
        # before any refresh, so we exercise the error path instead.
        comm._client = object()  # type: ignore[assignment]
        comm.connected = True
        comm.function_code = 0x01
        # The dummy client has no read_coils -> exception -> _handle_error ->
        # _emit_refresh.
        comm.transaction()
        assert refreshed["n"] == 1
        assert comm.errors == 1

    def test_no_page_refresh_sync(self) -> None:
        refreshed = {"n": 0}

        def cb() -> None:
            refreshed["n"] += 1

        comm = ModbusComm(refresh_cb=cb)
        comm._handle_error("synthetic")
        assert refreshed["n"] == 1
        assert comm.errors == 1


# --------------------------------------------------------------------------- #
# timeout coercion
# --------------------------------------------------------------------------- #


class TestOfflineTimeoutCoercion:
    """Timeout is coerced to >= 1.0s (QMODMASTER.md §10.2 correction)."""

    def test_zero_timeout_becomes_one_second(self) -> None:
        comm = ModbusComm(page=FakePage())
        # connect_tcp validates first; use a valid IP/port that will fail to
        # connect, but the timeout is set before the connect attempt.
        comm.connect_tcp("127.0.0.1", 50201, timeout=0)
        assert comm.timeout == 1.0

    def test_string_timeout_coerced(self) -> None:
        comm = ModbusComm(page=FakePage())
        comm.connect_tcp("127.0.0.1", 50201, timeout="2000")
        assert comm.timeout == 2.0

    def test_small_timeout_clamped(self) -> None:
        comm = ModbusComm(page=FakePage())
        comm.connect_tcp("127.0.0.1", 50201, timeout=500)
        assert comm.timeout == 1.0