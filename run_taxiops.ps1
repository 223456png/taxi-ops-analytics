param(
    [switch]$NoOpen,
    [switch]$SkipData,
    [switch]$SkipRun
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$root = $PSScriptRoot
$repo = if (Test-Path (Join-Path $root "src\01_etl.py")) { $root } else { Join-Path $root "TaxiOps" }
$zipUrl = "https://codeload.github.com/223456png/taxi-ops-analytics/zip/refs/heads/main"

function Download($url, $out) {
    if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
        & curl.exe -L --retry 3 --retry-all-errors --max-time 600 -o $out $url
        if ($LASTEXITCODE -ne 0) { throw "Download failed: $url" }
    } else {
        Invoke-WebRequest -Uri $url -OutFile $out
    }
}

if (-not (Test-Path (Join-Path $repo "src\01_etl.py"))) {
    Write-Host "Downloading latest repository..."
    $zip = Join-Path $root "taxiops_latest.zip"
    $tmp = Join-Path $root "_taxiops_extract"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Download $zipUrl $zip
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $src = Join-Path $tmp "taxi-ops-analytics-main"
    if (-not (Test-Path $repo)) { New-Item -ItemType Directory -Path $repo | Out-Null }
    Copy-Item (Join-Path $src "*") $repo -Recurse -Force
    Remove-Item -Recurse -Force $tmp
    Remove-Item -Force $zip
}

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
$pyArgs = @()
if (-not $python) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { $python = $py.Source; $pyArgs = @("-3") }
    else { throw "Python not found. Install Python 3.10+ and retry." }
}

Write-Host "Installing Python dependencies..."
& $python @pyArgs -m pip install -r (Join-Path $repo "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

if (-not $SkipData) {
    $data = Join-Path $repo "data"
    New-Item -ItemType Directory -Force -Path $data | Out-Null
    $base = "https://d37ci6vzurychx.cloudfront.net/trip-data"
    foreach ($y in 2023, 2024) {
        foreach ($m in "01", "02", "03") {
            $f = "yellow_tripdata_$y-$m.parquet"
            $out = Join-Path $data "yellow_$y-$m.parquet"
            if ((Test-Path $out) -and ((Get-Item $out).Length -gt 5MB)) { continue }
            Write-Host "Downloading $f ..."
            Download "$base/$f" $out
        }
    }
    $zl = Join-Path $data "taxi_zone_lookup.csv"
    if (-not (Test-Path $zl)) {
        Download "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv" $zl
    }
}

if (-not $SkipRun) {
    Push-Location $repo
    try {
        Write-Host "Running ETL..."
        & $python @pyArgs "src\01_etl.py"
        if ($LASTEXITCODE -ne 0) { throw "01_etl.py failed." }
        $dataDir = (Join-Path $repo "powerbi\exports") + "\"
        Write-Host "Generating Power BI project..."
        & $python @pyArgs "src\05_make_pbip.py" --datadir $dataDir
        if ($LASTEXITCODE -ne 0) { throw "05_make_pbip.py failed. Close Power BI and retry." }
    } finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "Done."
Write-Host "Folder: $repo"
if (-not $NoOpen) {
    $pbip = Join-Path $repo "powerbi\TaxiOps.pbip"
    if (Test-Path $pbip) { Start-Process $pbip }
    else { Write-Host "PBIP not found: $pbip" }
}
Write-Host "Expected: page 2 deviation ~ -31.9%; page 4 Manhattan excess gap ~ -563."
Write-Host "If values differ, double-click verify_expected.cmd."
