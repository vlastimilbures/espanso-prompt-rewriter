# Contributor install: the CLI from this checkout, pinned to uv.lock, then the managed
# deploy (`prompt-workflow espanso deploy`) writes the match files into Espanso.
# -WithConfig is kept only to say it was removed.
param([switch]$WithConfig)

$ErrorActionPreference = "Stop"

if ($WithConfig) {
    Write-Error "-WithConfig was removed: Espanso's own config\default.yml is never replaced."
    exit 1
}

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

# `uv tool install` ignores uv.lock, so pass the locked versions as constraints (#34). The
# tool gets its own isolated environment; dev dependencies are not needed to deploy.
$Constraints = [System.IO.Path]::GetTempFileName()
try {
    uv export --frozen --no-dev --no-emit-project --no-hashes --quiet -o $Constraints
    Assert-Exit "uv export"
    uv tool install --editable . --force -c $Constraints
    Assert-Exit "uv tool install"
} finally {
    Remove-Item $Constraints -ErrorAction SilentlyContinue
}

# Ask uv where it installed the tool rather than Get-Command, which would pick up an
# activated project venv whose binary disappears if .venv is removed.
$ToolBin = (uv tool dir --bin)
Assert-Exit "uv tool dir --bin"
$ToolDir = (uv tool dir)
Assert-Exit "uv tool dir"
$Cli = Join-Path $ToolBin.Trim() "prompt-workflow.exe"
$ToolPython = Join-Path $ToolDir.Trim() "espanso-prompt-rewriter\Scripts\python.exe"
if (-not (Test-Path $Cli) -or -not (Test-Path $ToolPython)) {
    Write-Error "Could not locate prompt-workflow. Check 'uv tool install' output."
    exit 1
}
& $ToolPython scripts\check_tool_lock.py --python $ToolPython
Assert-Exit "lock check"

# Shows the plan, keeps any match file you edited (see `prompt-workflow espanso status`),
# writes the rest with this launcher (forward slashes, path guards), and restarts Espanso.
& $Cli espanso deploy --yes --launcher $Cli
Assert-Exit "prompt-workflow espanso deploy"
Write-Host "Installed. Test -p- and -i- in any text field."
Write-Host "Settings are read from $RepoDir\.env (copy .env.example), or from the file"
Write-Host "named by PROMPT_WORKFLOW_ENV."
