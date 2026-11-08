param(
    [Parameter(Mandatory=$true)][string]$Destination,
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$Revision = '9a8f82f8b8701dfa4facc4dc2b3ffbf1eb2dfe5e'
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Use a full immutable commit SHA' }
$target = [IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $target) { throw 'Destination must not exist; never reuse a habitual environment' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Explicit Python executable missing' }
function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit $LASTEXITCODE" }
}
$names = @('PIP_CACHE_DIR','PIP_CONFIG_FILE','PYTHONNOUSERSITE','TEMP','TMP')
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name,'Process') }
try {
    New-Item -ItemType Directory -Path $target | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $target 'tmp') | Out-Null
    $env:PIP_CACHE_DIR = Join-Path $target 'pip-cache'
    $env:PIP_CONFIG_FILE = 'NUL'
    $env:PYTHONNOUSERSITE = '1'
    $env:TEMP = Join-Path $target 'tmp'
    $env:TMP = $env:TEMP
    Invoke-Checked $Python @('-I','-c','import sys,struct; assert sys.version_info[:3] == (3,13,14) and struct.calcsize("P")==8')
    $checkout = Join-Path $target 'checkout'
    Invoke-Checked 'git' @('clone','--no-checkout','https://github.com/JARVISS-project/Jarvis.git',$checkout)
    Invoke-Checked 'git' @('-C',$checkout,'checkout','--detach',$Revision)
    $actual = (& git -C $checkout rev-parse HEAD).Trim()
    if ($actual -ne $Revision) { throw 'Commit mismatch' }
    $venv = Join-Path $target 'venv'
    Invoke-Checked $Python @('-I','-m','venv',$venv)
    $pythonNew = Join-Path $venv 'Scripts/python.exe'
    Push-Location $checkout
    try {
        Invoke-Checked $pythonNew @('-I','-m','pip','install','--index-url','https://pypi.org/simple','--require-hashes','--only-binary=:all:','-r','requirements/windows-py313/build.lock','-r','requirements/windows-py313/mvp.lock','--report',(Join-Path $target 'installation.json'))
        Invoke-Checked $pythonNew @('-I','-m','pip','install','--no-deps','--no-build-isolation','-e','.[dev,voice,fast-voice,ui,windows-media]')
    } finally { Pop-Location }
    $checks = Join-Path $PSScriptRoot 'checks.py'
    Invoke-Checked $pythonNew @('-I',$checks,'--checkout',$checkout)
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name,$previous[$name],'Process') }
}
