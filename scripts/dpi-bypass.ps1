#Requires -Version 5.1
# Recommended: PowerShell 7+ (winget install Microsoft.PowerShell)
param(
    [switch]$Stop,
    [switch]$Check
)

$ErrorActionPreference = "Stop"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $shell  = (Get-Process -Id $PID).Path
    $psArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($Stop)  { $psArgs += "-Stop" }
    if ($Check) { $psArgs += "-Check" }
    $psArgs += $args

    # Prefer Windows Terminal — wt.exe may not be in elevated PATH, check extra locations
    $wtExe = $null
    $wtInPath = Get-Command wt.exe -ErrorAction SilentlyContinue
    foreach ($candidate in @(
        $(if ($wtInPath) { $wtInPath.Source } else { $null }),
        "$env:LOCALAPPDATA\Microsoft\WindowsApps\wt.exe",
        "$env:ProgramFiles\WindowsApps\Microsoft.WindowsTerminal_*\wt.exe"
    )) {
        if ($candidate) {
            $resolved = Get-Item $candidate -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($resolved) { $wtExe = $resolved.FullName; break }
        }
    }
    if ($wtExe) {
        Start-Process $wtExe -ArgumentList (@($shell) + $psArgs) -Verb RunAs
    } else {
        Start-Process $shell -ArgumentList $psArgs -Verb RunAs
    }
    exit
}

# scripts/dpi-bypass.ps1 → project root (pyproject.toml, main.py)
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

function Stop-DllHolders {
    foreach ($dll in @("WinDivert64.dll", "WinDivert.dll")) {
        $raw = & cmd.exe /c "tasklist /M $dll /FO CSV /NH" 2>$null
        if (-not $raw) { continue }
        foreach ($line in $raw) {
            if ($line -match '^"([^"]+)","(\d+)"' -and [int]$Matches[2] -ne $PID) {
                & taskkill.exe /F /T /PID $Matches[2] 2>$null | Out-Null
            }
        }
    }
    Start-Sleep -Milliseconds 500
}

function Stop-WinDivertDriver {
    # WinDivert kernel driver stays registered even after python exits.
    # Stop it so DLL files (inside .venv) are no longer locked.
    foreach ($svc in @("WinDivert", "WinDivert14")) {
        $s = Get-Service -Name $svc -ErrorAction SilentlyContinue
        if ($s -and $s.Status -ne "Stopped") {
            & sc.exe stop $svc 2>$null | Out-Null
            Start-Sleep -Milliseconds 400
        }
    }
}

function Stop-ProjectProcesses {
    # Match uv/console-script launches: main.py, dpi-bypass, dpi_bypass, ...
    $pattern = "main\.py|dpi[-_]bypass"
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessId -ne $PID -and
            $(if ($_.Name) { $_.Name } else { "" }) -in @("python.exe", "pythonw.exe") -and
            $(if ($_.CommandLine) { $_.CommandLine } else { "" }) -match $pattern
        } |
        ForEach-Object {
            & taskkill.exe /F /T /PID $_.ProcessId 2>$null | Out-Null
        }
    # Also kill the console-script shim if present (dpi-bypass.exe)
    Get-Process -Name "dpi-bypass" -ErrorAction SilentlyContinue |
        Where-Object { $_.Id -ne $PID } |
        ForEach-Object {
            & taskkill.exe /F /T /PID $_.Id 2>$null | Out-Null
        }
    Start-Sleep -Milliseconds 300
}

if ($Check) {
    Write-Host "[*] Quality check" -ForegroundColor Cyan
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv required" }
    uv sync --group dev 2>$null; if ($LASTEXITCODE -ne 0) { uv sync }
    uv run ruff check .; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    uv tool run pyright .; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    uv run python -m pytest tests/ -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "[+] All checks passed" -ForegroundColor Green
    exit 0
}

if ($Stop) {
    Write-Host "[*] Stopping DPI bypass..." -ForegroundColor Cyan
    Stop-ProjectProcesses
    Stop-DllHolders
    Stop-WinDivertDriver
    Write-Host "[+] Done — file locks released" -ForegroundColor Green
    exit 0
}

Stop-ProjectProcesses
Stop-DllHolders

# Always disconnect when this host process ends (q / Ctrl+C / window close race /
# crash). Connect is temporary; normal internet must be restored on exit.
function Disconnect-DpiBypass {
    Write-Host "[*] Disconnecting — releasing WinDivert..." -ForegroundColor Cyan
    Stop-ProjectProcesses
    Stop-DllHolders
    Stop-WinDivertDriver
}

$exitCode = 0
try {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv not found — https://docs.astral.sh/uv/"
    }
    uv sync
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

    $Host.UI.RawUI.WindowTitle = "DPI Fragment Bypass"
    & uv run dpi-bypass
    $exitCode = $LASTEXITCODE
}
catch {
    $exitCode = 1
    Write-Host "[!] $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    # Runs on normal exit, Ctrl+C (PipelineStopped), and most terminating errors.
    # Console X-button is primarily handled inside Python (SetConsoleCtrlHandler);
    # this is the PS-side safety net if python dies uncleanly.
    Disconnect-DpiBypass
    if ($exitCode -ne 0) { Read-Host "Press Enter to close" }
}
exit $exitCode
