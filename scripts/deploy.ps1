param(
    [switch]$PreserveProvisioning = $true,
    [switch]$ResetConfig,
    [switch]$Wipe,
    [switch]$SkipReset,
    [string]$Port
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

function Get-MissingRemotePaths {
    param([string[]]$RemotePaths)

    # `mpremote fs ls` fails on files ("Not a directory"), so stat every path on
    # the device in one run instead. Returns the paths that don't exist.
    $pathList = ($RemotePaths | ForEach-Object { "'" + $_.TrimStart(':') + "'," }) -join ' '
    $checkScript = Join-Path $buildDir 'check_paths.py'
    Set-Content -Path $checkScript -Encoding ascii -Value @(
        'import os',
        "for p in ($pathList):",
        '    try:',
        '        os.stat(p)',
        '    except OSError:',
        "        print('MISSING:' + p)"
    )
    $output = Invoke-MpRemote -Args @('run', $checkScript)
    return @($output | ForEach-Object { "$_" } | Where-Object { $_ -match '^MISSING:(.+)$' } | ForEach-Object { ':' + $Matches[1].Trim() })
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

function Clear-DeviceFilesystem {
    # Delete every file and directory on the device, so the deploy starts from a
    # blank filesystem (no config, provisioning or device name).
    $wipeScript = Join-Path $buildDir 'wipe_device.py'
    Set-Content -Path $wipeScript -Encoding ascii -Value @(
        'import os',
        'def rm(d):',
        '    for e in os.ilistdir(d):',
        "        p = d.rstrip('/') + '/' + e[0]",
        '        if e[1] == 0x4000:',
        '            rm(p)',
        '            os.rmdir(p)',
        '        else:',
        '            os.remove(p)',
        "rm('/')",
        "print('WIPED', len(os.listdir('/')))"
    )
    $output = Invoke-MpRemote -Args @('run', $wipeScript)
    if (-not ($output | Where-Object { "$_" -match '^WIPED 0$' })) {
        throw "Device wipe did not leave an empty filesystem:`n$output"
    }
}

function Assert-DeviceReachable {
    param([int]$Attempts = 3)

    for ($i = 1; $i -le $Attempts; $i++) {
        try {
            Invoke-MpRemote -Args @('fs', 'ls') | Out-Null
            return
        }
        catch {
            if ($i -eq $Attempts) {
                throw "Device not reachable. If the port is in use, close whatever has it open (a REPL, another deploy).`n$_"
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
# because the current directory comes first on sys.path. A raw /lib/microdot.py
# (e.g. from copying src/ by hand) would be imported instead of the .mpy.
$remoteFilesToRemove += @(
    ':/microdot.py',
    ':/microdot.mpy',
    ':/lib/microdot.py',
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

if ($Wipe) {
    Write-Host 'Wiping the device filesystem...'
    Clear-DeviceFilesystem
    $ResetConfig = $true
}

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
if ($ResetConfig -or @(Get-MissingRemotePaths -RemotePaths @(':/config.json')).Count -gt 0) {
    Write-Host "Copying config from $(Split-Path -Leaf $localConfig)..."
    Invoke-MpRemote -Args @('fs', 'cp', $localConfig, ':/config.json') | Out-Null
    $configDeployed = $true
}
else {
    Write-Host 'Keeping existing config.json on device (use -ResetConfig to overwrite).'
}

Write-Host 'Verifying deployed runtime files...'
$missing = @(Get-MissingRemotePaths -RemotePaths $requiredRemoteFiles)
if ($missing.Count -gt 0) {
    throw "Deployment verification failed. Missing on device:`n$($missing -join "`n")"
}

if (-not $SkipReset) {
    Write-Host 'Soft resetting device...'
    Invoke-MpRemote -Args @('exec', 'import machine; machine.soft_reset()') | Out-Null
}

$deployEnd = Get-Date
$durationSeconds = [int]($deployEnd - $deployStart).TotalSeconds
$summaryPort = if ($SelectedPort) { $SelectedPort } else { 'auto' }
Write-Host 'Deploy complete.'
Write-Host "Summary: port=$summaryPort, files=$($runtimePythonFiles.Count), config=$configDeployed, verified=$($requiredRemoteFiles.Count), reset=$($SkipReset -eq $false), duration=${durationSeconds}s"
Write-Host "DEPLOY_SUCCESS $($deployEnd.ToString('s'))"
if ($PreserveProvisioning -and -not $Wipe) {
    Write-Host 'Provisioning files were preserved. Use -PreserveProvisioning:$false to wipe provisioning/device name.'
}
