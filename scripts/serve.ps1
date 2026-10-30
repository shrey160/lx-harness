param(
    [ValidateRange(1,65535)][int]$Port = 8765,
    [string]$Distro = 'Ubuntu',
    [switch]$Wsl
)
$ErrorActionPreference = 'Stop'
$taskSource = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$nativePython = Join-Path $taskSource '.venv-win\Scripts\python.exe'
if (-not $Wsl -and (Test-Path $nativePython)) {
    & $nativePython -m lx.server --port $Port
    exit $LASTEXITCODE
}
$taskConverted = & wsl -d $Distro -- wslpath -a ($taskSource.Replace('\','/'))
if ($LASTEXITCODE -ne 0) { throw 'Could not find the app folder inside WSL.' }
$taskLinuxSource = ($taskConverted -join '').Trim()
& wsl -d $Distro -- bash -c 'cd "$1" && exec .venv/bin/python -m lx.server --port "$2"' -- $taskLinuxSource $Port
exit $LASTEXITCODE
