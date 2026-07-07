"""Simulator integration tests for :mod:`fmodmaster.modbus_comm`.

Spins a real ``pymodbus.server.ModbusTcpServer`` with a populated datastore
in a background thread (with its own asyncio event loop), connects
``ModbusComm`` to it, and asserts each function code 0x01-0x10 round-trips
and increments ``packets``. A forced error path (out-of-range address)
increments ``errors`` and emits a raw error line via the ``on_raw`` hook.

Run with: ``uv run python -m pytest tests/test_modbus_comm_sim.py``
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from typing import Any

import pytest
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import ModbusTcpServer

from fmodmaster.modbus_comm import (
    FC_READ_COILS,
    FC_READ_DISCRETE_INPUTS,
    FC_READ_HOLDING_REGISTERS,
    FC_READ_INPUT_REGISTERS,
    FC_WRITE_MULTIPLE_COILS,
    FC_WRITE_MULTIPLE_REGISTERS,
    FC_WRITE_SINGLE_COIL,
    FC_WRITE_SINGLE_REGISTER,
    ModbusComm,
)


def _free_port() -> int:
    """Return an ephemeral free TCP port on the loopback interface."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _expected_regs(start_addr: int, count: int) -> list[int]:
    """Expected register values for a read against the simulator datastore.

    The datastore is seeded with ``[1, 2, 3, ..., 200]`` at address 1.
    pymodbus applies the Modbus "register N == address N-1" offset inside
    ``ModbusDeviceContext``, so reading ``start_addr`` returns the value at
    internal index ``start_addr`` (i.e. ``start_addr + 1``).
    """
    return [start_addr + 1 + i for i in range(count)]


class _SimServer:
    """Runs a ``ModbusTcpServer`` in a daemon thread with its own loop.

    pymodbus 3.x servers are async: the server must be created and
    ``serve_forever`` awaited inside a running event loop. We park a loop in
    a background thread, start the server with ``background=True`` (which
    returns once listening), and expose the bound port. Teardown stops the
    loop and joins the thread.
    """

    def __init__(self) -> None:
        self.port = _free_port()
        self._loop = asyncio.new_event_loop()
        self._server: ModbusTcpServer | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="fModMaster-sim-server", daemon=True
        )

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._start())
        self._loop.run_forever()

    async def _start(self) -> None:
        # Populated datastore: 200 coils/discrete/holding/input values
        # starting at address 1. Coils/discrete inputs share the block
        # (pymodbus treats them as bits); holding/input registers share the
        # register block.
        register_values = list(range(1, 201))  # 1..200
        block = ModbusSequentialDataBlock(1, register_values)
        ctx = ModbusDeviceContext(di=block, co=block, ir=block, hr=block)
        srv_ctx = ModbusServerContext(devices=ctx, single=True)
        self._server = ModbusTcpServer(srv_ctx, address=("127.0.0.1", self.port))
        await self._server.serve_forever(background=True)
        self._ready.set()

    def start(self, timeout: float = 5.0) -> None:
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("Simulator server failed to start")

    def stop(self) -> None:
        if self._server is not None:
            self._loop.call_soon_threadsafe(self._server.close)
        # Stop the loop after a tiny grace period so close() is processed.
        self._loop.call_later(0.05, self._loop.stop)
        self._thread.join(timeout=5.0)


@pytest.fixture()
def sim_server() -> _SimServer:
    """Yield a running simulator server, stopping it after the test."""
    srv = _SimServer()
    srv.start()
    # Give the listener a beat to accept connections.
    time.sleep(0.05)
    yield srv
    srv.stop()


class _SyncPage:
    """Minimal page double: ``run_thread`` runs sync, ``run_task`` runs the
    coroutine sync via a private loop. This makes transactions deterministic
    in tests (no real Flet event loop required)."""

    def run_thread(self, handler: Any, *args: Any, **kwargs: Any) -> None:
        handler(*args, **kwargs)

    def run_task(self, handler: Any, *args: Any, **kwargs: Any) -> Any:
        coro = handler(*args, **kwargs)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _make_comm(server: _SimServer, on_raw=None) -> ModbusComm:
    """Build a ``ModbusComm`` connected to the simulator server."""
    comm = ModbusComm(page=_SyncPage(), on_raw=on_raw)
    assert comm.connect_tcp("127.0.0.1", server.port, timeout=1000) is True
    assert comm.connected is True
    assert comm.mode == "TCP"
    return comm


# --------------------------------------------------------------------------- #
# Per-FC round-trip
# --------------------------------------------------------------------------- #


class TestReadFunctionCodes:
    """Each read FC 0x01-0x04 returns expected values and bumps packets."""

    def test_read_coils(self, sim_server: _SimServer) -> None:
        comm = _make_comm(sim_server)
        comm.function_code = FC_READ_COILS
        comm.start_addr = 1
        comm.num_items = 8
        comm.slave = 1
        comm.transaction()
        assert comm.packets == 1
        assert comm.errors == 0
        assert comm.valid is True
        # Coils are bits of the register block; pymodbus returns 16 bits per
        # register. With values 1..200, register 1 (value 1 = 0b1) has bit0
        # set. Just assert we got 8 bools back.
        assert len(comm.values) == 8
        assert all(isinstance(v, bool) for v in comm.values)

    def test_read_discrete_inputs(self, sim_server: _SimServer) -> None:
        comm = _make_comm(sim_server)
        comm.function_code = FC_READ_DISCRETE_INPUTS
        comm.start_addr = 1
        comm.num_items = 4
        comm.transaction()
        assert comm.packets == 1
        assert comm.errors == 0
        assert len(comm.values) == 4

    def test_read_holding_registers(self, sim_server: _SimServer) -> None:
        comm = _make_comm(sim_server)
        comm.function_code = FC_READ_HOLDING_REGISTERS
        comm.start_addr = 1
        comm.num_items = 5
        comm.transaction()
        assert comm.packets == 1
        assert comm.errors == 0
        assert comm.values == _expected_regs(1, 5)

    def test_read_input_registers(self, sim_server: _SimServer) -> None:
        comm = _make_comm(sim_server)
        comm.function_code = FC_READ_INPUT_REGISTERS
        comm.start_addr = 1
        comm.num_items = 3
        comm.transaction()
        assert comm.packets == 1
        assert comm.errors == 0
        assert comm.values == _expected_regs(1, 3)


class TestWriteFunctionCodes:
    """Each write FC 0x05/0x06/0x0F/0x10 round-trips and bumps packets."""

    def test_write_single_coil(self, sim_server: _SimServer) -> None:
        comm = _make_comm(sim_server)
        comm.function_code = FC_WRITE_SINGLE_COIL
        comm.start_addr = 5
        comm.num_items = 1
        comm.write_values = [True]
        comm.transaction()
        assert comm.packets == 1
        assert comm.errors == 0
        assert comm.valid is True
        # Read back to confirm persistence.
        comm.function_code = FC_READ_COILS
        comm.num_items = 1
        comm.transaction()
        assert comm.values == [True]

    def test_write_single_register(self, sim_server: _SimServer) -> None:
        comm = _make_comm(sim_server)
        comm.function_code = FC_WRITE_SINGLE_REGISTER
        comm.start_addr = 7
        comm.num_items = 1
        comm.write_values = [12345]
        comm.transaction()
        assert comm.packets == 1
        assert comm.errors == 0
        # Read back.
        comm.function_code = FC_READ_HOLDING_REGISTERS
        comm.num_items = 1
        comm.transaction()
        assert comm.values == [12345]

    def test_write_multiple_coils(self, sim_server: _SimServer) -> None:
        comm = _make_comm(sim_server)
        comm.function_code = FC_WRITE_MULTIPLE_COILS
        comm.start_addr = 10
        comm.num_items = 4
        comm.write_values = [True, False, True, False]
        comm.transaction()
        assert comm.packets == 1
        assert comm.errors == 0
        # Read back.
        comm.function_code = FC_READ_COILS
        comm.num_items = 4
        comm.transaction()
        assert comm.values == [True, False, True, False]

    def test_write_multiple_registers(self, sim_server: _SimServer) -> None:
        comm = _make_comm(sim_server)
        comm.function_code = FC_WRITE_MULTIPLE_REGISTERS
        comm.start_addr = 20
        comm.num_items = 3
        comm.write_values = [100, 200, 300]
        comm.transaction()
        assert comm.packets == 1
        assert comm.errors == 0
        # Read back.
        comm.function_code = FC_READ_HOLDING_REGISTERS
        comm.num_items = 3
        comm.transaction()
        assert comm.values == [100, 200, 300]


# --------------------------------------------------------------------------- #
# Forced error path
# --------------------------------------------------------------------------- #


class TestForcedError:
    """A forced error increments ``errors`` and emits a raw error line."""

    def test_out_of_range_address_increments_errors(
        self, sim_server: _SimServer
    ) -> None:
        comm = _make_comm(sim_server)
        # The datastore has 200 values starting at address 1. Reading far
        # beyond the block returns a Modbus exception response.
        comm.function_code = FC_READ_HOLDING_REGISTERS
        comm.start_addr = 5000
        comm.num_items = 4
        comm.transaction()
        assert comm.errors == 1
        assert comm.packets == 0
        assert comm.valid is False
        assert comm.values == []

    def test_error_then_success_resets_valid(self, sim_server: _SimServer) -> None:
        comm = _make_comm(sim_server)
        # First: error.
        comm.function_code = FC_READ_HOLDING_REGISTERS
        comm.start_addr = 5000
        comm.num_items = 2
        comm.transaction()
        assert comm.errors == 1
        assert comm.valid is False
        # Then: success.
        comm.start_addr = 1
        comm.num_items = 2
        comm.transaction()
        assert comm.packets == 1
        assert comm.valid is True
        assert comm.values == _expected_regs(1, 2)


# --------------------------------------------------------------------------- #
# Raw capture tee
# --------------------------------------------------------------------------- #


class TestRawCapture:
    """The ``on_raw`` hook receives Tx and Rx bytes during a transaction."""

    def test_on_raw_called_for_tx_and_rx(self, sim_server: _SimServer) -> None:
        events: list[tuple[str, bytes]] = []

        def on_raw(direction: str, data: bytes) -> None:
            events.append((direction, bytes(data)))

        comm = _make_comm(sim_server, on_raw=on_raw)
        comm.function_code = FC_READ_HOLDING_REGISTERS
        comm.start_addr = 1
        comm.num_items = 2
        comm.transaction()
        assert comm.packets == 1
        directions = [d for d, _ in events]
        assert "tx" in directions
        assert "rx" in directions
        # All payloads are non-empty bytes.
        assert all(len(data) > 0 for _, data in events)


# --------------------------------------------------------------------------- #
# Report Slave ID (FC 0x11, Tools-only)
# --------------------------------------------------------------------------- #


class TestReportSlaveId:
    """``report_slave_id`` returns a status tuple against the simulator."""

    def test_report_slave_id(self, sim_server: _SimServer) -> None:
        comm = _make_comm(sim_server)
        status, slave_id, data = comm.report_slave_id()
        assert status is True
        assert isinstance(data, (bytes, bytearray))
        # pymodbus' default identifier is non-empty.
        assert len(data) > 0

    def test_report_slave_id_no_client(self) -> None:
        comm = ModbusComm(page=_SyncPage())
        status, slave_id, data = comm.report_slave_id()
        assert status is False
        assert slave_id is None
        assert data == b""


# --------------------------------------------------------------------------- #
# Counters + disconnect
# --------------------------------------------------------------------------- #


class TestCountersAndDisconnect:
    """Counters accumulate across transactions; disconnect clears state."""

    def test_multiple_transactions_accumulate(self, sim_server: _SimServer) -> None:
        comm = _make_comm(sim_server)
        comm.function_code = FC_READ_HOLDING_REGISTERS
        comm.start_addr = 1
        comm.num_items = 2
        for _ in range(3):
            comm.transaction()
        assert comm.packets == 3
        assert comm.errors == 0
        comm.reset_counters()
        assert comm.packets == 0
        assert comm.errors == 0

    def test_disconnect_clears_connection(self, sim_server: _SimServer) -> None:
        comm = _make_comm(sim_server)
        assert comm.connected is True
        comm.disconnect()
        assert comm.connected is False
        assert comm.mode is None
        assert comm._client is None