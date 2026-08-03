$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher = Join-Path $RepoRoot 'traid.ps1'
$ProfilePath = $PROFILE.CurrentUserCurrentHost
$ProfileDirectory = Split-Path -Parent $ProfilePath

if (-not (Test-Path $Launcher)) {
    throw "Launcher not found: $Launcher"
}

if (-not (Test-Path $ProfileDirectory)) {
    New-Item -ItemType Directory -Path $ProfileDirectory -Force | Out-Null
}

if (-not (Test-Path $ProfilePath)) {
    New-Item -ItemType File -Path $ProfilePath -Force | Out-Null
}

$startMarker = '# >>> TRAID COMMAND >>>'
$endMarker = '# <<< TRAID COMMAND <<<'
$escapedLauncher = $Launcher.Replace("'", "''")
$block = @"
$startMarker
function traid {
    param(
        [ValidateSet('start', 'stop', 'restart', 'status')]
        [string]`$Action = 'start',
        [switch]`$NoBrowser
    )

    & '$escapedLauncher' -Action `$Action -NoBrowser:`$NoBrowser
}
$endMarker
"@

$profileText = [string](Get-Content $ProfilePath -Raw -ErrorAction SilentlyContinue)
$escapedStart = [regex]::Escape($startMarker)
$escapedEnd = [regex]::Escape($endMarker)
$pattern = "(?s)\r?\n?$escapedStart.*?$escapedEnd\r?\n?"
$profileText = [regex]::Replace($profileText, $pattern, "`r`n")
$profileText = $profileText.TrimEnd() + "`r`n`r`n" + $block.Trim() + "`r`n"
Set-Content -Path $ProfilePath -Value $profileText -Encoding UTF8

. $ProfilePath

Write-Host "Installed the 'traid' PowerShell command." -ForegroundColor Green
Write-Host 'Available commands:' -ForegroundColor Cyan
Write-Host '  traid'
Write-Host '  traid restart'
Write-Host '  traid status'
Write-Host '  traid stop'
