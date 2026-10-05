"""Verify every declared bundle file after extraction; never modify originals."""
import hashlib
import json
import stat
from pathlib import Path

root=Path(__file__).resolve().parent
manifest=json.loads((root/'BUNDLE-MANIFEST.json').read_text(encoding='utf-8'))
errors=[]
for item in manifest['files']:
    p=root/item['path']
    try:
        for part in (p,*p.parents):
            s=part.lstat()
            if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&0x400:
                raise ValueError('Link or reparse point rejected')
            if part==root:break
        if root not in p.resolve().parents or not p.is_file():raise ValueError('Missing or outside bundle')
        h=hashlib.sha256()
        with p.open('rb') as f:
            for b in iter(lambda:f.read(1048576),b''):h.update(b)
        if p.stat().st_size!=item['bytes'] or h.hexdigest()!=item['sha256']:raise ValueError('Size/hash mismatch')
    except (OSError,ValueError) as e:
        errors.append({'path':item['path'],'error':str(e)})
print(json.dumps({'checked':len(manifest['files']),'errors':errors,'status':'verified' if not errors else 'failed'},ensure_ascii=False,indent=2))
raise SystemExit(bool(errors))
