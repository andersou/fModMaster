"""Tests for ``fmodmaster.config.Settings``."""

from __future__ import annotations

import os

import pytest

from fmodmaster.config import Settings


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def ini_path(tmp_path, monkeypatch):
    """Point ``fModMaster.ini`` at a temp cwd so tests never touch the repo."""
    monkeypatch.chdir(tmp_path)
    return os.path.join(tmp_path, "fModMaster.ini")


@pytest.fixture
def ses_path(tmp_path):
    return os.path.join(tmp_path, "session.ses")


# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #


def test_defaults_match_cpp(ini_path):
    s = Settings()
    assert s.tcp_port == "502"
    assert s.slave_ip == "127.000.000.001"
    assert s.serial_port == "1"
    assert s.baud == "9600"
    assert s.data_bits == "8"
    assert s.stop_bits == "1"
    assert s.parity == "None"
    assert s.max_no_of_lines == "60"
    assert s.base_addr == "0"
    assert s.time_out == "0"
    assert s.logging_level == 3
    assert s.modbus_mode == 0
    assert s.slave_id == 1
    assert s.scan_rate == 1000
    assert s.function_code == 0
    assert s.start_addr == 0
    assert s.no_of_regs == 0
    assert s.base == 1
    assert s.default_base == 1
    assert s.register_formats == {}
    assert s.register_float_endians == {}
    # SerialDev / RTS / SerialPortName are platform-dependent.
    if os.name == "nt":
        assert s.serial_dev == "COM"
        assert s.rts == "Disable"
        assert s.serial_port_name == "COM1"
    else:
        assert s.serial_dev == "/dev/ttyS"
        assert s.rts == "None"
        assert s.serial_port_name == "/dev/ttyS0"


def test_defaults_round_trip(ini_path):
    s = Settings()
    s.save_settings(ini_path)
    loaded = Settings()
    loaded.load_settings(ini_path)
    assert loaded.tcp_port == s.tcp_port
    assert loaded.slave_ip == s.slave_ip
    assert loaded.serial_dev == s.serial_dev
    assert loaded.serial_port == s.serial_port
    assert loaded.serial_port_name == s.serial_port_name
    assert loaded.baud == s.baud
    assert loaded.data_bits == s.data_bits
    assert loaded.stop_bits == s.stop_bits
    assert loaded.parity == s.parity
    assert loaded.rts == s.rts
    assert loaded.max_no_of_lines == s.max_no_of_lines
    assert loaded.base_addr == s.base_addr
    assert loaded.time_out == s.time_out
    assert loaded.logging_level == s.logging_level
    assert loaded.modbus_mode == s.modbus_mode
    assert loaded.slave_id == s.slave_id
    assert loaded.scan_rate == s.scan_rate
    assert loaded.function_code == s.function_code
    assert loaded.start_addr == s.start_addr
    assert loaded.no_of_regs == s.no_of_regs
    assert loaded.base == s.base
    assert loaded.default_base == s.default_base
    assert loaded.register_formats == s.register_formats
    assert loaded.register_float_endians == s.register_float_endians


# --------------------------------------------------------------------------- #
# strip_ip
# --------------------------------------------------------------------------- #


def test_strip_ip_strips_leading_zeros():
    s = Settings()
    assert s.strip_ip("127.000.000.001") == "127.0.0.1"
    assert Settings.strip_ip("127.000.000.001") == "127.0.0.1"


def test_strip_ip_normal_ip():
    assert Settings.strip_ip("192.168.001.010") == "192.168.1.10"


def test_strip_ip_invalid_returns_empty():
    assert Settings.strip_ip("not-an-ip") == ""
    assert Settings.strip_ip("1.2.3") == ""
    assert Settings.strip_ip("1.2.3.4.5") == ""


# --------------------------------------------------------------------------- #
# timeout_seconds
# --------------------------------------------------------------------------- #


def test_timeout_seconds_zero():
    s = Settings()
    assert s.time_out == "0"
    assert s.timeout_seconds() == 0


def test_timeout_seconds_nonzero():
    s = Settings()
    s.time_out = "1500"
    assert s.timeout_seconds() == 1500


def test_timeout_seconds_garbage():
    s = Settings()
    s.time_out = "garbage"
    assert s.timeout_seconds() == 0


# --------------------------------------------------------------------------- #
# Session (.ses) round-trip
# --------------------------------------------------------------------------- #


def test_session_round_trip_identical_values(ses_path):
    s = Settings()
    s.tcp_port = "1502"
    s.slave_ip = "010.000.000.001"
    s.baud = "19200"
    s.logging_level = 1
    s.modbus_mode = 1  # TCP
    s.slave_id = 7
    s.scan_rate = 500
    s.function_code = 3
    s.start_addr = 100
    s.no_of_regs = 20
    s.base = 0  # Hex
    s.default_base = 0
    s.time_out = "2000"
    s.save_session(ses_path)

    loaded = Settings()
    loaded.load_session(ses_path)
    assert loaded.tcp_port == "1502"
    assert loaded.slave_ip == "010.000.000.001"
    assert loaded.baud == "19200"
    assert loaded.logging_level == 1
    assert loaded.modbus_mode == 1
    assert loaded.slave_id == 7
    assert loaded.scan_rate == 500
    assert loaded.function_code == 3
    assert loaded.start_addr == 100
    assert loaded.no_of_regs == 20
    assert loaded.base == 0
    assert loaded.default_base == 0
    assert loaded.time_out == "2000"


def test_register_format_maps_round_trip(ses_path):
    s = Settings()
    s.default_base = 10
    s.register_formats = {0: 3, 2: 2, 3: 16}
    s.register_float_endians = {0: 0, 4: 1}

    s.save_session(ses_path)

    loaded = Settings()
    loaded.load_session(ses_path)

    assert loaded.base == 10
    assert loaded.default_base == 10
    assert loaded.register_formats == {0: 3, 2: 2, 3: 16}
    assert loaded.register_float_endians == {0: 0, 4: 1}


def test_legacy_session_base_loads_default_base_when_default_base_missing(ses_path):
    with open(ses_path, "w", encoding="utf-8") as fh:
        fh.write("[Session]\nBase = 16\n")

    loaded = Settings()
    loaded.load_session(ses_path)

    assert loaded.base == 16
    assert loaded.default_base == 16


def test_invalid_register_format_entries_are_ignored(ses_path):
    with open(ses_path, "w", encoding="utf-8") as fh:
        fh.write(
            "[RegisterFormats]\n"
            "0 = 3\n"
            "bad-key = 2\n"
            "2 = not-int\n"
            "[RegisterFloatEndians]\n"
            "4 = 1\n"
            "x = y\n"
        )

    loaded = Settings()
    loaded.load_session(ses_path)

    assert loaded.register_formats == {0: 3}
    assert loaded.register_float_endians == {4: 1}


def test_session_load_missing_file_keeps_defaults(ses_path):
    # File does not exist — defaults must survive.
    s = Settings()
    s.load_session(ses_path)
    assert s.tcp_port == "502"
    assert s.slave_ip == "127.000.000.001"


# --------------------------------------------------------------------------- #
# Corrupt INI fallback
# --------------------------------------------------------------------------- #


def test_corrupt_ini_missing_sections_falls_back(ini_path):
    # File exists but has no recognised sections.
    with open(ini_path, "w", encoding="utf-8") as fh:
        fh.write("[Other]\nkey = value\n")
    s = Settings()
    s.load_settings(ini_path)
    # Defaults untouched.
    assert s.tcp_port == "502"
    assert s.slave_ip == "127.000.000.001"
    assert s.slave_id == 1


def test_corrupt_ini_garbage_falls_back(ini_path):
    with open(ini_path, "w", encoding="utf-8") as fh:
        fh.write("this is not\na valid ini file\n[[[\n")
    s = Settings()
    s.load_settings(ini_path)
    assert s.tcp_port == "502"
    assert s.slave_ip == "127.000.000.001"


def test_partial_ini_overlays_only_present_keys(ini_path):
    with open(ini_path, "w", encoding="utf-8") as fh:
        fh.write("[TCP]\nTCPPort = 9999\n")
    s = Settings()
    s.load_settings(ini_path)
    assert s.tcp_port == "9999"
    # SlaveIP absent -> default retained.
    assert s.slave_ip == "127.000.000.001"
    # Other sections absent -> defaults retained.
    assert s.slave_id == 1
    assert s.baud == "9600"


# --------------------------------------------------------------------------- #
# Acceptance one-liner from the task
# --------------------------------------------------------------------------- #


def test_acceptance_one_liner(ini_path):
    s = Settings()
    s.save_settings(ini_path)
    s2 = Settings()
    s2.load_settings(ini_path)
    assert s2.tcp_port == "502"
    assert s2.slave_ip == "127.000.000.001"
    assert s2.strip_ip("127.000.000.001") == "127.0.0.1"
