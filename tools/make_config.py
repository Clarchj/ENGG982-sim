"""Writes web/config.js from the .env file in this folder. Only the project URL and the PUBLIC anon key are copied.
A service-role key (or any other secret) is never written to the page. Run from sim-standalone:  python tools/make_config.py"""
import json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
env = {}
p = os.path.join(ROOT, '.env')
if os.path.exists(p):
    for line in open(p, encoding='utf-8-sig').read().replace('\r', '').split('\n'):
        m = re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$', line)
        if m and not line.lstrip().startswith('#'):
            env[m.group(1)] = m.group(2).strip('"\'')
url = next((env[k] for k in ('SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_URL', 'VITE_SUPABASE_URL') if env.get(k)), '')
key = next((env[k] for k in ('SUPABASE_ANON_KEY', 'SUPABASE_PUBLISHABLE_KEY', 'NEXT_PUBLIC_SUPABASE_ANON_KEY', 'VITE_SUPABASE_ANON_KEY') if env.get(k)), '')
out = os.path.join(ROOT, 'web', 'config.js')
if not (url.startswith('https://') and key):
    open(out, 'w').write('// no database configured\n'); print('No usable URL and anon key in .env; wrote an empty config.js. The page works without it.'); sys.exit(0)
if key.count('.') == 2:        # JWT-style key: refuse a service-role key
    import base64, json
    try:
        pad = key.split('.')[1]; pad += '=' * (-len(pad) % 4)
        if json.loads(base64.urlsafe_b64decode(pad)).get('role') == 'service_role':
            print('Refusing: that key has the service_role role. Put the anon (public) key in .env.'); sys.exit(1)
    except Exception:
        pass
if key.startswith('sb_secret_'):
    print('Refusing: that is a secret key. Use the publishable (anon) key.'); sys.exit(1)
open(out, 'w').write('window.SUPABASE_URL=%s;\nwindow.SUPABASE_ANON_KEY=%s;\n' % (json.dumps(url), json.dumps(key)))
print('Wrote web/config.js with the project URL and the public key (values not shown).')
