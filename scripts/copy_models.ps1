# Copy the model files out of LM Studio's folders into the project's own models folder,
# so LM Studio can be uninstalled. Copies, never moves: LM Studio keeps working until
# you uninstall it, and nothing is lost if a copy fails.
#
#   powershell -ExecutionPolicy Bypass -File scripts\copy_models.ps1
#
# Keeps LM Studio's <publisher>\<repo>\<file>.gguf layout (the same one the model vault
# uses). The bundled nomic embedding model lives outside LM Studio's models folder and is
# copied too. Each copied file is checked by size afterwards.
param(
    [string]$To = "D:\models",
    [string]$From = (Join-Path $env:USERPROFILE ".lmstudio\models"),
    [string]$Bundled = (Join-Path $env:USERPROFILE ".lmstudio\.internal\bundled-models")
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $To | Out-Null

foreach ($source in @($From, $Bundled) | Where-Object { Test-Path $_ }) {
    Write-Host "Copying $source -> $To"
    # /E subfolders, /XO skip files already copied, /NFL /NDL quieter; exit codes below 8 mean success.
    robocopy $source $To *.gguf /E /XO /R:2 /W:5 /NFL /NDL /NP | Out-Host
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }
    $global:LASTEXITCODE = 0
}

$bad = @()
foreach ($source in @($From, $Bundled) | Where-Object { Test-Path $_ }) {
    foreach ($file in Get-ChildItem $source -Recurse -File -Filter *.gguf) {
        $copy = Join-Path $To $file.FullName.Substring($source.Length).TrimStart("\")
        if (-not (Test-Path $copy) -or (Get-Item $copy).Length -ne $file.Length) { $bad += $file.FullName }
    }
}
$total = (Get-ChildItem $To -Recurse -File -Filter *.gguf | Measure-Object Length -Sum)
Write-Host ("{0} GGUF files, {1:N1} GB in {2}" -f $total.Count, ($total.Sum / 1GB), $To)
if ($bad) { throw "Missing or different size in ${To}:`n$($bad -join "`n")" }
Write-Host "All files copied. LM Studio's own copies are untouched."
