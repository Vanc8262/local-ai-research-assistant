# Start llama-swap on 127.0.0.1:1234 with services/llama-swap/config.yaml.
# Needs, once: scripts\install_llama_cpp.ps1 (llama-server), scripts\copy_models.ps1 (the
# GGUF files in D:\models) and `winget install mostlygeek.llama-swap`. assistant.toml has
# model_server = "llama-swap" to match.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_llama_swap.ps1
param(
    [string]$Listen = "127.0.0.1:1234",
    [string]$Models = "D:\models",
    [string]$LlamaCpp = (Join-Path $env:USERPROFILE "tools\llama.cpp")
)
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent

function Find-One([string]$Root, [string]$Pattern, [string]$What) {
    $hit = Get-ChildItem -Path $Root -Recurse -File -Filter $Pattern -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notlike "mmproj*" } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $hit) { throw "Could not find $What ($Pattern under $Root). Run scripts\copy_models.ps1, or download it into $Root." }
    return $hit.FullName
}

# llama-server from install_llama_cpp.ps1; its CUDA DLLs sit beside it.
$current = Join-Path $LlamaCpp "current.txt"
if (-not (Test-Path $current)) { throw "No llama.cpp install found. Run scripts\install_llama_cpp.ps1 first." }
$bin = (Get-Content $current -Raw).Trim()
$env:LLAMA_SERVER = Join-Path $bin "llama-server.exe"
if (-not (Test-Path $env:LLAMA_SERVER)) { throw "$env:LLAMA_SERVER is missing. Rerun scripts\install_llama_cpp.ps1." }
$env:PATH = "$bin;$env:PATH"

$env:GEMMA_GGUF = Find-One $Models "*gemma-4-E4B*q4_0*.gguf" "Gemma 4 E4B QAT"
$env:QWEN_GGUF = Find-One $Models "*Qwen3.5-9B*Q4_K_M*.gguf" "Qwen3.5 9B Q4_K_M"
$env:NOMIC_GGUF = Find-One $Models "*nomic-embed-text-v1.5*.gguf" "nomic-embed-text v1.5"

$swap = (Get-Command llama-swap -ErrorAction SilentlyContinue).Source
if (-not $swap) {
    $swap = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages") -Recurse -File `
        -Filter "llama-swap.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $swap) { throw "llama-swap not found. Install it with: winget install mostlygeek.llama-swap" }

Write-Host "llama-server: $env:LLAMA_SERVER"
Write-Host "Gemma: $env:GEMMA_GGUF`nQwen:  $env:QWEN_GGUF`nNomic: $env:NOMIC_GGUF"
Write-Host "llama-swap on http://$Listen (Ctrl+C to stop)"
& $swap --config (Join-Path $repo "services\llama-swap\config.yaml") --listen $Listen
