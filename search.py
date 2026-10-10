"""The GIS pages' search index: every bus, minibus and MTR stop with the routes it serves, and every route, as Parquet behind a
view-only DuckDB file (duckdb.org 2026-10-07): out/gis/hk/search/{stops,routes}.parquet and
search.duckdb, which only holds views naming gis/hk/search/*.parquet. The page attaches it with DuckDB-Wasm on the first
search (gis/src/places.ts) and reads it by HTTP Range.
Built from what bus.py and mtr.py already wrote (out/gis/hk/bus/*.json, out/gis/hk/mtr.json): no downloads.
Usage: python3 search.py     (after bus.py and mtr.py)"""
import glob, gzip, json, os
import duckdb
import sources

def main():
    hk = os.path.join(sources.OUT, 'gis', 'hk'); out = os.path.join(hk, 'search'); os.makedirs(out, exist_ok=True)
    stops, routes = {}, {}                                              # sid -> row; route key -> row
    for p in glob.glob(os.path.join(hk, 'bus', '*_*.json*')):
        t = json.load(gzip.open(p) if p.endswith('.gz') else open(p)); l0, ax, ay, b0, bx, by = t['ll']
        for sid, (en, tc, x, y, co, _z) in t['stops'].items():
            stops.setdefault(sid, {'id': sid, 'name_en': en, 'name_tc': tc, 'co': co, 'lon': round(l0 + ax * x + ay * y, 6), 'lat': round(b0 + bx * x + by * y, 6), 'routes': set()})
        for pc in t['pieces']:
            co, name, bound, gid, dest = t['routes'][pc['k']]
            r = routes.setdefault(pc['k'], {'key': pc['k'], 'co': co, 'route': name, 'bound': bound, 'dest': dest, 'stops': set(), 'last': (-1, None)})
            for seq, sid, _d in pc['stops']:
                stops[sid]['routes'].add(f'{co} {name}'); r['stops'].add(sid)
                if seq > r['last'][0]: r['last'] = (seq, sid)
    m = os.path.join(hk, 'mtr.json'); mtr = json.load(open(m)) if os.path.exists(m) else {'lines': [], 'names': {}}   # mtr.py is optional
    for l in mtr['lines']:
        for code, en, tc, _m, lon, lat, *_ in l['stations']:
            s = stops.setdefault(f'MTR-{code}', {'id': f'MTR-{code}', 'name_en': f'{en} Station', 'name_tc': f'{tc}站', 'co': 'MTR', 'lon': lon, 'lat': lat, 'routes': set()})
            s['routes'].add(f"MTR {l['line']}")
        routes.setdefault(f"MTR|{l['line']}|{l['branch'] or ''}", {'key': f"MTR|{l['line']}|{l['branch'] or ''}", 'co': 'MTR', 'route': l['line'],
                                                                 'bound': l['branch'] or '', 'dest': mtr['names'].get(l['stations'][-1][0], [l['stations'][-1][1]])[0], 'stops': {f'MTR-{s[0]}' for s in l['stations']}})
    # a route's place on the map: the middle of its stops (the search flies there)
    srows = [{**s, 'routes': sorted(s['routes'])} for s in stops.values()]
    rrows = []
    for r in routes.values():
        if not r['dest'] and r.get('last', (0, None))[1] in stops: r['dest'] = stops[r['last'][1]]['name_en']   # buses: the last stop
        pts = [stops[s] for s in r['stops'] if s in stops]
        if not pts: continue
        rrows.append({**{k: r[k] for k in ('key', 'co', 'route', 'bound', 'dest')}, 'stops': len(pts),
                      'lon': round(sum(p['lon'] for p in pts) / len(pts), 6), 'lat': round(sum(p['lat'] for p in pts) / len(pts), 6)})
    c = duckdb.connect()
    for name, rows in (('stops', srows), ('routes', rrows)):
        p = os.path.join(out, f'{name}.json'); json.dump(rows, open(p, 'w'), ensure_ascii=False)
        c.execute(f"COPY (SELECT * FROM read_json_auto('{p}') ORDER BY co, {'name_en' if name == 'stops' else 'route'}) TO '{os.path.join(out, name + '.parquet')}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 4096)")
        os.remove(p)
    db = os.path.join(out, 'search.duckdb')
    if os.path.exists(db): os.remove(db)
    here = os.getcwd(); os.chdir(sources.OUT)                           # DuckDB checks a view's files when it is made: from out/
    try:
        v = duckdb.connect(db)
        for name in ('stops', 'routes'): v.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('gis/hk/search/{name}.parquet')")
        v.close()
    finally: os.chdir(here)
    sizes = {f: os.path.getsize(os.path.join(out, f)) for f in os.listdir(out)}
    print(json.dumps({'stops': len(srows), 'routes': len(rrows), 'bytes': sizes}))

if __name__ == '__main__':
    main()
