param(
    [switch]$PreserveProvisioning = $true,
    [switch]$ResetConfig,
    [switch]$SkipReset,
    [string]$Port,
    [switch]$DisconnectRepl = $true,
    [switch]$ReconnectRepl = $true
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$srcDir = Join-Path $projectRoot 'src'
$buildDir = Join-Path $projectRoot 'build'
$deployStart = Get-Date

$MpremoteCommand = $null
$SelectedPort = $null

function Invoke-VSCodeCommandBestEffort {
    param([string]$CommandId)

    $codeCmd = Get-Command code -ErrorAction SilentlyContinue
    if (-not $codeCmd) {
        return $false
    }

    try {
        & code --command $CommandId 2>$null | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Disconnect-MicroPicoReplBestEffort {
    if (-not $DisconnectRepl) {
        return
    }

    Write-Host 'Attempting to disconnect MicroPico vREPL (best-effort)...'
    $attempted = $false
    foreach ($cmd in @('micropico.disconnect', 'microPico.disconnect')) {
        $attempted = $attempted -or (Invoke-VSCodeCommandBestEffort -CommandId $cmd)
    }

    if (-not $attempted) {
        Write-Host 'Could not auto-disconnect via VS Code command. Continuing with mpremote retries.'
    }
}

function Reconnect-MicroPicoReplBestEffort {
    if (-not $ReconnectRepl) {
        return
    }

    Write-Host 'Attempting to reconnect MicroPico vREPL (best-effort)...'
    foreach ($cmd in @('micropico.connect', 'microPico.connect')) {
        if (Invoke-VSCodeCommandBestEffort -CommandId $cmd) {
            break
        }
    }
}

function Resolve-MpremoteCommand {
    if (Get-Command mpremote -ErrorAction SilentlyContinue) {
        return @('mpremote')
    }

    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -m mpremote --help 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return @('py', '-m', 'mpremote')
        }
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m mpremote --help 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return @('python', '-m', 'mpremote')
        }
    }

    throw 'mpremote is not available. Install with: py -m pip install --user mpremote'
}

function Get-MpremoteConnectListOutput {
    $cmd = @($MpremoteCommand + @('connect', 'list'))
    $output = & $cmd[0] $cmd[1..($cmd.Count - 1)] 2>&1
    if ($LASTEXITCODE -ne 0) {
        return @()
    }
    return @($output)
}

function Resolve-TargetPort {
    param([string]$RequestedPort)

    if ($RequestedPort) {
        return $RequestedPort
    }

    $lines = Get-MpremoteConnectListOutput
    foreach ($line in $lines) {
        if ($line -match '^(COM\d+)\s+.*2e8a:0005') {
            return $Matches[1]
        }
    }

    foreach ($line in $lines) {
        if ($line -match '^(COM\d+)\s+') {
            return $Matches[1]
        }
    }

    return $null
}

function Invoke-MpRemote {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args,
        [switch]$AllowFailure
    )

    $cmd = @($MpremoteCommand)
    if ($SelectedPort) {
        $cmd += @('connect', $SelectedPort)
    }
    $cmd += $Args

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = & $cmd[0] $cmd[1..($cmd.Count - 1)] 2>&1
    $ErrorActionPreference = $previousErrorActionPreference
    if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) {
        throw "mpremote failed: $($cmd -join ' ')`n$output"
    }
    return @($output)
}

function Test-RemotePathExists {
    param([string]$RemotePath)

    Invoke-MpRemote -Args @('fs', 'ls', $RemotePath) -AllowFailure | Out-Null
    return $LASTEXITCODE -eq 0
}

function Remove-RemoteFileIfExists {
    param([string]$RemotePath)
    try {
        Invoke-MpRemote -Args @('fs', 'rm', $RemotePath) -AllowFailure | Out-Null
    }
    catch {
        # ignored
    }
}

function Remove-RemoteDirIfExists {
    param([string]$RemotePath)
    try {
        Invoke-MpRemote -Args @('fs', 'rm', '-r', $RemotePath) -AllowFailure | Out-Null
    }
    catch {
        # ignored
    }
}

function Assert-DeviceReachable {
    param([int]$Attempts = 4)

    for ($i = 1; $i -le $Attempts; $i++) {
        try {
            Invoke-MpRemote -Args @('fs', 'ls') | Out-Null
            return
        }
        catch {
            if ($i -eq 1) {
                Disconnect-MicroPicoReplBestEffort
            }
            if ($i -eq $Attempts) {
                throw
            }
            Write-Host "Device not reachable yet (attempt $i/$Attempts). Retrying..."
            Start-Sleep -Seconds 1
        }
    }
}

# Compile microdot.py -> build/lib/microdot.mpy before deploying to save heap on device
$microdotPy  = Join-Path $srcDir 'lib/microdot.py'
$buildLibDir = Join-Path $buildDir 'lib'
$microdotMpy = Join-Path $buildLibDir 'microdot.mpy'
New-Item -ItemType Directory -Force $buildLibDir | Out-Null
Write-Host 'Compiling microdot.py to microdot.mpy...'
$mxResult = py -m mpy_cross -march=armv6m $microdotPy -o $microdotMpy 2>&1
if ($LASTEXITCODE -ne 0) { throw "mpy-cross failed: $mxResult" }
Write-Host "microdot.mpy compiled ($([int](Get-Item $microdotMpy).Length / 1024) KB)"

# Every top-level .py in src/ is device code and goes to the device root.
$runtimePythonFiles = @(Get-ChildItem -Path $srcDir -Filter '*.py' -File | ForEach-Object { $_.Name })

# Libraries live in /lib on the device (on MicroPython's default sys.path).
$libFilesToDeploy = @{ $microdotMpy = ':/lib/microdot.mpy' }
$mqttAsLocalDir = Join-Path $srcDir 'lib/mqtt_as'

# config.json holds settings saved from the web UI, so it is only pushed when the
# device has none, or when -ResetConfig is given. A local config.json (gitignored)
# is used if present, otherwise config.example.json.
$localConfig = Join-Path $projectRoot 'config.json'
if (-not (Test-Path $localConfig)) {
    $localConfig = Join-Path $projectRoot 'config.example.json'
}

$requiredRemoteFiles = @($runtimePythonFiles | ForEach-Object { ":/$_" })
$requiredRemoteFiles += @(':/lib/microdot.mpy', ':/lib/mqtt_as/__init__.py', ':/config.json')

$remoteFilesToRemove = @($runtimePythonFiles | ForEach-Object { ":/$_" })
$remoteFilesToRemove += ':/lib/microdot.mpy'

# Remove files left by older deploys. Root copies of libraries would shadow /lib,
# because the current directory comes first on sys.path.
$remoteFilesToRemove += @(
    ':/microdot.py',
    ':/microdot.mpy',
    ':/messagetypes.py',
    ':/webui_new.py',
    ':/test_app_inspection.py',
    ':/test_http_diagnostics.py',
    ':/test_http_run.py',
    ':/test_http_start.py',
    ':/test_httpserver.py',
    ':/test_port_binding.py',
    ':/test_url_map.py'
)

if (-not $PreserveProvisioning) {
    $remoteFilesToRemove += @(':/provisioning.json', ':/device_name.json')
}

$remoteDirsToRemove = @(':/mqtt_as', ':/lib/mqtt_as', ':/typings')

Write-Host 'Resolving mpremote command...'
$MpremoteCommand = Resolve-MpremoteCommand
Write-Host "Using mpremote backend: $($MpremoteCommand -join ' ')"

$SelectedPort = Resolve-TargetPort -RequestedPort $Port
if ($SelectedPort) {
    Write-Host "Using serial port: $SelectedPort"
}
else {
    Write-Host 'No specific serial port resolved. Using mpremote auto-detect.'
}

Write-Host 'Checking connected device...'
Assert-DeviceReachable

Write-Host 'Cleaning old deployed files from device...'
foreach ($remoteFile in $remoteFilesToRemove) {
    Remove-RemoteFileIfExists -RemotePath $remoteFile
}
foreach ($remoteDir in $remoteDirsToRemove) {
    Remove-RemoteDirIfExists -RemotePath $remoteDir
}

Write-Host 'Copying firmware files...'
foreach ($file in $runtimePythonFiles) {
    Invoke-MpRemote -Args @('fs', 'cp', (Join-Path $srcDir $file), ":/$file") | Out-Null
}

Write-Host 'Copying libraries...'
Invoke-MpRemote -Args @('fs', 'mkdir', ':/lib') -AllowFailure | Out-Null
foreach ($localPath in $libFilesToDeploy.Keys) {
    Invoke-MpRemote -Args @('fs', 'cp', $localPath, $libFilesToDeploy[$localPath]) | Out-Null
}
if (-not (Test-Path $mqttAsLocalDir)) {
    throw "Missing local directory: $mqttAsLocalDir"
}
Invoke-MpRemote -Args @('fs', 'cp', '-r', $mqttAsLocalDir, ':/lib/') | Out-Null

$configDeployed = $false
if ($ResetConfig -or -not (Test-RemotePathExists -RemotePath ':/config.json')) {
    Write-Host "Copying config from $(Split-Path -Leaf $localConfig)..."
    Invoke-MpRemote -Args @('fs', 'cp', $localConfig, ':/config.json') | Out-Null
    $configDeployed = $true
}
else {
    Write-Host 'Keeping existing config.json on device (use -ResetConfig to overwrite).'
}

Write-Host 'Verifying deployed runtime files...'
$missing = @()
foreach ($remotePath in $requiredRemoteFiles) {
    if (-not (Test-RemotePathExists -RemotePath $remotePath)) {
        $missing += $remotePath
    }
}
if ($missing.Count -gt 0) {
    throw "Deployment verification failed. Missing on device:`n$($missing -join "`n")"
}

if (-not $SkipReset) {
    Write-Host 'Soft resetting device...'
    Invoke-MpRemote -Args @('exec', 'import machine; machine.soft_reset()') | Out-Null
}

Reconnect-MicroPicoReplBestEffort

$deployEnd = Get-Date
$durationSeconds = [int]($deployEnd - $deployStart).TotalSeconds
$summaryPort = if ($SelectedPort) { $SelectedPort } else { 'auto' }
Write-Host 'Deploy complete.'
Write-Host "Summary: port=$summaryPort, files=$($runtimePythonFiles.Count), config=$configDeployed, verified=$($requiredRemoteFiles.Count), reset=$($SkipReset -eq $false), duration=${durationSeconds}s"
Write-Host "DEPLOY_SUCCESS $($deployEnd.ToString('s'))"
if ($PreserveProvisioning) {
    Write-Host 'Provisioning files were preserved. Use -PreserveProvisioning:$false to wipe provisioning/device name.'
}
