# DPI Fragment Bypass

> Splits TLS ClientHello across multiple TCP segments to bypass SNI-based DPI blocking on Korean ISPs (KT · SKT · LGU+).

**[Project page →](https://savvy773.github.io/dpi-bye/)**  ·  License: [MIT](LICENSE)  ·  [Tech stack](docs/TECH_STACK.md)

Built with [WinDivert](https://github.com/basil00/WinDivert) + [pydivert](https://github.com/ffalcinelli/pydivert). No installer — portable Python tool.

---

## How It Works

ISP DPI reads the **SNI** (Server Name Indication) inside TLS `ClientHello` — a plaintext hostname sent before encryption starts. If the DPI sees a blocked domain, it drops the connection.

Most DPI engines inspect one TCP segment at a time and do not reassemble streams. This tool intercepts the outbound packet, fragments the `ClientHello` payload, and re-injects the pieces as separate segments. The server reassembles them normally; the DPI sees an incomplete SNI and passes the traffic through.

```
Browser ──► ClientHello (SNI = blocked.site)
                │
           WinDivert (this tool)
                │
         split into fragments
                │
         ┌──────┴──────┐
         ▼             ▼
    [seg A: no SNI]  [seg B: no TLS header]
         │             │
    ISP DPI: can't match SNI → pass through
         │             │
    Server: reassembles stream → TLS OK ✓
```

### Fragment modes

| Mode | How | When to use |
|------|-----|-------------|
| **packet_size** ★ | Split into N-byte chunks — more segments, stronger bypass | Default; works on most ISPs |
| **offset** | Parse SNI extension, split at hostname midpoint — 2 segments only | Lower CPU; try if packet_size is too slow |

> **VMware NAT note:** NAT reassembles TCP before forwarding, which defeats both modes. Use Bridged networking instead.

---

## Requirements

| | |
|--|--|
| OS | Windows 10 / 11 |
| Runtime | [uv](https://docs.astral.sh/uv/) (installs Python + dependencies automatically) |
| PowerShell | 5.1 (built-in) works · **7+ recommended** (`winget install Microsoft.PowerShell`) |
| Privileges | Administrator — WinDivert loads a kernel driver |

---

## Quick Start

```powershell
# Install uv if not already present
winget install astral-sh.uv

# Install dependencies (includes WinDivert DLL/SYS inside .venv)
uv sync

# Launch
.\dpi-bypass.ps1
```

Press `c` to connect, `q` to quit (WinDivert is released on exit — internet restores automatically).

> Do **not** copy `.venv/` between machines. Run `uv sync` on each new PC.

---

## TUI Controls

| Key | Action |
|-----|--------|
| `↑` `↓` | Navigate menu |
| `←` `→` | Cycle presets |
| `c` | Connect / Disconnect |
| `q` | Quit — releases WinDivert, restores normal internet |

---

## Settings

| Option | Default | Presets |
|--------|---------|---------|
| **Mode** | `packet_size` ★ | `packet_size` · `offset` |
| **Packet size** | `mid (10)` ★ | `5` aggressive · `10` recommended · `20` light |
| **Offset** | `small (4)` | `4` · `10` · `20` — fallback when SNI auto-detect fails |

★ Recommended. Settings save to `.data/user_settings.json` automatically and persist across sessions.

---

## Scripts

```powershell
.\dpi-bypass.ps1            # launch (auto-elevates to Administrator)
.\dpi-bypass.ps1 -Stop      # kill processes + stop WinDivert driver + unlock files
.\dpi-bypass.ps1 -Check     # run ruff + pyright + pytest

# Fallback if execution policy blocks the main script
powershell -ExecutionPolicy Bypass -File .\scripts\stop.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\stop.ps1 -CacheOnly
```

---

## Project Structure

```
dpi-bypass/
├── dpi-bypass.ps1       # entry point — elevate, sync, run
├── main.py              # TUI, settings, key handling
├── engine.py            # WinDivert engine, SNI parsing, fragmentation
├── scripts/
│   └── stop.ps1         # cleanup: processes · WinDivert service · cache
├── tests/
│   └── test_packet.py
├── pyproject.toml
└── uv.lock
```

`.venv/` and `.data/` are git-ignored and created locally.

---

## Troubleshooting

**Can't delete folder / WinDivert file locked**

```powershell
.\dpi-bypass.ps1 -Stop
```

A reboot also clears all WinDivert kernel state.

**Bypass not working**

1. Navigate to **Packet size** or **Offset** row (↑↓) to switch fragment mode
2. Try a smaller packet size preset (e.g. `5` aggressive)
3. Check for conflicting tools — GoodbyeDPI, SafeVisit, VPN clients using WinDivert cannot run simultaneously

**Already running error**

```powershell
.\dpi-bypass.ps1 -Stop
.\dpi-bypass.ps1
```

---

## Notes

- Intercepts outbound TCP 443 `ClientHello` only. All other traffic passes through unmodified.
- QUIC / HTTP3 (UDP 443) is not handled.
- WinDivert driver is loaded on Connect and unloaded on Disconnect/Quit — no permanent installation.
- For personal and educational use. Verify compliance with your ISP's terms of service.
