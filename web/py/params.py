"""Lukaut Haul-Plan twin: parameter sheet (notional pit).
Every value is tagged 'Assumption' (team estimate, owner to confirm) or 'Sourced'.
Units: hours, km, km/h, tonnes, AUD. Sized to assumption A-01 (haulage AUD 90 M a year).
Owners follow blueprint step 2: Pokharel (haul), Budhathoki (plan), Lamichhane (crusher),
Wang (reliability), Chakraborty (shift, speed limits).
"""
import copy

BASE_SEED = 20261004          # recorded for every run; replication r uses SeedSequence([BASE_SEED, r, stream])
N_REPS = 30                   # replications per scenario (blueprint)

P = dict(
    # ---- shift and horizon (Chakraborty) ----
    shift_h=12.0, changeover_h=1/3, break_h=0.5, break_at_h=6.0,
    horizon_h=168.0, warmup_h=12.0,
    op_hours_per_year=8000.0,
    # ---- fleets (Pokharel) ----
    fleets={
        'A': dict(n=12, payload=100.0, load_min=3.0, spot_min=0.7, dump_min=1.0, cost_h=665.0,
                  mtbf_h=60.0, mttr_h=6.0, beta=1.8),
        'B': dict(n=8,  payload=60.0,  load_min=1.8, spot_min=0.7, dump_min=0.8, cost_h=410.0,
                  mtbf_h=50.0, mttr_h=5.0, beta=1.6),
    },
    cv_service=0.25,          # gamma CV of loading and dumping times
    sigma_travel=0.08,        # lognormal sigma on each travel leg
    cv_repair=0.6,            # lognormal CV of repair time
    # ---- speeds by grade (Pokharel / Chakraborty limits), km/h; grade % positive = uphill in travel direction ----
    grade_pts=[-8, -4, 0, 4, 8, 10],
    v_loaded=[22, 30, 40, 24, 15, 12],
    v_empty=[30, 40, 45, 35, 28, 24],
    speed_factor=1.0,         # S3 sets 0.75
    # ---- haul-road graph: (a, b, km, grade % from a to b) ----
    edges=[('F1', 'J1', 0.6, 4), ('F2', 'J1', 1.1, 6), ('F3', 'J1', 1.8, 8), ('F4', 'J1', 0.8, 5),
           ('J1', 'J2', 2.2, 8),      # main ramp
           ('J1', 'J2', 3.4, 5),      # east ramp (longer, flatter)
           ('J2', 'CR', 1.2, 0), ('J2', 'RP', 1.0, 0), ('J2', 'WD', 2.6, 1)],
    ore_fronts=['F1', 'F2', 'F3'], waste_fronts=['F4'],
    # ---- plan per shift (Budhathoki) ----
    plan_ore_t=12000.0, strip_ratio=2.0,
    ore_share={'F1': 0.30, 'F2': 0.35, 'F3': 0.35},   # blend plan
    front_band=0.20,          # each ore front may deviate +-20% from its plan share (blend limits)
    loader_avail=1.0, n_loaders={'F1': 1, 'F2': 1, 'F3': 1, 'F4': 2}, n_dump_cr=1, n_dump_rp=2, n_dump_wd=2,
    # ---- crusher and ROM pad (Lamichhane) ----
    crusher_tph=1400.0, hopper_t=400.0, pad_cap_t=20000.0, rehandle_tph=900.0, rehandle_below=0.6,
    crusher_outage=None,      # (start_h, end_h) for S4
    # ---- reliability and predictive maintenance (Wang) ----
    predictive_phi=0.0,       # share of failures caught early (S5 sets 0.5)
    planned_factor=0.5,       # planned repair takes this share of the unplanned repair time
    # ---- dispatch ----
    dispatch='fixed',         # 'fixed' (S0) or 'plan' (S2)
    compliance=1.0,           # share of dispatch decisions that follow the plan (advisory use)
    # ---- unit costs (A-12) are per fleet above (AUD per truck-hour) ----
)

TAGS = [  # (ID, name, value text, unit, tag, owner)
 ('P01', 'Shift length / changeover / break', '12 h / 20 min / 30 min at hour 6', 'h', 'Assumption', 'Chakraborty'),
 ('P02', 'Fleet A: trucks, payload', '12 trucks, 100 t', '-', 'Assumption', 'Pokharel'),
 ('P03', 'Fleet B: trucks, payload', '8 trucks, 60 t', '-', 'Assumption', 'Pokharel'),
 ('P04', 'Load time (spot + load): A, B', '0.7+3.0 min, 0.7+1.8 min; gamma CV 0.25', 'min', 'Assumption', 'Pokharel'),
 ('P05', 'Dump time: A, B', '1.0 min, 0.8 min; gamma CV 0.25', 'min', 'Assumption', 'Pokharel'),
 ('P06', 'Haul-road graph', '4 fronts, 2 junctions, crusher, ROM pad, dump; haul 4.0 to 5.6 km one way; two ramps', 'km', 'Assumption', 'Pokharel'),
 ('P07', 'Speed by grade, loaded / empty', '-8%: 22/30, 0%: 40/45, +8%: 15/28 km/h', 'km/h', 'Assumption', 'Pokharel'),
 ('P08', 'Plan per shift', '12 kt ore, strip ratio 2.0, blend share 30/35/35%, front band +-20%; one loader per ore front, two at the waste front', 't', 'Assumption', 'Budhathoki'),
 ('P09', 'Crusher rate, hopper, ROM pad', '1,400 t/h, 400 t, 20 kt; rehandle 900 t/h', 't/h', 'Assumption', 'Lamichhane'),
 ('P10', 'Truck reliability: A, B', 'MTBF 60 h, 50 h; MTTR 6 h, 5 h; Weibull shape 1.8, 1.6 (true values behind the synthetic history)', 'h', 'Assumption', 'Wang'),
 ('P11', 'Predictive maintenance (S5)', '50% of failures caught early; planned repair takes 50% of the unplanned time', '-', 'Assumption', 'Wang'),
 ('P12', 'Unit cost per truck-hour: A, B', 'AUD 665, AUD 410 (fleet cost AUD 90.1 M a year at 8,000 h)', 'AUD/h', 'Assumption', 'Pokharel / Long'),
 ('P13', 'Annual operating hours', '8,000', 'h', 'Assumption', 'Budhathoki'),
 ('P14', 'Plan compliance (advisory use)', '100% base; 60% and 80% tested', '%', 'Assumption', 'Long'),
]

def make(**over):
    """Deep copy of P with overrides (dotted keys not needed; pass nested dict updates for fleets)."""
    q = copy.deepcopy(P)
    for k, v in over.items():
        if k == 'fleet_n':          # e.g. fleet_n={'A': 14}
            for f, n in v.items():
                q['fleets'][f]['n'] = n
        elif k == 'fleet_set':      # e.g. fleet_set={'A': {'load_min': 3.6}}
            for f, d in v.items():
                q['fleets'][f].update(d)
        else:
            q[k] = v
    return q
