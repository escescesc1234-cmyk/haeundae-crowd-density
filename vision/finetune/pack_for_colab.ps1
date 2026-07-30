# Package code + collected raw frames into ONE zip for Colab GPU labeling+training.
# Output: vision/finetune/gwangalli_colab.zip  (upload this to Google Drive root)
#
# Contents (keeps vision/ layout so imports & finetune/raw work on Colab):
#   vision/*.py                     (detection code: realtime_safety_map, safety_map, ...)
#   vision/finetune/prelabel.py     (GPU auto-labeler)
#   vision/finetune/make_dataset.py (train/val split)
#   vision/finetune/raw/*.jpg       (your collected frames = training data)
#
# Usage (from vision/):
#   powershell -ExecutionPolicy Bypass -File .\finetune\pack_for_colab.ps1

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$vision = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$zip = Join-Path $vision "finetune\gwangalli_colab.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }

$raw = Join-Path $vision "finetune\raw"
$nRaw = (Get-ChildItem $raw -Filter *.jpg -File -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "[pack] raw frames: $nRaw"
if ($nRaw -lt 1) { Write-Host "[pack] no raw frames - collect first."; exit 1 }

$z = [System.IO.Compression.ZipFile]::Open($zip, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    function Add-One($full, $entry) {
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($z, $full, $entry) | Out-Null
    }

    # 1) vision root .py (detection code + local module deps)
    Get-ChildItem $vision -Filter *.py -File | ForEach-Object {
        Add-One $_.FullName ("vision/" + $_.Name)
    }
    # 2) finetune scripts needed on Colab
    foreach ($f in @("prelabel.py", "make_dataset.py", "flag_suspect.py")) {
        $p = Join-Path $vision "finetune\$f"
        if (Test-Path $p) { Add-One $p ("vision/finetune/" + $f) }
    }
    # 3) raw frames = training data
    Get-ChildItem $raw -Filter *.jpg -File | ForEach-Object {
        Add-One $_.FullName ("vision/finetune/raw/" + $_.Name)
    }
} finally {
    $z.Dispose()
}

$mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "[pack] DONE -> $zip ($mb MB)"
Write-Host "[pack] next: upload this zip to Google Drive (MyDrive root),"
Write-Host "[pack]       then open finetune/label_and_train_colab.ipynb in Colab (GPU)."
