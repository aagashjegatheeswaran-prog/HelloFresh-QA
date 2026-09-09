"""Publish monitoring findings in the GitHub Actions run summary."""
import html
import json
import os
from pathlib import Path


def cell(value):
    return html.escape(str(value)).replace('|', '&#124;').replace('\n', '<br>')


def render(path=Path('monitor-data/latest.json')):
    if not path.exists():
        return '# Daily QA Monitor\n\nNo monitoring report was produced. Check the installation, validation, and runner logs. This is not a healthy-site result.\n'
    report = json.loads(path.read_text())
    lines = ['# Daily QA Monitor', '', f"Overall result: **{cell(report['status'])}**", '',
             '| Brand | Device | Result | Attempts |', '| --- | --- | --- | --- |']
    for r in report['reports']:
        lines.append(f"| {cell(r['brand'])} | {cell(r['device'])} | {cell(r['status'])} | {r['attempts']} |")
    lines += ['', '## Findings and coverage', '', '| Brand / device | Check | Status | Evidence |', '| --- | --- | --- | --- |']
    for r in report['reports']:
        for c in r['checks']:
            if c['status'] != 'passed':
                lines.append(f"| {cell(r['brand'])} / {cell(r['device'])} | {cell(c['title'])} | {cell(c['status'])} | {cell(c.get('actual', ''))} |")
    lines += ['', 'Download the run artifact for JSON, SQLite, screenshots, and Playwright failure traces.',
              'Skipped funnel or voucher checks mean those features were not tested. A green Actions run does not establish full funnel health.', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    summary = render()
    target = os.environ.get('GITHUB_STEP_SUMMARY')
    if target:
        with open(target, 'a', encoding='utf-8') as f:
            f.write(summary)
    else:
        print(summary)
