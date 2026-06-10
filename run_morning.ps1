param(
    [switch]$SkipDiscovery,
    [switch]$SkipNaukri
)

$start = Get-Date
$log = "run_morning_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

function Log { param([string]$msg) $timestamp = Get-Date -Format 'HH:mm:ss'; "$timestamp  $msg" | Tee-Object -FilePath $log -Append }

Log "=== MORNING JOB HUNT START ==="
Log ""

# ── Step 1: Multi-Portal Discovery ──────────────────────────────────────────
if (-not $SkipDiscovery) {
    Log "[1/3] Multi-portal job discovery (LinkedIn + Indeed + Foundit + IIMJobs)..."
    Log "      This fetches ALL tracks (SM / PM / DIR) across 4 portals."
    Log "      Expect 5-15 minutes depending on network."
    Log ""

    Push-Location -LiteralPath "D:\Job"
    python multi_portal_job_hunter.py --cloud 2>&1 | ForEach-Object { Log $_ }
    Pop-Location

    Log ""
    Log "[1/3] Discovery complete."
} else {
    Log "[1/3] SKIPPED (--SkipDiscovery)"
}
Log ""

# ── Step 2: Naukri Batch Apply ──────────────────────────────────────────────
if (-not $SkipNaukri) {
    foreach ($track in @("SM", "PM", "DIR")) {
        Log "[2/3] Naukri batch-apply for $track..."
        Log "      Applies to eligible jobs with 1-min interval between applies."
        Log ""

        Push-Location -LiteralPath "D:\Job"
        python naukri_job_hunter.py --track $track --batch-apply --interval 1 2>&1 | ForEach-Object { Log $_ }
        Pop-Location

        Log ""
        Log "[2/3] $track batch-apply complete."
        Log ""
    }
} else {
    Log "[2/3] SKIPPED (--SkipNaukri)"
}

# ── Step 3: Generate Report + Status ────────────────────────────────────────
Log "[3/3] Generating HTML report and status summary..."
Log ""

Push-Location -LiteralPath "D:\Job"
Log "--- Status Summary ---"
python multi_portal_job_hunter.py --status 2>&1 | ForEach-Object { Log $_ }
Log ""
Log "--- Naukri Stats ---"
python naukri_job_hunter.py --stats 2>&1 | ForEach-Object { Log $_ }
Log ""
Log "--- Generating HTML Report ---"
python multi_portal_job_hunter.py --report 2>&1 | ForEach-Object { Log $_ }
Pop-Location

Log ""
$elapsed = [math]::Round(((Get-Date) - $start).TotalMinutes, 1)
Log "=== MORNING JOB HUNT COMPLETE ($elapsed min) ==="
Log "Log saved to: $log"
Log ""
Log "Next: python multi_portal_job_hunter.py --review    (interactive review)"
Log "      python multi_portal_job_hunter.py --report    (open HTML report)"
