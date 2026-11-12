param(
    [ValidateSet("format", "lint", "type", "test", "web", "architecture", "verify", "tooling", "doctor")]
    [string]$Task = "verify"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
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

function Invoke-NativeStage {
    param([string]$Name, [string]$Executable, [string[]]$Arguments)
    Write-Host "THOTH_CHECK $Name"
    & $Executable @Arguments
    Assert-NativeSuccess $Name
}

function Invoke-BackendLint {
    Invoke-NativeStage "LINT" $Python @("-m", "ruff", "check", "src", "apps", "tests", "migrations")
    Invoke-NativeStage "FORMAT_CHECK" $Python @("-m", "ruff", "format", "--check", "src", "apps", "tests", "migrations")
}

function Invoke-BackendType {
    Invoke-NativeStage "TYPE" $Python @("-m", "basedpyright")
}

function Invoke-BackendTest {
    param([string]$StageName = "BACKEND", [string[]]$Targets = @())
    $arguments = @("-m", "pytest")
    $arguments += $Targets
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
