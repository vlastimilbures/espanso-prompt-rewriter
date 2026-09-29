# -WithConfig also deploys espanso\config\default.yml, which sets toggle_key and
# search_shortcut. Off by default so an existing Espanso setup is left alone.
param([switch]$WithConfig)

$ErrorActionPreference = "Stop"

foreach ($tool in "uv", "espanso") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Error "'$tool' was not found on PATH. Install it before running this script."
        exit 1
    }
}

$RepoDir = Split-Path -Parent $PSScriptRoot
Set-Location $RepoDir

# $ErrorActionPreference does not stop on a native command's nonzero exit code in
# Windows PowerShell 5.1, so check $LASTEXITCODE after each one that matters.
function Assert-Exit([string]$What) {
    if ($LASTEXITCODE -ne 0) {
        Write-Error "$What failed with exit code $LASTEXITCODE."
        exit 1
    }
}

# The tool gets its own isolated environment; dev dependencies are not needed to deploy.
uv tool install --editable . --force
Assert-Exit "uv tool install"

# Resolve the absolute CLI path. GUI-launched Espanso does not inherit PATH.
# Ask uv where it installed the tool rather than Get-Command, which would pick up an
# activated project venv whose binary disappears if .venv is removed.
$ToolBin = (uv tool dir --bin)
Assert-Exit "uv tool dir"
$Cli = Join-Path $ToolBin.Trim() "prompt-workflow.exe"
if (-not (Test-Path $Cli)) {
    Write-Error "Could not locate prompt-workflow. Check 'uv tool install' output."
    exit 1
}
# Espanso YAML uses forward slashes; normalize for safety inside quotes.
$CliEscaped = $Cli -replace '\\', '/'
# The path goes inside a double-quoted YAML string that cmd.exe runs; refuse characters
# either would interpret there, rather than try to escape them.
if ($CliEscaped -match '["%^&|<>]') {
    Write-Error "CLI path contains a character cmd.exe or YAML would interpret: $Cli"
    exit 1
}
Write-Host "Using CLI at: $Cli"

# 'espanso path config' prints just the config dir (no 'Config:'/'Runtime:' label),
# unlike 'espanso path' which prints labelled lines for config/packages/runtime.
$EspansoDir = (espanso path config).Trim()
Assert-Exit "espanso path config"
$MatchDir = Join-Path $EspansoDir "match"
New-Item -ItemType Directory -Force -Path $MatchDir | Out-Null

# Read and write UTF-8 explicitly: Windows PowerShell 5.1's Get-Content reads a file
# without a BOM as ANSI, which turns the template's em dashes into mojibake.
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false
$Stamp = Get-Date -Format "yyyyMMddHHmmss"

function Backup-File([string]$Path) {
    Copy-Item $Path "$Path.bak-$Stamp"
    Write-Host "Backed up existing $Path to $Path.bak-$Stamp"
}

# Write $Content to $Target, first backing up an existing $Target whose content differs,
# so a file the user edited (or Espanso's own default.yml) is never lost.
function Install-File([string]$Content, [string]$Target) {
    if ((Test-Path $Target) -and ([System.IO.File]::ReadAllText($Target, $Utf8NoBom) -ne $Content)) {
        Backup-File $Target
    }
    [System.IO.File]::WriteAllText($Target, $Content, $Utf8NoBom)
}

# Before 0.9 the -p- template shipped as match\base.yml, the file Espanso itself creates
# for the user's own snippets. Retire our copy (backed up) so -p- is not defined twice,
# and leave any other base.yml alone.
$Legacy = Join-Path $MatchDir "base.yml"
if (Test-Path $Legacy) {
    $LegacyText = [System.IO.File]::ReadAllText($Legacy, $Utf8NoBom)
    if ($LegacyText.Contains('trigger: "-p-"') -and $LegacyText.Contains("prompt-workflow")) {
        Backup-File $Legacy
        Remove-Item $Legacy
    }
}

Get-ChildItem "$RepoDir\espanso\match\*.yml" | ForEach-Object {
    $Content = [System.IO.File]::ReadAllText($_.FullName, $Utf8NoBom)
    # Literal .Replace(), not regex -replace: a '$' in a path would be read as a group reference.
    Install-File ($Content.Replace('__PROMPT_WORKFLOW__', $CliEscaped)) (Join-Path $MatchDir $_.Name)
}

# Only with -WithConfig: Espanso's default.yml hard-sets toggle_key/search_shortcut the
# user may have customized.
if ($WithConfig) {
    $ConfigDir = Join-Path $EspansoDir "config"
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    Get-ChildItem "$RepoDir\espanso\config\*.yml" | ForEach-Object {
        Install-File ([System.IO.File]::ReadAllText($_.FullName, $Utf8NoBom)) (Join-Path $ConfigDir $_.Name)
    }
}

# Same fallback as the macOS installer: restart fails when Espanso is not running.
espanso restart
if ($LASTEXITCODE -ne 0) {
    espanso start
    Assert-Exit "espanso start"
}
if (-not $WithConfig) {
    Write-Host "Left Espanso's config\default.yml untouched (pass -WithConfig to deploy ours)."
}
Write-Host "Installed. Test -p- and -i- in any text field."
Write-Host "Settings are read from $RepoDir\.env (copy .env.example), or from the file"
Write-Host "named by PROMPT_WORKFLOW_ENV."
