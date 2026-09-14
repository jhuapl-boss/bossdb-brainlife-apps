#!/usr/bin/env python3
"""Register this app after its GitHub repository has been published."""
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


def main():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / 'app.json').read_text())
    token = os.environ.get('BRAINLIFE_TOKEN')
    if not token:
        token = (Path.home() / '.config/brainlife.io/.jwt').read_text().strip()
    base = 'https://brainlife.io/api/warehouse/app'
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    query = urllib.parse.urlencode({'find': json.dumps({'github': manifest['github']}), 'limit': 100})
    with urllib.request.urlopen(urllib.request.Request(base + '?' + query, headers=headers), timeout=30) as response:
        matches = json.load(response)['apps']
    if matches:
        print('Already registered: ' + ', '.join(app['_id'] for app in matches))
        return
    req = urllib.request.Request(base, data=json.dumps(manifest).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=60) as response:
        app = json.load(response)
    print('Registered: https://connects.brainlife.io/apps/' + app['_id'])


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as error:
        print(f'Registration failed (HTTP {error.code}). Check Brainlife access and publish the GitHub repo first.')
        raise SystemExit(1)
