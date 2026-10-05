"""Prove step: discrete-event simulation (SimPy) of trucks, loaders, crusher and ROM pad on the notional pit.
Trucks cycle: empty travel -> queue at loader -> spot and load -> loaded travel to junction J2 -> choose crusher or
ROM pad -> travel -> queue at dump -> dump -> next cycle. Random failures (Weibull), shift changeovers and breaks,
optional crusher outage and predictive maintenance. One call = one replication."""
import math
import numpy as np
import simpy
from roads import travel_matrices, build_graph
from params import BASE_SEED


class Truck:
    __slots__ = ('id', 'fleet', 'rng', 'fc', 'pos', 'front', 'load', 'down', 'acc', 'n_planned', 'n_unplanned', 'fix_front')


class Twin:
    def __init__(self, P, rep, plan_w=None, fixed=None, fit=None, failures=True, randomness=True, record=False,
                 seed=BASE_SEED):
        self.P, self.rep, self.fail, self.rand, self.record = P, rep, failures, randomness, record
        self.env = simpy.Environment()
        self.L, self.E, self.legs, self.paths = travel_matrices(P)
        self.trace = []          # animation trace, only filled when record=True
        self._gl, self._ge = build_graph(P, True), build_graph(P, False)
        self.warm, self.H = P['warmup_h'], P['horizon_h']
        self.plan_w = plan_w
        self.fit = fit or {c: (d['beta'], d['mtbf_h'] / math.gamma(1 + 1 / d['beta'])) for c, d in P['fleets'].items()}
        fronts = P['ore_fronts'] + P['waste_fronts']
        self.fronts = fronts
        self.loader = {i: simpy.Resource(self.env, capacity=P['n_loaders'][i]) for i in fronts}
        self.dump = {'CR': simpy.Resource(self.env, capacity=P['n_dump_cr']),
                     'RP': simpy.Resource(self.env, capacity=P['n_dump_rp']),
                     'WD': simpy.Resource(self.env, capacity=P['n_dump_wd'])}
        self.hopper = simpy.Container(self.env, capacity=P['hopper_t'], init=0.0)
        self.pad = simpy.Container(self.env, capacity=max(P['pad_cap_t'], 1.0), init=0.0)
        self.use_pad = P['pad_cap_t'] > 0
        # statistics
        self.dumps = []          # (t, tonnes, dest)
        self.loadq = []          # (t, front, wait_min, fleet)
        self.dumpq = []          # (t, dest, wait_min)
        self.cycles = []         # (t, fleet, minutes)
        self.loader_busy = {i: 0.0 for i in fronts}
        self.crushed = 0.0
        self.crushed_log = []    # (t, cumulative)
        self.loaded_t = 0.0
        self.dumped_t = 0.0
        self.log = []            # (truck, state, start, end)
        self.max_cr_queue = 0
        # trucks
        self.trucks = []
        fx = fixed or {}
        k = 0
        for c, d in P['fleets'].items():
            slots = []
            for i, n in (fx.get(c) or {}).items():
                slots += [i] * n
            for m in range(d['n']):
                T = Truck()
                T.id, T.fleet = f'{c}{m + 1}', c
                T.rng = np.random.default_rng(np.random.SeedSequence([seed, rep, (100 if c == 'A' else 200) + m]))
                T.front = slots[m] if m < len(slots) else fronts[m % len(fronts)]
                T.fix_front = T.front
                T.pos, T.load, T.down = 'J2', 0.0, 0.0
                T.n_planned = T.n_unplanned = 0
                T.acc = dict(travel=0.0, load=0.0, dump=0.0, qload=0.0, qdump=0.0, repair=0.0)
                T.fc = self.init_fc(T) if failures else 1e18
                self.trucks.append(T)
        for T in self.trucks:
            self.env.process(self.truck(T))
        self.env.process(self.crusher())
        self.env.process(self.rehandle())

    # ---------- helpers ----------
    def draw_fc(self, T):
        b, eta = self.fit[T.fleet]
        return eta * T.rng.weibull(b)

    def init_fc(self, T, span=600.0):
        """Residual life at time zero: run a renewal process for `span` running hours and keep the leftover, so the
        fleet starts in steady state and not 'as new' (which would hide early failures)."""
        t = 0.0
        for _ in range(100000):
            x = self.draw_fc(T)
            if t + x > span:
                return t + x - span
            t += x
        raise ValueError('failure interval is effectively zero; check mtbf_h')

    def svc(self, T, mean_min, cv):
        if not self.rand:
            return mean_min / 60.0
        k = 1.0 / cv ** 2
        return T.rng.gamma(k, mean_min / k) / 60.0

    def tt(self, T, minutes):
        if not self.rand:
            return minutes / 60.0
        s = self.P['sigma_travel']
        return minutes / 60.0 * math.exp(T.rng.normal(-s * s / 2, s))

    def route(self, kind, a, b):
        """Node list and cumulative time fractions along it (for the animation)."""
        if kind == 'empty':
            nodes, g = self.paths[('empty', a, b)], self._ge
        elif kind == 'up':                  # loaded, front -> J2
            nodes, g = self.paths[('loaded', a, 'J2')], self._gl
        else:                               # loaded, J2 -> destination
            nodes, g = ['J2', b], self._gl
        ts = []
        for u, v in zip(nodes[:-1], nodes[1:]):
            ts.append(min(w for n, w, _ in g[u] if n == v))
        tot = sum(ts) or 1.0
        cum, c = [0.0], 0.0
        for x in ts:
            c += x
            cum.append(round(c / tot, 4))
        return nodes, cum

    def ev(self, *row):
        if self.record:
            self.trace.append(row)

    def add(self, T, state, a, b):
        lo, hi = max(a, self.warm), min(b, self.H)
        if hi > lo:
            T.acc[state] += hi - lo
        if self.record:
            self.log.append((T.id, state, a, b))

    def gate_wait(self, t):
        P = self.P
        m = t % P['shift_h']
        if m < P['changeover_h'] - 1e-12:
            return P['changeover_h'] - m
        if P['break_at_h'] - 1e-12 <= m < P['break_at_h'] + P['break_h'] - 1e-12:
            return P['break_at_h'] + P['break_h'] - m
        return 0.0

    def crusher_up(self, t):
        o = self.P['crusher_outage']
        return not (o and o[0] <= t < o[1])

    # ---------- truck process ----------
    def repair(self, T):
        P = self.P
        d = P['fleets'][T.fleet]
        predicted = T.rng.random() < P['predictive_phi']
        mean = d['mttr_h'] * (P['planned_factor'] if predicted else 1.0)
        if self.rand:
            sig = math.sqrt(math.log(1 + P['cv_repair'] ** 2))
            dur = T.rng.lognormal(math.log(mean) - sig * sig / 2, sig)
        else:
            dur = mean
        t0 = self.env.now
        yield self.env.timeout(dur)
        T.down += dur
        self.add(T, 'repair', t0, self.env.now)
        self.ev('r', T.id, round(t0, 4), round(self.env.now, 4), 1 if predicted else 0)
        if predicted:
            T.n_planned += 1
        else:
            T.n_unplanned += 1

    def run(self, T, hours, rt=None, loaded=False):
        """Travel: operating clock runs, failure can strike. Repair time is not counted as travel.
        rt = (nodes, cumulative fractions) for the animation trace."""
        remaining = hours
        seg = self.env.now
        done = 0.0

        def log(t0, t1, f0, f1):
            if rt:
                self.ev('m', T.id, round(t0, 4), round(t1, 4), rt[0], rt[1], round(f0, 4), round(f1, 4), 1 if loaded else 0)

        while remaining > 1e-12:
            if self.fail and T.fc <= remaining:
                dt = max(T.fc, 0.0)
                yield self.env.timeout(dt)
                remaining -= dt
                f0, f1 = done / hours, (done + dt) / hours
                done += dt
                self.add(T, 'travel', seg, self.env.now)
                log(seg, self.env.now, f0, f1)
                yield from self.repair(T)
                seg = self.env.now
                T.fc = self.draw_fc(T)
            else:
                yield self.env.timeout(remaining)
                T.fc -= remaining
                f0, f1 = done / hours, 1.0
                done += remaining
                remaining = 0.0
                log(seg, self.env.now, f0, f1)
        self.add(T, 'travel', seg, self.env.now)

    def decide_dest(self, T, planned):
        if planned != 'CR' or not self.use_pad:
            return planned
        pend = len(self.dump['CR'].users) + len(self.dump['CR'].queue)
        pay = self.P['fleets'][T.fleet]['payload']
        blocked = (not self.crusher_up(self.env.now)) or (self.hopper.level + pay * (pend + 1) > self.hopper.capacity)
        if blocked and self.pad.level + pay <= self.P['pad_cap_t']:
            return 'RP'
        return 'CR'

    def choose_front(self, T):
        P = self.P
        if P['dispatch'] == 'plan' and self.plan_w and T.rng.random() < P['compliance']:
            w = self.plan_w[T.fleet]
            names = list(w.keys())
            p = np.array([w[n] for n in names])
            return names[int(T.rng.choice(len(names), p=p / p.sum()))]
        return T.fix_front

    def truck(self, T):
        P, env = self.P, self.env
        d = P['fleets'][T.fleet]
        pay = d['payload']
        yield env.timeout(T.rng.uniform(0, 0.1) if self.rand else 0.0)
        while True:
            gw = self.gate_wait(env.now)
            if gw > 0:
                self.ev('i', T.id, round(env.now, 4), round(env.now + gw, 4), T.pos)
                yield env.timeout(gw)
            i = self.choose_front(T)
            if i == 'IDLE':                      # plan has no work for this truck: stand by
                self.ev('i', T.id, round(env.now, 4), round(env.now + 0.5, 4), T.pos)
                yield env.timeout(0.5)
                continue
            planned = 'CR' if i in P['ore_fronts'] else 'WD'
            t_start, down0 = env.now, T.down
            yield from self.run(T, self.tt(T, self.E[(T.pos, i)]), self.route('empty', T.pos, i) if self.record else None, False)
            q0 = env.now
            with self.loader[i].request() as rq:
                yield rq
                w = env.now - q0
                self.add(T, 'qload', q0, env.now)
                self.ev('q', T.id, round(q0, 4), round(env.now, 4), i)
                self.loadq.append((env.now, i, w * 60, T.fleet))
                dur = self.svc(T, d['spot_min'] + d['load_min'], P['cv_service'])
                t1 = env.now
                yield env.timeout(dur)
                T.fc -= dur
                self.add(T, 'load', t1, env.now)
                self.ev('l', T.id, round(t1, 4), round(env.now, 4), i, pay)
                lo, hi = max(t1, self.warm), min(env.now, self.H)
                if hi > lo:
                    self.loader_busy[i] += hi - lo
            T.load = pay
            self.loaded_t += pay
            yield from self.run(T, self.tt(T, self.L[(i, 'J2')]), self.route('up', i, 'J2') if self.record else None, True)
            dest = self.decide_dest(T, planned)
            yield from self.run(T, self.tt(T, self.legs[('L', 'J2', dest)]), self.route('dest', 'J2', dest) if self.record else None, True)
            q1 = env.now
            with self.dump[dest].request() as rq:
                yield rq
                self.max_cr_queue = max(self.max_cr_queue, len(self.dump['CR'].queue)) if dest == 'CR' else self.max_cr_queue
                self.add(T, 'qdump', q1, env.now)
                self.ev('q', T.id, round(q1, 4), round(env.now, 4), dest)
                self.dumpq.append((env.now, dest, (env.now - q1) * 60))
                dur = self.svc(T, d['dump_min'], P['cv_service'])
                t2 = env.now
                yield env.timeout(dur)
                T.fc -= dur
                if dest == 'CR':
                    yield self.hopper.put(pay)
                elif dest == 'RP':
                    yield self.pad.put(pay)
                self.add(T, 'dump', t2, env.now)
                self.ev('d', T.id, round(t2, 4), round(env.now, 4), dest, pay)
            T.load = 0.0
            self.dumped_t += pay
            self.dumps.append((env.now, pay, dest))
            T.pos = dest
            cyc = (env.now - t_start - (T.down - down0)) * 60
            self.cycles.append((env.now, T.fleet, cyc))

    # ---------- crusher and ROM pad ----------
    def crusher(self):
        P, env = self.P, self.env
        dt = 1.0 / 60.0
        while True:
            yield env.timeout(dt)
            if self.crusher_up(env.now):
                amt = min(P['crusher_tph'] * dt, self.hopper.level)
                if amt > 1e-9:
                    yield self.hopper.get(amt)
                    self.crushed += amt
            self.crushed_log.append((env.now, self.crushed))

    def rehandle(self):
        P, env = self.P, self.env
        dt = 1.0 / 60.0
        while True:
            yield env.timeout(dt)
            if self.use_pad and self.pad.level > 1e-9 and self.hopper.level < P['rehandle_below'] * self.hopper.capacity:
                amt = min(P['rehandle_tph'] * dt, self.pad.level, self.hopper.capacity - self.hopper.level)
                if amt > 1e-9:
                    yield self.pad.get(amt)
                    yield self.hopper.put(amt)

    # ---------- run and summarise ----------
    def run_sim(self):
        self.env.run(until=self.H)
        return self.summary()

    def summary(self):
        P = self.P
        W = self.H - self.warm
        win = lambda rows: [r for r in rows if r[0] >= self.warm]
        dumps = win(self.dumps)
        tot = sum(r[1] for r in dumps)
        ore = sum(r[1] for r in dumps if r[2] in ('CR', 'RP'))
        waste = tot - ore
        c0 = next((c for t, c in self.crushed_log if t >= self.warm), 0.0)
        crushed = self.crushed - c0
        sh = P['shift_h']
        out = dict(tonnes_shift=tot / W * sh, ore_shift=ore / W * sh, waste_shift=waste / W * sh,
                   crushed_shift=crushed / W * sh, strip_ratio=(waste / ore if ore else float('nan')))
        fl = {}
        for c in P['fleets']:
            cy = [r[2] for r in win(self.cycles) if r[1] == c]
            fl[c] = float(np.mean(cy)) if cy else float('nan')
        out['cycle_A'], out['cycle_B'] = fl['A'], fl['B']
        lq = win(self.loadq)
        out['queue_loader_min'] = float(np.mean([r[2] for r in lq])) if lq else float('nan')
        out['queue_loader_by_front'] = {i: float(np.mean([r[2] for r in lq if r[1] == i])) if any(r[1] == i for r in lq) else 0.0
                                         for i in self.fronts}
        dq = win(self.dumpq)
        out['queue_crusher_min'] = float(np.mean([r[2] for r in dq if r[1] == 'CR'])) if any(r[1] == 'CR' for r in dq) else 0.0
        out['queue_dump_by_dest'] = {j: float(np.mean([r[2] for r in dq if r[1] == j])) if any(r[1] == j for r in dq) else 0.0
                                      for j in ('CR', 'RP', 'WD')}
        prod = sum(T.acc['travel'] + T.acc['load'] + T.acc['dump'] for T in self.trucks)
        down = sum(T.acc['repair'] for T in self.trucks)
        out['truck_util'] = prod / (W * len(self.trucks))
        out['truck_downtime_share'] = down / (W * len(self.trucks))
        out['loader_util'] = sum(self.loader_busy.values()) / (W * sum(P['n_loaders'].values()))
        # match factor: offered load per loader = sum_c n_c A_c t_load_c / t_cycle_c over loaders
        mf = 0.0
        for c, d in P['fleets'].items():
            A = d['mtbf_h'] / (d['mtbf_h'] + d['mttr_h'])
            # cycle time without queue: subtract mean queues
            cyc_nq = (fl[c] - (out['queue_loader_min'] + out['queue_crusher_min'])) if fl[c] == fl[c] else float('nan')
            mf += d['n'] * A * (d['spot_min'] + d['load_min']) / cyc_nq
        out['match_factor'] = mf / sum(P['n_loaders'].values())
        cost_h = sum(d['n'] * d['cost_h'] for d in P['fleets'].values())
        out['cost_per_t'] = cost_h * W / tot if tot else float('nan')
        out['unplanned'] = sum(T.n_unplanned for T in self.trucks)
        out['planned'] = sum(T.n_planned for T in self.trucks)
        out['hopper_blocked_q'] = self.max_cr_queue
        out['loaded_t'] = self.loaded_t
        out['dumped_t'] = self.dumped_t
        out['in_transit_t'] = sum(T.load for T in self.trucks)
        out['pad_end_t'] = self.pad.level
        return out


def one(args):
    """Picklable single replication: args = (P, rep, plan_w, fixed, fit, kwargs)"""
    P, rep, plan_w, fixed, fit, kw = args
    return Twin(P, rep, plan_w=plan_w, fixed=fixed, fit=fit, **kw).run_sim()
