# One-command auto-labeling -> Colab-ready dataset zip.
#
# Runs (1) teacher auto-label on all collected raw frames, then
#      (2) make_dataset (train/val split + data.yaml + zip).
# Result: vision/finetune/gwangalli_dataset.zip  -> upload to Google Drive -> Colab.
#
# Usage (from vision/):
#   powershell -ExecutionPolicy Bypass -File .\finetune\auto_prepare.ps1
#   powershell -ExecutionPolicy Bypass -File .\finetune\auto_prepare.ps1 -Fast   # quick draft (yolo26s)
#   powershell -ExecutionPolicy Bypass -File .\finetune\auto_prepare.ps1 -Limit 800

param(
    [switch]$Fast,        # use fast draft model instead of teacher
    [int]$Limit = 0,      # max frames (0 = all)
    [double]$Val = 0.2    # validation split ratio
)

$py = "C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe"
$vision = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $vision

$raw = Join-Path $vision "finetune\raw"
$n = (Get-ChildItem $raw -File -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "[auto] raw frames: $n"
if ($n -lt 30) {
    Write-Host "[auto] WARNING: fewer than 30 frames. More data = better model. Continue anyway..."
}

# Step 1: auto-label
$labelArgs = @("finetune/prelabel.py")
if (-not $Fast) { $labelArgs += "--teacher" }
if ($Limit -gt 0) { $labelArgs += @("--limit", "$Limit") }
$mode = if ($Fast) { "FAST(yolo26s)" } else { "TEACHER(yolo26m)" }
Write-Host "[auto] step 1/2 auto-label  mode=$mode  (offline, may take a while)"
& $py @labelArgs
if ($LASTEXITCODE -ne 0) { Write-Host "[auto] prelabel FAILED"; exit 1 }

# Step 2: package dataset
Write-Host "[auto] step 2/2 make_dataset  val=$Val"
& $py "finetune/make_dataset.py" "--val" "$Val"
if ($LASTEXITCODE -ne 0) { Write-Host "[auto] make_dataset FAILED"; exit 1 }

$zip = Join-Path $vision "finetune\gwangalli_dataset.zip"
if (Test-Path $zip) {
    $mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Write-Host "[auto] DONE -> $zip ($mb MB)"
    Write-Host "[auto] next: upload to Google Drive, open finetune/train_yolo26_colab.ipynb in Colab (GPU)."
} else {
    Write-Host "[auto] DONE but zip not found - check logs above."
}
