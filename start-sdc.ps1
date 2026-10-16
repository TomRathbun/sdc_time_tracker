# Start the tracker. Mail settings live in smtp.local.ps1 and are not printed.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$mail = Join-Path $PSScriptRoot "smtp.local.ps1"
if (Test-Path -LiteralPath $mail) {
    . $mail
}

if ([string]::IsNullOrWhiteSpace($env:SMTP_HOST)) {
    Write-Host "Mail: off"
} else {
    Write-Host "Mail: on"
}

uv run python run.py --no-ssl
