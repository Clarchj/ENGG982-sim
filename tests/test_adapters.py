"""Adapter and service checks. Run:  python tests/test_adapters.py   (from sim-standalone)"""
import json, os, subprocess, sys, tempfile, threading, time, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'engine')); sys.path.insert(0, os.path.join(ROOT, 'adapters'))
import twin_api as T, csv_adapter as A, cli
EX = os.path.join(ROOT, 'adapters', 'examples')
ok = True
def check(name, cond, info=''):
    global ok; ok &= bool(cond); print(('PASS ' if cond else 'FAIL ') + name, info)

base = T.default_config()
# 1. example CSVs rebuild the default configuration
cfg = A.build_config(base, f'{EX}/schedule.csv', f'{EX}/fleet.csv', f'{EX}/roads.csv')
merged = T.merge(base, cfg)
check('csv config equals default (plan_ore_t, strip, shares, fleets, edges)',
      abs(merged['plan_ore_t'] - base['plan_ore_t']) < 1e-6 and abs(merged['strip_ratio'] - base['strip_ratio']) < 1e-9
      and all(abs(merged['ore_share'][k] - base['ore_share'][k]) < 1e-9 for k in base['ore_share'])
      and merged['fleets'] == base['fleets'] and [list(e) for e in merged['edges']] == [list(e) for e in base['edges']])
# 2. same plan through CSV route and direct API
r_direct = T.run(dict(op='solve'))
r_csv = T.run(dict(op='solve', config=cfg))
check('CSV route gives the same plan total as the direct API',
      abs(r_direct['plan']['total_tonnes_per_shift'] - r_csv['plan']['total_tonnes_per_shift']) < 1e-6,
      f"{r_direct['plan']['total_tonnes_per_shift']}")
# 3. unknown front is rejected with a clear message
with tempfile.TemporaryDirectory() as d:
    p = f'{d}/s.csv'; open(p, 'w').write('front,material,tonnes_per_shift\nZZ,ore,1000\n')
    try: A.schedule_to_config(p, base); check('unknown front rejected', False)
    except ValueError as e: check('unknown front rejected', 'ZZ' in str(e), str(e)[:60])
    # 4. CLI: solve, compare, fit write files
    rc = cli.main(['solve', '--schedule', f'{EX}/schedule.csv', '--fleet', f'{EX}/fleet.csv', '--roads', f'{EX}/roads.csv', '--out', f'{d}/dispatch.csv'])
    rows = open(f'{d}/dispatch.csv').read().strip().splitlines()
    check('cli solve writes dispatch.csv', rc == 0 and rows[0].startswith('front,destination') and len(rows) > 3, f'{len(rows)-1} rows')
    rc = cli.main(['compare', '--reps', '3', '--out', f'{d}/range.csv'])
    txt = open(f'{d}/range.csv').read()
    check('cli compare writes range.csv with gain row', rc == 0 and 'S2_vs_S0' in txt and os.path.exists(f'{d}/range_plan.csv'))
    rc = cli.main(['fit', '--failures', f'{EX}/failures.csv', '--out', f'{d}/fit.csv'])
    fit = open(f'{d}/fit.csv').read().splitlines()
    check('cli fit writes fits', rc == 0 and len(fit) == 3, fit[1] if len(fit) > 1 else '')
# 5. service round trip
port = 18765
p = subprocess.Popen([sys.executable, os.path.join(ROOT, 'serve.py'), str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        try: urllib.request.urlopen(f'http://127.0.0.1:{port}/api', timeout=1); break
        except urllib.error.HTTPError: break
        except Exception: time.sleep(0.25)
    def post(o):
        r = urllib.request.Request(f'http://127.0.0.1:{port}/api', json.dumps(o).encode(), {'Content-Type': 'application/json'})
        return json.loads(urllib.request.urlopen(r, timeout=60).read())
    j = post(dict(op='solve'))
    check('service POST /api solve matches direct', j['ok'] and abs(j['plan']['total_tonnes_per_shift'] - r_direct['plan']['total_tonnes_per_shift']) < 1e-6)
    j = post(dict(op='nonsense'))
    check('service returns an error object for a bad op', j['ok'] is False and 'error' in j, j.get('error'))
finally:
    p.terminate()
print('ALL PASS' if ok else 'SOME FAILED'); sys.exit(0 if ok else 1)
