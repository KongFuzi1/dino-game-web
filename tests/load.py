"""Simulate N phones: each polls /api/scores every 5 s and posts a score every ~20 s, for DURATION s.
# Usage: python3 tests/load.py <phones> <seconds> [burst_spread_seconds]   e.g. 300 90 30
Also a one-off burst where everyone loads the page assets at once (the QR-code moment)."""
import http.client, json, random, sys, threading, time, collections
HOST, PORT = "129.151.208.139", 8080
N, DURATION = int(sys.argv[1]), int(sys.argv[2])
lat = collections.defaultdict(list); codes = collections.Counter(); errors = collections.Counter()
lock = threading.Lock()
_tl = threading.local()
def conn():
    if getattr(_tl, "c", None) is None:
        _tl.c = http.client.HTTPConnection(HOST, PORT, timeout=15)
    return _tl.c
def req(method, path, body=None):
    t = time.perf_counter()
    try:
        c = conn()
        hdr = {"Host": "dino.129-151-208-139.nip.io", "Content-Type": "application/json", "Connection": "keep-alive"}
        c.request(method, path, body=json.dumps(body) if body else None, headers=hdr)
        r = c.getresponse(); r.read(); code = r.status
    except Exception as e:
        code = type(e).__name__
        try: _tl.c.close()
        except Exception: pass
        _tl.c = None
    dt = time.perf_counter() - t
    with lock: lat[f"{method} {path}"].append(dt); codes[code] += 1
    return code
BURST_SPREAD = float(sys.argv[3]) if len(sys.argv) > 3 else 0
def page_burst(i):
    time.sleep(random.random() * BURST_SPREAD)   # QR scans arrive over BURST_SPREAD seconds
    for p in ["/", "/static/runner.js", "/static/runner.css", "/static/assets/default_200_percent/200-offline-sprite.png", "/api/scores"]:
        req("GET", p)
def player(i, stop):
    name = f"load{i}"
    time.sleep(random.random() * 5)
    nxt_post = time.time() + random.uniform(5, 20)
    while time.time() < stop:
        req("GET", "/api/scores")
        if time.time() >= nxt_post:
            req("POST", "/api/scores", {"name": name, "score": random.randint(20, 900)}); nxt_post = time.time() + random.uniform(15, 25)
        time.sleep(5)
t0 = time.time()
print(f"burst: {N} phones load the page within {BURST_SPREAD}s"); ths = [threading.Thread(target=page_burst, args=(i,)) for i in range(N)]
[t.start() for t in ths]; [t.join() for t in ths]; print(f"burst done in {time.time()-t0:.1f}s")
stop = time.time() + DURATION
ths = [threading.Thread(target=player, args=(i, stop)) for i in range(N)]
[t.start() for t in ths]; [t.join() for t in ths]
print(f"\nphase: {N} players for {DURATION}s"); print("status codes:", dict(codes))
for k, v in sorted(lat.items()):
    v.sort(); n = len(v)
    print(f"{k:60s} n={n:6d} p50={v[n//2]*1000:7.0f}ms p95={v[int(n*.95)]*1000:7.0f}ms max={v[-1]*1000:7.0f}ms")
