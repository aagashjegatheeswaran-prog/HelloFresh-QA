# HelloFresh QA — daily GitHub Actions monitor

The **Daily QA Monitor** workflow runs at **07:00 America/Toronto**, follows daylight saving time, and also runs manually or when monitoring code changes on main. GitHub may delay scheduled starts.

Open [Actions](https://github.com/aagashjegatheeswaran-prog/HelloFresh-QA/actions/workflows/daily-monitor.yml), select a run, and read its summary. Download its `qa-monitor` artifact for the JSON report, SQLite database, screenshots, and failure traces (30-day retention). No AI API key is required.

Current coverage: HelloFresh Canada, Chef's Plate Canada, and Factor Canada on desktop and mobile. Checks include landing-page response, page readiness time (5-second threshold), HTML image loading, HTTP failures, and JavaScript errors. Failures get a fresh-browser retry.

**Funnel and voucher coverage is not configured in the supplied archive.** These checks are explicitly skipped, not reported as passing. Configure exact pre-purchase steps and eligible voucher expectations in `monitor-config.json` to extend coverage. No order is placed. Each Actions run has a fresh database; cross-day issue history is available in separate artifacts, not a shared live dashboard. The Streamlit dashboard is included for local use and is not hosted by this workflow.

Failed or blocked checks fail the Actions job. Review, flaky, and partial results are visible in the summary even if the job is green. The fixture suite runs before production checks. This workflow does not send Slack or email messages.

The Actions schedule is defined in `.github/workflows/daily-monitor.yml`; editing the local dashboard schedule does not change it. GitHub disables schedules in public repositories after 60 days without repository activity; re-enable from Actions if that occurs.

## Local dashboard

```bash
python -m pip install -r requirements-monitor.txt
python -m playwright install chromium
python -m streamlit run monitor_app.py
```

For local monitoring: `python -m monitoring.runner`. The optional local scheduler is `python -m monitoring.scheduler`; it is unnecessary when GitHub Actions is active.

## Validation

Five logic tests passed locally. Local Chromium download was blocked by network access; the Actions workflow installs Chromium and runs all eleven tests, including six browser fixture tests, before monitoring the real sites.
