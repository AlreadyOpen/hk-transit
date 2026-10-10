"""Lands D Digital Terrain Model (dataset hk-landsd-openmap-5m-grid-dtm): heights in mPD for any HK1980 point, for the bakes
that place things outside the photomesh (bus.py, mtr.py). 5 m grid, ±5 m; it is the top surface: it includes elevated roads
and bridges, and the tops of trees where land is wooded (Lands D's own description).
The ASC (302 MB inside a 28.7 MB zip, 2019) is converted once, a row at a time, to int16 decimetres in data/cache/dtm/dtm.npy
(245 MB), which is then memory-mapped. Usage: python3 dtm.py [E N]   (builds the grid; prints the height at E, N)
       python3 dtm.py tiles  (MapLibre terrain tiles, see tiles())"""
import json, os, subprocess, sys, zipfile
import numpy as np
import sources

URL = 'https://www.landsd.gov.hk/landsd_psi_data/SMO/data/Whole_HK_DTM_5m.zip'
D = os.path.join(sources.CACHE, 'dtm')
NPY, HDR = os.path.join(D, 'dtm.npy'), os.path.join(D, 'dtm-header.txt')
_grid = None

def build():
    z = os.path.join(D, 'Whole_HK_DTM_5m.zip'); os.makedirs(D, exist_ok=True)
    if not os.path.exists(z):   # the server drops HTTP/2 streams mid-file: HTTP/1.1 with resume
        subprocess.run(['curl', '-fsSL', '--http1.1', '--retry', '5', '-C', '-', '-o', z, URL], check=True)
    with zipfile.ZipFile(z) as zf, zf.open(zf.namelist()[0]) as f:
        h = {}
        for _ in range(6): k, v = f.readline().split(); h[k.decode().lower()] = float(v)
        nc, nr = int(h['ncols']), int(h['nrows'])
        out = np.lib.format.open_memmap(NPY + '.part', mode='w+', dtype=np.int16, shape=(nr, nc))
        for r in range(nr):
            row = np.array(f.readline().split(), dtype=np.float32)
            out[r] = np.where(row == h['nodata_value'], -32768, np.round(row * 10)).astype(np.int16)
        out.flush(); del out
    os.replace(NPY + '.part', NPY)
    open(HDR, 'w').write(' '.join(f'{k}={h[k]}' for k in h))

def _load():
    global _grid
    if _grid is None:
        if not os.path.exists(NPY): build()
        h = dict(kv.split('=') for kv in open(HDR).read().split())
        _grid = (np.load(NPY, mmap_mode='r'), float(h['xllcorner']), float(h['yllcorner']), float(h['cellsize']))
    return _grid

def _cell(E, N):
    g, x0, y0, s = _load()
    return g, (E - x0) / s - 0.5, (g.shape[0] - 1) - ((N - y0) / s - 0.5)   # row 0 is the north edge; cell centres

def height(E, N):
    """mPD at (E, N), bilinear over the 4 nearest cells; None off the grid or on no data"""
    g, c, r = _cell(E, N); c0, r0 = int(np.floor(c)), int(np.floor(r))
    if not (0 <= c0 < g.shape[1] - 1 and 0 <= r0 < g.shape[0] - 1): return None
    w = np.asarray(g[r0:r0 + 2, c0:c0 + 2], dtype=np.float32)
    if (w == -32768).any(): return None
    tc, tr = c - c0, r - r0
    return float(((w[0, 0] * (1 - tc) + w[0, 1] * tc) * (1 - tr) + (w[1, 0] * (1 - tc) + w[1, 1] * tc) * tr) / 10)

def lowest(E, N, r_m=15):
    """the lowest mPD within r_m of (E, N): ground beside a deck, where the point itself is the deck; None if no data"""
    g, c, r = _cell(E, N); k = int(np.ceil(r_m / _load()[3]))
    c0, r0 = int(round(c)), int(round(r))
    w = np.asarray(g[max(0, r0 - k):r0 + k + 1, max(0, c0 - k):c0 + k + 1])
    w = w[w != -32768]
    return float(w.min()) / 10 if w.size else None

def tiles(zmin=8, zmax=14):
    """MapLibre raster-dem tiles (Terrarium PNG, 256 px) of the DTM over HK in out/gis/hk/terrain/{z}/{x}/{y}.png, for the
    map's own terrain (gis-page ?terrain=1). Sea and no data are 0 mPD."""
    import math
    from PIL import Image
    from pyproj import Transformer
    g, x0, y0, s = _load(); T = Transformer.from_crs(4326, 2326, always_xy=True)
    out = os.path.join(sources.OUT, 'gis', 'hk', 'terrain'); n = 0
    W, S, E_, N_ = 113.81, 22.13, 114.45, 22.58                          # HK's land and the grid's extent
    lat2y = lambda lat, z: (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2 ** z
    for z in range(zmin, zmax + 1):
        for tx in range(int((W + 180) / 360 * 2 ** z), int((E_ + 180) / 360 * 2 ** z) + 1):
            for ty in range(int(lat2y(N_, z)), int(lat2y(S, z)) + 1):
                px = (np.arange(256) + 0.5) / 256
                lon = (tx + px) / 2 ** z * 360 - 180
                lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (ty + px) / 2 ** z))))
                LON, LAT = np.meshgrid(lon, lat); Ee, Nn = T.transform(LON, LAT)
                c = np.clip(np.round((Ee - x0) / s - 0.5).astype(int), 0, g.shape[1] - 1)
                r = np.clip(np.round((g.shape[0] - 1) - ((Nn - y0) / s - 0.5)).astype(int), 0, g.shape[0] - 1)
                h = np.asarray(g[r, c], dtype=np.float32) / 10
                h[(h < -100) | (Ee < x0) | (Nn < y0)] = 0
                v = h + 32768
                img = np.dstack([np.floor(v / 256), np.floor(v) % 256, np.floor((v - np.floor(v)) * 256)]).astype(np.uint8)
                p = os.path.join(out, str(z), str(tx)); os.makedirs(p, exist_ok=True)
                Image.fromarray(img, 'RGB').save(os.path.join(p, f'{ty}.png'), optimize=True); n += 1
    print(json.dumps({'tiles': n, 'dir': out}))

if __name__ == '__main__':
    if not os.path.exists(NPY): build()
    if sys.argv[1:] == ['tiles']: tiles()
    elif len(sys.argv) == 3: E, N = map(float, sys.argv[1:]); print({'height': height(E, N), 'lowest15': lowest(E, N)})
