# Install a standalone llama.cpp CUDA build (llama-server and its CUDA DLLs) from the
# official GitHub releases, so the project no longer needs LM Studio's copy.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_llama_cpp.ps1              # newest release
#   powershell -ExecutionPolicy Bypass -File scripts\install_llama_cpp.ps1 -Tag b9999   # a fixed one
#
# Unpacks into <Dest>\<tag>\ and writes <Dest>\current.txt with that folder, which
# start_llama_swap.ps1 and run_stage0.ps1 read. Older versions stay, so going back is
# editing current.txt. No admin rights needed.
param(
    [string]$Tag = "latest",
    [string]$Dest = (Join-Path $env:USERPROFILE "tools\llama.cpp"),
    [string]$Cuda = "12"          # CUDA major version of the build; the RTX 3060 driver runs 12.x
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is very slow with the progress bar on

$api = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
$headers = @{ "User-Agent" = "local-ai-research-assistant" }
if ($Tag -eq "latest") {
    # The b<N> builds are marked pre-release, so /releases/latest is a marker release with no binaries.
    # The parentheses make PowerShell 5.1 unroll the JSON array before filtering.
    $release = (Invoke-RestMethod -Uri "${api}?per_page=20" -Headers $headers) |
        Where-Object { $_.assets.name -match "-bin-win-cuda-$Cuda\." } | Select-Object -First 1
    if (-not $release) { throw "No Windows CUDA $Cuda build among the last 20 llama.cpp releases." }
} else {
    $release = Invoke-RestMethod -Uri "$api/tags/$Tag" -Headers $headers
}
$tagName = $release.tag_name
$bin = $release.assets | Where-Object { $_.name -match "^llama-.*-bin-win-cuda-$Cuda\.[\d.]+-x64\.zip$" } | Select-Object -First 1
if (-not $bin) { throw "Release $tagName has no Windows CUDA $Cuda build. Assets: $($release.assets.name -join ', ')" }
$cudaVersion = [regex]::Match($bin.name, "cuda-([\d.]+)-x64").Groups[1].Value
$rt = $release.assets | Where-Object { $_.name -eq "cudart-llama-bin-win-cuda-$cudaVersion-x64.zip" } | Select-Object -First 1

$target = Join-Path $Dest $tagName
if (Test-Path (Join-Path $target "llama-server.exe")) {
    Write-Host "$tagName is already installed in $target"
} else {
    New-Item -ItemType Directory -Force $target | Out-Null
    foreach ($asset in @($bin, $rt) | Where-Object { $_ }) {
        $zip = Join-Path $env:TEMP $asset.name
        Write-Host "Downloading $($asset.name) ($([math]::Round($asset.size / 1MB)) MB)"
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath $target -Force
        Remove-Item $zip
    }
    # Some releases nest the files one folder down; flatten so llama-server.exe sits at the top.
    $exe = Get-ChildItem $target -Recurse -Filter "llama-server.exe" | Select-Object -First 1
    if (-not $exe) { throw "llama-server.exe not found after unpacking $($bin.name)" }
    if ($exe.DirectoryName -ne $target) {
        Get-ChildItem $exe.DirectoryName | Move-Item -Destination $target -Force
    }
}
Set-Content -Path (Join-Path $Dest "current.txt") -Value $target -Encoding ascii
& (Join-Path $target "llama-server.exe") --version
Write-Host "llama.cpp $tagName (CUDA $cudaVersion) ready: $target"
