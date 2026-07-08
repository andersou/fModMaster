# fModMaster

fModMaster is a desktop Modbus master application built with [Flet](https://flet.dev), [pymodbus](https://github.com/pymodbus-dev/pymodbus), and [pyserial](https://pyserial.readthedocs.io/). It recreates the core [qModMaster](https://github.com/epsilonrt/qmodmaster) workflow in Python, providing an intuitive GUI for RTU/TCP connection setup, register table reads and writes, scan mode, bus monitoring, settings/session persistence, and diagnostic tools.

## Features

- **Dual Mode Support**: Connect via Modbus RTU (serial) or Modbus TCP.
- **Function Codes**: Read/Write Coils, Discrete Inputs, Holding Registers, and Input Registers (FC 01–06, 0F, 10).
- **Scan Mode**: Continuous polling with configurable scan rate.
- **Bus Monitor**: Real-time raw Tx/Rx frame inspection.
- **Session Management**: Save and load complete session configurations (`.ses` files).
- **Settings Persistence**: INI-based settings compatible with qModMaster defaults.
- **Cross-Platform**: Runs on Windows, macOS, and Linux (desktop builds via Flet).

## Requirements

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) (recommended for dependency management and running)
- For desktop packaging: platform-specific toolchains (e.g., Xcode on macOS for `flet build macos`)

## Installation

Clone the repository and install dependencies:

```bash
uv sync
```

## Running the Desktop App

Launch the application using the source entry point:

```bash
uv run flet run src/fmodmaster/main.py
```

> Note: `uv run flet run` alone is not sufficient because this repository uses a `src/` layout without a root-level `main.py`.

On startup, the app reads `fModMaster.ini` from the current working directory. If the file is missing or corrupt, it falls back to qModMaster-compatible defaults and continues launching. Logs are written to `fModMaster.log` in the current working directory and to stderr.

## Project Structure

```
fModMaster/
├── src/fmodmaster/        # Application source code
│   ├── main.py            # Entry point
│   ├── app.py             # App orchestration and startup
│   ├── main_view.py       # Main window UI and state machine
│   ├── modbus_comm.py     # Modbus RTU/TCP communication layer
│   ├── registers.py       # Register table/grid rendering
│   ├── bus_monitor.py     # Bus monitor dialog/controller
│   ├── tools_view.py      # Tools dialog/controller
│   ├── config.py          # Settings/session persistence
│   └── logging_helper.py  # Structured logging setup
├── tests/                 # Test suite
├── docs/                  # Documentation and manuals
├── fModMaster.ini         # Default runtime settings
└── pyproject.toml         # Project metadata and dependencies
```

## Development

Run the full test suite:

```bash
uv run python -m pytest
```

Run the static type checker:

```bash
uv run rtk mypy src/fmodmaster
```

## Building

Desktop packaging metadata is defined in `[tool.flet]` inside `pyproject.toml` (`name = "fModMaster"`, `version = "0.1.0"`, `desktop_flavor = "full"`).

To build for macOS:

```bash
uv run flet build macos src/fmodmaster --yes
```

> RTU mode requires serial-port access at runtime. Packaged builds should be smoke-tested on the target OS with an actual or simulated serial device before release.

## Contributing

Contributions are welcome! Please ensure your changes pass the test suite and type checks before submitting a pull request.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Acknowledgments

Inspired by [qModMaster](https://github.com/epsilonrt/qmodmaster) and the underlying [libmodbus](https://libmodbus.org/) / [pymodbus](https://github.com/pymodbus-dev/pymodbus) ecosystems.
