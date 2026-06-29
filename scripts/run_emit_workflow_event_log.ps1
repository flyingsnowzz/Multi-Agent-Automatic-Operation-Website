param(
  [string]$PythonPath = "",
  [switch]$ResetLog,
  [int]$TailLines = 20
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$smokeScript = Join-Path $projectRoot "scripts\emit_workflow_event_log.py"
$logFile = Join-Path $projectRoot "logs\app.log"
$logDir = Split-Path -Parent $logFile
$expectedStages = @("start", "research", "write", "edit", "seo", "image", "cms", "evolve", "end")

function Get-PythonInvocation {
  param(
    [string]$PreferredPath
  )

  $candidates = @()

  if ($PreferredPath) {
    $candidates += $PreferredPath
  }

  $repoCandidates = @(
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path $projectRoot "venv\Scripts\python.exe")
  )
  $candidates += $repoCandidates

  $userProfilePython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
  $candidates += $userProfilePython

  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) {
      return @{
        Executable = $candidate
        Arguments = @()
        Display = $candidate
      }
    }
  }

  $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
  if ($pythonCmd) {
    return @{
      Executable = $pythonCmd.Source
      Arguments = @()
      Display = $pythonCmd.Source
    }
  }

  $pyCmd = Get-Command py -ErrorAction SilentlyContinue
  if ($pyCmd) {
    return @{
      Executable = $pyCmd.Source
      Arguments = @("-3.12")
      Display = "$($pyCmd.Source) -3.12"
    }
  }

  throw "No usable Python found. Pass -PythonPath or install/configure Python 3.12."
}

$python = Get-PythonInvocation -PreferredPath $PythonPath
$traceId = "log_smoke_" + [DateTime]::UtcNow.ToString("yyyyMMddHHmmssfff")
$topicId = "log-smoke-topic"

Push-Location $projectRoot
try {
  if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
  }

  if ($ResetLog -and (Test-Path $logFile)) {
    Remove-Item $logFile -Force
  }

  Write-Host ("Using Python: {0}" -f $python.Display)
  & $python.Executable @(
    $python.Arguments +
    $smokeScript +
    "--trace-id", $traceId,
    "--topic-id", $topicId,
    "--log-dir", $logDir
  )

  if (-not (Test-Path $logFile)) {
    throw "Script finished, but logs/app.log was not created."
  }

  $matchedLines = Get-Content $logFile -Encoding UTF8 | Where-Object { $_ -like "*`"trace_id`": `"$traceId`"*"}
  if (-not $matchedLines) {
    throw ("No workflow_event lines found for trace_id={0} in logs/app.log." -f $traceId)
  }

  $missingStages = @()
  foreach ($stage in $expectedStages) {
    $stagePattern = "`"stage`": `"$stage`""
    if (-not ($matchedLines | Where-Object { $_ -like "*$stagePattern*" })) {
      $missingStages += $stage
    }
  }

  if ($missingStages.Count -gt 0) {
    throw ("Missing expected stages for trace_id={0}: {1}" -f $traceId, ($missingStages -join ", "))
  }

  Write-Host ""
  Write-Host ("Verified workflow_event chain for trace_id={0}" -f $traceId)
  Write-Host ("Verified stages: {0}" -f ($expectedStages -join " -> "))

  Write-Host ""
  Write-Host "Current trace log lines:"
  $matchedLines | Select-Object -Last $TailLines
} finally {
  Pop-Location
}
