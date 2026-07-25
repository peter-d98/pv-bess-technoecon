param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(0, 11)]
    [int]$MachineIndex,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 14)]
    [int]$MaxProc = 12
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Write-Host "START $(Get-Date -Format o)" -ForegroundColor Cyan
Write-Host "MachineIndex=$MachineIndex MaxProc=$MaxProc" -ForegroundColor Cyan

# Resolve script root so the launcher can be run from any directory.
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$PY = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $PY)) {
    throw "Python not found at $PY. Create the venv first: py -3 -m venv .venv"
}

$Machine = "machine{0:d2}" -f ($MachineIndex + 1)

$CacheDir = Join-Path $Root "results\cache\sweep_v2"
$LogsDir = Join-Path $Root "results\logs"
$PartsDir = Join-Path $Root "results\parts"
New-Item -ItemType Directory -Force -Path $CacheDir, $LogsDir, $PartsDir | Out-Null

$locs = @("inverness", "manchester", "plymouth")
$pvs = @("1", "2", "3", "4", "5", "6")
$tars = @("flat", "e7", "agile")
$pens = @("0", "0.01", "0.03", "0.05", "0.07", "0.09")

# 324 MILP jobs + 54 rules jobs, deterministic ordering.
$all = @()
foreach ($l in $locs) {
    foreach ($pv in $pvs) {
        foreach ($t in $tars) {
            foreach ($p in $pens) {
                $all += ,@("milp", $l, $pv, $t, $p)
            }
            $all += ,@("rules", $l, $pv, $t, "na")
        }
    }
}

$mine = @()
for ($i = 0; $i -lt $all.Count; $i++) {
    if (($i % 12) -eq $MachineIndex) {
        $mine += ,$all[$i]
    }
}

Write-Host "total jobs $($all.Count); this machine $($mine.Count)" -ForegroundColor Green

$running = @()
$launched = 0

foreach ($j in $mine) {
    while (@($running | Where-Object { -not $_.HasExited }).Count -ge $MaxProc) {
        Start-Sleep -Seconds 5
        $running = @($running | Where-Object { -not $_.HasExited })
    }

    $kind = $j[0]
    $loc = $j[1]
    $pv = $j[2]
    $tar = $j[3]
    $pen = $j[4]
    $tag = "${kind}_${loc}_pv${pv}_${tar}_${pen}"

    if ($kind -eq "milp") {
        $ctrl = @("--controllers", "milp", "--deg-scenarios", "${pen}:6000")
    }
    else {
        $ctrl = @("--controllers", "self_consumption", "self_consumption_tou")
    }

    $argList = @(
        "scripts\run_sweep.py",
        "--locations", $loc,
        "--pv-sizes", $pv,
        "--tariffs", $tar,
        "--solver", "SCIPY",
        "--cache-dir", $CacheDir,
        "--out", (Join-Path $PartsDir "part_${tag}.csv"),
        "--peak-out", (Join-Path $PartsDir "peaks_${tag}.csv")
    ) + $ctrl

    $proc = Start-Process -FilePath $PY -ArgumentList $argList `
        -RedirectStandardOutput (Join-Path $LogsDir "${tag}.out.txt") `
        -RedirectStandardError (Join-Path $LogsDir "${tag}.err.txt") `
        -WindowStyle Hidden -PassThru

    $running += $proc
    $launched += 1
    Write-Host ("launched {0}/{1}: {2} (pid {3})" -f $launched, $mine.Count, $tag, $proc.Id)
}

Write-Host "All jobs launched. Waiting for completion..." -ForegroundColor Yellow
$running | ForEach-Object { $_.WaitForExit() }

$errs = Get-ChildItem $LogsDir -Filter "*.err.txt" | Where-Object { $_.Length -gt 0 }
$curveCount = (Get-ChildItem $CacheDir -Filter "*.pkl" -ErrorAction SilentlyContinue).Count

Write-Host "DONE $Machine - curves: $curveCount" -ForegroundColor Green
if ($errs.Count -gt 0) {
    Write-Host "Non-empty error logs detected:" -ForegroundColor Red
    $errs | ForEach-Object { Write-Host "  $($_.Name)" -ForegroundColor Red }
    exit 1
}

Write-Host "No non-empty error logs found." -ForegroundColor Green
