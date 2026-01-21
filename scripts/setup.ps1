Write-Host "Setting up Python dev environment..."

$py312 = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
$python = if (Test-Path $py312) { $py312 } else { 'python' }

& $python -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) {
  Write-Error "ensurepip failed. Install full Python distribution or run get-pip.py."
  exit 1
}

& $python -m venv .venv
if ($LASTEXITCODE -ne 0) {
  Write-Error "venv creation failed."
  exit 1
}

& .\.venv\Scripts\Activate.ps1
if ($LASTEXITCODE -ne 0) {
  Write-Error "venv activation failed."
  exit 1
}

& .\.venv\Scripts\python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
  Write-Error "pip upgrade failed."
  exit 1
}

pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
  Write-Error "dependency install failed."
  exit 1
}

Write-Host "Setup complete."
