<#
Stage 0 benchmark runner (semi-automated).

Run from the repo folder in PowerShell:

    powershell -ExecutionPolicy Bypass -File scripts\run_stage0.ps1             # smoke test, then full run, every model
    powershell -ExecutionPolicy Bypass -File scripts\run_stage0.ps1 -SmokeOnly  # 2 items per suite only
    powershell -ExecutionPolicy Bypass -File scripts\run_stage0.ps1 -Models qwen3.5-9b
    powershell -ExecutionPolicy Bypass -File scripts\run_stage0.ps1 -Models qwen3.5-9b,gemma-4-e4b-qat

What it does:
  1. Turns this folder into a git checkout of the GitHub repo if it is not one yet.
  2. Creates .venv, installs the harness, and runs its self-tests.
  3. For each model: unloads everything, loads the model at 8K context with full GPU
     offload under the id used in bench.toml, runs a 2-item smoke test, then the full run.
  4. Deletes the smoke-test folders and rebuilds results\summary.md.

LM Studio must be open with the local server running (Developer tab -> Status: Running).
A transcript of everything printed is saved in results\.
#>
param(
    [switch]$SmokeOnly,
    [string[]]$Models = @("qwen3.5-9b", "gemma-4-12b-qat"),
    [int]$Context = 8192
)

# With -File, "-Models a,b" arrives as one string "a,b"; split it so both forms work.
$Models = @($Models | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

# bench.toml id -> LM Studio model key (as shown by `lms ls`)
$ModelKeys = @{
    "qwen3.5-9b"      = "qwen/qwen3.5-9b"          # loads its selected variant, Q4_K_M
    "gemma-4-12b-qat" = "google/gemma-4-12b-qat"
    # lms load does not accept "@variant" keys, so the Q6_K file was hard-linked in as
    # its own model: lms import <Qwen3.5-9B-Q6_K.gguf and mmproj> --hard-link --user-repo local/Qwen3.5-9B-Q6_K-GGUF
    "qwen3.5-9b-q6"   = "local/qwen3.5-9b"
    "granite-4.2-8b"  = "granite-4.2-8b"
    "gemma-4-e4b-qat" = "gemma-4-e4b-it-qat"
}

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Check($what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)." }
}

New-Item -ItemType Directory -Force results | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
Start-Transcript -Path "results\run_stage0_$stamp.log" | Out-Null

try {
    # 1. Git checkout ------------------------------------------------------
    if (-not (Test-Path .git)) {
        Step "Linking this folder to the GitHub repo"
        git init -q -b main;                                                         Check "git init"
        git remote add origin https://github.com/Vanc8262/local-ai-research-assistant.git
        git fetch -q origin;                                                         Check "git fetch"
        git reset -q origin/main;                                                    Check "git reset"
        git branch -q --set-upstream-to=origin/main main
        git status --short
    }

    # 2. Python environment -------------------------------------------------
    $py = ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Step "Creating .venv with Python 3.13"
        py -3.13 -m venv .venv;                                                      Check "venv creation"
    }
    Step "Installing the harness"
    & $py -m pip install -q --upgrade pip
    & $py -m pip install -q -e .;                                                    Check "pip install"
    Step "Harness self-tests (no model needed)"
    & $py -m pytest -q;                                                              Check "pytest"

    # 3. LM Studio CLI ------------------------------------------------------
    $lms = (Get-Command lms -ErrorAction SilentlyContinue).Source
    if (-not $lms) { $lms = Join-Path $env:USERPROFILE ".lmstudio\bin\lms.exe" }
    if (-not (Test-Path $lms)) { throw "Could not find the lms CLI. In LM Studio: Settings -> Developer, or run ~\.lmstudio\bin\lms.exe bootstrap." }
    & $lms server status

    foreach ($id in $Models) {
        $key = $ModelKeys[$id]
        if (-not $key) { throw "No LM Studio key for '$id'. Add it to `$ModelKeys at the top of this script." }

        Step "Loading $id ($key) at $Context context"
        & $lms unload --all
        & $lms load $key --context-length $Context --gpu max --identifier $id -y
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Load failed. Models on disk:" -ForegroundColor Yellow
            & $lms ls
            throw "lms load $key failed. Fix the key in `$ModelKeys and rerun."
        }
        & $py -m bench models

        Step "Smoke test: $id (2 items per suite)"
        & $py -m bench run --model $id --limit 2 --tag smoke;                        Check "Smoke test for $id"

        if (-not $SmokeOnly) {
            Step "Full run: $id"
            & $py -m bench run --model $id;                                          Check "Full run for $id"
        }
    }

    # 4. Tidy up -------------------------------------------------------------
    if (-not $SmokeOnly) {
        Get-ChildItem results -Directory -Filter "*_smoke" | Remove-Item -Recurse -Force
        & $py -m bench report
    }
    & $lms unload --all

    Step "Done"
    Write-Host "Summary:      results\summary.md"
    Write-Host "Manual review: results\<run>\manual_review.md -> fill in manual_scores.csv, then: .venv\Scripts\python.exe -m bench report"
}
finally {
    Stop-Transcript | Out-Null
}
