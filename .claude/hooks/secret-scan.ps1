# PreToolUse secret scanner for Write/Edit/MultiEdit.
#
# Reads the tool-call JSON on stdin, inspects the content about to be written,
# and blocks (exit 2) if it looks like a hardcoded secret is being committed to
# a tracked source file. Fails OPEN on any parse/IO error so it can never wedge
# the session. Skips files where secrets legitimately live or that are not
# tracked by git (.env*, .claude/settings.local.json).
#
# Exit codes (Claude Code hook contract):
#   0 -> allow
#   2 -> block, stderr is shown to the model

$ErrorActionPreference = 'Stop'

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }
    $payload = $raw | ConvertFrom-Json
} catch {
    exit 0  # fail open — never block on a malformed payload
}

$ti = $payload.tool_input
if ($null -eq $ti) { exit 0 }

$path = [string]$ti.file_path
if ([string]::IsNullOrWhiteSpace($path)) { exit 0 }

$lower = $path.ToLower()
# .env / *.env / .env.* are gitignored; .env.example holds placeholders;
# settings.local.json is machine-local and never committed.
if ($lower -match '(^|[\\/])\.env($|\.|[\\/])' -or
    $lower -match 'settings\.local\.json$') {
    exit 0
}

# Gather the new content: Write -> content, Edit -> new_string,
# MultiEdit -> edits[].new_string.
$content = ''
if ($ti.content)    { $content += [string]$ti.content }
if ($ti.new_string) { $content += "`n" + [string]$ti.new_string }
if ($ti.edits) {
    foreach ($e in $ti.edits) {
        if ($e.new_string) { $content += "`n" + [string]$e.new_string }
    }
}
if ([string]::IsNullOrWhiteSpace($content)) { exit 0 }

$patterns = @(
    @{ name = 'Discord bot token';   rx = '[MNO][A-Za-z\d]{23}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,}' },
    @{ name = 'AWS access key id';   rx = 'AKIA[0-9A-Z]{16}' },
    @{ name = 'Private key block';   rx = '-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----' },
    @{ name = 'Hardcoded password';  rx = '(?i)(?:password|passwd|pwd)\s*[:=]\s*["''][^"''\s]{6,}["'']' },
    @{ name = 'DB URL w/ password';  rx = '(?i)(?:postgres(?:ql)?|mysql|mariadb|mongodb)(?:\+\w+)?://[^:@/\s]+:[^@/\s]+@' },
    @{ name = 'API key / token';     rx = '(?i)(?:secret|api[_-]?key|access[_-]?token|auth[_-]?token)\s*[:=]\s*["''][^"''\s]{12,}["'']' }
)

$hits = @()
foreach ($p in $patterns) {
    if ($content -match $p.rx) { $hits += $p.name }
}

if ($hits.Count -gt 0) {
    $msg = "[secret-scan] BLOCKED write to $path - possible hardcoded secret(s): " +
           ($hits -join ', ') + ". " +
           "Load secrets from environment variables / .env (gitignored) instead, e.g. os.environ[...]. " +
           "If this is a false positive, store the literal in .env or another scanned-exempt path."
    [Console]::Error.WriteLine($msg)
    exit 2
}

exit 0
