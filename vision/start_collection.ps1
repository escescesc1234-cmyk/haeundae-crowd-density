# One-click starter for fine-tune data collection.
# Usage:  cd vision ;  powershell -ExecutionPolicy Bypass -File .\start_collection.ps1
# Starts (1) realtime server, (2) frame collector, (3) auto-box watcher in the
# background if not already running. Run this tomorrow morning to resume.
# The watcher draws boxes on every collected frame -> finetune/dataset/preview/.

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

# 3) auto-box watcher (light YOLO, fast; boxes every collected frame)
if (Test-Running 'autolabel_watch.py') {
    Write-Host "[watch] already running"
} else {
    Start-Process -FilePath $py `
        -ArgumentList "finetune/autolabel_watch.py", "--interval", "10", "--upscale", "1.0", "--imgsz", "1920" `
        -WorkingDirectory $here `
        -RedirectStandardOutput "finetune/autolabel_watch.out.log" `
        -RedirectStandardError "finetune/autolabel_watch.err.log" `
        -WindowStyle Hidden
    Write-Host "[watch] started -> boxes to finetune/dataset/preview/"
}

Start-Sleep -Seconds 3
$n = (Get-ChildItem "finetune/raw" -File -ErrorAction SilentlyContinue | Measure-Object).Count
$b = (Get-ChildItem "finetune/dataset/preview" -File -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "[status] raw frames: $n   boxed previews: $b"
