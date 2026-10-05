"""Prescribe step: linear program (SciPy HiGHS) for multi-commodity haul flow with truck-time capacity.
Variables y[i,j,c] = trips per shift by fleet c from front i to destination j (j = CR for ore, WD for waste).
Tonnes x[i,j] = sum_c payload_c * y[i,j,c].
Maximise total tonnes moved subject to:
 (1) truck time per fleet: sum y*(t_loaded + t_empty + t_spot + t_load + t_dump + e_ij) <= n_c * T_eff * A_c
 (2) loader time per front: sum y*(t_spot + t_load) <= T_eff * loader_avail
 (3) dump position time per destination: sum y*t_dump <= T_eff * n_positions
 (4) crusher: ore tonnes <= crusher_rate * T_eff
 (5) front bounds: LB_i <= x_i <= UB_i (blend plan +- band)
 (6) strip ratio: waste tonnes = SR * ore tonnes
Delay e_ij (queue time) is calibrated from simulated queues: that is the team's design choice, not Genoa's published method."""
import numpy as np
from scipy.optimize import linprog
from roads import travel_matrices

IDLE_H = 0.5   # length of one stand-by decision, hours


def solve_plan(P, avail, e_front=None, e_dest=None, n_override=None):
    """Solve; if the plan cannot be met (wet season, small fleet) shrink the plan in 2% steps until feasible and
    report the scale used (a planner would do the same)."""
    scale = 1.0
    while True:
        try:
            r = _solve(P, avail, e_front, e_dest, n_override, scale)
            r['plan_scale'] = scale
            return r
        except RuntimeError:
            scale -= 0.02
            if scale < 0.5:
                raise


def _solve(P, avail, e_front, e_dest, n_override, scale):
    """avail: {'A': .., 'B': ..} availability from the Predict step. e_front/e_dest: queue minutes."""
    L, E, legs, _ = travel_matrices(P)
    fronts = P['ore_fronts'] + P['waste_fronts']
    Teff = P['shift_h'] - P['changeover_h'] - P['break_h']
    e_front = e_front or {}
    e_dest = e_dest or {}
    fl = list(P['fleets'].keys())
    var = [(i, 'CR' if i in P['ore_fronts'] else 'WD', c) for i in fronts for c in fl]
    nv = len(var)
    idx = {v: k for k, v in enumerate(var)}
    S = {c: P['fleets'][c]['payload'] for c in fl}
    n = {c: (n_override or {}).get(c, P['fleets'][c]['n']) for c in fl}
    # minutes per trip
    def trip_min(i, j, c):
        d = P['fleets'][c]
        return (L[(i, j)] + E[(j, i)] + d['spot_min'] + d['load_min'] + d['dump_min']
                + e_front.get(i, 0.0) + e_dest.get(j, 0.0))
    A_ub, b_ub, names = [], [], []
    for c in fl:                                   # (1)
        row = np.zeros(nv)
        for (i, j, cc), k in idx.items():
            if cc == c: row[k] = trip_min(i, j, c) / 60.0
        A_ub.append(row); b_ub.append(n[c] * Teff * avail[c]); names.append(f'truck_time_{c}')
    for i in fronts:                               # (2)
        row = np.zeros(nv)
        for (ii, j, c), k in idx.items():
            if ii == i: row[k] = (P['fleets'][c]['spot_min'] + P['fleets'][c]['load_min']) / 60.0
        A_ub.append(row); b_ub.append(Teff * P['loader_avail'] * P['n_loaders'][i]); names.append(f'loader_time_{i}')
    for j, npos in (('CR', P['n_dump_cr']), ('WD', P['n_dump_wd'])):   # (3)
        row = np.zeros(nv)
        for (i, jj, c), k in idx.items():
            if jj == j: row[k] = P['fleets'][c]['dump_min'] / 60.0
        A_ub.append(row); b_ub.append(Teff * npos); names.append(f'dump_time_{j}')
    row = np.zeros(nv)                             # (4)
    for (i, j, c), k in idx.items():
        if j == 'CR': row[k] = S[c]
    A_ub.append(row); b_ub.append(P['crusher_tph'] * Teff); names.append('crusher')
    for i in P['ore_fronts']:                      # (5)
        row = np.zeros(nv)
        for (ii, j, c), k in idx.items():
            if ii == i: row[k] = S[c]
        plan = P['plan_ore_t'] * scale * P['ore_share'][i]
        A_ub.append(row); b_ub.append(plan * (1 + P['front_band'])); names.append(f'front_ub_{i}')
        A_ub.append(-row); b_ub.append(-plan * (1 - P['front_band'])); names.append(f'front_lb_{i}')
    A_eq = np.zeros((1, nv))                       # (6)
    for (i, j, c), k in idx.items():
        A_eq[0, k] = S[c] if j == 'WD' else -P['strip_ratio'] * S[c]
    obj = -np.array([S[c] for (_, _, c) in var])
    r = linprog(obj, A_ub=np.array(A_ub), b_ub=np.array(b_ub), A_eq=A_eq, b_eq=[0.0],
                bounds=[(0, None)] * nv, method='highs')
    if r.status != 0:
        raise RuntimeError('LP failed: ' + r.message)
    y = {v: float(r.x[k]) for v, k in idx.items()}
    slack = dict(zip(names, (np.array(b_ub) - np.array(A_ub) @ r.x).tolist()))
    tonnes_front = {i: sum(S[c] * y[(i, ('CR' if i in P['ore_fronts'] else 'WD'), c)] for c in fl) for i in fronts}
    # dispatch weights per fleet: probability of choosing front i. Truck time the plan cannot use (slack) becomes
    # IDLE decisions of 0.5 h, so trucks the plan has no work for stand by instead of over-producing.
    w = {}
    for c in fl:
        tot = sum(y[(i, j, c)] for (i, j, cc) in var if cc == c)
        idle = max(slack[f'truck_time_{c}'], 0.0) / IDLE_H
        den = tot + idle
        w[c] = {i: (y[(i, j, c)] / den if den > 0 else 0.0) for (i, j, cc) in var if cc == c}
        w[c]['IDLE'] = idle / den if den > 0 else 0.0
    return dict(y=y, w=w, tonnes_front=tonnes_front, total=float(-r.fun), slack=slack,
                tight=[k for k, v in slack.items() if abs(v) < 1e-6], Teff=Teff, status=r.message)


def fixed_assignment(P, n_override=None):
    """S0 (current practice): each truck is tied to one front for the shift. Trucks of each fleet are spread over the
    fronts in proportion to the truck-hours the plan needs there (planned tonnes / payload x nominal cycle time),
    largest remainder rounding. This is a workload-based rule of thumb, not a straw man."""
    L, E, legs, _ = travel_matrices(P)
    fronts = P['ore_fronts'] + P['waste_fronts']
    plan = {i: P['plan_ore_t'] * P['ore_share'][i] for i in P['ore_fronts']}
    plan['F4'] = P['plan_ore_t'] * P['strip_ratio']
    out = {}
    for c, d in P['fleets'].items():
        n = (n_override or {}).get(c, d['n'])
        raw = {}
        for i in fronts:
            j = 'CR' if i in P['ore_fronts'] else 'WD'
            cyc = L[(i, j)] + E[(j, i)] + d['spot_min'] + d['load_min'] + d['dump_min']
            raw[i] = plan[i] / d['payload'] * cyc
        tot = sum(raw.values())
        raw = {i: n * v / tot for i, v in raw.items()}
        base = {i: int(np.floor(v)) for i, v in raw.items()}
        rem = n - sum(base.values())
        for i in sorted(fronts, key=lambda i: raw[i] - base[i], reverse=True)[:rem]:
            base[i] += 1
        out[c] = base
    return out


if __name__ == '__main__':
    from params import P
    avail = {'A': 60 / 66, 'B': 50 / 55}
    r = solve_plan(P, avail)
    print('total t/shift', round(r['total']), 'front tonnes', {k: round(v) for k, v in r['tonnes_front'].items()})
    print('tight', r['tight'])
    print('weights', {c: {i: round(x, 3) for i, x in w.items()} for c, w in r['w'].items()})
    print('fixed', fixed_assignment(P))


def fixed_from_plan(P, plan, avail, e_front=None, e_dest=None, n_override=None):
    """S0*: the best case for fixed assignment. Trucks are tied to fronts in proportion to the truck-hours the
    LP plan gives each front (whole trucks, largest remainder rounding)."""
    L, E, legs, _ = travel_matrices(P)
    out = {}
    for c, d in P['fleets'].items():
        n = (n_override or {}).get(c, d['n'])
        h = {}
        for (i, j, cc), y in plan['y'].items():
            if cc != c:
                continue
            m = (L[(i, j)] + E[(j, i)] + d['spot_min'] + d['load_min'] + d['dump_min']
                 + (e_front or {}).get(i, 0) + (e_dest or {}).get(j, 0))
            h[i] = y * m
        tot = sum(h.values())
        raw = {i: n * v / tot for i, v in h.items()}
        base = {i: int(np.floor(v)) for i, v in raw.items()}
        rem = n - sum(base.values())
        for i in sorted(raw, key=lambda i: raw[i] - base[i], reverse=True)[:rem]:
            base[i] += 1
        out[c] = base
    return out
