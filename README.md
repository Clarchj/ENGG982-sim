# Lukaut Haul Twin (standalone showcase)

A small simulator that shows the Predict, Prescribe, Prove loop on notional pit data. It is a feasibility showcase, not an industrial product, and its results show a method, not a forecast for any site.

## What is in the folder

| Path | Purpose |
|---|---|
| `engine/` | The core. Python, no screen, no files. One JSON request in, one JSON response out (`twin_api.py`). |
| `web/` | Single-page showcase. Runs the same engine in the browser (Pyodide), with an animated haul map and live statistics. |
| `adapters/` | CSV and command-line route for tools that exchange files. |
| `serve.py` | Local web service: `POST /api` with the same JSON. Binds to 127.0.0.1 only. |
| `tools/build_web.py` | Copies the engine into `web/py` so the page runs the same code as the report. |
| `tools/make_config.py` | Writes `web/config.js` from `.env` (project URL and public key only). |
| `supabase.sql` | One-off table for the optional shared run log. |
| `tests/test_adapters.py` | Checks the CSV route and the service. |

## Run it

1. Page, no install: `python tools/build_web.py` then `python -m http.server --directory web 8000`, open http://localhost:8000. The page loads Pyodide from a public CDN. For offline use, run `python tools/build_web.py --pyodide` once.
2. Command line: `python engine/twin_api.py request.json` (see the example call on the page).
3. Files: `python adapters/cli.py solve --schedule adapters/examples/schedule.csv --fleet adapters/examples/fleet.csv --roads adapters/examples/roads.csv --out dispatch.csv`
4. Service: `python serve.py 8080`, then POST JSON to `/api`.

Needs Python 3.10 or later with numpy, scipy and simpy for routes 2 to 4. The page itself needs only a modern browser.

## The request

```json
{"op": "compare", "n_reps": 10, "config": {"fleets": {"A": {"n": 12}}, "plan_ore_t": 12000}}
```

Operations: `solve` (optimiser plan), `simulate` (replications of one scenario, optional animation trace), `compare` (fixed assignment against optimiser advice, paired), `fleet_curve`, `fit_failures` (Weibull fit to maintenance records). Every response has `ok`; on failure it carries an `error` string instead of crashing the caller.

## Plugging into industrial tools

The engine only exchanges data, so each connection is a thin adapter.

| Tool type | Route | Status |
|---|---|---|
| Mine planning and scheduling (Deswik and similar) | Export planned tonnes by front as CSV; read `dispatch.csv` and `range.csv` back. A script inside the tool can also call `POST /api`. | CSV route tested here. Not tested against Deswik. The 2012 Deswik scripting manual describes .NET access and CSV file reading and writing, so this looks feasible but needs confirming with the vendor. |
| Fleet management and dispatch | Fleet list, payloads, cycle times and breakdown records as CSV or JSON in; advice out. | Same file route. Not tested against a real system. |
| Road and path optimisation | Road graph as `roads.csv`; travel-time matrix in the `solve` response. | Own Dijkstra on the sample graph only. |

Design limits to keep: advice only, every output goes to a named person, and the engine has no write path to equipment or to any control system.

## Optional shared run log (Supabase)

1. Run `supabase.sql` once in the Supabase SQL editor.
2. Put the project URL and the public anon key in `.env` (`SUPABASE_URL`, `SUPABASE_ANON_KEY`), then run `python tools/make_config.py`.
3. The page then shows Save and Show buttons. Without `config.js` the page works the same, minus those buttons.

Never put a service-role or secret key in `.env` for this page; `make_config.py` refuses one. Anyone holding the public key can add rows, so treat the table as a notice board.

## Known limits

Notional data. Not validated against a real mine. Gains come from holding the planned ore-to-waste mix, not from moving more tonnes. The optimiser plan stops at the front rate limits when the fleet grows past about 24 trucks, where fixed assignment moves more tonnes at higher cost per tonne. Queue-time delays in the plan are calibrated from the simulation, which is this team's design choice, not a published method.
