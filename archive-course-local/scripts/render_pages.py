"""Render PDF, or PPT via an explicitly installed LibreOffice, to preview PNGs.
Never installs software; rendering success is not visual review.
"""
import argparse
import hashlib
import json
import stat
import subprocess
from pathlib import Path


def guarded(value, root=None, exists=False):
    p = Path(value)
    if not p.is_absolute():
        raise ValueError('Explicit absolute paths required')
    for part in (p, *p.parents):
        try:
            s = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Links and reparse points unsupported')
    p = p.resolve(strict=exists)
    if root and root not in p.parents:
        raise ValueError('Path must be strictly inside workspace root')
    return p


def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for n in ('workspace-root', 'input', 'output-dir', 'pdftoppm'):
        ap.add_argument('--' + n, required=True)
    ap.add_argument('--soffice', help='Required only for PPTX; already installed executable')
    ap.add_argument('--expected-pages', type=int, required=True)
    args = ap.parse_args()
    root = guarded(args.workspace_root, exists=True)
    source = guarded(args.input, root, True)
    out = guarded(args.output_dir, root)
    poppler = guarded(args.pdftoppm, exists=True)
    if not root.is_dir() or not source.is_file() or not poppler.is_file():
        raise ValueError('Root and regular source/tool required')
    if out.exists() or source.suffix.lower() not in ('.pdf', '.pptx') or args.expected_pages < 1:
        raise ValueError('New output and supported input/positive expected pages required')
    office = None
    if source.suffix.lower() == '.pptx':
        if not args.soffice:
            raise ValueError('PPT rendering pending: provide an already installed --soffice; no installation performed')
        office = guarded(args.soffice, exists=True)
        if not office.is_file():
            raise ValueError('LibreOffice executable unavailable')
    original_hash = sha(source)
    before = source.stat()
    identity = (before.st_ino, before.st_size, before.st_mtime_ns)
    out.mkdir(parents=True)
    result = dict(input=str(source), input_sha256=original_hash, status='running',
                  human_review=False, steps=[])
    def run(command, name):
        with (out / (name + '.log')).open('xb') as log:
            try:
                cp = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=120)
            except subprocess.TimeoutExpired:
                result['steps'].append(dict(name=name, status='timeout'))
                raise
        result['steps'].append(dict(name=name, returncode=cp.returncode))
        if cp.returncode:
            raise RuntimeError(name + ' failed; see saved log')
    try:
        pdf = source
        if office:
            profile = out / 'office-profile'
            run([str(office), '-env:UserInstallation=' + profile.as_uri(), '--headless',
                 '--convert-to', 'pdf', '--outdir', str(out), str(source)], 'ppt-to-pdf')
            pdf = out / (source.stem + '.pdf')
            if not pdf.is_file() or not pdf.stat().st_size:
                raise RuntimeError('LibreOffice did not produce a nonempty PDF')
        run([str(poppler), '-png', '-r', '96', str(pdf), str(out / 'page')], 'pdf-to-png')
        previews = sorted(out.glob('page-*.png'), key=lambda p: int(p.stem.rsplit('-', 1)[1]))
        if len(previews) != args.expected_pages or any(not p.stat().st_size for p in previews):
            raise ValueError('Preview page count/nonempty check failed')
        guarded(args.input, root, True)
        after = source.stat()
        if sha(source) != original_hash or (after.st_ino, after.st_size, after.st_mtime_ns) != identity:
            raise ValueError('Input changed during rendering')
        result.update(status='rendered_requires_visual_review', pages=len(previews),
                      previews=[dict(file=p.name, sha256=sha(p)) for p in previews])
    except BaseException as error:
        result.update(status='failed', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        with (out / 'render-result.json').open('x', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
