"""Command line for the file route.   python adapters/cli.py solve --schedule s.csv --fleet f.csv --roads r.csv --out dispatch.csv
Commands: solve | compare | fit.  Run from the sim-standalone folder."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'engine'))
sys.path.insert(0, HERE)
import twin_api as T          # noqa: E402
import csv_adapter as A       # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('cmd', choices=['solve', 'compare', 'fit'])
    ap.add_argument('--schedule'); ap.add_argument('--fleet'); ap.add_argument('--roads'); ap.add_argument('--failures')
    ap.add_argument('--out', default='out.csv'); ap.add_argument('--json', help='also save the full JSON response here')
    ap.add_argument('--reps', type=int, default=10)
    a = ap.parse_args(argv)
    base = T.default_config()
    if a.cmd == 'fit':
        req = A.failures_to_request(a.failures, {k: v['mttr_h'] for k, v in base['fleets'].items()})
    else:
        req = dict(op=a.cmd, n_reps=a.reps, config=A.build_config(base, a.schedule, a.fleet, a.roads))
    resp = T.run(req)
    if not resp.get('ok'):
        print('Engine error:', resp.get('error'), file=sys.stderr); return 2
    if a.json:
        with open(a.json, 'w') as f: json.dump(resp, f, indent=1)
    if a.cmd == 'solve':
        A.plan_to_csv(resp, a.out)
    elif a.cmd == 'compare':
        A.range_to_csv(resp, a.out); A.plan_to_csv(resp, os.path.splitext(a.out)[0] + '_plan.csv')
    else:
        import csv
        with open(a.out, 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['fleet', 'beta', 'eta_h', 'mtbf_h', 'availability', 'n_failures'])
            for k, v in resp['fits'].items() if 'fits' in resp else resp.items():
                if isinstance(v, dict) and 'beta' in v:
                    w.writerow([k, v['beta'], v['eta'], v['mtbf_h'], v.get('availability'), v['n_failures']])
    print('wrote', a.out); return 0


if __name__ == '__main__':
    sys.exit(main())
