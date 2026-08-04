# Adds EyeRest to the current user's Windows Startup folder.
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Error "Python was not found on PATH. Install Python or add it to PATH, then retry."
}
$python = $pythonCmd.Source

$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "EyeRest.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $python
$shortcut.Arguments = "`"$scriptDir\eyerest.py`" --no-demo"
$shortcut.WorkingDirectory = $scriptDir
$shortcut.WindowStyle = 7  # Minimized
$shortcut.Description = "EyeRest - screen blink reminder every 25 minutes"
$shortcut.Save()

Write-Host "EyeRest will start minimized at login."
Write-Host "Shortcut: $shortcutPath"
Write-Host ""
Write-Host "To remove it later, delete that shortcut or run uninstall-startup.ps1"
