#Requires -Version 5.1
<#
.SYNOPSIS
    Kill DPI bypass processes, stop WinDivert service, clean build cache.
.DESCRIPTION
    python kill -> WinDivert kernel driver stop -> delete __pycache__ / .pytest_cache / .ruff_cache
    If ps1 execution policy blocks the main script, run:
        powershell -ExecutionPolicy Bypass -File .\scripts\stop.ps1
#>
param([switch]$CacheOnly)

# ── admin elevation ──
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($CacheOnly) { $argList += "-CacheOnly" }
    Start-Process (Get-Process -Id $PID).Path -ArgumentList $argList -Verb RunAs
    exit
}

$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

if (-not $CacheOnly) {
    Write-Host "[*] Killing processes..." -ForegroundColor Cyan

    foreach ($name in @("python", "pythonw")) {
        Get-Process -Name $name -ErrorAction SilentlyContinue |
            Stop-Process -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 400

    foreach ($dll in @("WinDivert64.dll", "WinDivert.dll")) {
        $raw = & cmd.exe /c "tasklist /M $dll /FO CSV /NH" 2>$null
        if (-not $raw) { continue }
        foreach ($line in $raw) {
            if ($line -match '^"[^"]+","(\d+)"') {
                & taskkill.exe /F /T /PID $Matches[1] 2>$null | Out-Null
            }
        }
    }
    Start-Sleep -Milliseconds 300

    Write-Host "[*] Stopping WinDivert driver..." -ForegroundColor Cyan
    foreach ($svc in @("WinDivert", "WinDivert14")) {
        $s = Get-Service -Name $svc -ErrorAction SilentlyContinue
        if ($s -and $s.Status -ne "Stopped") {
            & sc.exe stop $svc 2>$null | Out-Null
            Start-Sleep -Milliseconds 400
        }
    }
}

Write-Host "[*] Cleaning cache..." -ForegroundColor Cyan
foreach ($name in @("__pycache__", ".pytest_cache", ".ruff_cache")) {
    Get-ChildItem -Path $root -Filter $name -Recurse -Directory -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "[+] Done" -ForegroundColor Green
