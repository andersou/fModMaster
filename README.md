# fModMaster

A desktop Modbus master application built with [Flet](https://flet.dev) and
[pymodbus](https://github.com/pymodbus-dev/pymodbus).

## Features (planned)

- Modbus RTU (serial) and TCP master communication
- Register map browsing and editing
- Live bus monitor
- Auxiliary tools view

## Development

This project uses [`uv`](https://docs.astral.sh/uv/) as the package manager.

```bash
uv sync            # install dependencies
uv run flet run    # launch the desktop app
uv run pytest      # run tests
uv run mypy src    # type-check
```
