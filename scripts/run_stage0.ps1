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
  3. For each model: starts llama-server on 127.0.0.1:1234 with the model at 8K context,
     full GPU offload and the model's vision projector (mmproj) when it has one, under the
     id used in bench.toml; runs a 2-item smoke test, then the full run; stops the server.
  4. Deletes the smoke-test folders and rebuilds results\summary.md.

Needs scripts\install_llama_cpp.ps1 and the models in D:\models (scripts\copy_models.ps1).
Stop llama-swap first: both use port 1234. Results before 2026-11-11 ran under LM Studio,
which used more system RAM (prompt cache on, 4 slots), so headroom numbers are not comparable.
A transcript of everything printed is saved in results\.
#>
param(
    [switch]$SmokeOnly,
    [string[]]$Models = @("qwen3.5-9b", "gemma-4-12b-qat"),
    [int]$Context = 8192,
    [string]$ModelDir = "D:\models",
    [string]$LlamaCpp = (Join-Path $env:USERPROFILE "tools\llama.cpp")
)

# With -File, "-Models a,b" arrives as one string "a,b"; split it so both forms work.
$Models = @($Models | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

# bench.toml id -> GGUF file name pattern under $ModelDir (the mmproj beside it is found automatically)
$ModelFiles = @{
    "qwen3.5-9b"          = "Qwen3.5-9B-Q4_K_M.gguf"
    "qwen3.5-9b-q6"       = "Qwen3.5-9B-Q6_K.gguf"
    "gemma-4-12b-qat"     = "*gemma-4-12B*q4_0*.gguf"
    "granite-4.2-8b"      = "*granite-4.2-8b*.gguf"
    "gemma-4-e4b-qat"     = "*gemma-4-E4B*q4_0*.gguf"
    # llmfan46/gemma-4-E4B-it-ultra-uncensored-heretic-GGUF, Q4_K_M
    "gemma-4-e4b-heretic" = "*heretic*.gguf"
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

    # 3. llama-server ----------------------------------------------------------
    $current = Join-Path $LlamaCpp "current.txt"
    if (-not (Test-Path $current)) { throw "No llama.cpp install found. Run scripts\install_llama_cpp.ps1 first." }
    $bin = (Get-Content $current -Raw).Trim()
    $server = Join-Path $bin "llama-server.exe"
    if (Get-NetTCPConnection -LocalPort 1234 -State Listen -ErrorAction SilentlyContinue) {
        throw "Port 1234 is in use (llama-swap?). Stop it before benchmarking."
    }

    foreach ($id in $Models) {
        $pattern = $ModelFiles[$id]
        if (-not $pattern) { throw "No file pattern for '$id'. Add it to `$ModelFiles at the top of this script." }
        $gguf = Get-ChildItem $ModelDir -Recurse -File -Filter $pattern -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notlike "mmproj*" } | Select-Object -First 1
        if (-not $gguf) { throw "No file matching $pattern under $ModelDir for '$id'." }
        $mmproj = Get-ChildItem $gguf.DirectoryName -File -Filter "mmproj*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1

        Step "Starting llama-server: $id ($($gguf.Name)) at $Context context"
        $serverArgs = @("-m", "`"$($gguf.FullName)`"", "--alias", $id, "-c", $Context, "-ngl", "99",
                        "-lm", "mmap", "-cram", "0", "-np", "1", "-kvu", "-fa", "auto", "-t", "4",
                        "--host", "127.0.0.1", "--port", "1234")
        if ($mmproj) { $serverArgs += @("--mmproj", "`"$($mmproj.FullName)`"") } else { $serverArgs += "--no-mmproj" }
        $log = "results\llama-server_${id}_$stamp.log"
        $proc = Start-Process -FilePath $server -ArgumentList $serverArgs -WorkingDirectory $bin -PassThru `
            -WindowStyle Hidden -RedirectStandardError $log -RedirectStandardOutput "$log.out"
        $ready = $false
        foreach ($i in 1..180) {
            Start-Sleep -Seconds 1
            if ($proc.HasExited) { break }
            try { if ((Invoke-WebRequest -UseBasicParsing http://127.0.0.1:1234/health -TimeoutSec 2).StatusCode -eq 200) { $ready = $true; break } } catch {}
        }
        if (-not $ready) {
            if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
            throw "llama-server did not come up for $id; see $log"
        }
        try {
            & $py -m bench models

            Step "Smoke test: $id (2 items per suite)"
            & $py -m bench run --model $id --limit 2 --tag smoke;                    Check "Smoke test for $id"

            if (-not $SmokeOnly) {
                Step "Full run: $id"
                & $py -m bench run --model $id;                                      Check "Full run for $id"
            }
        }
        finally {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }

    # 4. Tidy up -------------------------------------------------------------
    if (-not $SmokeOnly) {
        Get-ChildItem results -Directory -Filter "*_smoke" | Remove-Item -Recurse -Force
        & $py -m bench report
    }

    Step "Done"
    Write-Host "Summary:      results\summary.md"
    Write-Host "Manual review: results\<run>\manual_review.md -> fill in manual_scores.csv, then: .venv\Scripts\python.exe -m bench report"
}
finally {
    Stop-Transcript | Out-Null
}
