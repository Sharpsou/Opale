[CmdletBinding()]
param(
    [string]$Target = (Join-Path $HOME ".config/opencode"),
    [switch]$Force,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Source = [System.IO.Path]::GetFullPath($PSScriptRoot)
$Runtime = Join-Path $Source "opencode"
$RunnerRuntime = Join-Path $Source "runner"
$Target = [System.IO.Path]::GetFullPath($Target)
$RunnerTarget = Join-Path $Target "opale-runner"
$ExistingModels = $null
$DeprecatedManagedModels = @(
    "local-qwen35-9b:latest"
)
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

if ($Runtime.TrimEnd("\") -eq $Target.TrimEnd("\")) {
    throw "La source et la cible doivent etre differentes."
}

if (Test-Path -LiteralPath $Target) {
    $Existing = Get-ChildItem -Force -LiteralPath $Target
    if ($Existing -and -not $Force) {
        throw "La cible n'est pas vide. Utiliser -Force pour restaurer par-dessus."
    }
    $ExistingConfigPath = Join-Path $Target "opencode.json"
    if (Test-Path -LiteralPath $ExistingConfigPath) {
        $ExistingConfig = Get-Content -Raw -LiteralPath $ExistingConfigPath | ConvertFrom-Json
        $ExistingModels = $ExistingConfig.provider.ollama.models
    }
} else {
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
}

$Payload = @(
    "AGENTS.md",
    "opencode.json",
    "agents",
    "tools"
)

foreach ($Name in $Payload) {
    $SourcePath = Join-Path $Runtime $Name
    if (-not (Test-Path -LiteralPath $SourcePath)) {
        throw "Fichier requis absent : $Name"
    }
    $DestinationPath = Join-Path $Target $Name
    if ((Get-Item -LiteralPath $SourcePath).PSIsContainer) {
        New-Item -ItemType Directory -Path $DestinationPath -Force | Out-Null
        Get-ChildItem -Force -LiteralPath $SourcePath | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $DestinationPath -Recurse -Force
        }
    } else {
        Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
    }
}

if (-not (Test-Path -LiteralPath $RunnerRuntime)) {
    throw "Runtime runner absent : runner"
}
New-Item -ItemType Directory -Path $RunnerTarget -Force | Out-Null
Get-ChildItem -Force -LiteralPath $RunnerRuntime | ForEach-Object {
    if ($_.Name -eq "__pycache__") {
        return
    }
    Copy-Item -LiteralPath $_.FullName -Destination $RunnerTarget -Recurse -Force
}
$RunnerCache = Join-Path $RunnerTarget "__pycache__"
if (Test-Path -LiteralPath $RunnerCache) {
    Remove-Item -LiteralPath $RunnerCache -Recurse -Force
}

$LegacyRunnerEnv = Join-Path $RunnerTarget "opale.env.json"
if (Test-Path -LiteralPath $LegacyRunnerEnv) {
    Remove-Item -LiteralPath $LegacyRunnerEnv -Force
}

$ConfigPath = Join-Path $Target "opencode.json"
$Config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
$Config.instructions = @(
    (Join-Path $Target "AGENTS.md")
)
if ($ExistingModels) {
    foreach ($Property in $ExistingModels.PSObject.Properties) {
        if ($DeprecatedManagedModels -contains $Property.Name) {
            continue
        }
        if (-not $Config.provider.ollama.models.PSObject.Properties[$Property.Name]) {
            $Config.provider.ollama.models | Add-Member -NotePropertyName $Property.Name -NotePropertyValue $Property.Value
        }
    }
}
$ConfigJson = $Config | ConvertTo-Json -Depth 100
[System.IO.File]::WriteAllText($ConfigPath, $ConfigJson, $Utf8NoBom)

# Nettoie uniquement les anciens fichiers geres par OPALE avant cette structure.
@("OPALE.md", ".gitignore", "plugin", "plugins", "scripts", "agents/agents") | ForEach-Object {
    $LegacyPath = Join-Path $Target $_
    if (Test-Path -LiteralPath $LegacyPath) {
        Remove-Item -LiteralPath $LegacyPath -Recurse -Force
    }
}

# Nettoie les agents runner historiques. Le runner Python n'appelle plus
# `opencode run`; il pilote directement Ollama natif et le disque.
@(
    "agents/runner-product-architect.md",
    "agents/runner-code-worker.md",
    "agents/runner-verifier.md"
) | ForEach-Object {
    $LegacyAgent = Join-Path $Target $_
    if (Test-Path -LiteralPath $LegacyAgent) {
        Remove-Item -LiteralPath $LegacyAgent -Force
    }
}

Write-Output "OPALE v0.3 Machine d'etat globale restauree dans $Target"
Write-Output "Runner : Set-Content -Encoding UTF8 -LiteralPath `"`$env:TEMP\opale-prompt.txt`" -Value `"...`"; powershell `"$RunnerTarget\opale.ps1`" -Project `"D:\prog\PongW`" -PromptFile `"`$env:TEMP\opale-prompt.txt`""
