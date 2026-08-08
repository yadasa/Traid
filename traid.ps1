param(
    [ValidateSet('start', 'stop', 'restart', 'status')]
    [string]$Action = 'start',
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$RuntimeFile = Join-Path $RepoRoot '.traid-runtime.json'
$BackendUrl = 'http://127.0.0.1:8000/health'
$DashboardUrl = 'http://127.0.0.1:3000/'
$ChartUrl = 'http://127.0.0.1:3000/chart'

function Test-ListeningPort {
    param([int]$Port)
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Test-HttpEndpoint {
    param([string]$Url)
    try {
        Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Read-RuntimeState {
    if (-not (Test-Path $RuntimeFile)) {
        return $null
    }

    try {
        return Get-Content $RuntimeFile -Raw | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Stop-TraidProcess {
    param(
        [Nullable[int]]$ProcessId,
        [string]$Name
    )

    if (-not $ProcessId) {
        return
    }

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $ProcessId -Force
        Write-Host "Stopped $Name (PID $ProcessId)." -ForegroundColor Yellow
    }
}

function Stop-Traid {
    $runtime = Read-RuntimeState
    if ($runtime) {
        Stop-TraidProcess -ProcessId $runtime.backendPid -Name 'backend'
        Stop-TraidProcess -ProcessId $runtime.dashboardPid -Name 'dashboard'
    }

    if (Test-Path $RuntimeFile) {
        Remove-Item $RuntimeFile -Force
    }

    Write-Host 'Traid services stopped.' -ForegroundColor Green
}

function Show-TraidStatus {
    $backendListening = Test-ListeningPort 8000
    $dashboardListening = Test-ListeningPort 3000
    $backendHealthy = Test-HttpEndpoint $BackendUrl
    $dashboardHealthy = Test-HttpEndpoint $DashboardUrl
    $chartHealthy = Test-HttpEndpoint $ChartUrl

    [pscustomobject]@{
        BackendPort = if ($backendListening) { 'Listening' } else { 'Stopped' }
        BackendHealth = if ($backendHealthy) { 'Healthy' } else { 'Unavailable' }
        DashboardPort = if ($dashboardListening) { 'Listening' } else { 'Stopped' }
        DashboardHealth = if ($dashboardHealthy) { 'Healthy' } else { 'Unavailable' }
        ChartRoute = if ($chartHealthy) { 'Healthy' } else { 'Unavailable' }
    } | Format-Table -AutoSize
}

function Start-Traid {
    if (-not (Test-Path $Python)) {
        throw "Virtual-environment Python was not found at: $Python"
    }

    $backendProcess = $null
    $dashboardProcess = $null

    if (Test-ListeningPort 8000) {
        Write-Host 'Backend is already listening on port 8000.' -ForegroundColor Cyan
    }
    else {
        $backendProcess = Start-Process `
            -FilePath $Python `
            -ArgumentList @('-m', 'traid_live.cli', 'serve', '--host', '127.0.0.1', '--port', '8000') `
            -WorkingDirectory $RepoRoot `
            -PassThru
        Write-Host "Started backend (PID $($backendProcess.Id))." -ForegroundColor Green
    }

    if (Test-ListeningPort 3000) {
        Write-Host 'Dashboard is already listening on port 3000.' -ForegroundColor Cyan
    }
    else {
        $dashboardProcess = Start-Process `
            -FilePath $Python `
            -ArgumentList @('-m', 'traid_live.dashboard_server', '--host', '127.0.0.1', '--port', '3000', '--directory', 'dashboard') `
            -WorkingDirectory $RepoRoot `
            -PassThru
        Write-Host "Started dashboard (PID $($dashboardProcess.Id))." -ForegroundColor Green
    }

    [pscustomobject]@{
        backendPid = if ($backendProcess) { $backendProcess.Id } else { $null }
        dashboardPid = if ($dashboardProcess) { $dashboardProcess.Id } else { $null }
        startedAt = (Get-Date).ToString('o')
    } | ConvertTo-Json | Set-Content $RuntimeFile

    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if ((Test-HttpEndpoint $BackendUrl) -and (Test-HttpEndpoint $DashboardUrl) -and (Test-HttpEndpoint $ChartUrl)) {
            break
        }
        Start-Sleep -Milliseconds 500
    }

    Show-TraidStatus

    if (-not $NoBrowser) {
        Start-Process $DashboardUrl
    }
}

switch ($Action) {
    'start' { Start-Traid }
    'stop' { Stop-Traid }
    'restart' {
        Stop-Traid
        Start-Sleep -Milliseconds 750
        Start-Traid
    }
    'status' { Show-TraidStatus }
}
