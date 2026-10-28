param(
    [switch]$PreserveProvisioning = $true,
    [switch]$ResetConfig,
    [switch]$Wipe,
    [switch]$SkipReset,
    [string]$Port,
    # Update over Wi-Fi instead of USB: the devices' hostnames or IP addresses,
    # comma-separated for several (built once). Needs each device's web password
    # (PLASMASTICK_WEB_PASSWORD, or asked for per device).
    [string[]]$WiFi,
    # Compile everything again, not just what changed.
    [switch]$Rebuild
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

# Everything except main.py is precompiled to .mpy. Importing a .py makes the
# device compile it in RAM, which spikes heap use at boot and leaves it
# fragmented. main.py must stay a .py, because MicroPython only runs main.py.
# Only what changed is compiled again: a .mpy is kept while it's newer than its
# source. A new mpy-cross version (or -Rebuild) starts from scratch.
$script:compiled = 0
$script:upToDate = 0
function Compress-ToMpy {
    param([string]$Source, [string]$Destination)
    if ((Test-Path $Destination) -and (Get-Item $Destination).LastWriteTimeUtc -ge (Get-Item $Source).LastWriteTimeUtc) {
        $script:upToDate++
        return
    }
    New-Item -ItemType Directory -Force (Split-Path -Parent $Destination) | Out-Null
    $mxResult = py -m mpy_cross -march=armv6m $Source -o $Destination 2>&1
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -Force $Destination -ErrorAction SilentlyContinue
        throw "mpy-cross failed for ${Source}: $mxResult"
    }
    $script:compiled++
}

$buildDeviceDir = Join-Path $buildDir 'device'
$buildLibDir = Join-Path $buildDir 'lib'
$stampFile = Join-Path $buildDir 'mpy-cross.version'
$mpyCrossVersion = (py -m mpy_cross --version 2>&1 | Out-String).Trim()
$stamp = if (Test-Path $stampFile) { (Get-Content -Raw $stampFile).Trim() } else { '' }
if ($Rebuild -or $stamp -ne $mpyCrossVersion) {
    foreach ($dir in @($buildDeviceDir, $buildLibDir)) {
        if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
    }
}
New-Item -ItemType Directory -Force $buildDir | Out-Null
Set-Content -Path $stampFile -Value $mpyCrossVersion -Encoding ascii

# Every top-level .py in src/ is device code and goes to the device root.
$moduleNames = @(Get-ChildItem -Path $srcDir -Filter '*.py' -File | Where-Object { $_.Name -ne 'main.py' } | ForEach-Object { $_.BaseName })
# A module removed from src/ must not be deployed from an old build.
if (Test-Path $buildDeviceDir) {
    Get-ChildItem -Path $buildDeviceDir -Filter '*.mpy' -File | Where-Object { $_.BaseName -notin ($moduleNames + 'otaboot') } | Remove-Item -Force
}
foreach ($name in $moduleNames) {
    Compress-ToMpy -Source (Join-Path $srcDir "$name.py") -Destination (Join-Path $buildDeviceDir "$name.mpy")
}
$runtimeFiles = @{ (Join-Path $srcDir 'main.py') = ':/main.py' }
foreach ($name in $moduleNames) {
    $runtimeFiles[(Join-Path $buildDeviceDir "$name.mpy")] = ":/$name.mpy"
}
# Static web UI files, served from /www on the device.
$wwwDir = Join-Path $srcDir 'www'
foreach ($file in @(Get-ChildItem -Path $wwwDir -File)) {
    $runtimeFiles[$file.FullName] = ":/www/$($file.Name)"
}

# App Base (src/base, a copy made by App Base's update-base.ps1) goes to /base, as it is in src/, but for what's
# only for a Pi (pi.py, piweb.py, mqttclient.py) and otaboot, which goes to the root: like main.py it's only updated
# over USB.
$baseDir = Join-Path $srcDir 'base'
$buildBaseDir = Join-Path $buildDir 'base'
$baseSkip = @('pi.py', 'piweb.py', 'mqttclient.py', 'pico/otaboot.py')
$baseModules = @(Get-ChildItem -Path $baseDir -Recurse -Filter '*.py' -File | Where-Object { $_.FullName -notmatch '__pycache__' } |
    ForEach-Object { $_.FullName.Substring($baseDir.Length + 1).Replace('\', '/') } | Where-Object { $_ -notin $baseSkip })
if (Test-Path $buildBaseDir) {
    # A module removed from the base must not be deployed from an old build
    $wanted = @($baseModules | ForEach-Object { $_ -replace '\.py$', '.mpy' })
    Get-ChildItem -Path $buildBaseDir -Recurse -Filter '*.mpy' -File | Where-Object {
        $_.FullName.Substring($buildBaseDir.Length + 1).Replace('\', '/') -notin $wanted } | Remove-Item -Force
}
foreach ($module in $baseModules) {
    $mpy = $module -replace '\.py$', '.mpy'
    $built = Join-Path $buildBaseDir $mpy
    Compress-ToMpy -Source (Join-Path $baseDir $module) -Destination $built
    $runtimeFiles[$built] = ":/base/$mpy"
}
foreach ($file in @(Get-ChildItem -Path (Join-Path $baseDir 'www') -File)) {
    $runtimeFiles[$file.FullName] = ":/base/www/$($file.Name)"
}
$otabootMpy = Join-Path $buildDeviceDir 'otaboot.mpy'
Compress-ToMpy -Source (Join-Path $baseDir 'pico/otaboot.py') -Destination $otabootMpy
$runtimeFiles[$otabootMpy] = ':/otaboot.mpy'

# Libraries live in /lib on the device (on MicroPython's default sys.path).
$mqttAsMpy = Join-Path $buildLibDir 'mqtt_as/__init__.mpy'
Compress-ToMpy -Source (Join-Path $srcDir 'lib/mqtt_as/__init__.py') -Destination $mqttAsMpy
$libFilesToDeploy = @{ $mqttAsMpy = ':/lib/mqtt_as/__init__.mpy' }
$totalKb = [int]((@($runtimeFiles.Keys) + @($libFilesToDeploy.Keys) | ForEach-Object { (Get-Item $_).Length } | Measure-Object -Sum).Sum / 1024)
Write-Host "Compiled $($script:compiled) module(s), $($script:upToDate) up to date ($totalKb KB in all)"

if ($WiFi) {
    # Hand the same files to the Wi-Fi updater (main.py and otaboot excepted: USB only).
    $fileMap = [ordered]@{}
    foreach ($table in @($runtimeFiles, $libFilesToDeploy)) {
        foreach ($localPath in $table.Keys) {
            $devicePath = $table[$localPath].TrimStart(':').TrimStart('/')
            if ($devicePath -notin @('main.py', 'otaboot.mpy')) { $fileMap[$devicePath] = $localPath }
        }
    }
    $mapFile = Join-Path $buildDir 'files.json'
    $fileMap | ConvertTo-Json | Set-Content -Path $mapFile -Encoding utf8
    # Built once, sent to each board in turn.
    $failed = @()
    foreach ($wifiHost in $WiFi) {
        Write-Host "`n=== $wifiHost"
        # A board that fails is reported, and the rest still get updated.
        $ErrorActionPreference = 'Continue'
        & py (Join-Path $scriptDir 'wifi_update.py') --host $wifiHost --files $mapFile
        $code = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($code -ne 0) { $failed += $wifiHost }
    }
    if ($failed.Count -gt 0) {
        Write-Host "`nNot updated: $($failed -join ', ')"
        exit 1
    }
    exit 0
}

# config.json holds settings saved from the web UI, so it is only pushed when the
# device has none, or when -ResetConfig is given. A local config.json (gitignored)
# is used if present, otherwise config.example.json.
$localConfig = Join-Path $projectRoot 'config.json'
if (-not (Test-Path $localConfig)) {
    $localConfig = Join-Path $projectRoot 'config.example.json'
}

$requiredRemoteFiles = @($runtimeFiles.Values) + @($libFilesToDeploy.Values) + @(':/config.json')

# Remove both forms of every module first: a stale .py next to a fresh .mpy
# would be imported instead of it, because MicroPython looks for .py first.
# boot.py: never run by this firmware; one left from testing would only confuse.
$remoteFilesToRemove = @(':/main.py', ':/boot.py', ':/update_state.json')
foreach ($name in $moduleNames) {
    $remoteFilesToRemove += @(":/$name.py", ":/$name.mpy")
}
$remoteFilesToRemove += ':/lib/microdot.mpy'
# The firmware before App Base had these in the root: the base does them now
foreach ($name in @('api', 'asyncdns', 'captivedns', 'clock', 'devicebutton', 'deviceconfig', 'deviceidentity',
        'htmlutil', 'httpserver', 'ledstrip', 'log', 'mqttgroup', 'mqttha', 'mqttstate', 'ntp', 'ota', 'passwords',
        'picoled', 'provisioning', 'schedule', 'settings', 'watchdog', 'wificrypto', 'wlan')) {
    $remoteFilesToRemove += @(":/$name.py", ":/$name.mpy")
}

# Remove files left by older deploys. Root copies of libraries would shadow /lib,
# because the current directory comes first on sys.path. A raw /lib/microdot.py
# (e.g. from copying src/ by hand) would be imported instead of the .mpy.
$remoteFilesToRemove += @(
    ':/microdot.py',
    ':/microdot.mpy',
    ':/lib/microdot.py',
    ':/messagetypes.py',
    ':/webui_new.py',
    ':/webui.py',
    ':/webui.mpy',
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

# /update and /prev hold a Wi-Fi update's staged files and backups: a USB deploy
# replaces everything, so a stale rollback must not undo it.
$remoteDirsToRemove = @(':/mqtt_as', ':/lib/mqtt_as', ':/typings', ':/www', ':/base', ':/update', ':/prev')

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
foreach ($dir in @(':/www', ':/base', ':/base/www', ':/base/pico')) {
    Invoke-MpRemote -Args @('fs', 'mkdir', $dir) -AllowFailure | Out-Null
}
foreach ($localPath in $runtimeFiles.Keys) {
    Invoke-MpRemote -Args @('fs', 'cp', $localPath, $runtimeFiles[$localPath]) | Out-Null
}

Write-Host 'Copying libraries...'
Invoke-MpRemote -Args @('fs', 'mkdir', ':/lib') -AllowFailure | Out-Null
Invoke-MpRemote -Args @('fs', 'mkdir', ':/lib/mqtt_as') -AllowFailure | Out-Null
foreach ($localPath in $libFilesToDeploy.Keys) {
    Invoke-MpRemote -Args @('fs', 'cp', $localPath, $libFilesToDeploy[$localPath]) | Out-Null
}

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
    # A hard reset, as at power-on. A soft reset from mpremote exec happens in the
    # raw REPL, which restarts the interpreter without running main.py.
    Write-Host 'Resetting device...'
    Invoke-MpRemote -Args @('reset') | Out-Null
}

$deployEnd = Get-Date
$durationSeconds = [int]($deployEnd - $deployStart).TotalSeconds
$summaryPort = if ($SelectedPort) { $SelectedPort } else { 'auto' }
Write-Host 'Deploy complete.'
Write-Host "Summary: port=$summaryPort, files=$($runtimeFiles.Count), config=$configDeployed, verified=$($requiredRemoteFiles.Count), reset=$($SkipReset -eq $false), duration=${durationSeconds}s"
Write-Host "DEPLOY_SUCCESS $($deployEnd.ToString('s'))"
if ($PreserveProvisioning -and -not $Wipe) {
    Write-Host 'Provisioning files were preserved. Use -PreserveProvisioning:$false to wipe provisioning/device name.'
}
