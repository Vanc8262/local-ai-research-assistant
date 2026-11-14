# Download the offline copies (ZIM files) that the search router's offline layer serves
# through kiwix-serve: English Wikipedia without pictures, the DevDocs manuals for the
# codex topics, and three Stack Exchange sites. Stack Overflow (107 GB) only with
# -WithStackOverflow.
#
#   powershell -ExecutionPolicy Bypass -File scripts\get_kiwix.ps1 [-To D:\kiwix] [-WithStackOverflow]
#
# Picks the newest file of each series from download.kiwix.org and resumes a part-done
# download, so it is safe to rerun (also monthly, to refresh). Older files are left in
# place; delete them by hand once the new ones serve. Then start kiwix-serve:
#
#   $env:KIWIX_DIR = "D:\kiwix"; docker compose up -d kiwix
param(
    [string]$To = "D:\kiwix",
    [switch]$WithStackOverflow,
    [switch]$ListOnly
)
$ErrorActionPreference = "Stop"
$base = "https://download.kiwix.org/zim"
# Smallest first, so the manuals and Stack Exchange sites serve while Wikipedia downloads.
$wanted = @()
foreach ($doc in "python", "pandas", "numpy", "scikit-learn", "pytorch", "docker", "kubernetes",
                 "terraform", "git", "postgresql", "fastapi") {
    $wanted += , @("devdocs", "devdocs_en_$doc")
}
$wanted += , @("stack_exchange", "ai.stackexchange.com_en_all")
$wanted += , @("stack_exchange", "datascience.stackexchange.com_en_all")
$wanted += , @("stack_exchange", "stats.stackexchange.com_en_all")
$wanted += , @("wikipedia", "wikipedia_en_all_nopic")
if ($WithStackOverflow) { $wanted += , @("stack_exchange", "stackoverflow.com_en_all") }

New-Item -ItemType Directory -Force $To | Out-Null
$listings = @{}
foreach ($pair in $wanted) {
    $dir, $prefix = $pair
    if (-not $listings.ContainsKey($dir)) {
        $listings[$dir] = (Invoke-WebRequest -UseBasicParsing "$base/$dir/").Content
    }
    $pattern = 'href="(' + [regex]::Escape($prefix) + '_\d{4}-\d{2}\.zim)"'
    $names = [regex]::Matches($listings[$dir], $pattern) | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
    if (-not $names) { Write-Warning "no file found for $prefix in $base/$dir/"; continue }
    $name = @($names)[-1]
    $target = Join-Path $To $name
    if ($ListOnly) { Write-Host "$dir/$name"; continue }
    if (Test-Path $target) { Write-Host "Have $name"; continue }
    Write-Host "Downloading $name"
    # Into a .part file, so kiwix-serve never opens a half-written ZIM. -C - resumes it;
    # --fail stops on an HTTP error instead of saving the error page.
    curl.exe -L --fail --retry 5 --retry-delay 10 -C - -o "$target.part" "$base/$dir/$name"
    if ($LASTEXITCODE -eq 0) { Move-Item "$target.part" $target }
    else { Write-Warning "download of $name stopped (curl exit $LASTEXITCODE); rerun to resume" }
}
Write-Host "Done. Files in ${To}:"
Get-ChildItem $To -Filter *.zim | Sort-Object Name | Format-Table Name, @{n = "GB"; e = { [math]::Round($_.Length / 1GB, 2) } }
