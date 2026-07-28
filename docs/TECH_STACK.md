# Tech Stack

## Runtime

| Component | Choice | Notes |
|---|---|---|
| Language | Python 3.10+ (targets 3.14 in `.venv`) | see `pyproject.toml` |
| Package / env manager | [uv](https://docs.astral.sh/uv/) | installs Python itself, syncs `.venv`, no system Python required |
| Packet interception | [WinDivert](https://github.com/basil00/WinDivert) (kernel driver) | loaded on Connect, unloaded on Disconnect/Quit — no permanent install |
| Driver binding | [pydivert](https://github.com/ffalcinelli/pydivert) 3.x | ships the WinDivert DLL/SYS inside `.venv` |
| OS | Windows 10 / 11 only | relies on `msvcrt`, `SetConsoleCtrlHandler`, `IsUserAnAdmin` via `ctypes` |
| Entry point | `dpi-bypass.ps1` (PowerShell 5.1+, 7+ recommended) | self-elevates to Administrator, runs `uv sync` then `uv run dpi-bypass` |

## Application layers

- **`main.py`** — TUI: ANSI rendering, key handling (`msvcrt.getch` on Windows), settings persistence (`.data/user_settings.json`), Windows console-close handling (`SetConsoleCtrlHandler`) so the bypass disconnects even if the window is closed without pressing `q`.
- **`engine.py`** — `Engine`/`Settings` dataclasses; owns the WinDivert handle and the packet-processing thread. Detects TLS `ClientHello` (`0x16 ... 0x01`), fragments the payload (`packet_size` or `offset` mode), re-injects segments with adjusted TCP sequence numbers.
- **`dpi-bypass.ps1`** — Windows entry script: self-elevation, single-instance/driver cleanup (`Stop-DllHolders`, `Stop-WinDivertDriver`, `Stop-ProjectProcesses`), `uv sync`, launch, and a `-Check` mode for CI-style linting.
- **`scripts/stop.ps1`** — standalone fallback cleanup if the main script's execution policy is blocked.

## Dev tooling

| Tool | Purpose |
|---|---|
| [ruff](https://docs.astral.sh/ruff/) | lint + format (`E`, `F`, `I`, `UP` rule sets, line length 88) |
| [pyright](https://github.com/microsoft/pyright) | static type checking, targets the project `.venv` |
| [pytest](https://docs.pytest.org/) | unit tests (`tests/`) |

Run all three via `.\dpi-bypass.ps1 -Check`.

## Filter & protocol details

- WinDivert filter: `outbound and tcp.DstPort == 443 and tcp.PayloadLength > 0`
- Only intercepts plaintext TLS `ClientHello` records — all other TCP/443 traffic passes through unmodified.
- QUIC / HTTP3 (UDP 443) is out of scope.

## Distribution

- No installer, no bundled binaries in git — `WinDivert.dll`/`.sys` are fetched into `.venv` by `uv sync` from the `pydivert` wheel.
- `.venv/` and `.data/` are local-only and git-ignored; the repo ships source + `uv.lock` only.
