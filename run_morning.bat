@echo off
echo =============================================
echo  MORNING JOB HUNT - Vikram
echo  Started: %date% %time%
echo =============================================
echo.
cd /d D:\Job

:: ── Step 1: Multi-Portal Discovery ──
echo [1/3] Multi-portal discovery (LinkedIn + Indeed + Foundit + IIMJobs)...
echo       This fetches ALL tracks across 4 portals.
echo       Expect 5-15 minutes.
echo.
python multi_portal_job_hunter.py
echo.
echo [1/3] Discovery complete.
echo.

:: ── Step 2: Naukri Batch Apply ──
echo [2/3] Naukri batch-apply for SM...
python naukri_job_hunter.py --track SM --batch-apply --interval 1
echo.

echo [2/3] Naukri batch-apply for PM...
python naukri_job_hunter.py --track PM --batch-apply --interval 1
echo.

echo [2/3] Naukri batch-apply for DIR...
python naukri_job_hunter.py --track DIR --batch-apply --interval 1
echo.

:: ── Step 3: Report ──
echo [3/3] Generating report...
python multi_portal_job_hunter.py --status
python naukri_job_hunter.py --stats
python multi_portal_job_hunter.py --report
echo.
echo =============================================
echo  ALL DONE! Check job_queue_report.html
echo =============================================
pause
