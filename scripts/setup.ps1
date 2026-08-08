[CmdletBinding()]
param(
    [switch]$EnableDetailedMatching
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

function Set-EnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $envPath = Join-Path $projectRoot ".env"
    $lines = [System.Collections.Generic.List[string]]::new()
    if (Test-Path -LiteralPath $envPath) {
        foreach ($line in [System.IO.File]::ReadAllLines($envPath)) {
            $lines.Add($line)
        }
    }

    $replacement = "$Name=$Value"
    $replaced = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match "^$([regex]::Escape($Name))=") {
            $lines[$index] = $replacement
            $replaced = $true
            break
        }
    }
    if (-not $replaced) {
        $lines.Add($replacement)
    }
    [System.IO.File]::WriteAllLines($envPath, $lines, [System.Text.UTF8Encoding]::new($false))
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop is not installed or docker.exe is not in PATH."
}

docker desktop status | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not running. Start it and run this script again."
}

docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose v2 is unavailable. Update Docker Desktop."
}

docker network inspect local-code-worker-network *> $null
if ($LASTEXITCODE -ne 0) {
    docker network create local-code-worker-network | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the local-code-worker-network Docker network."
    }
}

$envPath = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot ".env.example") -Destination $envPath
}

$envLines = [System.IO.File]::ReadAllLines($envPath)
$keyLine = $envLines | Where-Object { $_ -match "^APP_BROWSER_STATE_ENCRYPTION_KEY=" } |
    Select-Object -First 1
if (-not $keyLine -or $keyLine -eq "APP_BROWSER_STATE_ENCRYPTION_KEY=") {
    $randomBytes = [byte[]]::new(32)
    $randomGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $randomGenerator.GetBytes($randomBytes)
    } finally {
        $randomGenerator.Dispose()
    }
    $fernetKey = [Convert]::ToBase64String($randomBytes).Replace("+", "-").Replace("/", "_")
    Set-EnvValue -Name "APP_BROWSER_STATE_ENCRYPTION_KEY" -Value $fernetKey
}

if ($EnableDetailedMatching) {
    Set-EnvValue -Name "APP_MATCHING_V2_ENABLED" -Value "true"
    Write-Warning (
        "Detailed matching downloads several gigabytes of models and is intended for machines " +
        "with at least 16 GB RAM and 12 GB free disk space."
    )
    docker compose --profile browser --profile matching up --build -d --wait
} else {
    Set-EnvValue -Name "APP_MATCHING_V2_ENABLED" -Value "false"
    docker compose --profile browser up --build -d --wait
}

if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose could not start the application. Run 'docker compose logs --tail 200'."
}

$health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 20
if ($health.status -ne "ok") {
    throw "The API started but did not report a healthy status."
}

Write-Host ""
$projectVersion = (Get-Content -LiteralPath (Join-Path $projectRoot "VERSION") -Raw).Trim()
Write-Host "Job Searching Assistant $projectVersion is ready."
Write-Host "Open: http://127.0.0.1:8000/dashboard"
