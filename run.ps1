$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "C:\venvs\p313\Scripts\python.exe"
$entrypoint = Join-Path $projectRoot "pdf_to_jats\main.py"

# Local override for OpenRouter. Replace the placeholder with your actual key
# or set $env:OPENROUTER_API_KEY before launching this script.
if (-not $env:OPENROUTER_API_KEY) {
    $env:OPENROUTER_API_KEY = "NULL"
}

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
