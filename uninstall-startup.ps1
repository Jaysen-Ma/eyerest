# Removes EyeRest from the current user's Windows Startup folder.
$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "EyeRest.lnk"

if (Test-Path $shortcutPath) {
    Remove-Item $shortcutPath -Force
    Write-Host "Removed EyeRest from startup."
} else {
    Write-Host "EyeRest was not in the Startup folder."
}
