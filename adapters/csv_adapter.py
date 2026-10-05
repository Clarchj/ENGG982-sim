"""CSV adapters: plain files in, plain files out, so any planning or fleet tool that can read and write CSV can use the engine.

Inputs (all optional; whatever is missing falls back to default_config.json):
  schedule.csv   front,material,tonnes_per_shift      material = ore | waste
  fleet.csv      fleet,n,payload,load_min,spot_min,dump_min,cost_h,mtbf_h,mttr_h,beta
  roads.csv      from,to,km,grade_pct
  failures.csv   fleet,hours,failed                   hours = running hours of one spell, failed = 1 failure / 0 still running
Outputs:
  dispatch.csv   front,destination,fleet,trips_per_shift,tonnes_per_shift   (the plan, advice only)
  range.csv      measure,mean,ci95                    (from a compare run)
Only the Python standard library is used here."""
import csv
import json


def _rows(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        return [{k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()} for r in csv.DictReader(f)]


def _f(x):
    return float(x)


def schedule_to_config(path, base):
    """Planned tonnes per front per shift -> plan_ore_t, strip_ratio, ore_share, front lists. Fronts must exist in the road graph."""
    rows = _rows(path)
    nodes = {n for e in base['edges'] for n in e[:2]}
    ore = {r['front']: _f(r['tonnes_per_shift']) for r in rows if r['material'].lower() == 'ore'}
    waste = {r['front']: _f(r['tonnes_per_shift']) for r in rows if r['material'].lower() == 'waste'}
    unknown = [n for n in list(ore) + list(waste) if n not in nodes]
    if unknown:
        raise ValueError(f'fronts not in the road graph: {unknown}. Add them to roads.csv first.')
    if not ore:
        raise ValueError('schedule has no ore rows')
    tot_ore = sum(ore.values())
    cfg = dict(plan_ore_t=tot_ore, strip_ratio=(sum(waste.values()) / tot_ore) if waste else base['strip_ratio'],
               ore_fronts=list(ore), waste_fronts=list(waste) or base['waste_fronts'],
               ore_share={k: v / tot_ore for k, v in ore.items()})
    nl = dict(base['n_loaders'])
    for k in list(ore) + list(waste):
        nl.setdefault(k, 1)
    cfg['n_loaders'] = nl
    return cfg


def fleet_to_config(path, base):
    fl = {}
    for r in _rows(path):
        d = dict(base['fleets'].get(r['fleet'], {}))
        for k, v in r.items():
            if k != 'fleet' and v != '':
                d[k] = int(float(v)) if k == 'n' else _f(v)
        fl[r['fleet']] = d
    return dict(fleets=fl)


def roads_to_config(path):
    return dict(edges=[[r['from'], r['to'], _f(r['km']), _f(r['grade_pct'])] for r in _rows(path)])


def failures_to_request(path, mttr_h=None):
    return dict(op='fit_failures', records=[dict(fleet=r['fleet'], hours=_f(r['hours']), failed=int(float(r['failed']))) for r in _rows(path) if _f(r['hours']) > 0],   # zero-hour spells carry no information
                mttr_h=mttr_h or {})


def plan_to_csv(resp, path):
    trips = resp['plan']['trips']
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['front', 'destination', 'fleet', 'trips_per_shift', 'tonnes_per_shift'])
        for t in sorted(trips, key=lambda t: (t['front'], t['destination'], t['fleet'])):
            w.writerow([t['front'], t['destination'], t['fleet'], t['trips_per_shift'], t['tonnes_per_shift']])


def range_to_csv(resp, path):
    """Mean and 95% interval for every measure of S0 and S2, plus the paired gain."""
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scenario', 'measure', 'mean', 'ci95'])
        for sc in ('S0', 'S2'):
            for k, v in resp[sc].items():
                if isinstance(v, dict) and 'mean' in v:
                    w.writerow([sc, k, round(v['mean'], 4), round(v.get('ci95', v.get('ci', 0)) or 0, 4)])
        g = resp['gain_S2_vs_S0']
        w.writerow(['S2_vs_S0', 'gain_cost_per_plan_equivalent_tonne', round(g['mean'], 5), round(g.get('ci95', g.get('ci', 0)), 5)])


def build_config(base, schedule=None, fleet=None, roads=None):
    from copy import deepcopy
    cfg = {}
    b = deepcopy(base)
    if roads:
        r = roads_to_config(roads); cfg.update(r); b.update(r)
    if schedule:
        cfg.update(schedule_to_config(schedule, b))
    if fleet:
        cfg.update(fleet_to_config(fleet, b))
    return cfg
