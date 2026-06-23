[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Project,

    [Parameter(Mandatory = $false)]
    [string]$Prompt,

    [Parameter(Mandatory = $false)]
    [string]$PromptFile,

    [int]$MaxRepairs = 2,
    [int]$TimeoutPlan = 600,
    [int]$TimeoutImplement = 1800,
    [int]$TimeoutVerify = 900,
    [int]$TimeoutBuild = 900,
    [string]$OpencodeBin,
    [string]$LogDir,
    [string]$RunDir,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $ScriptDir "opale_runner.py"

if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Runner OPALE introuvable : $Runner"
}

$ArgsList = @(
    $Runner,
    "--project", $Project,
    "--max-repairs", $MaxRepairs,
    "--timeout-plan", $TimeoutPlan,
    "--timeout-implement", $TimeoutImplement,
    "--timeout-verify", $TimeoutVerify,
    "--timeout-build", $TimeoutBuild
)

if ($PromptFile) {
    $ArgsList += @("--prompt-file", $PromptFile)
} elseif ($Prompt) {
    $ArgsList += @("--prompt", $Prompt)
} else {
    throw "Fournir -Prompt ou -PromptFile."
}

if ($OpencodeBin) {
    $ArgsList += @("--opencode-bin", $OpencodeBin)
}

if ($LogDir) {
    $ArgsList += @("--log-dir", $LogDir)
}

if ($RunDir) {
    $ArgsList += @("--run-dir", $RunDir)
}

if ($DryRun) {
    $ArgsList += "--dry-run"
}

python @ArgsList
exit $LASTEXITCODE
