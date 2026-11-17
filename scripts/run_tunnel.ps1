# Reveal-X's public address, through Cloudflare Tunnel. Run it beside
# scripts\run_windows_server.ps1, which serves the app on 127.0.0.1:5000.
#
# With a git-ignored .tunnel-token file in the project root (the token
# Cloudflare shows when you create a tunnel in its dashboard), this runs that
# named tunnel: a fixed hostname on your own domain, set in the dashboard.
# Without the file it runs a quick tunnel: a random trycloudflare.com URL that
# changes every run.
#
# The token reaches cloudflared through TUNNEL_TOKEN, not the command line, so
# it does not show in the process list. The named tunnel is given this
# project's own config file, so a ~/.cloudflared/config.yml that belongs to
# another tunnel on this PC is neither read nor changed.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$exe = Get-ChildItem (Join-Path $root '.vendor-dl') -Filter 'cloudflared.exe' -Recurse -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $exe) {
    $found = Get-Command cloudflared -ErrorAction SilentlyContinue
    if (-not $found) { throw 'cloudflared.exe not found in .vendor-dl or on PATH.' }
    $exe = $found.Source
}

$tokenFile = Join-Path $root '.tunnel-token'
if (Test-Path $tokenFile) {
    $env:TUNNEL_TOKEN = (Get-Content $tokenFile -Raw).Trim()
    $config = Join-Path $PSScriptRoot 'cloudflared-revealx.yml'
    Write-Host 'Named tunnel (fixed address; the hostname is set in the Cloudflare dashboard).'
    & $exe --config $config tunnel run
} else {
    Write-Host 'No .tunnel-token: quick tunnel, random URL that changes every run.'
    & $exe tunnel --no-autoupdate --url http://127.0.0.1:5000
}
