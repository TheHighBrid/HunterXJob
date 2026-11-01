#!/usr/bin/env python3
"""Preview or apply direct user-authorized HunterXJob /api/answers seed records.

Preview is the default. This does not create or submit applications.
"""
import argparse, json, os, sys, urllib.request, urllib.error
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--file', required=True, type=Path)
    p.add_argument('--base-url', default='http://127.0.0.1:8011')
    p.add_argument('--apply', action='store_true', help='Actually overwrite/upsert vault answers')
    args=p.parse_args()
    entries=json.loads(args.file.read_text(encoding='utf-8'))
    if not isinstance(entries,list): raise SystemExit('Expected a list of AnswerIn objects')
    if not args.apply:
        print(f'PREVIEW: {len(entries)} individual /api/answers calls, no changes made.')
        for e in entries:print(f" - {e['scope']}: {e['key']} = {e['value']}")
        print('Use --apply only after reconciling any existing records; applying overwrites the same key/scope.')
        return
    key=os.environ.get('HUNTERXJOB_API_KEY','')
    if not key:raise SystemExit('Set HUNTERXJOB_API_KEY in environment first')
    base=args.base_url.rstrip('/')
    if not (base.startswith('http://127.0.0.1:') or base.startswith('http://localhost:') or base.startswith('https://')):
        raise SystemExit('Refusing HTTP connection to a non-local host. Use HTTPS for remote servers.')
    errors=[]
    for e in entries:
        data=json.dumps(e).encode('utf-8')
        req=urllib.request.Request(base+'/api/answers', data=data,method='POST',headers={'Content-Type':'application/json','X-API-Key':key})
        try:
            with urllib.request.urlopen(req,timeout=15) as resp:
                if resp.status not in (200,201):raise RuntimeError(f'HTTP {resp.status}')
            print('SAVED:',e['key'])
        except (urllib.error.URLError,RuntimeError) as ex:
            errors.append((e['key'],str(ex)))
            print('FAILED:',e['key'],ex,file=sys.stderr)
    print(f'Finished: {len(entries)-len(errors)} saved / {len(errors)} failed. No job applications submitted.')
    if errors:raise SystemExit(1)
if __name__=='__main__':main()
