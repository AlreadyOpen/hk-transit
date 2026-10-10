"""MTR lines and stations around a GIS area, for the GIS pages' moving trains (gis/src/metro.ts).
  - track: Lands D iB5000 topographic map, Transportation/RailLine (FEATURECODE MTR), with DATALEVEL: OG over ground (at grade
    or on a viaduct), UG underground (US / AS: under / above another structure, drawn as over ground)
  - stations: iB5000 PlacesOfInterest/POIPoint FEATURECODE RSN ('Mass Transit Railway <name> Station'); where a station has
    no RSN point (most underground ones), the middle of its exits (MTA '... <name> Station-<x> Access')
  - order and codes: MTR open data mtr_lines_and_stations.csv (the codes the next-train API uses)
Each sheet zip (~22 MB) is fetched whole with curl and only its two GML members kept: the map server answers Range requests
with the whole file, and its TLS chain lacks the Hongkong Post intermediate (curl on macOS completes it; Python and curl on Linux
do not), so curl verifies against certifi's roots plus that intermediate (certs/, public, from the leaf's AIA URL). For each line (and branch) the stations near the area,
plus one beyond each end, are joined along the track graph (shortest path between consecutive stations); the result has
each station's distance along it and the runs of track level, so the page places a train by distance.
Heights for outside the photomesh: Lands D DTM (dtm.py) every Z_STEP m along each line ('z', from its first point; over ground the
DTM at the track, which includes viaducts; underground the ground above, the page goes below it) and at each station (the
lowest within 15 m: street level); 'zr' = the rise above the local ground (0 at grade and underground), which the page uses on
the flat map (Hills off: the imagery lies at 0 m).
Usage: python3 mtr.py <area id>     (centre and radius from out/gis/<area>/index.json; writes mtr.json, adds it to index.json)
       python3 mtr.py hk            (every line and station in HK, all 193 sheets, ~4 GB fetched once: writes gis/hk/mtr.json
                                     and points every area's index.json at it)"""
import csv, datetime, heapq, io, json, math, os, re, subprocess, sys, urllib.parse, urllib.request, zipfile
import certifi
from pyproj import Transformer
import dtm, sources

SHEETS = 'https://portal.csdi.gov.hk/server/rest/services/common/landsd_rcd_1637224243141_96556/FeatureServer/0/query'
STATIONS = 'https://opendata.mtr.com.hk/data/mtr_lines_and_stations.csv'
TO_LL = Transformer.from_crs(2326, 4326, always_xy=True)
NEAR_M, SNAP_M, STATION_M, Z_STEP = 1500, 3, 250, 20        # stations within radius + 1500 m; track ends within 3 m join; station to track
COLOR = {'AEL': '#00888e', 'TCL': '#f38b00', 'TML': '#9a3b26', 'TKL': '#7d499d', 'EAL': '#5eb6e4', 'SIL': '#bac429',
         'TWL': '#e2231a', 'ISL': '#0860a8', 'KTL': '#00ab4e', 'DRL': '#f173ac'}

def ca_bundle():
    """certifi's roots + the Hongkong Post intermediate open.hkmapservice.gov.hk does not send (expires 2032-06-03), as one file"""
    p = os.path.join(sources.CACHE, 'ca-bundle.pem')
    if not os.path.exists(p):
        os.makedirs(sources.CACHE, exist_ok=True)
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'certs', 'hongkongpost-e-cert-ssl-ca-3-17.pem')
        open(p + '.part', 'w').write(open(certifi.where()).read() + open(here).read()); os.replace(p + '.part', p)
    return p

def sheet_members(a, day):
    """RailLine and POIPoint GML of one iB5000 sheet, cached by sheet and revision"""
    rev = datetime.datetime.fromtimestamp(a['REVISIONDATE'] / 1000, datetime.UTC).date().isoformat()
    d = os.path.join(sources.CACHE, 'mtr', f"{a['SHEETNO']}-{rev}"); os.makedirs(d, exist_ok=True); out = {}
    wants = ('Transportation/RailLine.gml', 'PlacesOfInterest/POIPoint.gml')
    if not all(os.path.exists(os.path.join(d, os.path.basename(w))) for w in wants):
        z = os.path.join(d, 'sheet.zip')
        subprocess.run(['curl', '-fsSL', '--retry', '3', '--cacert', ca_bundle(), '-o', z, a['GML']], check=True)
        with zipfile.ZipFile(z) as zf:
            for w in wants:
                name = next((n for n in zf.namelist() if n.endswith(w)), None); p = os.path.join(d, os.path.basename(w))   # a sheet with no railway has no RailLine
                open(p + '.part', 'wb').write(zf.read(name) if name else b''); os.replace(p + '.part', p)
        os.remove(z)
    return {w: open(os.path.join(d, os.path.basename(w)), encoding='utf-8').read() for w in wants}

def gml_features(text, kind):
    for f in re.findall(rf'<fme:{kind} .*?</fme:{kind}>', text, re.S):
        g = lambda k: (re.search(rf'<fme:{k}>([^<]*)<', f) or [None, None])[1]
        pos = re.search(r'<gml:pos(?:List)?>([^<]*)<', f).group(1).split()
        pts = [(float(pos[i + 1]), float(pos[i])) for i in range(0, len(pos), 3)]   # iB5000 GML lists northing first
        yield g, pts

def z_along(coords, levels):
    """DTM heights every Z_STEP m along [(E, N, m)]: over ground at the track, underground the lowest within 15 m (the ground above)"""
    out, i, m0 = [], 1, coords[0][2]
    for k in range(int((coords[-1][2] - m0) // Z_STEP) + 2):
        d = min(m0 + k * Z_STEP, coords[-1][2])
        while i < len(coords) - 1 and coords[i][2] < d: i += 1
        (xa, ya, ma), (xb, yb, mb) = coords[i - 1], coords[i]; t = (d - ma) / ((mb - ma) or 1); x, y = xa + (xb - xa) * t, ya + (yb - ya) * t
        lv = 0
        for dd, l in levels:
            if dd > d: break
            lv = l
        g = dtm.lowest(x, y); h = g if lv < 0 else dtm.height(x, y)
        out.append((None if h is None else round(h, 1), 0 if h is None or g is None else round(max(0.0, h - g), 1)))
    return out

def main(area):
    whole = area == 'hk'
    d = os.path.join(sources.OUT, 'gis', area); os.makedirs(d, exist_ok=True); day = datetime.date.today().isoformat()
    if whole: E = N = R = None; q = {'where': '1=1'}
    else:
        idx = json.load(open(os.path.join(d, 'index.json')))
        (E, N), R = idx['centre_E_N'], float(idx['radius_m']); far = R + NEAR_M + 2500
    q = {**q, 'outFields': 'SHEETNO,REVISIONDATE,GML', 'returnGeometry': 'false', 'f': 'json'} if whole else {'geometry': f'{round(E - far)},{round(N - far)},{round(E + far)},{round(N + far)}', 'geometryType': 'esriGeometryEnvelope', 'inSR': 2326,
         'spatialRel': 'esriSpatialRelIntersects', 'outFields': 'SHEETNO,REVISIONDATE,GML', 'returnGeometry': 'false', 'f': 'json'}
    sheets = json.load(urllib.request.urlopen(f'{SHEETS}?{urllib.parse.urlencode(q)}', timeout=60))['features']
    segs, rsn, exits = [], {}, {}
    for n, s in enumerate(sheets):                                       # fetch first (slow, cached), with progress
        sheet_members(s['attributes'], day); print(f"sheet {n + 1}/{len(sheets)} {s['attributes']['SHEETNO']}", file=sys.stderr, flush=True)                                       # [(pts, level)], station name -> (E, N) / exit points
    for s in sheets:
        m = sheet_members(s['attributes'], day)
        for g, pts in gml_features(m['Transportation/RailLine.gml'], 'RailLine'):
            if g('FEATURECODE') == 'MTR' and len(pts) > 1: segs.append((pts, g('DATALEVEL')))
        for g, pts in gml_features(m['PlacesOfInterest/POIPoint.gml'], 'POIPoint'):
            nm = re.fullmatch(r'Mass Transit Railway (.+) Station', g('ENGLISHNAME') or '')
            if g('FEATURECODE') == 'RSN' and nm: rsn[nm.group(1).lower()] = pts[0]
            ex = re.fullmatch(r'Mass Transit Railway (.+) Station(?:-\S+)? Access', g('ENGLISHNAME') or '')
            if g('FEATURECODE') == 'MTA' and ex: exits.setdefault(ex.group(1).lower(), []).append(pts[0])
    for n, ps in exits.items(): rsn.setdefault(n, (sum(p[0] for p in ps) / len(ps), sum(p[1] for p in ps) / len(ps)))
    # track graph: segment ends snapped to a 3 m grid (neighbouring cells joined), edges keep their geometry and level
    key = lambda p: (round(p[0] / SNAP_M), round(p[1] / SNAP_M))
    parent = {}
    def find(k):
        while parent.setdefault(k, k) != k: parent[k] = parent[parent[k]]; k = parent[k]
        return k
    for pts, _ in segs:
        for p in (pts[0], pts[-1]):
            k = key(p); find(k)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if (k[0] + dx, k[1] + dy) in parent: parent[find((k[0] + dx, k[1] + dy))] = find(k)
    adj, at = {}, {}                                                     # every vertex is a node (a station joins the nearest one)
    for si, (pts, lv) in enumerate(segs):
        ids = [find(key(pts[0]))] + [(si, i) for i in range(1, len(pts) - 1)] + [find(key(pts[-1]))]
        for (a, p), (b, q) in zip(zip(ids, pts), zip(ids[1:], pts[1:])):
            L = math.dist(p, q); adj.setdefault(a, []).append((b, L, [p, q], lv)); adj.setdefault(b, []).append((a, L, [q, p], lv)); at[a] = p; at[b] = q
    # dead ends (a track piece that stops short of the next, e.g. at a portal) join the nearest other track within 25 m
    for u in [u for u, e in adj.items() if len(e) == 1]:
        v = min((v for v in at if v != u and v != adj[u][0][0]), key=lambda v: math.dist(at[u], at[v]), default=None)
        if v is not None and math.dist(at[u], at[v]) <= 25:
            L = math.dist(at[u], at[v]); lv = adj[u][0][3]
            adj[u].append((v, L, [at[u], at[v]], lv)); adj.setdefault(v, []).append((u, L, [at[v], at[u]], lv))
    def path(srcs, dsts):
        """shortest track from any node in srcs to any in dsts (an interchange's nearest track can be the other line's):
        (legs, the node reached) or None"""
        dist, prev, h = {a: 0 for a in srcs}, {}, [(0, a) for a in srcs]; heapq.heapify(h); dsts = set(dsts); b = None
        while h:
            dd, u = heapq.heappop(h)
            if u in dsts: b = u; break
            if dd > dist[u]: continue
            for v, L, pts, lv in adj.get(u, []):
                if dd + L < dist.get(v, 1e18): dist[v] = dd + L; prev[v] = (u, pts, lv); heapq.heappush(h, (dd + L, v))
        if b is None: return None
        out, u = [], b
        while u in prev and u not in srcs: u0, pts, lv = prev[u]; out.append((pts, lv)); u = u0
        return out[::-1], b
    nodes = list(at)
    def near(p): return [n for n in nodes if math.dist(at[n], p) <= STATION_M]

    rows = list(csv.DictReader(io.StringIO(urllib.request.urlopen(STATIONS, timeout=60).read().decode('utf-8-sig'))))
    lines, gaps = [], []
    for (line, dirn) in sorted({(r['Line Code'], r['Direction']) for r in rows if r['Line Code'] and r['Direction'].endswith('UT')}):
        st = sorted((r for r in rows if r['Line Code'] == line and r['Direction'] == dirn), key=lambda r: float(r['Sequence']))
        pos = [rsn.get(r['English Name'].lower()) for r in st]
        inside = [i for i, p in enumerate(pos) if p and (whole or math.dist(p, (E, N)) <= R + NEAR_M)]
        if not inside: continue
        i0, i1 = max(0, min(inside) - 1), min(len(st) - 1, max(inside) + 1)
        chain = [(r, p, near(p) if p else []) for r, p in zip(st[i0:i1 + 1], pos[i0:i1 + 1])]
        chain = [c for c in chain if c[2]]
        coords, levels, stations, m, here = [], [], [], 0.0, None
        for k, (r, p, n) in enumerate(chain):
            if k:
                found = path(here, n)
                if found is None:
                    gaps.append(f"{line} {chain[k - 1][0]['Station Code']}-{r['Station Code']}"); legs = [([chain[k - 1][1], p], 'OG')]; here = n
                else: legs, b = found; here = [b]
                for pts, lv in legs:
                    lvl = -1 if lv == 'UG' else 0
                    if not levels or levels[-1][1] != lvl: levels.append([round(m, 1), lvl])
                    for a, b in zip(pts, pts[1:]):
                        if not coords: coords.append((*a, m))
                        m += math.dist(a, b); coords.append((*b, m))
            if not k: here = n
            zs = dtm.lowest(*p)
            stations.append([r['Station Code'], r['English Name'], r['Chinese Name'], round(m, 1), *(round(c, 6) for c in TO_LL.transform(*p)), None if zs is None else round(zs, 1)])
        if len(stations) < 2: continue
        lines.append({'line': line, 'branch': dirn[:-3] or None, 'color': COLOR.get(line, '#888888'), 'stations': stations, 'levels': levels,
                      'track': [[*(round(c, 6) for c in TO_LL.transform(x, y)), round(mm, 1)] for x, y, mm in coords], **dict(zip(('z', 'zr'), map(list, zip(*z_along(coords, levels)))))})
    out = {'area': area, 'built': datetime.datetime.now().isoformat(timespec='seconds'), 'z_step': Z_STEP,
           'source': f"Lands D iB5000 (RailLine, POIPoint) sheets {', '.join(sorted(s['attributes']['SHEETNO'] for s in sheets))} + MTR lines and stations CSV, {day}",
           'lines': lines, 'gaps': gaps, 'names': {r['Station Code']: [r['English Name'], r['Chinese Name']] for r in rows if r['Station Code']}}   # every code: train destinations
    json.dump(out, open(os.path.join(d, 'mtr.json'), 'w'), ensure_ascii=False, separators=(',', ':'))
    if not whole: json.dump(idx | {'layers': idx['layers'] | {'mtr': {'file': f'gis/{area}/mtr.json', 'features': len(lines), 'source': out['source']}}}, open(os.path.join(d, 'index.json'), 'w'), indent=1)
    else:                                                                # every area shows the whole network
        for a in sorted(os.listdir(os.path.join(sources.OUT, 'gis'))):
            p = os.path.join(sources.OUT, 'gis', a, 'index.json')
            if a == 'hk' or not os.path.exists(p): continue
            ix = json.load(open(p)); ix['layers']['mtr'] = {'file': 'gis/hk/mtr.json', 'features': len(lines), 'source': out['source']}
            json.dump(ix, open(p, 'w'), indent=1)
    print(json.dumps({'lines': [(l['line'], l['branch'], [s[0] for s in l['stations']], round(l['track'][-1][2])) for l in lines], 'gaps': gaps}))

if __name__ == '__main__':
    main(sys.argv[1])
