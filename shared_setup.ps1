param([string]$SharedRoot = "")

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PointerFile = Join-Path $AppRoot "shared_components_path.txt"
$RuntimeFile = Join-Path $AppRoot "runtime_path.txt"

function Select-SharedRoot {
    param([string]$Requested)
    if ($Requested) { return [IO.Path]::GetFullPath($Requested) }
    if (Test-Path $PointerFile) {
        $saved = (Get-Content $PointerFile -Raw).Trim()
        if ($saved) { return [IO.Path]::GetFullPath($saved) }
    }
    if ($env:OVERLAY_STUDIO_SHARED_COMPONENTS) {
        return [IO.Path]::GetFullPath($env:OVERLAY_STUDIO_SHARED_COMPONENTS)
    }
    if ($env:MUZ_SHARED_COMPONENTS) {
        return [IO.Path]::GetFullPath($env:MUZ_SHARED_COMPONENTS)
    }
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = "Choose a drive/folder for reusable app components"
        $dialog.ShowNewFolderButton = $true
        if ($dialog.ShowDialog() -eq "OK") {
            if ((Split-Path $dialog.SelectedPath -Leaf) -ieq "Shared_Components") {
                return $dialog.SelectedPath
            }
            return Join-Path $dialog.SelectedPath "Shared_Components"
        }
    } catch {}
    return Join-Path $env:LOCALAPPDATA "OverlayTextStudio\Shared_Components"
}

$SharedRoot = Select-SharedRoot $SharedRoot
$Downloads = Join-Path $SharedRoot "Downloads"
$PythonHome = Join-Path $SharedRoot "Python312"
$FFmpegHome = Join-Path $SharedRoot "FFmpeg"
$PipCache = Join-Path $SharedRoot "PipCache"
$WheelRoot = Join-Path $SharedRoot "Wheelhouse"
$LogDir = Join-Path $SharedRoot "Logs\OverlayTextStudio"
@($SharedRoot, $Downloads, $PipCache, $WheelRoot, $LogDir) | ForEach-Object {
    New-Item -ItemType Directory -Force -Path $_ | Out-Null
}
$LogFile = Join-Path $LogDir ("setup_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

function Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

function Test-Python([string]$Exe) {
    if (-not (Test-Path $Exe -PathType Leaf)) { return $false }
    & $Exe -c "import sys; raise SystemExit(0 if (3,11) <= sys.version_info[:2] <= (3,13) and sys.maxsize > 2**32 else 1)" 2>$null
    return $LASTEXITCODE -eq 0
}

function Resolve-SystemPython {
    foreach ($spec in @("3.12", "3.11", "3.13")) {
        try {
            $candidate = (& py "-$spec" -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1).Trim()
            if (Test-Python $candidate) { return $candidate }
        } catch {}
    }
    try {
        $candidate = (& python -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1).Trim()
        if (Test-Python $candidate) { return $candidate }
    } catch {}
    return ""
}

function Download-File([string]$Url, [string]$Destination) {
    $partial = "$Destination.partial"
    Log "Downloading $Url"
    Invoke-WebRequest -Uri $Url -OutFile $partial -UseBasicParsing
    Move-Item -Force $partial $Destination
}

function Test-FFmpegPair([string]$FFmpeg, [string]$FFprobe) {
    if (-not (Test-Path $FFmpeg -PathType Leaf) -or -not (Test-Path $FFprobe -PathType Leaf)) {
        return $false
    }
    & $FFmpeg -version *> $null
    if ($LASTEXITCODE -ne 0) { return $false }
    & $FFprobe -version *> $null
    return $LASTEXITCODE -eq 0
}

Log "Shared component root: $SharedRoot"
[IO.File]::WriteAllText($PointerFile, $SharedRoot, $Utf8NoBom)

# 1) Installed Python, 2) cached installer, 3) fresh download.
$PythonExe = Join-Path $PythonHome "python.exe"
if (Test-Python $PythonExe) {
    Log "Python already installed in shared space; reusing it."
} else {
    $systemPython = Resolve-SystemPython
    if ($systemPython) {
        $PythonExe = $systemPython
        Log "Compatible Python already installed on Windows; reusing $PythonExe."
    } else {
        $cachedPython = Get-ChildItem $Downloads -Filter "python-3.12*-amd64.exe" -File -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
        if (-not $cachedPython) {
            $pythonInstaller = Join-Path $Downloads "python-3.12.10-amd64.exe"
            Download-File "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe" $pythonInstaller
            $cachedPython = Get-Item $pythonInstaller
        } else {
            Log "Python is not installed; using cached installer $($cachedPython.FullName)."
        }
        $signature = Get-AuthenticodeSignature $cachedPython.FullName
        if ($signature.Status -ne "Valid") {
            throw "Cached Python installer signature is not valid: $($signature.Status)"
        }
        New-Item -ItemType Directory -Force -Path $PythonHome | Out-Null
        Log "Installing cached Python into shared space (no admin)."
        $installArgs = '/quiet InstallAllUsers=0 PrependPath=0 Include_launcher=0 ' +
            'Include_test=0 SimpleInstall=1 TargetDir="' + $PythonHome + '"'
        $process = Start-Process -FilePath $cachedPython.FullName -ArgumentList $installArgs -Wait -PassThru
        if ($process.ExitCode -ne 0 -or -not (Test-Python $PythonExe)) {
            throw "Python installation failed with exit code $($process.ExitCode)."
        }
    }
}

# FFmpeg uses the same precedence and requires both ffmpeg and ffprobe.
$FFmpegExe = Join-Path $FFmpegHome "bin\ffmpeg.exe"
$FFprobeExe = Join-Path $FFmpegHome "bin\ffprobe.exe"
$systemFFmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
$systemFFprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
$sharedFFmpegReady = Test-FFmpegPair $FFmpegExe $FFprobeExe
$systemFFmpegReady = $false
if ($systemFFmpeg -and $systemFFprobe) {
    $systemFFmpegReady = Test-FFmpegPair $systemFFmpeg.Source $systemFFprobe.Source
}
if ($sharedFFmpegReady) {
    Log "FFmpeg and FFprobe already installed in shared space; reusing them."
} elseif ($systemFFmpegReady) {
    Log "FFmpeg and FFprobe already installed on Windows PATH; reusing them."
} else {
    $cachedFFmpeg = Get-ChildItem $Downloads -Filter "ffmpeg*.zip" -File -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
    if (-not $cachedFFmpeg) {
        $ffmpegZip = Join-Path $Downloads "ffmpeg-release-essentials.zip"
        Download-File "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" $ffmpegZip
        $cachedFFmpeg = Get-Item $ffmpegZip
    } else {
        Log "FFmpeg is not installed; using cached archive $($cachedFFmpeg.FullName)."
    }
    Log "FFmpeg archive SHA256: $((Get-FileHash $cachedFFmpeg.FullName -Algorithm SHA256).Hash)"
    $extractRoot = Join-Path $SharedRoot ("Temp\ffmpeg_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
    Expand-Archive -Path $cachedFFmpeg.FullName -DestinationPath $extractRoot -Force
    $foundFFmpeg = Get-ChildItem $extractRoot -Filter ffmpeg.exe -File -Recurse | Select-Object -First 1
    $foundFFprobe = Get-ChildItem $extractRoot -Filter ffprobe.exe -File -Recurse | Select-Object -First 1
    if (-not $foundFFmpeg -or -not $foundFFprobe) { throw "Cached FFmpeg archive is incomplete." }
    $sourceHome = Split-Path -Parent (Split-Path -Parent $foundFFmpeg.FullName)
    if (Test-Path $FFmpegHome) {
        Move-Item $FFmpegHome ($FFmpegHome + "_broken_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
    }
    Move-Item $sourceHome $FFmpegHome
    if (-not (Test-FFmpegPair $FFmpegExe $FFprobeExe)) {
        throw "FFmpeg extraction did not produce ffmpeg.exe and ffprobe.exe."
    }
    & $FFmpegExe -version | Select-Object -First 1 | ForEach-Object { Log $_ }
    Remove-Item $extractRoot -Recurse -Force -ErrorAction SilentlyContinue
}

# Requirements-hash runtime: installed -> cached wheelhouse -> download once.
$RequirementFile = Join-Path $AppRoot "requirements-lock.txt"
if (-not (Test-Path $RequirementFile)) {
    $RequirementFile = Join-Path $AppRoot "requirements.txt"
}
$RequirementHash = (Get-FileHash $RequirementFile -Algorithm SHA256).Hash.ToLower()
$PythonTag = (& $PythonExe -c "import sys; print(f'py{sys.version_info.major}{sys.version_info.minor}')").Trim()
$Wheelhouse = Join-Path $WheelRoot ("$PythonTag-$RequirementHash")
$RuntimeRoot = Join-Path $SharedRoot ("Environments\$PythonTag-$RequirementHash")
$Marker = Join-Path $RuntimeRoot "overlay_requirements.sha256"
$RuntimePython = Join-Path $RuntimeRoot "Scripts\python.exe"
$runtimeReady = $false
$runtimePythonHealthy = Test-Python $RuntimePython
if ((Test-Path $RuntimePython) -and $runtimePythonHealthy -and (Test-Path $Marker)) {
    $savedHash = (Get-Content $Marker -Raw).Trim()
    if ($savedHash -eq $RequirementHash) {
        & $RuntimePython -c "import streamlit,pandas,numpy,PIL,openpyxl,cv2" 2>$null
        if ($LASTEXITCODE -eq 0) {
            & $RuntimePython -m pip check *> $null
            $runtimeReady = $LASTEXITCODE -eq 0
        }
    }
}

if ($runtimeReady) {
    Log "Overlay runtime already installed and healthy; package install skipped."
} else {
    if ((Test-Path $RuntimePython) -and (-not $runtimePythonHealthy)) {
        $brokenRuntime = $RuntimeRoot + "_broken_" + (Get-Date -Format "yyyyMMdd_HHmmss")
        Log "Existing shared runtime is incompatible; moving it to $brokenRuntime."
        Move-Item $RuntimeRoot $brokenRuntime
    }
    if (-not (Test-Path $RuntimePython)) {
        Log "Creating Overlay runtime in shared space."
        & $PythonExe -m venv $RuntimeRoot
        if ($LASTEXITCODE -ne 0) { throw "Could not create shared runtime." }
    }
    $env:PIP_CACHE_DIR = $PipCache
    $wheelReady = Test-Path (Join-Path $Wheelhouse ".download_complete")
    if ($wheelReady) {
        Log "Dependencies already downloaded; installing from shared wheelhouse without internet."
    } else {
        New-Item -ItemType Directory -Force -Path $Wheelhouse | Out-Null
        Log "Dependencies are neither installed nor cached; downloading once to shared wheelhouse."
        & $RuntimePython -m pip download -r $RequirementFile --dest $Wheelhouse
        if ($LASTEXITCODE -ne 0) { throw "Dependency download failed." }
        Set-Content (Join-Path $Wheelhouse ".download_complete") $RequirementHash -Encoding ASCII
    }
    Log "Installing dependencies from shared wheelhouse."
    & $RuntimePython -m pip install --no-index --find-links $Wheelhouse -r $RequirementFile
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    & $RuntimePython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Installed dependency check failed." }
    Set-Content $Marker $RequirementHash -Encoding ASCII
}

[IO.File]::WriteAllText($RuntimeFile, $RuntimeRoot, $Utf8NoBom)
Log "Setup complete. Future runs work offline and reuse: $SharedRoot"
Write-Host ""
Write-Host "Setup complete. Run START_APP.bat."
