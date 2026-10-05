"""Read-only audit of normally downloaded materials; never download or extract."""
import argparse
import datetime
import hashlib
import json
import math
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath


def reject_links(path):
    # Inspect lexical ancestors before resolve(), including broken links.
    for node in (path, *path.parents):
        try:
            metadata = node.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 1024:
            raise ValueError('Linked/reparse paths are not accepted')


def authorized_root(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('Absolute authorized root required')
    reject_links(path)
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError('Authorized root must be a directory')
    return path


def bound(root, value):
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    reject_links(path)
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError('Path outside root')
    return path


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def zip_audit(path, args):
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > args.max_zip_entries:
            raise ValueError('ZIP entry limit exceeded')
        total = 0
        for item in infos:
            name = item.filename.replace('\\', '/')
            pieces = PurePosixPath(name).parts
            mode = item.external_attr >> 16
            if name.startswith('/') or '..' in pieces or ':' in name or '\x00' in name:
                raise ValueError('Unsafe ZIP member path')
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError('ZIP link or special file rejected')
            if item.flag_bits & 1:
                raise ValueError('Encrypted ZIP cannot be verified')
            total += item.file_size
            if (item.file_size > args.max_zip_member_bytes or total > args.max_zip_total_bytes
                    or item.file_size / max(1, item.compress_size) > args.max_zip_ratio):
                raise ValueError('ZIP expansion limit exceeded')
        # Read each member in memory-bounded blocks. zipfile checks CRC at EOF.
        actual_total = 0
        for item in infos:
            actual = 0
            with z.open(item) as f:
                for block in iter(lambda: f.read(65536), b''):
                    actual += len(block)
                    actual_total += len(block)
                    if actual > args.max_zip_member_bytes or actual_total > args.max_zip_total_bytes:
                        raise ValueError('Actual ZIP expansion limit exceeded')
            if actual != item.file_size:
                raise ValueError('ZIP member length mismatch')
        return {'entries': len(infos), 'uncompressed_bytes': actual_total, 'crc_verified': True,
                'extracted': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True)
    p.add_argument('--manifest', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--max-zip-entries', type=int, default=10000)
    p.add_argument('--max-zip-member-bytes', type=int, default=256 * 1024 * 1024)
    p.add_argument('--max-zip-total-bytes', type=int, default=1024 * 1024 * 1024)
    p.add_argument('--max-zip-ratio', type=float, default=200)
    a = p.parse_args()
    if (not math.isfinite(a.max_zip_ratio)
            or min(a.max_zip_entries, a.max_zip_member_bytes, a.max_zip_total_bytes, a.max_zip_ratio) <= 0):
        raise ValueError('Limits must be positive')
    root = authorized_root(a.root)
    source, output = bound(root, a.manifest), bound(root, a.output)
    if output.exists() or not output.parent.is_dir():
        raise ValueError('New output file in an existing directory required')
    source_hash = digest(source)
    data = json.loads(source.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise ValueError('Expected object with items array')
    allowed = {'downloaded', 'saved', 'failed', 'pending'}
    for item in data['items']:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                                            for k in ('source', 'lesson', 'title', 'status')):
            raise ValueError('Every item requires nonempty source, lesson, title, status strings')
        if item['status'] not in allowed or (item['status'] in {'saved', 'downloaded'}
                                            and (not isinstance(item.get('path'), str) or not item['path'])):
            raise ValueError('Invalid status or missing saved path')
    rows, identities = [], {}
    for item in data['items']:
        row = dict(item)
        row['local_audit_status'] = 'pending' if item['status'] == 'pending' else 'failed'
        if item['status'] in {'pending', 'failed'}:
            row['reason'] = item.get('reason', 'Not available for local verification')
            rows.append(row)
            continue
        try:
            path = bound(root, item['path'])
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError('Missing, empty or non-file material')
            before = digest(path)
            identities[str(path)] = before
            row.update(bytes=path.stat().st_size, sha256=before)
            if item.get('sha256') and item['sha256'] != before:
                raise ValueError('Expected SHA256 differs')
            with path.open('rb') as f:
                head = f.read(262144).decode('utf-8', errors='replace')
            html = path.suffix.lower() in {'.html', '.htm'} or bool(re.search(r'<!doctype\s+html|<html[\s>]', head, re.I))
            if html and re.search(r'登录|登陆|密码|访问被拒绝|无权限|验证码|\blog[ -]?in\b|sign[ -]?in|access denied|forbidden', head, re.I):
                row.update(local_audit_status='needs_review', reason='Possible login/access/error HTML; inspect actual content')
            else:
                if path.suffix.lower() in {'.zip', '.docx', '.xlsx', '.pptx'} or zipfile.is_zipfile(path):
                    row['zip'] = zip_audit(path, a)
                row.update(local_audit_status='local_integrity_passed_content_review_pending',
                           reason='Presence/hash/container integrity only; course content and completeness require review')
            if digest(path) != before:
                raise ValueError('Material changed during audit')
        except Exception as error:
            row.update(local_audit_status='failed', reason=str(error))
        rows.append(row)
    unchanged = digest(source) == source_hash and all(digest(Path(k)) == v for k, v in identities.items())
    if not unchanged:
        raise ValueError('Source changed; audit output not written')
    report = {'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'manifest_sha256': source_hash, 'originals_unchanged': True, 'items': rows,
              'all_materials_completed': False,
              'limitations': ['No extraction/download', 'No full PDF/image/content rendering',
                              'Local integrity is not lesson relevance or completeness']}
    with output.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    if json.loads(output.read_text(encoding='utf-8')) != report:
        raise ValueError('Output readback mismatch')
    print(json.dumps({'output': str(output), 'items': len(rows), 'originals_unchanged': True}))


if __name__ == '__main__':
    main()
