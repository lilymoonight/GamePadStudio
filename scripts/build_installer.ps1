$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
$compiler = Join-Path (Get-Location) '.tools\InnoSetup\ISCC.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Install Inno Setup 6 in .tools\InnoSetup first.' }
& $compiler 'installer\GamePadStudio.iss'
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
