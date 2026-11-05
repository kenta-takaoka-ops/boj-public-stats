"""Fetch public Japanese financial statistics into data/ (run by GitHub Actions).

Only public statistics are fetched. No personal data is used or stored, and no API key is stored in this repository
(the two keys for e-Stat / EDINET live only in GitHub Actions secrets; see codes/keyed.json below).
Sources (government statistics only; index providers' files are NOT stored here because their terms forbid redistribution):
  - Bank of Japan time-series API (stat-search.boj.or.jp): series listed in codes/*.json
  - Bank of Japan release calendar (tkohyos.xlsx)
  - Ministry of Finance JGB yield curve (jgbcm_all.csv / jgbcm.csv), auction history (xls)
  - plus any extra sources listed in codes/extra.json  ({"name": "url", ...}): MOF auction history,
    World Bank API, BIS data portal API
  - codes/optional.json: more public sources (World Bank, IMF, Eurostat, OECD, BIS, ECB, Federal Reserve Board).
    They change weekly to yearly,
    so each file is fetched once a week (6+ days after its last fetch). A failure here is only a warning (manifest
    "warnings"), so one moved URL does not stop the daily update.
    A value can be {"page": URL, "match": REGEX}: the page is read and the first link matching REGEX is fetched
    (for files whose URL changes, e.g. the BCBS CCyB workbook).
    A value {"fed": REL, "label": TEXT, "lastobs": N} fetches a preformatted package of the Federal Reserve Board
    Data Download Program (csv; lastobs "" = all observations).
    Data are taken from the original publishers only (no FRED / third-party redistribution services).
  - codes/keyed.json: sources that need an API key (e-Stat, EDINET). Keys come ONLY from GitHub Actions secrets
    (environment variables ESTAT_APP_ID / EDINET_API_KEY). They are never written to data/, the manifest or the
    log: error messages are redacted, and a response that contains the key is not saved.
BOJ specs with "every": "weekly" (資金循環統計) are fetched once a week and on their release day in the BOJ calendar.
manifest.json lists every file with its hash ("files") and the time it was last fetched ("at").
Each file is written only when its content changes, so the git history shows real updates.
"""
import json, os, re, sys, time, hashlib, datetime, urllib.request, urllib.error, urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data')
UA = 'Mozilla/5.0 (compatible; public-stats-fetcher; +https://github.com)'
LOG = []

def get(url, tries=4, timeout=90):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa
            err = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f'{url}: {err}')

KEYS = {k: os.environ.get(k, '') for k in ('ESTAT_APP_ID', 'EDINET_API_KEY')}

def redact(text):
    text = str(text)
    for v in KEYS.values():
        if v: text = text.replace(v, '***').replace(urllib.parse.quote(v), '***')
    return re.sub(r'(appId|Subscription-Key|api_key)=[^&\s]+', r'\1=***', text)

def get_page_link(page, match, tries):
    html = get(page, tries=tries).decode('utf-8', 'replace')
    for href in re.findall(r'href="([^"]+)"', html):
        if re.search(match, href): return get(urllib.parse.urljoin(page, href), tries=tries)
    raise RuntimeError(f'{page}: no link matching {match}')

FED_DDP = 'https://www.federalreserve.gov/datadownload/'

def get_fed_package(rel, label, lastobs, tries):
    """Federal Reserve Board Data Download Program: a preformatted package (csv).
    The package list on Choose.aspx carries the query string of each package; the one whose label starts with
    `label` is fetched, with `lastobs` replaced when given ("" = all observations, "60" = last 60)."""
    html = get(f'{FED_DDP}Choose.aspx?rel={rel}', tries=tries).decode('utf-8', 'replace')
    for m in re.finditer(r'value="(rel=[^"]*?series=[0-9a-f]{32}[^"]*)"', html):
        seg = html[m.end():m.end() + 1200]
        seg = re.sub(r'<[^>]+>', ' ', seg.split('value="')[0])
        if label not in seg: continue
        q = m.group(1).replace('&amp;', '&')
        if lastobs is not None: q = re.sub(r'lastobs=[^&]*', f'lastobs={lastobs}', q)
        return get(f'{FED_DDP}Output.aspx?{q}', tries=tries)
    raise RuntimeError(f'Fed DDP {rel}: no package labelled {label}')

def get_source(src, tries=2):
    if isinstance(src, str): return get(src, tries=tries)
    if 'fed' in src: return get_fed_package(src['fed'], src['label'], src.get('lastobs'), tries)
    return get_page_link(src['page'], src['match'], tries)

def save(rel, blob):
    p = os.path.join(DATA, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    old = open(p, 'rb').read() if os.path.exists(p) else None
    if old != blob:
        open(p, 'wb').write(blob); LOG.append(f'updated {rel} ({len(blob):,} bytes)')
    return hashlib.sha256(blob).hexdigest()[:16]

def select_by_rule(db, rule):
    """Pick series codes from the BOJ metadata API. rule: {"all": true} or
    {"prefix": [...], "sectors": [...], "frequency": "QUARTERLY"} (sectors = characters 9-11 of FF codes)."""
    j = json.loads(get(f'https://www.stat-search.boj.or.jp/api/v1/getMetadata?format=json&lang=jp&db={db}', timeout=180))
    rs = [x for x in (j.get('RESULTSET') or []) if x.get('SERIES_CODE')]
    if rule.get('frequency'): rs = [x for x in rs if x.get('FREQUENCY') == rule['frequency']]
    if rule.get('prefix'): rs = [x for x in rs if any(x['SERIES_CODE'].startswith(p) for p in rule['prefix'])]
    if rule.get('sectors'):
        sec = set(rule['sectors']); rs = [x for x in rs if x['SERIES_CODE'][8:11] in sec]
    return sorted(x['SERIES_CODE'] for x in rs)

def boj(db, freq, codes, start):
    """BOJ getDataCode API, <=50 codes per request. start=None or daily -> no startDate (full history)."""
    out = []
    sd = '' if (freq == 'D' or start is None) else f'&startDate={start[freq]}'
    for i in range(0, len(codes), 50):
        chunk = codes[i:i + 50]
        url = (f'https://www.stat-search.boj.or.jp/api/v1/getDataCode?format=json&lang=jp&db={db}'
               f'&code={",".join(chunk)}' + sd)
        j = json.loads(get(url))
        if j.get('STATUS') != 200:
            # one bad code breaks the whole request: retry one by one and skip failures
            for c in chunk:
                u1 = url.split('&code=')[0] + f'&code={c}' + sd
                j1 = json.loads(get(u1))
                if j1.get('STATUS') == 200: out += j1.get('RESULTSET') or []
                else: LOG.append(f'WARN {db} {c}: {j1.get("STATUS")} {j1.get("MESSAGE")}')
            continue
        out += j.get('RESULTSET') or []
        time.sleep(1)
    slim = [{'c': s['SERIES_CODE'], 'lu': str(s.get('LAST_UPDATE', '')),
             'd': s['VALUES']['SURVEY_DATES'], 'v': s['VALUES']['VALUES']} for s in out]
    return slim

WEEKLY_DAYS = 6   # 「週1回」の取得先は、前回の取得から6日以上たっていれば取りに行く

def jst_today():
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)).date()

def release_dates(name):
    """BOJ release calendar (data/boj/tkohyos.xlsx) -> set of YYYY-MM-DD for the statistic `name`"""
    import io, zipfile, xml.etree.ElementTree as ET
    p = os.path.join(DATA, 'boj', 'tkohyos.xlsx')
    if not os.path.exists(p): return set()
    try:
        z = zipfile.ZipFile(io.BytesIO(open(p, 'rb').read())); ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        sst = []
        if 'xl/sharedStrings.xml' in z.namelist():
            for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', ns):
                sst.append(''.join(t.text or '' for t in si.iter(f'{{{ns["m"]}}}t') if not any(t in r.iter() for r in si.findall('m:rPh', ns))))
        out = set()
        for sh in sorted(n for n in z.namelist() if re.match(r'xl/worksheets/sheet\d+\.xml$', n)):
            for row in ET.fromstring(z.read(sh)).iter(f'{{{ns["m"]}}}row'):
                cells = {}
                for c in row.findall('m:c', ns):
                    v = c.find('m:v', ns); t = v.text if v is not None else ''
                    if c.get('t') == 's' and t: t = sst[int(t)]
                    cells[re.sub(r'\d+', '', c.get('r'))] = t or ''
                if (cells.get('B') or '').replace('\n', '') != name: continue
                for col in 'EFGHIJKLMNOP':
                    v = cells.get(col, '')
                    if re.fullmatch(r'\d{5}', v): out.add((datetime.date(1899, 12, 30) + datetime.timedelta(days=int(v))).isoformat())
        return out
    except Exception as e:
        LOG.append(f'WARN calendar: {e}'); return set()

def main():
    today = datetime.date.today()
    y = today.year - 3   # three years of history: enough to pick up revisions
    start = {'M': f'{y}01', 'Q': f'{y}01', 'S': f'{y}01', 'A': f'{y}'}
    prev = {}
    pm = os.path.join(DATA, 'manifest.json')
    if os.path.exists(pm):
        try: prev = json.load(open(pm))
        except ValueError: prev = {}
    now = datetime.datetime.now(datetime.timezone.utc)
    man = {'fetched_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'files': {}, 'at': {}, 'errors': [], 'warnings': [], 'skipped': []}
    def due(rel):
        t = (prev.get('at') or {}).get(rel)
        if not t or rel not in (prev.get('files') or {}) or not os.path.exists(os.path.join(DATA, rel)): return True
        return (now - datetime.datetime.fromisoformat(t.replace('Z', '+00:00'))).days >= WEEKLY_DAYS
    def keep(rel):   # not fetched this time: carry the previous entry so the manifest still lists the file
        man['files'][rel] = prev['files'][rel]; man['at'][rel] = prev['at'][rel]; man['skipped'].append(rel)
    def put(rel, blob):
        man['files'][rel] = save(rel, blob); man['at'][rel] = man['fetched_utc']
    # MOF yield curve, BOJ release calendar, and the small daily files (codes/extra.json): every run
    files = {
        'boj/tkohyos.xlsx': 'https://www.boj.or.jp/statistics/outline/tkohyos.xlsx',
        'mof/jgbcm_all.csv': 'https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv',
        'mof/jgbcm.csv': 'https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv',
    }
    extra = os.path.join(ROOT, 'codes', 'extra.json')
    if os.path.exists(extra): files.update(json.load(open(extra)))
    for rel, url in files.items():
        try: put(rel, get(url))
        except Exception as e: man['errors'].append(f'{rel}: {e}')
    # BOJ time-series API. Specs with "every": "weekly" are fetched once a week and on their release day
    # ("release": the statistic's name in the BOJ calendar), e.g. 資金循環統計 (3,000 series, 64 requests).
    tday = jst_today().isoformat()
    for spec in sorted(f for f in os.listdir(os.path.join(ROOT, 'codes')) if f.startswith('boj') and f.endswith('.json')):
        for db, v in json.load(open(os.path.join(ROOT, 'codes', spec))).items():
            rel = f'boj/{db}.json'
            if v.get('every') == 'weekly' and not due(rel):
                rd = release_dates(v['release']) if v.get('release') else set()
                if tday not in rd and (jst_today() - datetime.timedelta(days=1)).isoformat() not in rd:
                    keep(rel); continue
            try:
                codes = v.get('codes') or select_by_rule(db, v['rule'])
                rs = boj(db, v['freq'], codes, None if v.get('full') else start)
                put(rel, json.dumps(rs, ensure_ascii=False, separators=(',', ':')).encode())
            except Exception as e:
                man['errors'].append(f'{db}: {e}')
                if rel in (prev.get('files') or {}): keep(rel)
    # optional public sources (World Bank, IMF, Eurostat, OECD, BIS, ECB, Fed): weekly; failures are warnings only
    opt = os.path.join(ROOT, 'codes', 'optional.json')
    for rel, src in (json.load(open(opt)).items() if os.path.exists(opt) else []):
        if not due(rel): keep(rel); continue
        try:
            put(rel, get_source(src))
        except Exception as e:
            man['warnings'].append(f'{rel}: {e}'[:300])
            if rel in (prev.get('files') or {}): keep(rel)
    # keyed sources (e-Stat; annual data): weekly, only when the secret is set; never store or print the key
    kj = os.path.join(ROOT, 'codes', 'keyed.json')
    for rel, src in (json.load(open(kj)).items() if os.path.exists(kj) else []):
        need = re.findall(r'\{(\w+)\}', src)
        if not all(KEYS.get(k) for k in need):
            man['warnings'].append(f'{rel}: skipped (secret not set)')
            if rel in (prev.get('files') or {}): keep(rel)
            continue
        if not due(rel): keep(rel); continue
        try:
            blob = get(src.format(**{k: urllib.parse.quote(KEYS[k]) for k in need}), tries=2)
            for k in need:  # never keep the key, even if the API echoes the request back
                blob = blob.replace(KEYS[k].encode(), b'***').replace(urllib.parse.quote(KEYS[k]).encode(), b'***')
            put(rel, blob)
        except Exception as e:
            man['warnings'].append(redact(f'{rel}: {e}')[:300])
            if rel in (prev.get('files') or {}): keep(rel)
    save('manifest.json', json.dumps(man, ensure_ascii=False, indent=1).encode())
    print('\n'.join(LOG) or 'no changes'); print('errors:', man['errors']); print('warnings:', man['warnings'])
    print('skipped (weekly, not due):', len(man['skipped']))
    fetched = len(man['files']) - len(man['skipped'])
    sys.exit(1 if fetched == 0 and man['errors'] else 0)

if __name__ == '__main__':
    main()
