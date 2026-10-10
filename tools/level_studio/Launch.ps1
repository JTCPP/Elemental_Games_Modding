$ErrorActionPreference = 'Stop'
$studioRoot = $PSScriptRoot
$studioExe = Join-Path $studioRoot 'windows\Azurik Level Studio.exe'
if (Test-Path -LiteralPath $studioExe) {
    Start-Process -FilePath $studioExe -WorkingDirectory $studioRoot
    exit
}
$studioPython = Join-Path $studioRoot '.venv-desktop\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $studioPython)) {
    & py -3 -m venv --system-site-packages (Join-Path $studioRoot '.venv-desktop')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 ou plus récent est requis pour lancer les sources.' }
}
& $studioPython -c "import webview, PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $studioPython -m pip install -r (Join-Path $studioRoot 'requirements-desktop.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Installation des dépendances impossible. Consulte requirements-desktop.txt.' }
}
$studioLogDir = Join-Path $studioRoot 'logs'
New-Item -ItemType Directory -Path $studioLogDir -Force | Out-Null
Start-Process -FilePath $studioPython -ArgumentList ('"' + (Join-Path $studioRoot 'desktop.py') + '"') -WorkingDirectory $studioRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $studioLogDir 'desktop.log') -RedirectStandardError (Join-Path $studioLogDir 'desktop-error.log')
