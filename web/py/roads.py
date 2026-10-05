"""Shortest-path travel times on the haul-road graph (Dijkstra, own implementation).
Edge time = distance / speed(grade, loaded?) * 60 minutes. Grade is positive uphill in the
direction of travel, so the reverse direction uses -grade."""
import heapq
import numpy as np


def speed(P, grade, loaded):
    v = P['v_loaded'] if loaded else P['v_empty']
    return float(np.interp(grade, P['grade_pts'], v)) * P['speed_factor']


def build_graph(P, loaded):
    """Directed adjacency: node -> list of (node, minutes, label)."""
    g = {}
    for a, b, km, gr in P['edges']:
        for (u, v, grade) in ((a, b, gr), (b, a, -gr)):
            t = km / speed(P, grade, loaded) * 60.0
            g.setdefault(u, []).append((v, t, f'{a}-{b}:{km}km'))
            g.setdefault(v, [])
    return g


def dijkstra(g, src):
    dist = {src: 0.0}
    prev = {}
    pq = [(0.0, src)]
    done = set()
    while pq:
        d, u = heapq.heappop(pq)
        if u in done:
            continue
        done.add(u)
        for v, w, _ in g[u]:
            nd = d + w
            if nd < dist.get(v, float('inf')) - 1e-12:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    return dist, prev


def path(prev, src, dst):
    p = [dst]
    while p[-1] != src:
        p.append(prev[p[-1]])
    return p[::-1]


def travel_matrices(P):
    """Minutes. loaded[(front, node)] front -> node; empty[(node, front)] node -> front,
    for node in J2, CR, RP, WD."""
    gl, ge = build_graph(P, True), build_graph(P, False)
    fronts = P['ore_fronts'] + P['waste_fronts']
    nodes = ['J2', 'CR', 'RP', 'WD']
    L, E, paths = {}, {}, {}
    for f in fronts:
        d, pv = dijkstra(gl, f)
        for n in nodes:
            L[(f, n)] = d[n]
            paths[('loaded', f, n)] = path(pv, f, n)
    for n in nodes:
        d, pv = dijkstra(ge, n)
        for f in fronts:
            E[(n, f)] = d[f]
            paths[('empty', n, f)] = path(pv, n, f)
    # J2 -> dest legs (loaded) and dest -> J2 legs (empty) come from the same matrices
    dj, pj = dijkstra(gl, 'J2')
    de, pe = dijkstra(ge, 'J2')
    legs = {('L', 'J2', n): dj[n] for n in ('CR', 'RP', 'WD')}
    legs.update({('E', n, 'J2'): dijkstra(ge, n)[0]['J2'] for n in ('CR', 'RP', 'WD')})
    return L, E, legs, paths


if __name__ == '__main__':
    from params import P
    L, E, legs, paths = travel_matrices(P)
    for k, v in sorted(L.items()):
        print('loaded', k, round(v, 2), 'min', '->'.join(paths[('loaded',) + k]))
