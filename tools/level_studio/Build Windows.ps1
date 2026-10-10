$ErrorActionPreference = 'Stop'
$studioRoot = $PSScriptRoot
$studioPython = Join-Path $studioRoot '.venv-desktop\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $studioPython)) {
    & py -3 -m venv --system-site-packages (Join-Path $studioRoot '.venv-desktop')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ est requis pour compiler.' }
}
& $studioPython -m pip install -r (Join-Path $studioRoot 'requirements-build.txt')
if ($LASTEXITCODE -ne 0) { throw 'Installation des dépendances de compilation impossible.' }
Push-Location $studioRoot
try {
    & $studioPython -m PyInstaller --noconfirm --distpath dist --workpath build desktop.spec
    if ($LASTEXITCODE -ne 0) { throw 'Compilation de l’exécutable impossible.' }
    $studioWindowsDir = Join-Path $studioRoot 'windows'
    New-Item -ItemType Directory -Path $studioWindowsDir -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $studioRoot 'dist\Azurik Level Studio.exe') -Destination $studioWindowsDir
    & $studioPython (Join-Path $studioRoot 'build_manifest.py')
    if ($LASTEXITCODE -ne 0) { throw 'Vérification de la compilation impossible.' }
} finally { Pop-Location }
