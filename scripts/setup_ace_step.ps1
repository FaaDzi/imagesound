# Installs ACE-Step 1.5 (the app's default music model) in its own environment.
#
# Everything stays INSIDE the project folder -- nothing is written to C:\:
#   third_party\ACE-Step-1.5\   the upstream code and its Python 3.12 virtualenv
#   .uv\                        uv's managed Python and wheel cache
#   .hf-cache\                  Hugging Face cache
#   models\ace-step\            the model weights (several GB)
#
# Usage (from the project root):
#   powershell -ExecutionPolicy Bypass -File scripts\setup_ace_step.ps1        # Turbo
#   powershell -ExecutionPolicy Bypass -File scripts\setup_ace_step.ps1 -Sft   # + higher-quality SFT
#
# Needs: git, and uv (https://docs.astral.sh/uv/). uv downloads Python 3.12 itself.
# Safe to re-run: a model counts as installed only when its weight file exists,
# so an interrupted download is resumed rather than trusted.

param([switch]$Sft)

$ErrorActionPreference = "Stop"
$root   = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ace    = Join-Path $root "third_party\ACE-Step-1.5"
$models = Join-Path $root "models\ace-step"

$env:UV_PYTHON_INSTALL_DIR   = Join-Path $root ".uv\python"
$env:UV_CACHE_DIR            = Join-Path $root ".uv\cache"
$env:HF_HOME                 = Join-Path $root ".hf-cache"
$env:ACESTEP_CHECKPOINTS_DIR = $models

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe" }
if (-not (Test-Path $uv)) { throw "uv not found. Install it first: https://docs.astral.sh/uv/" }

if (-not (Test-Path $ace)) {
    git clone --depth 1 https://github.com/ACE-Step/ACE-Step-1.5.git $ace
}

New-Item -ItemType Directory -Force -Path $models | Out-Null
Push-Location $ace
try {
    & $uv sync
    $py = ".venv\Scripts\python.exe"

    # --force because the downloader otherwise treats an existing (possibly
    # half-downloaded) folder as installed.
    if (-not (Test-Path (Join-Path $models "acestep-v15-turbo\model.safetensors"))) {
        & $py -m acestep.model_downloader --model main --force --dir $models
    }
    if ($Sft -and -not (Test-Path (Join-Path $models "acestep-v15-sft\model.safetensors"))) {
        & $py -m acestep.model_downloader --model acestep-v15-sft --skip-main --force --dir $models
    }
}
finally {
    Pop-Location
}

Write-Host "ACE-Step is ready. Restart the app; GET /models will list it."
