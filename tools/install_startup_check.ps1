# Adds (or with -Remove, removes) a sign-in check to the user's Startup folder:
# if Live Translator isn't running, put the real microphone and app volumes
# back. Covers a PC that was shut down while the app was running.
#   powershell -ExecutionPolicy Bypass -File tools\install_startup_check.ps1
#   powershell -ExecutionPolicy Bypass -File tools\install_startup_check.ps1 -Remove
param([switch]$Remove)

$root = Split-Path -Parent $PSScriptRoot
$link = Join-Path ([Environment]::GetFolderPath("Startup")) "Live Translator - mic check.lnk"

if ($Remove) {
    Remove-Item $link -ErrorAction SilentlyContinue
    "Removed: $link"
    return
}

$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($link)
$s.TargetPath = Join-Path $root ".venv\Scripts\pythonw.exe"
$s.Arguments = "-W ignore -m livetranslator.guard restore"
$s.WorkingDirectory = $root
$s.WindowStyle = 7  # minimized; pythonw has no window anyway
$s.Description = "Live Translator: restore the real microphone if the app isn't running"
$s.Save()
"Installed: $link"
