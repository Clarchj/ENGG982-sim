"""Builds the web/ folder from the canonical engine, so the page always runs the same code as the report.
    python tools/build_web.py [--pyodide]      --pyodide: also fetch a local Pyodide runtime (about 25 MB) so the page works offline.
Copies engine/*.py -> web/py, vendors simpy (pure Python), writes py/manifest.json, copies results and default config to web/data."""
import json, os, shutil, sys, urllib.request, tarfile, io
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
WEB = os.path.join(ROOT, 'web'); PY = os.path.join(WEB, 'py'); DATA = os.path.join(WEB, 'data')
RESULTS = os.path.join(ROOT, '..', 'model_evidence', 'results', 'results.json')
PYODIDE = 'https://github.com/pyodide/pyodide/releases/download/0.27.7/pyodide-0.27.7.tar.bz2'

os.makedirs(PY, exist_ok=True); os.makedirs(DATA, exist_ok=True)   # files are overwritten in place
for f in os.listdir(os.path.join(ROOT, 'engine')):
    if f.endswith('.py'): shutil.copy(os.path.join(ROOT, 'engine', f), PY)
import simpy
shutil.copytree(os.path.dirname(simpy.__file__), os.path.join(PY, 'simpy'), dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'py.typed'))
files = sorted(os.path.relpath(os.path.join(r, n), PY).replace(os.sep, '/') for r, _, fs in os.walk(PY) for n in fs if n.endswith('.py'))
json.dump(files, open(os.path.join(PY, 'manifest.json'), 'w'))
shutil.copy(os.path.join(ROOT, 'default_config.json'), DATA)
if os.path.exists(RESULTS): shutil.copy(RESULTS, os.path.join(DATA, 'results.json'))
else: print('warning: results.json not found at', RESULTS)
print(f'web/py: {len(files)} python files; simpy {simpy.__version__}')
if '--pyodide' in sys.argv:
    print('downloading Pyodide runtime...'); b = urllib.request.urlopen(PYODIDE).read()
    tmp = os.path.join(WEB, '_p'); tarfile.open(fileobj=io.BytesIO(b), mode='r:bz2').extractall(tmp)
    shutil.rmtree(os.path.join(WEB, 'pyodide'), ignore_errors=True); shutil.move(os.path.join(tmp, 'pyodide'), os.path.join(WEB, 'pyodide')); shutil.rmtree(tmp)
    print('web/pyodide ready')
print('done. Serve with:  python -m http.server --directory web 8000   (or python serve.py)')
