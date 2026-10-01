$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path -LiteralPath 'bin\ffmpeg.exe')) { throw 'Missing bundled FFmpeg: bin\ffmpeg.exe' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm GamePadStudio.spec
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Write-Host 'Ready: dist\GamePadStudio\GamePadStudio.exe'
