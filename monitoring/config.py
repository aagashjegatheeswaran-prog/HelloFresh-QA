import json
import re
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / 'monitor-config.json'


def load_config(path=DEFAULT_CONFIG):
    cfg = json.loads(Path(path).read_text(encoding='utf-8'))
    validate(cfg)
    return cfg


def validate(cfg):
    ZoneInfo(cfg.get('timezone', 'America/Toronto'))
    if not re.fullmatch(r'\d{2}:\d{2}', cfg.get('daily_at', '07:00')):
        raise ValueError('Daily time must use two-digit HH:MM, for example 07:00.')
    h, m = map(int, cfg.get('daily_at', '07:00').split(':'))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError('Daily time must be HH:MM.')
    ids = set()
    if not cfg.get('campaigns'):
        raise ValueError('Add at least one campaign.')
    for c in cfg['campaigns']:
        if not c.get('id') or c['id'] in ids:
            raise ValueError('Each campaign needs a unique id.')
        ids.add(c['id'])
        if urlparse(c['url']).scheme not in ('http', 'https') or not urlparse(c['url']).hostname:
            raise ValueError('Campaign URL must be http(s).')
        if not c.get('devices') or any(d not in ('desktop', 'mobile') for d in c['devices']):
            raise ValueError('Choose desktop and/or mobile.')
        if not c.get('ready_selector'):
            raise ValueError('A ready selector is required.')
        if not 0 < float(c.get('load_limit_ms', 5000)) <= 120000:
            raise ValueError('Load threshold must be between 0 and 120000 ms.')
        steps = c.get('steps', [])
        names = set()
        for step in steps:
            if not step.get('name') or step['name'] in names:
                raise ValueError('Steps need unique names within a campaign.')
            names.add(step['name'])
            if step['action'] not in ('click', 'fill', 'select', 'assert_text', 'assert_visible'):
                raise ValueError('Unsupported step action.')
            if not step.get('selector'):
                raise ValueError('Every step requires an exact Playwright selector.')
            if step['action'] in ('click', 'fill', 'select') and not step.get('ready_selector'):
                raise ValueError('Interactive steps require a ready_selector for their result.')
        voucher = c.get('voucher', {})
        if voucher.get('enabled'):
            for key in ('code', 'input_selector', 'apply_selector', 'result_selector', 'expected_text', 'valid_from', 'valid_until'):
                if not voucher.get(key):
                    raise ValueError(f'Voucher needs {key}.')
            from datetime import date
            if date.fromisoformat(voucher['valid_from']) > date.fromisoformat(voucher['valid_until']):
                raise ValueError('Voucher start must precede its expiry.')
            if not voucher.get('eligibility_confirmed'):
                raise ValueError('Confirm that the configured journey is eligible for this voucher.')
    return cfg


def save_config(cfg, path=DEFAULT_CONFIG):
    validate(cfg)
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    tmp.replace(path)
