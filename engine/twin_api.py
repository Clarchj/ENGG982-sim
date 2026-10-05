"""Lukaut Haul Twin: core API. JSON in, JSON out. No UI, no files, no global state.

    result = run({"op": "compare", "config": {...overrides...}, "n_reps": 10})

Operations (all take an optional "config" of overrides on top of default_config.json):
  solve         Prescribe: optimiser plan (trips and tonnes per front, fleet, shift) and travel-time matrix.
  simulate      Prove: n replications of one scenario; mean, sd, 95% CI; optional animation trace of replication 0.
  compare       Fixed assignment (S0) against optimiser-guided dispatch (S2), paired, with the gain and its 95% CI.
  fleet_curve   Throughput against fleet size for both dispatch modes.
  fit_failures  Predict: Weibull fit to failure records [{"fleet","hours","failed"}] -> beta, eta, MTBF, availability.
The same file runs on a laptop (CLI), behind a local web service (serve.py) and in the browser (Pyodide).
Everything here is a worked example on notional data: results show a method, not a forecast for any site."""
import json
import math
import sys
import numpy as np

from params import P as _DEFAULT, BASE_SEED
from failure import fit_weibull, mtbf_from, eta_from_mtbf, availability
from optimiser import solve_plan, fixed_assignment, fixed_from_plan
from roads import travel_matrices
from twin import Twin

SCHEMA_VERSION = '0.1'
KEYS = ['tonnes_shift', 'ore_shift', 'waste_shift', 'crushed_shift', 'strip_ratio', 'cycle_A', 'cycle_B',
        'queue_loader_min', 'queue_crusher_min', 'truck_util', 'truck_downtime_share', 'loader_util',
        'match_factor', 'cost_per_t', 'pet_shift', 'cost_per_pet', 'unplanned', 'planned']

try:                                        # t critical value for 95% two-sided CI
    from scipy import stats as _st
    def _t975(df): return float(_st.t.ppf(0.975, df))
except Exception:                           # pragma: no cover
    def _t975(df): return 1.96


# ---------------------------------------------------------------- configuration
def default_config():
    return json.loads(json.dumps(_DEFAULT))


def merge(base, over):
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def validate(cfg):
    """Clear messages for inputs the model cannot run (found by independent verification, defects D4 to D7)."""
    def bad(msg): raise ValueError(msg)
    if not cfg['horizon_h'] > cfg['warmup_h'] >= 0: bad('horizon_h must be larger than warmup_h, and warmup_h cannot be negative')
    if not cfg['shift_h'] > cfg['changeover_h'] + cfg['break_h']: bad('shift_h must be longer than changeover_h plus break_h')
    if not 0 <= cfg['compliance'] <= 1: bad('compliance must be between 0 and 1')
    if not (cfg['speed_factor'] > 0 and cfg['cv_service'] > 0 and cfg['sigma_travel'] >= 0): bad('speed_factor and cv_service must be above 0')
    if not (cfg['plan_ore_t'] > 0 and cfg['strip_ratio'] > 0): bad('plan_ore_t and strip_ratio must be above 0')
    if cfg.get('crusher_outage') and not cfg['crusher_outage'][0] < cfg['crusher_outage'][1]: bad('crusher_outage must be [start_h, end_h] with start before end')
    if sum(d['n'] for d in cfg['fleets'].values()) < 1: bad('at least one truck is needed')
    for c, d in cfg['fleets'].items():
        if d['n'] < 0: bad(f'fleet {c}: n cannot be negative')
        if not (d['payload'] > 0 and d['mtbf_h'] > 0 and d['mttr_h'] > 0 and d['beta'] > 0): bad(f'fleet {c}: payload, mtbf_h, mttr_h and beta must be above 0')
    nodes = {n for e in cfg['edges'] for n in e[:2]}
    for f in list(cfg['ore_fronts']) + list(cfg['waste_fronts']):
        if f not in nodes: bad(f'front {f} is not in the road graph')
        if cfg['n_loaders'].get(f, 0) < 1: bad(f'front {f} needs at least one loader in n_loaders')
    return cfg


def _cfg(req):
    if req.get('scenario') is not None and req['scenario'] not in SCENARIOS:
        raise ValueError(f"unknown scenario {req['scenario']!r}; choose from {sorted(SCENARIOS)}")
    cfg = merge(default_config(), req.get('config'))
    if req.get('scenario'):
        cfg = merge(cfg, SCENARIOS[req['scenario']])
    if int(req.get('n_reps', 1)) < 1:
        raise ValueError('n_reps must be at least 1')
    return validate(cfg)


SCENARIOS = {                               # named overrides, ids as in the report
    'S0': {'dispatch': 'fixed'},
    'S2': {'dispatch': 'plan'},
    'S3': {'dispatch': 'plan', 'speed_factor': 0.75},
    'S4': {'dispatch': 'plan', 'crusher_outage': [12.0, 16.0]},
    'S5': {'dispatch': 'fixed', 'predictive_phi': 0.5},
}


def _avail(cfg):
    return {c: availability(d['mtbf_h'], d['mttr_h']) for c, d in cfg['fleets'].items()}


def _fit(cfg):
    return {c: (d['beta'], eta_from_mtbf(d['beta'], d['mtbf_h'])) for c, d in cfg['fleets'].items()}


# ---------------------------------------------------------------- metrics
def pet(res, cfg):
    """Plan-equivalent tonnes per shift: progress against the plan, limited by the scarcer material."""
    ore_plan = cfg['plan_ore_t']
    waste_plan = ore_plan * cfg['strip_ratio']
    return min(res['ore_shift'] / ore_plan, res['waste_shift'] / waste_plan) * (ore_plan + waste_plan)


def _rep(cfg, rep, plan_w, fixed, fit, record=False):
    t = Twin(cfg, rep, plan_w=plan_w, fixed=fixed, fit=fit, record=record, seed=cfg.get('seed', BASE_SEED))
    r = t.run_sim()
    cost_h = sum(d['n'] * d['cost_h'] for d in cfg['fleets'].values())
    r['pet_shift'] = pet(r, cfg)
    r['cost_per_pet'] = cost_h * cfg['shift_h'] / r['pet_shift'] if r['pet_shift'] > 0 else float('nan')   # PET 0: no plan progress
    return r, (t.trace if record else None)


def summarise(rs):
    n = len(rs)
    tcrit = _t975(n - 1) if n > 1 else float('nan')
    out = {}
    for k in KEYS:
        v = np.array([r[k] for r in rs], float)
        sd = float(np.nanstd(v, ddof=1)) if n > 1 else 0.0
        out[k] = dict(mean=float(np.nanmean(v)), sd=sd, ci95=(tcrit * sd / math.sqrt(n)) if n > 1 else None)
    for k in ('queue_loader_by_front', 'queue_dump_by_dest'):
        out[k] = {kk: float(np.mean([r[k][kk] for r in rs])) for kk in rs[0][k]}
    out['conservation_gap_max_t'] = float(max(abs(r['loaded_t'] - r['dumped_t'] - r['in_transit_t']) for r in rs))
    return out


def paired_gain(ref, new, key='pet_shift'):
    """1 - ref/new per replication: fall in cost per plan-equivalent tonne for the same fleet cost."""
    g = np.array([1 - a[key] / b[key] if b[key] > 0 else float('nan') for a, b in zip(ref, new)], float)
    g = g[~np.isnan(g)]
    n = len(g)
    if n == 0: return dict(mean=None, sd=None, ci95=None)
    sd = float(g.std(ddof=1)) if n > 1 else 0.0
    return dict(mean=float(g.mean()), sd=sd, ci95=(_t975(n - 1) * sd / math.sqrt(n)) if n > 1 else None)


# ---------------------------------------------------------------- operations
def plan_for(cfg, iters=None, cal_reps=None):
    """Closed loop: LP -> simulate -> queue times become delay terms -> LP again."""
    cal = cfg.get('calibration', {})
    iters = cal.get('iters', 1) if iters is None else iters
    cal_reps = cal.get('reps', 3) if cal_reps is None else cal_reps
    avail, fit = _avail(cfg), _fit(cfg)
    ef, ed = {}, {}
    plan = solve_plan(cfg, avail, ef, ed)
    for _ in range(iters):
        c2 = dict(cfg, dispatch='plan')
        rs = [_rep(c2, r, plan['w'], fixed_assignment(cfg), fit)[0] for r in range(cal_reps)]
        ef = {i: float(np.mean([r['queue_loader_by_front'][i] for r in rs])) for i in rs[0]['queue_loader_by_front']}
        ed = {j: float(np.mean([r['queue_dump_by_dest'][j] for r in rs])) for j in ('CR', 'WD')}
        plan = solve_plan(cfg, avail, ef, ed)
    plan['e_front'], plan['e_dest'] = ef, ed
    return plan


def _plan_json(cfg, plan):
    L, E, legs, paths = travel_matrices(cfg)
    S = {c: d['payload'] for c, d in cfg['fleets'].items()}
    trips = [dict(front=i, destination=j, fleet=c, trips_per_shift=round(y, 3), tonnes_per_shift=round(y * S[c], 1))
             for (i, j, c), y in plan['y'].items() if y > 1e-6]
    return dict(status=plan['status'], plan_scale=plan['plan_scale'], total_tonnes_per_shift=round(plan['total'], 1),
                trips=trips, tonnes_by_front={k: round(v, 1) for k, v in plan['tonnes_front'].items()},
                dispatch_weights=plan['w'], tight_constraints=plan['tight'],
                delay_minutes=dict(front=plan.get('e_front', {}), destination=plan.get('e_dest', {})),
                travel_minutes=dict(loaded={f'{a}>{b}': round(v, 2) for (a, b), v in L.items()},
                                    empty={f'{a}>{b}': round(v, 2) for (a, b), v in E.items()}))


def op_solve(req):
    cfg = _cfg(req)
    plan = plan_for(cfg, iters=req.get('calibration_iters'))
    return dict(plan=_plan_json(cfg, plan))


def _run_set(cfg, n_reps, seed_shift=0, record_first=False):
    fit = _fit(cfg)
    plan = plan_for(cfg) if cfg['dispatch'] == 'plan' else None
    if cfg.get('fixed_rule') == 'plan' and cfg['dispatch'] == 'fixed':
        plan0 = plan_for(cfg)
        fixed = fixed_from_plan(cfg, plan0, _avail(cfg), plan0['e_front'], plan0['e_dest'])
    else:
        fixed = fixed_assignment(cfg)
    rs, trace = [], None
    for r in range(n_reps):
        res, tr = _rep(cfg, r + seed_shift, plan['w'] if plan else None, fixed, fit, record=(record_first and r == 0))
        rs.append(res)
        if tr is not None:
            trace = tr
    return rs, plan, fixed, trace


def op_simulate(req):
    cfg = _cfg(req)
    n = int(req.get('n_reps', 5))
    rs, plan, fixed, trace = _run_set(cfg, n, record_first=bool(req.get('trace')))
    out = dict(summary=summarise(rs), fixed_assignment=fixed, plan=_plan_json(cfg, plan) if plan else None,
               replications=[{k: r[k] for k in ('tonnes_shift', 'pet_shift', 'cost_per_t')} for r in rs])
    if trace is not None:
        out['trace'] = trace
        out['trace_meta'] = dict(trucks=[T for T in sorted({t[1] for t in trace})], horizon_h=cfg['horizon_h'])
    return out


def op_compare(req):
    cfg = _cfg(req)
    n = int(req.get('n_reps', 5))
    c0, c2 = dict(cfg, dispatch='fixed'), dict(cfg, dispatch='plan')
    r0, _, fx, _ = _run_set(c0, n)
    r2, plan, _, trace = _run_set(c2, n, record_first=bool(req.get('trace')))
    out = dict(S0=summarise(r0), S2=summarise(r2), gain_S2_vs_S0=paired_gain(r0, r2), fixed_assignment=fx,
               plan=_plan_json(cfg, plan),
               note='gain = fall in cost per plan-equivalent tonne at equal fleet cost; notional data, a method not a forecast')
    if trace is not None:
        out['trace'] = trace
    return out


def op_fleet_curve(req):
    cfg = _cfg(req)
    n = int(req.get('n_reps', 3))
    pts = []
    for nA in req.get('sizes_A', [8, 10, 12, 14, 16]):
        c = merge(cfg, {'fleets': {'A': {'n': nA}}})
        r0 = _run_set(dict(c, dispatch='fixed'), n)[0]
        r2 = _run_set(dict(c, dispatch='plan'), n)[0]
        pts.append(dict(trucks_A=nA, trucks_total=nA + c['fleets']['B']['n'],
                        S0=summarise(r0)['tonnes_shift'], S2=summarise(r2)['tonnes_shift'],
                        S0_cost_per_t=summarise(r0)['cost_per_t'], S2_cost_per_t=summarise(r2)['cost_per_t']))
    return dict(curve=pts)


def op_fit_failures(req):
    """records: [{'fleet': 'A', 'hours': 41.2, 'failed': 1}, ...]; hours = running hours of that spell, failed 0 = still running."""
    if not req.get('records'): raise ValueError('records is empty')
    by = {}
    for rec in req['records']:
        if rec['hours'] <= 0: raise ValueError('hours must be above 0 in every record')
        by.setdefault(rec['fleet'], []).append(rec)
    out = {}
    for fleet, rows in by.items():
        t = np.array([r['hours'] for r in rows], float)
        e = np.array([r['failed'] for r in rows], float)
        if e.sum() < 3: raise ValueError(f'fleet {fleet}: {int(e.sum())} failures is too few for a Weibull fit (need at least 3)')
        b, eta = fit_weibull(t, e)
        mttr = req.get('mttr_h', {}).get(fleet)
        out[fleet] = dict(beta=b, eta=eta, mtbf_h=float(mtbf_from(b, eta)), n_failures=int(e.sum()),
                          availability=float(availability(mtbf_from(b, eta), mttr)) if mttr else None)
    return dict(fits=out)


OPS = dict(solve=op_solve, simulate=op_simulate, compare=op_compare, fleet_curve=op_fleet_curve, fit_failures=op_fit_failures)


def _clean(o):
    """JSON has no NaN or Infinity: send null instead."""
    if isinstance(o, dict): return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list): return [_clean(v) for v in o]
    if isinstance(o, float) and not math.isfinite(o): return None
    return o


def run(request):
    """request: dict or JSON string. Returns a JSON-safe dict (always has 'ok')."""
    try:
        req = json.loads(request) if isinstance(request, str) else request
        out = OPS[req['op']](req)
        out.update(ok=True, schema=SCHEMA_VERSION, op=req['op'], seed=(req.get('config') or {}).get('seed', BASE_SEED))
        return _clean(json.loads(json.dumps(out, default=float)))
    except KeyError as ex:
        k = ex.args[0] if ex.args else ''
        if 'op' in locals().get('req', {}) and req['op'] not in OPS:
            msg = f"unknown op {req['op']!r}; choose from {sorted(OPS)}"
        elif not isinstance(locals().get('req'), dict) or 'op' not in req:
            msg = "the request needs an 'op' field"
        else:
            msg = f'unknown name or no road path to {k!r}: check front names, the road graph and required fields'
        return dict(ok=False, error=msg, schema=SCHEMA_VERSION)
    except ZeroDivisionError:
        return dict(ok=False, error='the run produced no plan progress (zero ore or zero waste); check fleet size, strip_ratio and plan_ore_t', schema=SCHEMA_VERSION)
    except Exception as ex:                 # a plugin should get an error object, not a crash
        return dict(ok=False, error=f'{type(ex).__name__}: {ex}', schema=SCHEMA_VERSION)


def run_json(request_json):
    return json.dumps(run(request_json), default=float)


if __name__ == '__main__':
    src = sys.stdin.read() if len(sys.argv) < 2 or sys.argv[1] == '-' else open(sys.argv[1]).read()
    print(json.dumps(run(src), indent=1, default=float))
