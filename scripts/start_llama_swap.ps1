# Start llama-swap on 127.0.0.1:1234 with services/llama-swap/config.yaml, in place of
# LM Studio's server. Uses the llama-server and GGUF files LM Studio already has, so
# nothing is downloaded twice. Stop LM Studio's server first (`lms server stop`), since
# both want port 1234. Set model_server = "llama-swap" in assistant.toml to match.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_llama_swap.ps1
param(
    [string]$Listen = "127.0.0.1:1234",
    [string]$Models = (Join-Path $env:USERPROFILE ".lmstudio\models")
)
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent

function Find-One([string[]]$Root, [string]$Pattern, [string]$What) {
    $hit = Get-ChildItem -Path $Root -Recurse -File -Filter $Pattern -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notlike "mmproj*" } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $hit) { throw "Could not find $What ($Pattern under $Root)." }
    return $hit.FullName
}

# llama-server: LM Studio's newest CUDA llama.cpp backend (by version, not date). Its CUDA DLLs live beside it.
$backends = Join-Path $env:USERPROFILE ".lmstudio\extensions\backends"
$server = Get-ChildItem -Path $backends -Recurse -File -Filter "llama-server.exe" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "cuda" } |
    Sort-Object { [version]($_.Directory.Name -replace ".*-", "") } -Descending | Select-Object -First 1
if (-not $server) { throw "No CUDA llama-server.exe under $backends. Is LM Studio's CUDA runtime installed?" }
$env:LLAMA_SERVER = $server.FullName
$dllDirs = @($server.DirectoryName) + (Get-ChildItem -Path $backends -Directory -Recurse -ErrorAction SilentlyContinue |
    Where-Object { Get-ChildItem $_.FullName -Filter "cudart*.dll" -ErrorAction SilentlyContinue } |
    ForEach-Object FullName)
$env:PATH = (($dllDirs | Select-Object -Unique) -join ";") + ";" + $env:PATH

$env:GEMMA_GGUF = Find-One $Models "*gemma-4-E4B*q4_0*.gguf" "Gemma 4 E4B QAT"
$env:QWEN_GGUF = Find-One $Models "*Qwen3.5-9B*Q4_K_M*.gguf" "Qwen3.5 9B Q4_K_M"
# LM Studio ships nomic as a bundled model, outside the models folder.
$env:NOMIC_GGUF = Find-One @($Models, (Join-Path $env:USERPROFILE ".lmstudio\.internal\bundled-models")) "*nomic-embed-text-v1.5*.gguf" "nomic-embed-text v1.5"

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
