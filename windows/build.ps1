$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
python -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
python -m PyInstaller --noconfirm --clean --onefile --windowed --name NBT-AAD-PC --recursive-copy-metadata paramiko --add-data '..\nbt-aad.sh;.' main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
$smoke = Start-Process -FilePath '.\dist\NBT-AAD-PC.exe' -ArgumentList '--self-test' -PassThru
if (-not $smoke.WaitForExit(60000)) { $smoke.Kill(); throw 'Frozen self-test timed out' }
if ($smoke.ExitCode -ne 0) { throw 'Frozen self-test failed' }
Write-Host 'Ready: windows\dist\NBT-AAD-PC.exe'
