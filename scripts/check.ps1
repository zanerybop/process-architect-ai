param([string]$BrowserExecutable = "", [switch]$WithoutBrowser)
$ErrorActionPreference = 'Stop'
function Invoke-CheckedNative {
    param([string]$Executable, [string[]]$Arguments, [string]$Failure)
    $command = Get-Command $Executable -ErrorAction Stop
    # Windows PowerShell maps native stderr to ErrorRecord when output is redirected.
    # Warnings must remain visible, while the native exit code decides success.
    $global:LASTEXITCODE = $null
    $ErrorActionPreference = 'Continue'
    try {
        & $command @Arguments
        $nativeExitCode = $global:LASTEXITCODE
    } finally { $ErrorActionPreference = 'Stop' }
    if ($null -eq $nativeExitCode -or $nativeExitCode -ne 0) { throw $Failure }
}
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $projectRoot
try {
    Invoke-CheckedNative uv @('run', 'pytest', '-q', '--junitxml=artifacts/pytest.xml') 'Python tests failed'
    Invoke-CheckedNative uv @('run', 'ruff', 'check', 'backend', 'worker', 'tests', 'scripts') 'Python lint failed'
    Invoke-CheckedNative uv @('run', 'python', '-m', 'scripts.compile_fixtures') 'Fixture compiler failed'
    Invoke-CheckedNative uv @('run', 'python', '-m', 'scripts.build_full_bpmn_fixtures') 'Document fixture compiler failed'
    Push-Location frontend
    try {
        Invoke-CheckedNative npm.cmd @('run', 'lint') 'Frontend lint failed'
        Invoke-CheckedNative npm.cmd @('run', 'typecheck') 'Frontend typecheck failed'
        Invoke-CheckedNative npm.cmd @('run', 'test') 'Frontend unit tests failed'
        Invoke-CheckedNative npm.cmd @('run', 'build') 'Frontend build failed'
        if (-not $WithoutBrowser) {
            if ($BrowserExecutable) { $env:PLAYWRIGHT_CHROMIUM_EXECUTABLE = $BrowserExecutable }
            Invoke-CheckedNative npm.cmd @('run', 'test:browser') 'Browser tests failed'
        }
    } finally { Pop-Location }
} finally { Pop-Location }
