param(
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"

$pythonExe = "C:\Users\Nadzilla\AppData\Local\Programs\Python\Python314\python.exe"

if (-not (Test-Path $pythonExe)) {
    throw "Python was not found at $pythonExe"
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$iconPath = Join-Path $projectRoot "assets\task_manager_icon.ico"
$versionInfoPath = Join-Path $projectRoot "version_info.txt"
$iconGeneratorPath = Join-Path $projectRoot "generate_app_icon.ps1"

if (-not (Test-Path $iconPath) -and (Test-Path $iconGeneratorPath)) {
    & $iconGeneratorPath
}

$arguments = @(
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name",
    "TaskManagerClone",
    "--icon",
    "assets\\task_manager_icon.ico",
    "--version-file",
    "version_info.txt",
    "--add-data",
    "styles;styles",
    "--add-data",
    "assets;assets",
    "main.py"
)

if ($OneFile) {
    $arguments += "--onefile"
}

Push-Location $projectRoot
try {
    & $pythonExe @arguments
}
finally {
    Pop-Location
}
