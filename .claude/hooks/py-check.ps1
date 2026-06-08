# PostToolUse syntax gate for Python edits.
#
# After a Write/Edit/MultiEdit touches a .py file under src/, tests/, scripts/
# (or main.py), byte-compile just that file with `python -m py_compile`. This is
# near-instant and catches syntax errors the moment they are introduced, without
# the cost of running the whole suite on every edit (CI runs the full suite).
#
# Exit 2 feeds stderr back to the model so it can fix the break immediately.
# Fails OPEN on any parse/IO error.

$ErrorActionPreference = 'Stop'

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }
    $payload = $raw | ConvertFrom-Json
} catch {
    exit 0
}

$path = [string]$payload.tool_input.file_path
if ([string]::IsNullOrWhiteSpace($path)) { exit 0 }
if ($path -notmatch '\.py$') { exit 0 }
if (-not (Test-Path -LiteralPath $path)) { exit 0 }

# Only gate project source / tests / scripts / main.py.
if ($path -notmatch '(?i)[\\/](?:src|tests|scripts)[\\/]' -and
    $path -notmatch '(?i)[\\/]main\.py$') {
    exit 0
}

# Prefer the active virtualenv's interpreter when present.
$py = 'python'
if ($env:VIRTUAL_ENV) {
    $candidate = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate) { $py = $candidate }
}

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

# Use Start-Process with redirected output files rather than `& $py ... 2>&1`.
# In Windows PowerShell 5.1, piping a native exe's stderr generates
# NativeCommandError records (and can terminate under EAP=Stop) which would mask
# py_compile's real exit code. Start-Process avoids that and yields a clean ExitCode.
$tmpOut = [System.IO.Path]::GetTempFileName()
$tmpErr = [System.IO.Path]::GetTempFileName()
try {
    $proc = Start-Process -FilePath $py `
        -ArgumentList @('-m', 'py_compile', "`"$path`"") `
        -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $tmpOut -RedirectStandardError $tmpErr
    $code = $proc.ExitCode
    $detail = (Get-Content -LiteralPath $tmpErr -Raw -ErrorAction SilentlyContinue)
} finally {
    Remove-Item -LiteralPath $tmpOut, $tmpErr -Force -ErrorAction SilentlyContinue
}

if ($code -ne 0) {
    [Console]::Error.WriteLine("[py-check] Syntax error introduced in ${path}:`n$detail")
    exit 2
}

exit 0
