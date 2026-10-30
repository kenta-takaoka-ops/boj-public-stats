"""Fetch public Japanese financial statistics into data/ (run by GitHub Actions).

Only public statistics are fetched. No API keys, tokens or personal data are used or stored.
Sources (government statistics only; index providers' files are NOT stored here because their terms forbid redistribution):
  - Bank of Japan time-series API (stat-search.boj.or.jp): series listed in codes/*.json
  - Bank of Japan release calendar (tkohyos.xlsx)
  - Ministry of Finance JGB yield curve (jgbcm_all.csv / jgbcm.csv), auction history (xls)
  - plus any extra sources listed in codes/extra.json  ({"name": "url", ...})
Each file is written only when its content changes, so the git history shows real updates.
"""
import json, os, sys, time, hashlib, datetime, urllib.request, urllib.error

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

def save(rel, blob):
    p = os.path.join(DATA, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    old = open(p, 'rb').read() if os.path.exists(p) else None
    if old != blob:
        open(p, 'wb').write(blob); LOG.append(f'updated {rel} ({len(blob):,} bytes)')
    return hashlib.sha256(blob).hexdigest()[:16]

def boj(db, freq, codes, start):
    """BOJ getDataCode API, <=50 codes per request. Daily DBs do not accept startDate (full history returned)."""
    out = []
    for i in range(0, len(codes), 50):
        chunk = codes[i:i + 50]
        url = (f'https://www.stat-search.boj.or.jp/api/v1/getDataCode?format=json&lang=jp&db={db}'
               f'&code={",".join(chunk)}' + ('' if freq == 'D' else f'&startDate={start[freq]}'))
        j = json.loads(get(url))
        if j.get('STATUS') != 200:
            # one bad code breaks the whole request: retry one by one and skip failures
            for c in chunk:
                u1 = url.split('&code=')[0] + f'&code={c}' + ('' if freq == 'D' else f'&startDate={start[freq]}')
                j1 = json.loads(get(u1))
                if j1.get('STATUS') == 200: out += j1.get('RESULTSET') or []
                else: LOG.append(f'WARN {db} {c}: {j1.get("STATUS")} {j1.get("MESSAGE")}')
            continue
        out += j.get('RESULTSET') or []
        time.sleep(1)
    slim = [{'c': s['SERIES_CODE'], 'lu': str(s.get('LAST_UPDATE', '')),
             'd': s['VALUES']['SURVEY_DATES'], 'v': s['VALUES']['VALUES']} for s in out]
    return slim

def main():
    today = datetime.date.today()
    y = today.year - 3   # three years of history: enough to pick up revisions
    start = {'M': f'{y}01', 'Q': f'{y}01', 'S': f'{y}01', 'A': f'{y}'}
    man = {'fetched_utc': datetime.datetime.utcnow().isoformat(timespec='seconds') + 'Z', 'files': {}, 'errors': []}
    for spec in sorted(f for f in os.listdir(os.path.join(ROOT, 'codes')) if f.startswith('boj') and f.endswith('.json')):
        for db, v in json.load(open(os.path.join(ROOT, 'codes', spec))).items():
            try:
                rs = boj(db, v['freq'], v['codes'], start)
                man['files'][f'boj/{db}.json'] = save(f'boj/{db}.json', json.dumps(rs, ensure_ascii=False, separators=(',', ':')).encode())
            except Exception as e:
                man['errors'].append(f'{db}: {e}')
    files = {
        'mof/jgbcm_all.csv': 'https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv',
        'mof/jgbcm.csv': 'https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv',
        'boj/tkohyos.xlsx': 'https://www.boj.or.jp/statistics/outline/tkohyos.xlsx',
    }
    extra = os.path.join(ROOT, 'codes', 'extra.json')
    if os.path.exists(extra): files.update(json.load(open(extra)))
    for rel, url in files.items():
        try: man['files'][rel] = save(rel, get(url))
        except Exception as e: man['errors'].append(f'{rel}: {e}')
    save('manifest.json', json.dumps(man, ensure_ascii=False, indent=1).encode())
    print('\n'.join(LOG) or 'no changes'); print('errors:', man['errors'])
    # fail the run only if nothing could be fetched at all
    sys.exit(1 if len(man['errors']) >= len(man['files']) + len(man['errors']) else 0)

if __name__ == '__main__':
    main()
