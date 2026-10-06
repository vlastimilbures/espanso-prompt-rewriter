# PromptMend user installer for Windows (#186). Each GitHub Release attaches this file with
# its own version filled in, so this installs exactly that release:
#
#   powershell -ExecutionPolicy ByPass -c "irm https://github.com/vlastimilbures/promptmend/releases/latest/download/install.ps1 | iex"
#
# It installs uv if missing (winget, else uv's official installer), then
# `uv tool install --force promptmend==<version> -c <that release's constraints.txt>`,
# `uv tool update-shell` and `promptmend doctor --no-clipboard`. It prints every command
# before it runs it, and running it again reinstalls or upgrades in place (the launcher stays
# in uv's tool bin folder). It never runs the setup or the Espanso deploy: it only prints them.
#
# Overrides (environment variables):
#   PROMPTMEND_VERSION       install this version instead (its Release's constraints.txt)
#   PROMPTMEND_WHEEL         install this local wheel instead (CI); needs
#   PROMPTMEND_CONSTRAINTS   a local constraints.txt
#
# Windows PowerShell 5.1 compatible and ASCII only (5.1 reads a file without a BOM as ANSI).
# Everything runs inside a function: `irm | iex` runs in the caller's session, so neither the
# strict mode nor the preferences leak into it, and a failure throws instead of `exit`, which
# would close the caller's window.

function Install-PromptMend {
    Set-StrictMode -Version Latest
    $ErrorActionPreference = 'Stop'
    # pwsh 7.4+: a nonzero exit is checked by hand below (doctor exits 4 on findings).
    $PSNativeCommandUseErrorActionPreference = $false

    $Repo = 'https://github.com/vlastimilbures/promptmend'
    # The release workflow replaces this with the release's version when it attaches the file.
    $ReleaseVersion = '__PROMPTMEND_VERSION__'
    $Unrendered = '__PROMPTMEND_' + 'VERSION__'

    function Write-Step([string]$Text) {
        Write-Host ''
        Write-Host "==> $Text" -ForegroundColor Cyan
    }

    # Prints the command, runs it and returns its exit code. Native stderr (uv's progress) is
    # not an error: under 'Stop', Windows PowerShell 5.1 would throw on it when redirected.
    function Invoke-Native([string]$Exe, [string[]]$Arguments) {
        Write-Host "> $Exe $($Arguments -join ' ')" -ForegroundColor DarkGray
        $ErrorActionPreference = 'Continue'
        & $Exe @Arguments | Out-Host
        return $LASTEXITCODE
    }

    function Assert-Native([string]$Exe, [string[]]$Arguments) {
        $code = Invoke-Native $Exe $Arguments
        if ($code -ne 0) {
            throw "'$Exe $($Arguments -join ' ')' failed with exit code $code."
        }
    }

    # Output of a command this script only reads (not printed: it changes nothing).
    function Read-Native([string]$Exe, [string[]]$Arguments) {
        $ErrorActionPreference = 'Continue'
        $out = & $Exe @Arguments 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE."
        }
        return (($out | ForEach-Object { "$_" }) -join "`n").Trim()
    }

    # The PATH a new terminal would get, plus what this session already had.
    function Update-SessionPath([string[]]$Extra) {
        $parts = @()
        foreach ($scope in 'Machine', 'User') {
            $value = [Environment]::GetEnvironmentVariable('Path', $scope)
            if ($value) { $parts += $value -split ';' }
        }
        $parts += $env:Path -split ';'
        $parts += $Extra
        $seen = @{}
        $kept = foreach ($p in $parts) {
            if ($p -and -not $seen.ContainsKey($p.ToLowerInvariant())) {
                $seen[$p.ToLowerInvariant()] = $true
                $p
            }
        }
        $env:Path = $kept -join ';'
    }

    # What to install: a local wheel (CI), or promptmend==<version> from PyPI with the
    # constraints.txt of the GitHub Release of that version.
    if ($env:PROMPTMEND_WHEEL) {
        if (-not $env:PROMPTMEND_CONSTRAINTS) {
            throw 'PROMPTMEND_WHEEL needs PROMPTMEND_CONSTRAINTS (a local constraints.txt).'
        }
        foreach ($file in $env:PROMPTMEND_WHEEL, $env:PROMPTMEND_CONSTRAINTS) {
            if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "$file not found." }
        }
        $Spec = (Resolve-Path -LiteralPath $env:PROMPTMEND_WHEEL).Path
        $Constraints = (Resolve-Path -LiteralPath $env:PROMPTMEND_CONSTRAINTS).Path
    } else {
        $Version = if ($env:PROMPTMEND_VERSION) { $env:PROMPTMEND_VERSION } else { $ReleaseVersion }
        if ($Version -eq $Unrendered) {
            throw ("This copy of install.ps1 names no release: use the one attached to a " +
                "GitHub Release ($Repo/releases), or set PROMPTMEND_VERSION.")
        }
        $Version = $Version.TrimStart('v')
        if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+([a-z]+[0-9]+)?$') {
            throw "'$Version' is not a PromptMend version such as 0.19.0."
        }
        $Spec = "promptmend==$Version"
        $Constraints = "$Repo/releases/download/v$Version/constraints.txt"
    }
    Write-Host "Installing $Spec"
    Write-Host "with the dependency versions in $Constraints"

    # uv: winget first, else uv's official installer (in its own process, as uv documents it).
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Step 'uv was not found: installing it'
        $installed = $false
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            $code = Invoke-Native 'winget' @('install', '--id', 'astral-sh.uv', '-e',
                '--accept-source-agreements', '--accept-package-agreements')
            if ($code -eq 0) { $installed = $true }
            else { Write-Warning "winget exited with $code; trying uv's own installer." }
        }
        if (-not $installed) {
            Assert-Native 'powershell' @('-NoProfile', '-ExecutionPolicy', 'ByPass', '-Command',
                'irm https://astral.sh/uv/install.ps1 | iex')
        }
        # Where winget's link and uv's installer put uv, until a new terminal reads PATH.
        $guesses = @(
            (Join-Path $env:USERPROFILE '.local\bin'),
            (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links')
        )
        Update-SessionPath $guesses
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            throw 'uv was installed but is not on PATH yet: open a new terminal and run this again.'
        }
    }
    Write-Host "uv: $((Get-Command uv).Source)"

    # The tool's name before 0.19.0 (#169) owns a `prompt-workflow` launcher where promptmend
    # puts its alias: uv refuses to install over it, so it goes first, only if uv lists it.
    $tools = Read-Native 'uv' @('tool', 'list')
    if ($tools -match '(?m)^espanso-prompt-rewriter ') {
        Write-Step 'Uninstalling espanso-prompt-rewriter, the old name of promptmend'
        Assert-Native 'uv' @('tool', 'uninstall', 'espanso-prompt-rewriter')
    }

    # --force replaces an installed promptmend (an upgrade, or a rerun), always at the same
    # launcher path in uv's tool bin folder, which the deployed match files call.
    Write-Step 'Installing promptmend'
    Assert-Native 'uv' @('tool', 'install', '--force', $Spec, '-c', $Constraints)

    Write-Step "Adding uv's tool folder to your PATH"
    Assert-Native 'uv' @('tool', 'update-shell')
    $bin = Read-Native 'uv' @('tool', 'dir', '--bin')
    Update-SessionPath @($bin)
    $cli = Join-Path $bin 'promptmend.exe'
    if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) {
        throw "promptmend.exe is not in $bin; check the uv tool install output above."
    }

    Write-Step 'Checking the install'
    Assert-Native $cli @('--version')
    $code = Invoke-Native $cli @('doctor', '--no-clipboard')
    if ($code -eq 4) {
        Write-Host 'doctor found something to do (expected before the first setup).'
    } elseif ($code -ne 0) {
        throw "promptmend doctor failed with exit code $code."
    }

    Write-Step 'Installed. Next steps (this script runs neither):'
    Write-Host '  promptmend setup             # settings, API key, a test call and the deploy'
    Write-Host '  promptmend espanso deploy    # or only write the Espanso match files'
    Write-Host 'Open a new terminal first if promptmend is not found there.'
    Write-Host "Update: run the same install command again. Uninstall: see $Repo#uninstall"
}

Install-PromptMend
