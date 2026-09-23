"""Persistent incremental recall batches; no network/workbook mutation here."""
from contextlib import contextmanager
import datetime as dt
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

BASE = Path(os.environ.get('RECALL_HOME', '.recall')).resolve()
CST = dt.timezone(dt.timedelta(hours=8))

@contextmanager
def lock(base=BASE):
    """Non-blocking process lock, released by the OS even after a crash."""
    path = Path(base) / 'batches' / 'run.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as f:
        if path.stat().st_size == 0:
            f.write(b'0')
            f.flush()
        f.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Another recall process is running') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def initialize(base=BASE, baseline=None):
    """Explicitly seed reviewed historical metadata; never infer today's baseline."""
    base = Path(base)
    ledger_path = base / 'ledger.json'
    baseline_path = base / 'baseline.json'
    if ledger_path.exists() or baseline_path.exists():
        raise ValueError('State already exists; refusing to overwrite it')
    entries = validate_candidates(baseline) if baseline is not None else []
    save(ledger_path, {'version': 1, 'entries': []})
    save(baseline_path, {'version': 1, 'entries': entries})
    return base


def today():
    return dt.datetime.now(CST).date().isoformat()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)

def title_key(value):
    return re.sub(r'[^\w]', '', unicodedata.normalize('NFKC', value or '')).lower()

def url_key(value):
    if not value:
        return ''
    u = urlsplit(value if not value.startswith('/') else 'https://www.samr.gov.cn' + value)
    host = (u.hostname or '').lower()
    if u.scheme not in ('http', 'https') or host not in ('samr.gov.cn', 'www.samr.gov.cn') or u.username or u.password:
        raise ValueError('公告 URL 必须来自市场监管总局官网')
    return host.removeprefix('www.') + u.path.rstrip('/')

def identity(e):
    return url_key(e.get('url') or e.get('href')) or e['date'] + '|' + title_key(e['title'])

def find(entries, candidate):
    u = url_key(candidate.get('url') or candidate.get('href'))
    for e in entries:
        eu = url_key(e.get('url') or e.get('href'))
        if u and eu:
            if u == eu:
                return e
            continue
        if e.get('date') == candidate.get('date') and title_key(e.get('title')) == title_key(candidate.get('title')):
            return e
    return None

def validate_candidates(items):
    if not isinstance(items, list) or not items:
        raise ValueError('候选列表为空或格式错误；不能把抓取失败当成无新增')
    result = []
    for raw in items:
        e = dict(raw)
        dt.date.fromisoformat(e['date'])
        if e['date'] > today():
            raise ValueError('候选日期不能晚于检查日期')
        if not e.get('title') or not (e.get('url') or e.get('href')):
            raise ValueError('候选缺少标题或官网 URL')
        if '…' in e['title'] or '...' in e['title']:
            raise ValueError('候选标题被截断，不能进行可靠去重')
        e['url'] = e.get('url') or 'https://www.samr.gov.cn' + e['href']
        url_key(e['url'])
        if not find(result, e):
            result.append(e)
    return result

def prepare(candidates, base=BASE):
    base = Path(base)
    pending = base / 'batches' / 'current.json'
    if pending.exists():
        old = read(pending)
        if old['status'] != 'complete':
            return old
    items = validate_candidates(candidates)
    ledger = read(base / 'ledger.json')
    baseline = read(base / 'baseline.json')
    new = [e for e in items if not find(ledger['entries'], e) and not find(baseline['entries'], e)]
    batch = {'version': 1, 'id': dt.datetime.now(CST).strftime('%Y%m%dT%H%M%S%f'),
             'date': today(), 'status': 'pending' if new else 'reviewed',
             'candidate_count': len(items), 'known_count': len(items)-len(new),
             'new': new, 'reviews': [], 'uploaded': {}, 'log_id': None}
    save(pending, batch)
    return batch

def review(batch, reviews):
    if batch['status'] != 'pending':
        raise ValueError('批次已核对或完成，不得重做分析')
    if not isinstance(reviews, list) or len(reviews) != len(batch['new']):
        raise ValueError('判定数量必须与本批 NEW 完全一致')
    result, seen = [], set()
    for r in reviews:
        source = find(batch['new'], r)
        if source is None or identity(source) in seen:
            raise ValueError('判定包含历史、重复或非本批公告')
        if not r.get('verdict') or r['verdict'] == '待判' or type(r.get('in_table')) is not bool:
            raise ValueError('必须提供最终判定和布尔 in_table')
        rows = r.get('rows', [])
        if not isinstance(rows, list) or bool(rows) != r['in_table']:
            raise ValueError('入表判定必须附增量 rows；排除/存疑不得附入表行')
        if not isinstance(r.get('crosscheck'), str) or not r['crosscheck'].strip():
            raise ValueError('必须记录真实交叉比对结果，不可自动假定一致')
        if rows and r.get('crosscheck_agreed') is not True:
            raise ValueError('入表需明确记录 crosscheck_agreed=true；有分歧不得上传')
        for row in rows:
            if url_key(row.get('来源URL')) != url_key(source['url']):
                raise ValueError('增量行来源与 NEW 公告不符')
            for k in ('日期str', '公司', '车型', '分类', '原因'):
                if not row.get(k):
                    raise ValueError('增量行缺少字段: ' + k)
            dt.date.fromisoformat(row['日期str'])
            if type(row.get('数量')) is not int or row['数量'] < 0:
                raise ValueError('数量必须为非负整数')
        checked = r.get('first_checked') or today()
        if not batch['date'] <= checked <= today():
            raise ValueError('首次核对日期必须在批次发现日至今天之间')
        dt.date.fromisoformat(checked)
        seen.add(identity(source))
        # Preserve the exact identity discovered from the official listing.
        result.append({**r, **{k: source[k] for k in ('date', 'title', 'url')},
                       'first_checked': checked})
    batch['reviews'] = result
    batch['status'] = 'reviewed'
    return batch

def complete(batch, base=BASE):
    if batch['status'] != 'reviewed':
        raise ValueError('仅已核对批次可完成')
    base = Path(base)
    ledger = read(base / 'ledger.json')
    for e in batch['reviews']:
        if not find(ledger['entries'], e):
            ledger['entries'].append({k: v for k, v in e.items() if k != 'rows'})
    if batch['reviews']:
        ledger['updated'] = today()
        save(base / 'ledger.json', ledger)
    batch['status'] = 'complete'
    save(base / 'batches' / (batch['id'] + '.json'), batch)
    save(base / 'batches' / 'current.json', batch)

def batch_rows(batch):
    if batch['status'] not in ('reviewed', 'complete'):
        raise ValueError('尚未完成 NEW 公告核对，禁止上传')
    return [row for r in batch['reviews'] for row in r.get('rows', [])]

def format_details(entries):
    """Plain multi-line text for Jiandaoyun textarea, without Markdown syntax."""
    def text(value):
        return ('' if value is None else str(value)).replace('\r\n', '\n').strip()
    blocks = []
    for i, entry in enumerate(entries, 1):
        lines = ['第 %d 条' % i]
        for label, value in entry.items():
            if value is not None and text(value):
                lines.append('%s：%s' % (label, text(value)))
        blocks.append('\n'.join(lines))
    return '\n\n'.join(blocks)


def log_text(batch):
    n = len(batch['new'])
    if not n:
        return {'conclusion': '召回监控：无新增公告（截至 %s 检查）' % batch['date'],
                'detail': '当日新增公告 0 条，无逐条判定',
                'crosscheck': '无新增公告，未启动分析或交叉比对。'}
    entries = []
    for r in batch['reviews']:
        entry = {'首次核对': r.get('first_checked'), '公告日期': r.get('date'),
                 '公告': r.get('title')}
        if r.get('company'):
            entry['公司'] = r['company']
        if r.get('memo'):
            entry['核对说明'] = r['memo']
        if r.get('rows'):
            entry['召回范围'] = '；'.join(
                '%s，%s，数量 %s' % (row['公司'], row['车型'], row['数量'])
                for row in r['rows'])
        entry['判定'] = r.get('verdict')
        entries.append(entry)
    return {'conclusion': '新增公告 %d 条；符合入表条件的新增召回 %d 条。' % (n, len(batch_rows(batch))),
            'detail': format_details(entries),
            'crosscheck': '\n'.join(r['crosscheck'] for r in batch['reviews'])}
