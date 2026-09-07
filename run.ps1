$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "C:\venvs\p313\Scripts\python.exe"
$entrypoint = Join-Path $projectRoot "pdf_to_jats\main.py"

if (-not (Test-Path -LiteralPath $python)) {
    $fallback = Join-Path $projectRoot "p313\Scripts\python.exe"
    if (Test-Path -LiteralPath $fallback) {
        $python = $fallback
    } else {
        throw "Python interpreter not found: $python "
    }
}

if (-not (Test-Path -LiteralPath $entrypoint)) {
    throw "Application entrypoint not found: $entrypoint"
}

& $python $entrypoint
