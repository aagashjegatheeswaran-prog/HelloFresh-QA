"""Integration tests run against an isolated local storefront, never real orders."""
import json
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from monitoring.runner import browser_attempt, merge_attempts, report_status, run, check, interact, Blocked
from monitoring.store import connect, save_result, run_lock
from monitoring.scheduler import due


class LogicTests(unittest.TestCase):
    def test_retry_preserves_initial_failure(self):
        merged=merge_attempts([check('x','Image','failed',actual='broken')],[check('x','Image','passed')])
        self.assertEqual(merged[0]['status'],'flaky')
        self.assertEqual(merged[0]['actual'],'broken')

    def test_blocked_retry_does_not_confirm_unreached_check(self):
        merged=merge_attempts([check('voucher','Voucher','failed')],[check('landing','Load','blocked')])
        self.assertIn('not confirmed',merged[0]['retry'])

    def test_issue_lifecycle(self):
        with tempfile.TemporaryDirectory() as d:
            db=connect(d)
            def save(status):
                save_result(db,'run',dict(campaign='hf',brand='HelloFresh',device='mobile',status=status,checks=[check('image','Image',status)]))
            save('failed');save('failed')
            self.assertEqual(db.execute('SELECT occurrences FROM issues').fetchone()[0],2)
            save('skipped')
            self.assertEqual(db.execute('SELECT status FROM issues').fetchone()[0],'Open')
            save('passed')
            self.assertEqual(db.execute('SELECT status FROM issues').fetchone()[0],'Resolved')
            save('failed')
            self.assertEqual(db.execute('SELECT status FROM issues').fetchone()[0],'Open')
            db.close()

    def test_schedule(self):
        local=datetime(2026,9,9,7,1,tzinfo=ZoneInfo('America/Toronto'))
        self.assertTrue(due(local,'07:00','2026-09-08'))
        self.assertFalse(due(local,'07:00','2026-09-09'))
        self.assertFalse(due(local,'08:00','2026-09-08'))

    def test_overlapping_runs_locked(self):
        with tempfile.TemporaryDirectory() as d:
            with run_lock(d):
                with self.assertRaises(RuntimeError):
                    with run_lock(d): pass


class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        if self.path=='/missing.png':
            self.send_response(404);self.end_headers();return
        if self.path=='/blocked':
            self.send_response(403);self.end_headers();return
        self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
        broken='<img src="/missing.png" width="100" height="100">' if self.path=='/broken' else ''
        discount='Invalid voucher' if self.path=='/wrong-voucher' else '$20 off your first box'
        self.wfile.write(f'''<!doctype html><html><body><h1>QA fixture</h1>{broken}
        <button id="start" onclick="document.querySelector('#plan').hidden=false">Get started</button>
        <div id="plan" hidden>Choose plan</div>
        <input id="voucher" aria-label="Voucher code">
        <button id="apply" onclick="document.querySelector('#result').innerText='{discount}'">Apply</button>
        <div id="result"></div>
        <button id="purchase" onclick="document.body.dataset.purchased='yes'">Place order</button>
        </body></html>'''.encode())


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
        cls.p=sync_playwright().start()
        cls.browser=cls.p.chromium.launch()
    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.p.stop();cls.server.shutdown();cls.server.server_close()
    def campaign(self,path='/'):
        return dict(id='fixture',name='Fixture',brand='Test',enabled=True,url=self.url+path,devices=['desktop'],ready_selector='h1',load_limit_ms=10000,
                    steps=[dict(name='Select plan',action='click',selector='#start',ready_selector='#plan')],
                    voucher=dict(enabled=True,code='TEST',input_selector='#voucher',apply_selector='#apply',result_selector='#result',expected_text='$20 off your first box',valid_from='2020-01-01',valid_until='2099-01-01',eligibility_confirmed=True))
    def attempt(self,path='/'):
        with tempfile.TemporaryDirectory() as d:
            checks=browser_attempt(self.browser,self.campaign(path),'desktop',Path(d),'America/Toronto')
            for c in checks:
                if c.get('screenshot'): self.assertTrue(Path(c['screenshot']).exists())
                if c.get('trace'): self.assertTrue(Path(c['trace']).exists())
            return checks
    def test_healthy_journey_and_discount(self):
        self.assertEqual(report_status(self.attempt()),'passed')
    def test_missing_image(self):
        checks=self.attempt('/broken')
        self.assertTrue(any(c['id']=='landing:images' and c['status']=='failed' for c in checks))
    def test_wrong_voucher(self):
        checks=self.attempt('/wrong-voucher')
        self.assertTrue(any(c['id']=='voucher:journey' and c['status']=='failed' for c in checks))
    def test_access_denied_is_blocked(self):
        self.assertEqual(report_status(self.attempt('/blocked')),'blocked')
    def test_payment_control_refused(self):
        page=self.browser.new_page();page.goto(self.url)
        with self.assertRaises(Blocked): interact(page,'click','#purchase')
        self.assertIsNone(page.locator('body').get_attribute('data-purchased'))
        page.close()
    def test_mobile_and_partial_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            c=self.campaign();c['steps']=[];c['voucher']={'enabled':False}
            results=browser_attempt(self.browser,c,'mobile',Path(d),'America/Toronto')
            self.assertEqual(report_status(results),'partial')


if __name__=='__main__': unittest.main()
