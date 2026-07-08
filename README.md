# fModMaster

fModMaster is a desktop Modbus master application built with
[Flet](https://flet.dev), [pymodbus](https://github.com/pymodbus-dev/pymodbus),
and [pyserial](https://pyserial.readthedocs.io/). It recreates the core
qModMaster workflow in Python: RTU/TCP connection setup, register table reads
and writes, scan mode, bus monitoring, settings/session persistence, and Tools
diagnostics.

## Run the desktop app

Install dependencies once:

```bash
uv sync
```

Launch the app with the source entry point:

```bash
uv run flet run src/fmodmaster/main.py
```

`uv run flet run` by itself is not enough because this repository does not have
a root-level `main.py`.

On startup the app reads `fModMaster.ini` from the current working directory. A
missing or corrupt INI file falls back to qModMaster-compatible defaults and the
app continues launching. The log is written to `fModMaster.log` in the current
working directory and to stderr.

## Development checks

Run the full test suite:

```bash
uv run python -m pytest
```

Run the accepted project type check:

```bash
uv run rtk mypy src/fmodmaster
```

## Build readiness

Desktop packaging metadata lives in `[tool.flet]` in `pyproject.toml`:
`name = "fModMaster"`, `version = "0.1.0"`, and `desktop_flavor = "full"`.

Flet 0.85 requires a target platform. From this source-layout repository,
attempt a macOS desktop build with:

```bash
uv run flet build
uv run flet build macos src/fmodmaster --yes
```

The first command is the generic build entry point and prints the target-platform
usage if no target is supplied. The second command points Flet at the app
directory containing `main.py`.

Flet desktop builds may need external platform toolchains. On macOS, the bundle
step requires a complete Xcode installation with `xcodebuild` available. RTU
mode also depends on serial-port access at runtime, so packaged builds should be
smoke-tested on the target OS with an actual or simulated serial device before
release.
