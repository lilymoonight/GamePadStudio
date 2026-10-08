$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path -LiteralPath 'bin\ffmpeg.exe')) {
    try {
        & .\.venv\Scripts\python.exe -c "import imageio_ffmpeg, shutil, os; os.makedirs('bin', exist_ok=True); shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), 'bin/ffmpeg.exe')"
    } catch {
        Write-Warning 'bin\ffmpeg.exe not found and imageio_ffmpeg bootstrap skipped; PyInstaller will check imageio_ffmpeg directly.'
    }
}
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm GamePadStudio.spec
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Write-Host 'Ready: dist\GamePadStudio\GamePadStudio.exe'
