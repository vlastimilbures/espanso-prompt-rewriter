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

# The tool gets its own isolated environment; dev dependencies are not needed to deploy.
uv tool install --editable . --force

# Resolve the absolute CLI path. GUI-launched Espanso does not inherit PATH.
# Ask uv where it installed the tool rather than Get-Command, which would pick up an
# activated project venv whose binary disappears if .venv is removed.
$Cli = Join-Path (uv tool dir --bin).Trim() "prompt-workflow.exe"
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
$MatchDir = Join-Path $EspansoDir "match"
New-Item -ItemType Directory -Force -Path $MatchDir | Out-Null

$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

Get-ChildItem "$RepoDir\espanso\match\*.yml" | ForEach-Object {
    $content = Get-Content $_.FullName -Raw
    # Literal .Replace(), not regex -replace: a '$' in a path would be read as a group reference.
    $content = $content.Replace('__PROMPT_WORKFLOW__', $CliEscaped)
    [System.IO.File]::WriteAllText((Join-Path $MatchDir $_.Name), $content, $Utf8NoBom)
}

# Only with -WithConfig. Back up any config file we would overwrite, since Espanso's
# default.yml hard-sets toggle_key/search_shortcut the user may have customized.
if ($WithConfig) {
    $ConfigDir = Join-Path $EspansoDir "config"
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    $Stamp = Get-Date -Format "yyyyMMddHHmmss"
    Get-ChildItem "$RepoDir\espanso\config\*.yml" | ForEach-Object {
        $Target = Join-Path $ConfigDir $_.Name
        if (Test-Path $Target) {
            $Backup = "$Target.bak-$Stamp"
            Copy-Item $Target $Backup
            Write-Host "Backed up existing $Target to $Backup"
        }
        Copy-Item $_.FullName $Target -Force
    }
}

espanso restart
if (-not $WithConfig) {
    Write-Host "Left Espanso's config\default.yml untouched (pass -WithConfig to deploy ours)."
}
Write-Host "Installed. Test -p- and -i- in any text field."
Write-Host "Settings are read from $RepoDir\.env (copy .env.example), or from the file"
Write-Host "named by PROMPT_WORKFLOW_ENV."
