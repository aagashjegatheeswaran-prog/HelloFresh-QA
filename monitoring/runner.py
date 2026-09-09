"""Scripted Playwright checks; no AI credentials required."""
import argparse
import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

from .config import DEFAULT_CONFIG, ROOT, load_config
from .store import connect, now, run_lock, save_result

PAYMENT = re.compile(r'place.{0,5}order|pay now|complete.{0,5}purchase|confirm.{0,5}(order|payment)|submit.{0,5}(order|payment)|subscribe|buy now|credit.?card|card.?number|cvv|cvc|security.?code|iban|commander|payer|confirmer.{0,5}commande', re.I)
BOT = re.compile(r'verify (that )?you are human|checking your browser|access denied|unusual traffic|captcha|security verification', re.I)


class Blocked(Exception):
    pass


def clean_url(url):
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, '', '', ''))


def check(check_id, title, status, expected='', actual='', severity='High', **extra):
    return dict(id=check_id, title=title, status=status, expected=expected,
                actual=actual, severity=severity, **extra)


def interact(page, action, selector, value=''):
    """Only configured actions run; refuse recognizable purchase/payment targets."""
    loc = page.locator(selector)
    loc.wait_for(state='visible')
    if loc.count() != 1:
        raise Blocked('Selector matched more than one element; refine the configuration.')
    description = loc.evaluate("e => [e.innerText,e.name,e.id,e.type,e.getAttribute('aria-label'),e.getAttribute('placeholder'),e.getAttribute('autocomplete')].filter(Boolean).join(' ')")
    if PAYMENT.search(description + ' ' + selector):
        raise Blocked('Stopped before a payment or purchase control.')
    if action == 'click':
        loc.click()
    elif action == 'fill':
        loc.fill(value)
    elif action == 'select':
        loc.select_option(value)


def browser_attempt(browser, campaign, device, folder, timezone):
    from playwright.sync_api import TimeoutError as PWTimeout, expect
    folder.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(
        viewport={'width':390,'height':844} if device == 'mobile' else {'width':1440,'height':1000},
        is_mobile=device == 'mobile', has_touch=device == 'mobile',
        locale=campaign.get('locale', 'en-CA'), timezone_id=timezone)
    context.set_default_timeout(12000)
    context.tracing.start(screenshots=True, snapshots=True, sources=False)
    page = context.new_page()
    checks, snapshots, responses, errors, transport = [], [], [], [], []
    page.on('response', lambda r: responses.append((r.status, clean_url(r.url), r.request.resource_type)))
    page.on('pageerror', lambda e: errors.append(str(e)[:500]))
    page.on('requestfailed', lambda r: transport.append(f'{clean_url(r.url)}: {r.failure}') if r.resource_type in ('document','xhr','fetch','script') else None)
    current = 'landing'

    def evidence(label):
        path = folder / f'{len(snapshots):02d}-{label}.png'
        try:
            page.screenshot(path=str(path), full_page=True,
                            mask=[page.locator('input, textarea')], timeout=8000)
            snapshots.append(str(path))
            return str(path)
        except Exception:
            return ''

    def inspect(label, ready, start):
        page.locator(ready).first.wait_for(state='visible')
        elapsed = round((time.perf_counter() - start) * 1000)
        text = page.locator('body').inner_text(timeout=5000)[:16000]
        if BOT.search(text):
            raise Blocked('Bot challenge or access restriction detected. No bypass attempted.')
        limit = campaign.get('load_limit_ms', 5000)
        checks.append(check(f'{label}:load', 'Page readiness time', 'failed' if elapsed > limit else 'passed',
                            f'Visible {ready} within {limit} ms', f'{elapsed} ms', 'Medium', duration_ms=elapsed))
        # Trigger lazy images in bounded scrolls, then wait for visible <img> elements.
        page.evaluate('''async () => {
          const max = Math.min(document.documentElement.scrollHeight, 16000);
          for(let y=0; y<max; y+=Math.max(600,innerHeight)) {
            scrollTo(0,y); await new Promise(r=>setTimeout(r,100));
          }
          await Promise.race([Promise.all([...document.images].map(i=>i.complete ? Promise.resolve() :
            new Promise(r=>{i.addEventListener('load',r,{once:true});i.addEventListener('error',r,{once:true});}))),
            new Promise(r=>setTimeout(r,4000))]);
          scrollTo(0,0);
        }''')
        images = page.evaluate('''() => [...document.images].filter(i=>i.getBoundingClientRect().width>8 && i.getBoundingClientRect().height>8)
          .map(i=>({src:i.currentSrc||i.src,complete:i.complete,width:i.naturalWidth}))''')
        broken = [clean_url(i['src']) for i in images if i['complete'] and i['width'] == 0]
        pending = [clean_url(i['src']) for i in images if not i['complete']]
        checks.append(check(f'{label}:images', 'Image loading', 'failed' if broken else 'review' if pending else 'passed',
                            'Visible HTML images decode successfully',
                            f'{len(broken)} broken; {len(pending)} still pending; {len(images)} inspected. ' + '; '.join((broken+pending)[:12]), 'Medium'))
        # HTTP failures in scripts/API/document requests are evidence for review;
        # image failures are handled above, and ads are not labelled checkout failures.
        failures = [f'{status} {url}' for status,url,typ in responses if status >= 400 and typ in ('document','xhr','fetch','script')] + transport
        checks.append(check(f'{label}:http', 'HTTP requests', 'review' if failures else 'passed',
                            'No failing page, script, or API requests', '\n'.join(failures[:20]) or 'No HTTP errors observed', 'Medium'))
        checks.append(check(f'{label}:javascript', 'Browser errors', 'review' if errors else 'passed',
                            'No uncaught JavaScript errors', '\n'.join(errors[:10]) or 'None observed', 'Low'))
        responses.clear(); errors.clear(); transport.clear()
        shot = evidence(label)
        for item in checks:
            if item['id'].startswith(label + ':'):
                item['screenshot'] = shot
                item['url'] = clean_url(page.url)
        return elapsed

    try:
        start = time.perf_counter()
        response = page.goto(campaign['url'], wait_until='domcontentloaded', timeout=45000)
        if response and response.status in (401,403,429):
            raise Blocked(f'Access restricted: HTTP {response.status}.')
        if response and response.status >= 400:
            checks.append(check('landing:http_status','Landing page response','failed','HTTP 2xx/3xx',f'HTTP {response.status}'))
        else:
            checks.append(check('landing:http_status','Landing page response','passed','HTTP 2xx/3xx',str(response.status if response else 'No document response')))
        inspect('landing', campaign['ready_selector'], start)
        checks.append(check('landing:journey', 'Landing page reachable', 'passed'))
        for index, step in enumerate(campaign.get('steps', [])):
            current = f'step-{index+1}-{re.sub(r"[^a-zA-Z0-9-]", "-", step["name"])}'
            start = time.perf_counter()
            action = step['action']
            if action == 'assert_text':
                expect(page.locator(step['selector'])).to_have_text(step['expected_text'])
            elif action == 'assert_visible':
                expect(page.locator(step['selector'])).to_be_visible()
            else:
                value = os.environ.get(step.get('value_env',''), step.get('value',''))
                if step.get('value_env') and not value:
                    raise Blocked(f'Missing environment value for step {step["name"]}.')
                interact(page, action, step['selector'], value)
            inspect(current, step.get('ready_selector', step['selector']), start)
            checks.append(check(f'{current}:journey',step['name'],'passed',screenshot=snapshots[-1] if snapshots else ''))
        checks.append(check('funnel:coverage', 'Configured funnel journey', 'passed' if campaign.get('steps') else 'skipped',
                            'Execute the configured pre-purchase steps', 'All configured steps completed' if campaign.get('steps') else 'No funnel steps configured; landing checks only.'))
        voucher = campaign.get('voucher', {})
        current = 'voucher'
        if not voucher.get('enabled'):
            checks.append(check('voucher:journey','Voucher discount','skipped',actual='No voucher configured.'))
        elif not voucher['valid_from'] <= datetime.now(ZoneInfo(timezone)).date().isoformat() <= voucher['valid_until']:
            checks.append(check('voucher:journey','Voucher discount','skipped',actual='Outside configured validity dates.'))
        else:
            interact(page, 'fill', voucher['input_selector'], voucher['code'])
            interact(page, 'click', voucher['apply_selector'])
            # Exact expected text, not a heuristic "code applied" banner.
            expect(page.locator(voucher['result_selector'])).to_have_text(voucher['expected_text'])
            checks.append(check('voucher:journey','Voucher discount','passed',voucher['expected_text'],
                                page.locator(voucher['result_selector']).inner_text(), screenshot=evidence('voucher')))
    except Blocked as e:
        checks.append(check(f'{current}:journey','Journey blocked','blocked','Reach the configured checkpoint',str(e),screenshot=evidence('blocked')))
    except (AssertionError, PWTimeout) as e:
        checks.append(check(f'{current}:journey','Expected funnel result not reached','failed','Reach the configured checkpoint',
                            str(e)[:1500],screenshot=evidence('failure')))
    except Exception as e:
        checks.append(check(f'{current}:journey','Browser run could not complete','blocked','Complete the browser check',
                            str(e)[:1500],screenshot=evidence('error')))
    finally:
        # Retain traces with failures only; screenshots for all checkpoints.
        trace = folder / 'trace.zip'
        try:
            if any(c['status'] in ('failed','blocked','review') for c in checks):
                context.tracing.stop(path=str(trace))
                for c in checks:
                    if c['status'] in ('failed','blocked','review'):
                        c['trace'] = str(trace)
            else:
                context.tracing.stop()
        finally:
            context.close()
    return checks


def merge_attempts(first, second):
    if second is None:
        return first
    original = {c['id']: c for c in first}
    retry = {c['id']: c for c in second}
    result = []
    for key in dict.fromkeys([*original, *retry]):
        a, b = original.get(key), retry.get(key)
        if a and a['status'] in ('failed','blocked','review'):
            if b and b['status'] == 'passed':
                result.append({**a, 'status':'flaky','severity':'Medium','retry':'Passed on retry; first-attempt evidence retained'})
            elif b and b['status'] in ('failed','blocked','review'):
                result.append({**b,'retry':'Observed on both attempts','first_attempt':a})
            else:
                result.append({**a,'retry':'Retry did not reach this check; not confirmed'})
        else:
            result.append(b or a)
    return result


def report_status(checks):
    states = {c['status'] for c in checks}
    for state in ('failed','blocked','flaky','review'):
        if state in states:
            return state
    return 'partial' if 'skipped' in states else 'passed'


def run(config_path=DEFAULT_CONFIG, data_dir=None):
    from playwright.sync_api import sync_playwright
    cfg = load_config(config_path)
    root = Path(data_dir or os.environ.get('QA_DATA_DIR', ROOT / 'monitor-data')).resolve()
    with run_lock(root):
        db = connect(root)
        run_id = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
        db.execute("UPDATE runs SET status='interrupted',finished=? WHERE status='running'", (now(),))
        db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(run_id,now(),None,'running',''))
        db.commit()
        reports = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    for c in cfg['campaigns']:
                        if not c.get('enabled', True):
                            continue
                        for device in c['devices']:
                            # IDs never become arbitrary filesystem paths.
                            safe = re.sub(r'[^a-zA-Z0-9_-]', '-', c['id'])
                            folder = root / 'artifacts' / run_id / safe / device
                            first = browser_attempt(browser,c,device,folder/'attempt-1',cfg['timezone'])
                            second = None
                            if any(x['status'] in ('failed','blocked','review') for x in first):
                                second = browser_attempt(browser,c,device,folder/'attempt-2',cfg['timezone'])
                            checks = merge_attempts(first,second)
                            report = dict(campaign=c['id'],brand=c['brand'],device=device,checks=checks,
                                          status=report_status(checks),url=c['url'],attempts=2 if second is not None else 1)
                            save_result(db,run_id,report)
                            reports.append(report)
                finally:
                    browser.close()
            status = report_status([{'status':r['status']} for r in reports]) if reports else 'empty'
            if status == 'passed' and any(r['status']=='partial' for r in reports):
                status='partial'
            summary = {'run_id':run_id,'status':status,'reports':reports}
            (root / 'latest.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
            db.execute('UPDATE runs SET finished=?,status=?,summary=? WHERE id=?',(now(),status,json.dumps(summary),run_id))
            db.commit()
            return summary
        except Exception as e:
            db.execute('UPDATE runs SET finished=?,status=?,summary=? WHERE id=?',(now(),'blocked',json.dumps({'error':str(e)}),run_id))
            db.commit()
            raise
        finally:
            db.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default=str(DEFAULT_CONFIG))
    parser.add_argument('--data-dir')
    args=parser.parse_args()
    try:
        result=run(args.config,args.data_dir)
        print(json.dumps({'run_id':result['run_id'],'status':result['status'],'journeys':len(result['reports'])}))
        raise SystemExit(1 if result['status'] in ('failed','blocked') else 0)
    except Exception as exc:
        print(f'Monitoring could not complete: {exc}')
        raise SystemExit(2)
