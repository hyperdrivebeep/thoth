param(
    [ValidateSet("format", "lint", "type", "test", "web", "architecture", "verify", "tooling", "doctor")]
    [string]$Task = "verify"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$script:VerificationRunId = $env:THOTH_VERIFICATION_RUN_ID
# Nested test processes must not reuse the parent's observation files.
Remove-Item Env:THOTH_VERIFICATION_RUN_ID -ErrorAction SilentlyContinue
$script:VerificationStages = @()
$script:StageTelemetryAvailable = $true
$Pnpm = $env:THOTH_PNPM
if ([string]::IsNullOrWhiteSpace($Pnpm)) {
    $PnpmCommand = Get-Command "pnpm.cmd" -ErrorAction SilentlyContinue
    if ($null -eq $PnpmCommand) {
        $PnpmCommand = Get-Command "pnpm" -ErrorAction SilentlyContinue
    }
    if ($null -eq $PnpmCommand) {
        throw "pnpm is required. Install pnpm or set THOTH_PNPM to its executable path."
    }
    $Pnpm = $PnpmCommand.Source
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project venv is missing. Run 'uv sync --all-extras --dev' first: $Python"
}

function Assert-NativeSuccess {
    param([string]$Label)
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Write-VerificationStage {
    param([string]$Name, [string]$State, [string]$StartedAt, $ExitCode, [double]$Elapsed)
    $stamp = [DateTimeOffset]::UtcNow.ToString("o")
    Write-Host "THOTH_VERIFY_STAGE $Name $State exit=$ExitCode elapsed_s=$Elapsed at=$stamp"
    if ([string]::IsNullOrWhiteSpace($script:VerificationRunId)) { return }
    if (-not $script:StageTelemetryAvailable) { return }
    try {
        if ($script:VerificationRunId -notmatch '^vr-[0-9a-f]{32}$') { throw "invalid run ID" }
        $directory = Join-Path $RepoRoot ".thoth/verification-runs/$script:VerificationRunId"
        $entry = @{
            name=$Name; state=$State; started_at=$StartedAt
            exit_code=$ExitCode; elapsed_s=$Elapsed
        }
        $current = $entry
        if ($State -ne "STARTED") {
            $script:VerificationStages += $entry
            $current = $null
        }
        $payload = @{
            schema_version="1.0.0"; run_id=$script:VerificationRunId; current=$current
            completed=@($script:VerificationStages); last_event_at=$stamp; producer_pid=$PID
        }
        $path = Join-Path $directory "stages.json"
        $temporary = "$path.$([Guid]::NewGuid().ToString('N')).tmp"
        $encoding = New-Object System.Text.UTF8Encoding($false)
        [IO.File]::WriteAllText($temporary, ($payload | ConvertTo-Json -Depth 8 -Compress), $encoding)
        if ([IO.File]::Exists($path)) { [IO.File]::Replace($temporary, $path, [NullString]::Value) }
        else { [IO.File]::Move($temporary, $path) }
    }
    catch {
        $script:StageTelemetryAvailable = $false
        Write-Warning "THOTH TELEMETRY_UNAVAILABLE"
    }
}

function Invoke-NativeStage {
    param([string]$Name, [string]$Executable, [string[]]$Arguments)
    $started = [DateTimeOffset]::UtcNow.ToString("o")
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $observedExit = $null
    Write-VerificationStage $Name "STARTED" $started $null 0
    try {
        if ($Name -in @("BACKEND", "TOOLING") -and -not [string]::IsNullOrWhiteSpace($script:VerificationRunId)) {
            # Safe structured reports replace raw capture, locals and parameter representations.
            & $Executable @Arguments *> $null
        }
        else { & $Executable @Arguments }
        $observedExit = $LASTEXITCODE
        Assert-NativeSuccess $Name
    }
    finally {
        $clock.Stop()
        $state = if ($null -eq $observedExit) { "EXIT_UNKNOWN" } elseif ($observedExit -eq 0) {
            "FINISHED"
        } else { "FAILED" }
        Write-VerificationStage $Name $state $started $observedExit $clock.Elapsed.TotalSeconds
    }
}

function Invoke-BackendLint {
    Invoke-NativeStage "LINT" $Python @("-m", "ruff", "check", "src", "apps", "tests", "migrations", "conftest.py", "scripts/pytest_environment.py")
}

function Invoke-BackendType {
    Invoke-NativeStage "TYPE" $Python @("-m", "basedpyright")
}

function Invoke-BackendTest {
    param([string]$StageName = "BACKEND", [string[]]$Targets = @())
    $arguments = @("-m", "pytest")
    $arguments += $Targets
    if (-not [string]::IsNullOrWhiteSpace($script:VerificationRunId)) {
        $arguments += @(
            "-p", "scripts.pytest_progress", "--thoth-progress-run", $script:VerificationRunId,
            "--durations=20", "--tb=no", "--show-capture=no", "--no-showlocals",
            "--no-summary", "-rN", "--disable-warnings", "--capture=fd"
        )
    }
    Invoke-NativeStage $StageName $Python $arguments
}

function Invoke-ArchitectureVerify {
    Invoke-NativeStage "ARCHITECTURE" $Python @(
        (Join-Path $RepoRoot "scripts\required_architecture_checks.py")
    )
}

function Invoke-WebVerify {
    Push-Location (Join-Path $RepoRoot "apps\web")
    try {
        Invoke-NativeStage "WEB_LINT" $Pnpm @("run", "lint")
        Invoke-NativeStage "WEB_TYPE" $Pnpm @("run", "typecheck")
        Invoke-NativeStage "WEB_TEST" $Pnpm @("exec", "vitest", "run")
        Invoke-NativeStage "WEB_BUILD" $Pnpm @("run", "build")
    }
    finally { Pop-Location }
}

switch ($Task) {
    "format" {
        & $Python -m ruff format src apps tests migrations
        Assert-NativeSuccess "Backend format"
    }
    "lint" { Invoke-BackendLint }
    "type" { Invoke-BackendType }
    "test" { Invoke-BackendTest }
    "web" { Invoke-WebVerify }
    "architecture" { Invoke-ArchitectureVerify }
    "doctor" { Invoke-NativeStage "DOCTOR" $Python @("-m", "thoth.cli", "doctor") }
    "tooling" {
        Invoke-ArchitectureVerify
        Invoke-BackendLint
        Invoke-BackendType
        Invoke-BackendTest "TOOLING" @("tests/architecture")
    }
    "verify" {
        Invoke-ArchitectureVerify
        Invoke-BackendLint
        Invoke-BackendType
        Invoke-BackendTest
        Invoke-WebVerify
        Invoke-NativeStage "DOCTOR" $Python @("-m", "thoth.cli", "doctor")
    }
}
