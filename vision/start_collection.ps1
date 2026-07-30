# One-click starter for fine-tune data collection.
# Usage:  cd vision ;  powershell -ExecutionPolicy Bypass -File .\start_collection.ps1
# Starts the realtime server and the frame collector in the background if not
# already running. Run this tomorrow morning to resume collection.

$py = "C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

function Test-Running($pattern) {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match $pattern }
    return [bool]$p
}

# 1) realtime server
if (Test-Running 'realtime_safety_map.py') {
    Write-Host "[server] already running"
} else {
    Start-Process -FilePath $py `
        -ArgumentList "realtime_safety_map.py", "--port", "8790", "--detector", "sahi" `
        -WorkingDirectory $here `
        -RedirectStandardOutput "realtime_safety_map.run.log" `
        -RedirectStandardError "realtime_safety_map.err.log" `
        -WindowStyle Hidden
    Write-Host "[server] started at http://127.0.0.1:8790/"
    Start-Sleep -Seconds 12
}

# 2) frame collector (every 45s)
if (Test-Running 'collect_finetune_frames.py') {
    Write-Host "[collect] already running"
} else {
    Start-Process -FilePath $py `
        -ArgumentList "collect_finetune_frames.py", "--every", "45" `
        -WorkingDirectory $here `
        -RedirectStandardOutput "collect_frames.out.log" `
        -RedirectStandardError "collect_frames.err.log" `
        -WindowStyle Hidden
    Write-Host "[collect] started -> saving to finetune/raw/"
}

Start-Sleep -Seconds 3
$n = (Get-ChildItem "finetune/raw" -File -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "[status] frames collected so far: $n"
