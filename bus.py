"""KMB/LWB, Citybus and green minibus routes and stops for the whole of HK, cut into map tiles, for the GIS pages' moving
buses (gis/src/transit.ts: it loads the tiles round the view and asks for live arrival times near it).
  - route lines: TD 'Bus Route' (CSDI FB_ROUTE_LINE, dataset hk-td-tis_23-routes-fares-geojson) and TD 'Green Minibus Route'
    (CSDI GMB_ROUTE_LINE, with the ETA API's ROUTE_ID / ROUTE_SEQ), HK1980
  - stops and their order: KMB/LWB open data (data.etabus.gov.hk, bulk lists), Citybus (rt.data.gov.hk: one request per route
    direction and per stop, paced at CTB_PER_S) and green minibus from TD's routes-and-fares GeoJSON (JSON_GMB.json: every
    route direction's stops in order with the same ids and positions as data.etagmb.gov.hk, checked 2026-10-10: 1944 stops,
    max 0.7 m apart; that server blocked this Mac with 403 even at 2 requests a second, so the bake never asks it). Citybus
    stop positions are kept across days (they rarely move); lists are fetched once a day. A joint route (KMB+CTB) gets one
    entry per company.
  - road levels: TD road network centrelines (CSDI IRN layer 10, all 36k) ELEVATION, -2..3: the level of the nearest roughly
    parallel centreline within LEVEL_M, so the page puts a bus on the deck or the road under it
  - heights: Lands D DTM (dtm.py): on a deck (level >= 1) the DTM at the point, else the lowest within 15 m (the ground
    beside a deck), and the rise above that ground ('r': 0 at grade): the page uses the rise on the flat map (Hills off, the
    imagery lies at 0 m) and the height with Hills on; the photomesh where it has one
Each route direction is matched to its stops (in order along TD's line), then sampled every STEP m along the whole line. The
samples go into TILE m square tiles (HK1980 grid from X0, Y0) as route pieces (one sample of overlap each side), with the
stops they pass. out/gis/hk/bus/index.json lists the tiles; <ix>_<iy>.json.gz is one tile (gzipped JSON) (coordinates in decimetres from the
tile's south-west corner, an affine to lon/lat per tile). Raw downloads: data/cache/bus/<date>/ and data/cache/bus/*-stops.json.
Usage: python3 bus.py     (~40 min the first time, mostly paced downloads; points every area's index.json at the tiles)"""
import datetime, gzip, json, math, os, shutil, sys, threading, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.ops import substring
import dtm, sources

TD = 'https://portal.csdi.gov.hk/server/rest/services/common/td_rcd_1638844988873_41214/FeatureServer/0/query'
GMB_LINES = 'https://portal.csdi.gov.hk/server/rest/services/common/td_rcd_1697082463580_57453/FeatureServer/0/query'
IRN = 'https://portal.csdi.gov.hk/server/rest/services/common/td_rcd_1638949160594_2844/FeatureServer/10/query'
KMB, CTB = 'https://data.etabus.gov.hk/v1/transport/kmb', 'https://rt.data.gov.hk/v2/transport/citybus'
TD_GMB = 'https://static.data.gov.hk/td/routes-fares-geojson/JSON_GMB.json'
TILE, STEP, X0, Y0 = 2000, 20, 800000, 800000
SNAP_M, LEVEL_M = 40, 12                       # a stop further than SNAP_M from its line is a mismatch; level search radius
CTB_PER_S = 5
NO_LEVEL = -9
TO_HK, TO_LL = Transformer.from_crs(4326, 2326, always_xy=True), Transformer.from_crs(2326, 4326, always_xy=True)
DAY = datetime.date.today().isoformat()
C = os.path.join(sources.CACHE, 'bus')

class Pace:
    """at most per_s calls a second, across threads"""
    def __init__(self, per_s): self.gap, self.next, self.lock = 1 / per_s, 0.0, threading.Lock()
    def wait(self):
        with self.lock: now = time.monotonic(); at = max(self.next, now); self.next = at + self.gap
        time.sleep(max(0.0, at - now))

def get(url, pace=None):
    for attempt in range(4):
        if pace: pace.wait()
        try:
            with urllib.request.urlopen(url, timeout=120) as r: return json.loads(r.read().decode('utf-8-sig'))
        except urllib.error.HTTPError as e:
            if e.code == 403: raise SystemExit(f'403 from {url}: blocked, stop and wait (see the docstring)')
            if e.code == 404 or attempt == 3: raise
        except urllib.error.URLError:
            if attempt == 3: raise
        time.sleep(2 ** attempt)

def daily(name, url, pace=None):
    p = os.path.join(C, DAY, name)
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True); d = get(url, pace)
        json.dump(d, open(p + '.part', 'w')); os.replace(p + '.part', p)
    return json.load(open(p, encoding='utf-8-sig'))                       # TD's GeoJSON starts with a BOM

def query_all(url, params, name):
    """every feature of a CSDI layer query, a page (3000) at a time, cached for the day"""
    out, off = [], 0
    while True:
        page = daily(f'{name}-{off}.json', f"{url}?{urllib.parse.urlencode({**params, 'resultOffset': off, 'resultRecordCount': 3000, 'f': 'json'})}")
        out += page['features']
        if not page.get('exceededTransferLimit'): return out
        off += len(page['features'])

def kept(name, ids, fetch, workers):
    """{id: record} kept across days in data/cache/bus/<name>.json; only ids not seen before are fetched"""
    p = os.path.join(C, name); have = json.load(open(p)) if os.path.exists(p) else {}
    need = sorted(set(ids) - have.keys())
    with ThreadPoolExecutor(workers) as ex:
        for n, (i, rec) in enumerate(zip(need, ex.map(fetch, need))):
            if rec is not None: have[i] = rec
            if n % 200 == 199: json.dump(have, open(p, 'w')); print(f'{name}: {n + 1}/{len(need)}', file=sys.stderr, flush=True)
    json.dump(have, open(p, 'w'))
    return have

def load():
    """route lines (TD, GMB) and stops in order: (features [(attrs, LineString, co, key, bound list)], order, stops)"""
    td = query_all(TD, {'where': "COMPANY_CODE LIKE '%KMB%' OR COMPANY_CODE LIKE '%LWB%' OR COMPANY_CODE LIKE '%CTB%'",
                        'outFields': 'ROUTE_NAMEE,ROUTE_SEQ,COMPANY_CODE', 'outSR': 2326, 'maxAllowableOffset': 1}, 'td-routes')
    gmb = query_all(GMB_LINES, {'where': '1=1', 'outFields': 'ROUTE_ID,ROUTE_SEQ,ROUTE_NAME,ED_STOP_NAMEE', 'outSR': 2326, 'maxAllowableOffset': 1}, 'gmb-routes')
    stops = {s['stop']: {**s, 'co': 'KMB'} for s in daily('kmb-stop.json', f'{KMB}/stop')['data']}
    order = {}                                                           # (company, route or GMB id, bound) -> [(seq, stop id)]
    for r in daily('kmb-route-stop.json', f'{KMB}/route-stop')['data']:
        if r['service_type'] == '1': order.setdefault(('KMB', r['route'], r['bound']), []).append((int(r['seq']), r['stop']))
    ctb_pace = Pace(CTB_PER_S)
    ctb_dirs = sorted({(f['attributes']['ROUTE_NAMEE'], b) for f in td if 'CTB' in f['attributes']['COMPANY_CODE'] for b in 'OI'})
    def ctb_dir(rb):
        try: return daily(f'ctb-route-stop-{rb[0]}-{rb[1]}.json', f'{CTB}/route-stop/CTB/{urllib.parse.quote(rb[0])}/{"outbound" if rb[1] == "O" else "inbound"}', ctb_pace)['data']
        except urllib.error.HTTPError: return []
    with ThreadPoolExecutor(4) as ex:
        for (route, b), rows in zip(ctb_dirs, ex.map(ctb_dir, ctb_dirs)):
            if rows: order[('CTB', route, b)] = sorted((int(x['seq']), x['stop']) for x in rows)
    ctb = kept('ctb-stops.json', {s for (co, *_), seq in order.items() if co == 'CTB' for _, s in seq}, lambda s: get(f'{CTB}/stop/{s}', ctb_pace)['data'], 4)
    stops.update({s: {**st, 'co': 'CTB'} for s, st in ctb.items() if 'long' in st})   # the API answers some ids with no data
    for f in daily('td-JSON_GMB.json', TD_GMB)['features']:
        a = f['properties']; s = str(a['stopId'])
        order.setdefault(('GMB', a['routeId'], str(a['routeSeq'])), []).append((a['stopSeq'], s))
        stops.setdefault(s, {'name_en': a['stopNameE'], 'name_tc': a['stopNameC'], 'long': f['geometry']['coordinates'][0], 'lat': f['geometry']['coordinates'][1], 'co': 'GMB'})
    feats = []
    for f in td:
        a = f['attributes']
        for co in [c for c in ('KMB', 'CTB') if c in a['COMPANY_CODE'] or (c == 'KMB' and 'LWB' in a['COMPANY_CODE'])]:
            feats.append((a, f, 'LWB' if co == 'KMB' and 'LWB' in a['COMPANY_CODE'] else co, co, a['ROUTE_NAMEE'], ('O', 'I') if a['ROUTE_SEQ'] == 1 else ('I', 'O'), None))
    for f in gmb:
        a = f['attributes']; feats.append((a, f, 'GMB', 'GMB', a['ROUTE_ID'], (str(a['ROUTE_SEQ']),), a['ED_STOP_NAMEE']))
    return feats, order, stops

def match(feats, order, stops):
    """each route direction: its whole line and its stops in order along it (the best of TD's drawings when there are several)"""
    best, skipped = {}, []
    hk = {s: Point(*TO_HK.transform(float(st['long']), float(st['lat']))) for s, st in stops.items()}
    for a, f, label, co, key, bounds, dest in feats:
        line = LineString([tuple(p[:2]) for path in f['geometry']['paths'] for p in path])
        def snap(seq):                                                   # in order, each beyond the previous (a route can pass a road twice)
            at, prev, bad = [], 0.0, 0
            for n, sid in seq:
                p = hk.get(sid)
                if p is None: bad += 1; continue
                rest = substring(line, prev, line.length); dd = prev + rest.project(p) if rest.geom_type == 'LineString' else line.length
                if line.interpolate(dd).distance(p) > SNAP_M: bad += 1; continue
                at.append((n, sid, dd)); prev = dd
            return at, bad
        tries = [(b, sorted(order.get((co, key, b), []))) for b in bounds]
        tries = [(b, seq, *snap(seq)) for b, seq in tries if seq]
        name = a.get('ROUTE_NAMEE') or a.get('ROUTE_NAME')
        if not tries: skipped.append(f'{label} {name}: no stop list'); continue
        bound, seq, at, bad = min(tries, key=lambda t: t[3] / len(t[1]))
        if len(at) < 2 or bad > len(seq) // 4: skipped.append(f'{label} {name}{bound}: {bad} of {len(seq)} stops off the line'); continue
        k = f'{label}|{key}|{bound}'
        if k in best and best[k][0] <= (bad, -len(at)): continue
        best[k] = ((bad, -len(at)), {'k': k, 'meta': [label, name, bound, key if co == 'GMB' else None, dest], 'line': line, 'stops': at})
    return [r for _, r in best.values()], skipped

def levels(xy, tangent, irn):
    """road level per sample: the nearest IRN centreline within LEVEL_M running roughly parallel (|cos| >= 0.85), else NO_LEVEL"""
    geoms = np.array([LineString([tuple(p[:2]) for p in path]) for f in irn for path in f['geometry']['paths']], dtype=object)
    lv = np.array([f['attributes']['ELEVATION'] or 0 for f in irn for _ in f['geometry']['paths']])
    tree = shapely.STRtree(geoms); out = np.full(len(xy), NO_LEVEL, dtype=np.int8)
    for c0 in range(0, len(xy), 200_000):                              # chunks keep the pair arrays small
        pts = shapely.points(xy[c0:c0 + 200_000]); pi, gi = tree.query(pts, predicate='dwithin', distance=LEVEL_M)
        if not len(pi): continue
        g, p = geoms[gi], pts[pi]; d = shapely.distance(g, p); s = shapely.line_locate_point(g, p)
        a, b = shapely.line_interpolate_point(g, np.maximum(s - 1, 0)), shapely.line_interpolate_point(g, s + 1)
        dv = shapely.get_coordinates(b) - shapely.get_coordinates(a); tv = tangent[c0 + pi]
        cos = np.abs((dv * tv).sum(1)) / (np.linalg.norm(dv, axis=1) * np.linalg.norm(tv, axis=1) + 1e-9)
        ok = cos >= 0.85; pi, d, gi = pi[ok], d[ok], gi[ok]
        o = np.lexsort((d, pi)); pi, gi = pi[o], gi[o]; first = np.r_[True, pi[1:] != pi[:-1]]
        out[c0 + pi[first]] = lv[gi[first]]
    return out

def main():
    feats, order, stops = load()
    routes, skipped = match(feats, order, stops)
    print(json.dumps({'routes': len(routes), 'skipped': len(skipped)}), file=sys.stderr, flush=True)
    # samples every STEP m along every route, with a tangent for the level search
    for r in routes:
        n = int(r['line'].length // STEP) + 1
        r['xy'] = shapely.get_coordinates(shapely.line_interpolate_point(r['line'], np.arange(n) * STEP))
    xy = np.concatenate([r['xy'] for r in routes])
    tangent = np.concatenate([np.gradient(r['xy'], axis=0) if len(r['xy']) > 1 else np.ones((1, 2)) for r in routes])
    irn = query_all(IRN, {'where': '1=1', 'outFields': 'ELEVATION', 'outSR': 2326}, 'irn-hk')
    lv = levels(xy, tangent, irn)
    zr = [dtm.place(x, y, l >= 1) for (x, y), l in zip(xy, lv)]          # the one height rule (dtm.place)
    z = np.array([a for a, _ in zr], dtype=object); rise = [b for _, b in zr]
    i = 0
    for r in routes: n = len(r['xy']); r['lv'], r['z'], r['rise'] = lv[i:i + n], z[i:i + n], rise[i:i + n]; i += n
    print('levels and heights done', file=sys.stderr, flush=True)
    # tiles: runs of samples in one tile, one sample of overlap each side
    tiles = {}
    tile_of = lambda x, y: (int((x - X0) // TILE), int((y - Y0) // TILE))
    for r in routes:
        t = [tile_of(x, y) for x, y in r['xy']]; j = 0
        while j < len(t):
            k = j
            while k + 1 < len(t) and t[k + 1] == t[j]: k += 1
            a, b = max(0, j - 1), min(len(t) - 1, k + 1); ox, oy = X0 + t[j][0] * TILE, Y0 + t[j][1] * TILE
            pts = r['xy'][a:b + 1]; zz = r['z'][a:b + 1]; ll = r['lv'][a:b + 1]; rr = r['rise'][a:b + 1]
            runs = [[q, int(ll[q])] for q in range(len(ll)) if q == 0 or ll[q] != ll[q - 1]]
            T = tiles.setdefault(t[j], {'routes': {}, 'pieces': [], 'stops': {}})
            st = [[n, s, round(d, 1)] for n, s, d in r['stops'] if a * STEP - STEP / 2 <= d <= b * STEP + STEP / 2]
            T['routes'][r['k']] = r['meta']
            T['pieces'].append({'k': r['k'], 'i0': a, 'p': [int(round(v)) for x, y in pts for v in ((x - ox) * 10, (y - oy) * 10)],
                                'z': [None if v is None else int(round(v * 10)) for v in zz], 'r': [int(round(v * 10)) for v in rr], 'lv': runs, 'stops': st})
            for n, s, d in st:
                if s not in T['stops']:
                    sx, sy = TO_HK.transform(float(stops[s]['long']), float(stops[s]['lat'])); sz = dtm.lowest(sx, sy)
                    T['stops'][s] = [stops[s]['name_en'], stops[s]['name_tc'], int(round((sx - ox) * 10)), int(round((sy - oy) * 10)), stops[s]['co'],
                                     None if sz is None else int(round(sz * 10))]
            j = k + 1
    out = os.path.join(sources.OUT, 'gis', 'hk', 'bus'); shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
    sizes = {}
    for (ix, iy), T in tiles.items():
        ox, oy = X0 + ix * TILE, Y0 + iy * TILE
        (l0, b0), (l1, b1), (l2, b2) = [TO_LL.transform(x, y) for x, y in ((ox, oy), (ox + TILE, oy), (ox, oy + TILE))]
        T['ll'] = [l0, (l1 - l0) / (TILE * 10), (l2 - l0) / (TILE * 10), b0, (b1 - b0) / (TILE * 10), (b2 - b0) / (TILE * 10)]   # per decimetre
        p = os.path.join(out, f'{ix}_{iy}.json.gz')                    # gzipped: 49 -> 14 MB; hosts like HF do not compress on the fly
        with gzip.open(p, 'wt', encoding='utf-8', compresslevel=9) as f: json.dump(T, f, ensure_ascii=False, separators=(',', ':'))
        sizes[f'{ix}_{iy}'] = os.path.getsize(p)
    # lon/lat -> tile, for the page: an affine fitted over HK (metres off by well under a tile)
    g = np.array([(x, y) for x in np.linspace(800000, 865000, 14) for y in np.linspace(800000, 850000, 11)])
    ll = np.array([TO_LL.transform(x, y) for x, y in g]); A = np.c_[np.ones(len(ll)), ll]
    fx, fy = np.linalg.lstsq(A, g[:, 0], rcond=None)[0], np.linalg.lstsq(A, g[:, 1], rcond=None)[0]
    idx = {'built': datetime.datetime.now().isoformat(timespec='seconds'), 'ext': '.json.gz', 'tile_m': TILE, 'origin': [X0, Y0], 'step_m': STEP, 'no_level': NO_LEVEL,
           'to_hk': [list(fx), list(fy)], 'tiles': sizes, 'routes': len(routes), 'skipped': skipped,
           'source': f'TD Bus Route + Green Minibus Route (CSDI), KMB/LWB, Citybus and GMB route-stop and stop lists, TD IRN levels, Lands D DTM, {DAY}'}
    json.dump(idx, open(os.path.join(out, 'index.json'), 'w'), ensure_ascii=False, separators=(',', ':'))
    for a in sorted(os.listdir(os.path.join(sources.OUT, 'gis'))):          # every area shows the whole network
        p = os.path.join(sources.OUT, 'gis', a, 'index.json')
        if a == 'hk' or not os.path.exists(p): continue
        ix = json.load(open(p)); ix['layers']['bus'] = {'file': 'gis/hk/bus/index.json', 'features': len(routes), 'source': idx['source']}
        json.dump(ix, open(p, 'w'), indent=1)
    print(json.dumps({'routes': len(routes), 'tiles': len(tiles), 'MB': round(sum(sizes.values()) / 1e6, 1), 'largest_kB': round(max(sizes.values()) / 1e3), 'skipped': len(skipped)}))

if __name__ == '__main__':
    main()
