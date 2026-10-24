param([ValidateSet('Prepare','Launch')][string]$Mode = 'Prepare', [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$taskProject = Split-Path -Parent $PSScriptRoot
$taskLocal = Join-Path $taskProject '.local'
$taskDownloads = Join-Path $taskProject '.downloads'
$taskManifestPath = Join-Path $PSScriptRoot 'download_manifest.json'
$taskManifest = Get-Content -LiteralPath $taskManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

function Test-ReaderPython([string]$Candidate) {
    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { return $false }
    $taskVersion = & $Candidate -c "import sys,struct,tkinter; print('OK' if sys.version_info[:2]==(3,12) and struct.calcsize('P')==8 else 'NO')" 2>$null
    return $LASTEXITCODE -eq 0 -and $taskVersion -eq 'OK'
}

function Get-ConfiguredPython {
    $taskConfig = Join-Path $taskLocal 'install.json'
    if (Test-Path -LiteralPath $taskConfig) {
        $taskInstall = Get-Content -LiteralPath $taskConfig -Raw -Encoding UTF8 | ConvertFrom-Json
        if (Test-ReaderPython $taskInstall.runtime_python) { return $taskInstall.runtime_python }
    }
    $taskOwnRuntime = Join-Path $taskProject '.runtime\Scripts\python.exe'
    if (Test-ReaderPython $taskOwnRuntime) { return $taskOwnRuntime }
    return $null
}

function Get-BasePython {
    $taskCandidates = @((Join-Path $taskProject '.python\python.exe'))
    foreach ($taskKey in @('HKCU:\Software\Python\PythonCore\3.12\InstallPath', 'HKLM:\Software\Python\PythonCore\3.12\InstallPath')) {
        if (Test-Path -LiteralPath $taskKey) {
            $taskValue = Get-ItemProperty -LiteralPath $taskKey
            if ($taskValue.ExecutablePath) { $taskCandidates += $taskValue.ExecutablePath }
            elseif ($taskValue.'(default)') { $taskCandidates += Join-Path $taskValue.'(default)' 'python.exe' }
        }
    }
    $taskLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($taskLauncher) {
        $taskDetected = & $taskLauncher.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0) { $taskCandidates += $taskDetected }
    }
    foreach ($taskCandidate in $taskCandidates) { if (Test-ReaderPython $taskCandidate) { return $taskCandidate } }
    return $null
}

function Install-BasePython {
    $taskItem = $taskManifest.files | Where-Object { $_.path -like '*/python-3.12.10-amd64.exe' } | Select-Object -First 1
    if (-not $taskItem) { throw 'Python installer is missing from manifest.' }
    $taskInstaller = Join-Path $taskDownloads $taskItem.path
    New-Item -ItemType Directory -Path (Split-Path -Parent $taskInstaller) -Force | Out-Null
    if (Test-Path -LiteralPath $taskInstaller) {
        if ((Get-FileHash -LiteralPath $taskInstaller -Algorithm SHA256).Hash -ne $taskItem.sha256) {
            throw 'Existing Python installer failed verification. Rename it and retry; it has been preserved.'
        }
    } else {
        $taskPart = $taskInstaller + '.part'
        Write-Host 'Preparing the local Python runtime (about 26 MB)...'
        & curl.exe --fail --location --continue-at - --connect-timeout 20 --speed-limit 1024 --speed-time 90 --max-time 7200 --retry 2 --output $taskPart $taskItem.urls[0]
        if ($LASTEXITCODE -ne 0) { throw 'Python download failed or timed out. Partial download was preserved. Run this script again.' }
        if ((Get-Item -LiteralPath $taskPart).Length -ne $taskItem.size -or (Get-FileHash -LiteralPath $taskPart -Algorithm SHA256).Hash -ne $taskItem.sha256) {
            throw 'Python installer size/hash verification failed. Partial file was preserved.'
        }
        Move-Item -LiteralPath $taskPart -Destination $taskInstaller
    }
    $taskBaseDirectory = Join-Path $taskProject '.python'
    $taskArguments = @('/quiet', 'InstallAllUsers=0', ('TargetDir="' + $taskBaseDirectory + '"'),
        'PrependPath=0', 'AppendPath=0', 'Include_launcher=0', 'InstallLauncherAllUsers=0',
        'AssociateFiles=0', 'Shortcuts=0', 'Include_test=0', 'Include_doc=0', 'Include_tools=0',
        'Include_dev=0', 'Include_tcltk=1', 'Include_pip=1')
    $taskProcess = Start-Process -FilePath $taskInstaller -ArgumentList $taskArguments -PassThru -WindowStyle Hidden
    if (-not $taskProcess.WaitForExit(300000)) { throw 'Python installation timed out. Check the installer before retrying.' }
    $taskProcess.Refresh()
    if ($taskProcess.ExitCode -notin @(0,3010)) { throw ('Python installation failed: ' + $taskProcess.ExitCode) }
    $taskPython = Join-Path $taskBaseDirectory 'python.exe'
    if (-not (Test-ReaderPython $taskPython)) { throw 'The new Python runtime did not pass its startup check.' }
    return $taskPython
}

function New-ReaderShortcut([string]$Python) {
    $taskDesktop = [Environment]::GetFolderPath('Desktop')
    $taskShell = New-Object -ComObject WScript.Shell
    $taskLink = $taskShell.CreateShortcut((Join-Path $taskDesktop '聆笺 JianVox.lnk'))
    $taskLink.TargetPath = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
    $taskLink.Arguments = '"' + (Join-Path $taskProject 'app\reader_app.py') + '"'
    $taskLink.WorkingDirectory = $taskProject
    $taskLink.Description = 'JianVox - Offline TTS reader with customizable hotkeys'
    $taskLink.IconLocation = Join-Path $taskProject 'assets\app.ico'
    $taskLink.Save()
}

try {
    $taskPython = Get-ConfiguredPython
    if ($CheckOnly) {
        [pscustomobject]@{ Project=$taskProject; ConfiguredPython=$taskPython; BasePython=(Get-BasePython); Files=$taskManifest.files.Count; NetworkUsed=$false } | ConvertTo-Json
        exit 0
    }
    $taskConfig = Join-Path $taskLocal 'install.json'
    if (Test-Path -LiteralPath $taskConfig) {
        $taskInstall = Get-Content -LiteralPath $taskConfig -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($taskInstall.downloads_path) { $taskDownloads = $taskInstall.downloads_path }
    }
    if ($Mode -eq 'Prepare') {
        if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
        if (-not (Test-Path -LiteralPath (Join-Path $env:WINDIR 'System32\msvcp140.dll'))) {
            throw 'Microsoft Visual C++ x64 runtime is missing. Install the official package first: https://aka.ms/vc14/vc_redist.x64.exe'
        }
        if (-not (Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue)) {
            throw 'This build requires an NVIDIA GPU and driver. RTX 3060 12GB is the tested configuration.'
        }
        if (-not $taskPython) {
            $taskBase = Get-BasePython
            if (-not $taskBase) { $taskBase = Install-BasePython }
            Write-Host 'Creating a private runtime...'
            & $taskBase -m venv (Join-Path $taskProject '.runtime')
            if ($LASTEXITCODE -ne 0) { throw 'Could not create a private runtime.' }
            $taskPython = Join-Path $taskProject '.runtime\Scripts\python.exe'
        }
        Write-Host 'Opening the domestic-source downloader...'
        & $taskPython (Join-Path $PSScriptRoot 'download_tts.py') --root $taskDownloads
        if ($LASTEXITCODE -eq 3) { Write-Host 'Paused. Run Prepare again to resume.'; exit 0 }
        if ($LASTEXITCODE -ne 0) { throw 'Download failed. Completed files have been preserved.' }
        Write-Host 'Verifying and installing from local files. No online pip access...'
        & $taskPython (Join-Path $PSScriptRoot 'setup_offline.py') --downloads $taskDownloads
        if ($LASTEXITCODE -ne 0) { throw 'Offline setup failed. Review the error above; all downloads were preserved.' }
        New-ReaderShortcut $taskPython
    }
    if (-not $taskPython) { throw 'Run the Prepare script first.' }
    $taskPythonW = Join-Path (Split-Path -Parent $taskPython) 'pythonw.exe'
    Start-Process -FilePath $taskPythonW -ArgumentList ('"' + (Join-Path $taskProject 'app\reader_app.py') + '"') -WorkingDirectory $taskProject -WindowStyle Hidden
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    if (-not $CheckOnly) { Read-Host 'Press Enter to close (files are preserved)' }
    exit 1
}
