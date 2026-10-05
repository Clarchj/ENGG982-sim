"""Predict step: failure-risk model. Weibull survival fit (maximum likelihood, right-censored) to
SYNTHETIC maintenance history. It feeds the breakdown parameter of the simulator and does not
forecast real failures. Availability = MTBF / (MTBF + MTTR)."""
import math
import numpy as np
from scipy.optimize import minimize
from scipy.special import gamma as G


def mtbf_from(beta, eta):
    return eta * G(1.0 + 1.0 / beta)


def eta_from_mtbf(beta, mtbf):
    return mtbf / G(1.0 + 1.0 / beta)


def synth_history(rng, n_trucks, hours_per_truck, beta, mtbf, mttr, cv_repair=0.6):
    """Operating-hours clock per truck: failures drawn Weibull, repairs lognormal, last spell censored."""
    eta = eta_from_mtbf(beta, mtbf)
    t_obs, event = [], []
    sig = math.sqrt(math.log(1 + cv_repair ** 2))
    mu = math.log(mttr) - sig ** 2 / 2
    for _ in range(n_trucks):
        clock = 0.0
        while True:
            x = eta * rng.weibull(beta)
            if clock + x >= hours_per_truck:
                t_obs.append(hours_per_truck - clock); event.append(0); break
            t_obs.append(x); event.append(1)
            clock += x + rng.lognormal(mu, sig)       # repair time passes in calendar, not in running hours
            clock -= 0.0
    return np.array(t_obs), np.array(event), mttr


def fit_weibull(t, e):
    """MLE of (beta, eta) with right censoring."""
    t = np.maximum(t, 1e-9)
    def nll(p):
        b, h = math.exp(p[0]), math.exp(p[1])
        z = (t / h) ** b
        ll = e * (math.log(b) - math.log(h) + (b - 1) * (np.log(t) - math.log(h))) - z
        return -ll.sum()
    r = minimize(nll, x0=[0.0, math.log(t.mean())], method='Nelder-Mead',
                 options=dict(xatol=1e-8, fatol=1e-10, maxiter=4000))
    return math.exp(r.x[0]), math.exp(r.x[1])


def fit_with_ci(t, e, rng, n_boot=300):
    b, h = fit_weibull(t, e)
    bs, ms = [], []
    n = len(t)
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        bb, hh = fit_weibull(t[idx], e[idx])
        bs.append(bb); ms.append(mtbf_from(bb, hh))
    return dict(beta=b, eta=h, mtbf=float(mtbf_from(b, h)),
                beta_ci=(float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))),
                mtbf_ci=(float(np.percentile(ms, 2.5)), float(np.percentile(ms, 97.5))),
                n_failures=int(e.sum()), n_spells=int(n))


def availability(mtbf, mttr):
    return mtbf / (mtbf + mttr)


def run_predict(P, base_seed, months=24, hours_per_month=667.0):
    """Fit each fleet; return fitted parameters. Hours of history per truck = months x 667 h (8,000 h a year)."""
    out = {}
    for k, (f, d) in enumerate(P['fleets'].items()):
        rng = np.random.default_rng(np.random.SeedSequence([base_seed, 9000 + k]))
        t, e, mttr = synth_history(rng, d['n'], months * hours_per_month, d['beta'], d['mtbf_h'], d['mttr_h'], P['cv_repair'])
        fit = fit_with_ci(t, e, np.random.default_rng(np.random.SeedSequence([base_seed, 9500 + k])))
        fit['true_beta'] = d['beta']; fit['true_mtbf'] = d['mtbf_h']; fit['mttr'] = d['mttr_h']
        fit['availability'] = float(availability(fit['mtbf'], d['mttr_h']))
        out[f] = fit
    return out


if __name__ == '__main__':
    from params import P, BASE_SEED
    r = run_predict(P, BASE_SEED)
    for f, v in r.items():
        print(f, {k: (round(x, 3) if isinstance(x, float) else x) for k, x in v.items()})
