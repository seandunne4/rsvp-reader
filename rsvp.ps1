# Run the rsvp CLI on Windows:  .\rsvp.ps1 <command> [args]
# Keeps Python, the virtualenv and the uv cache outside OneDrive and AppData,
# so they don't sync between machines and aren't redirected by app sandboxes.
$base = Join-Path $HOME '.rsvp-reader'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $base 'python'
$env:UV_PROJECT_ENVIRONMENT = Join-Path $base 'venv-windows'
$env:UV_CACHE_DIR = Join-Path $base 'uv-cache'
$env:UV_PYTHON_PREFERENCE = 'only-managed'
if ($args.Count -gt 0 -and $args[0] -eq 'test') {
    # .\rsvp.ps1 test [pytest args]
    & uv run --project $PSScriptRoot pytest @($args | Select-Object -Skip 1)
} else {
    & uv run --project $PSScriptRoot rsvp @args
}
exit $LASTEXITCODE
