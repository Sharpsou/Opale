[CmdletBinding()]
param(
    [string]$Target = (Join-Path $HOME ".config/opencode"),
    [switch]$Force,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Source = [System.IO.Path]::GetFullPath($PSScriptRoot)
$Target = [System.IO.Path]::GetFullPath($Target)

if ($Source.TrimEnd("\") -eq $Target.TrimEnd("\")) {
    throw "La source et la cible doivent etre differentes."
}

if (Test-Path -LiteralPath $Target) {
    $Existing = Get-ChildItem -Force -LiteralPath $Target
    if ($Existing -and -not $Force) {
        throw "La cible n'est pas vide. Utiliser -Force pour restaurer par-dessus."
    }
} else {
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
}

$Payload = @(
    ".gitignore",
    "AGENTS.md",
    "OPALE.md",
    "opencode.json",
    "package-lock.json",
    "package.json",
    "agents",
    "plugin",
    "scripts"
)

foreach ($Name in $Payload) {
    $SourcePath = Join-Path $Source $Name
    if (-not (Test-Path -LiteralPath $SourcePath)) {
        throw "Fichier requis absent : $Name"
    }
    Copy-Item -LiteralPath $SourcePath -Destination $Target -Recurse -Force
}

$ConfigPath = Join-Path $Target "opencode.json"
$Config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
$Config.instructions = @(
    (Join-Path $Target "AGENTS.md"),
    (Join-Path $Target "OPALE.md")
)
$ConfigJson = $Config | ConvertTo-Json -Depth 100
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ConfigPath, $ConfigJson, $Utf8NoBom)

if (-not $SkipInstall) {
    Push-Location $Target
    try {
        & npm ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci a echoue avec le code $LASTEXITCODE." }
    } finally {
        Pop-Location
    }
}

Write-Output "OPALE v0.2 Prompt global restaure dans $Target"
