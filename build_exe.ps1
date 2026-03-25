param(
    [switch]$OneFile,
    [switch]$SelfSign,
    [switch]$TrustSelfSigned
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
$manifestPath = Join-Path $projectRoot "app.manifest"
$signScriptPath = Join-Path $projectRoot "sign_exe.ps1"

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
    "--manifest",
    "app.manifest",
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

    if ($SelfSign) {
        if (-not (Test-Path $signScriptPath)) {
            throw "Self-sign script not found: $signScriptPath"
        }

        $outputExe = if ($OneFile) {
            Join-Path $projectRoot "dist\TaskManagerClone.exe"
        }
        else {
            Join-Path $projectRoot "dist\TaskManagerClone\TaskManagerClone.exe"
        }

        $signArgs = @{
            Path = $outputExe
        }
        if ($TrustSelfSigned) {
            $signArgs.TrustLocally = $true
        }
        & $signScriptPath @signArgs
    }
}
finally {
    Pop-Location
}
