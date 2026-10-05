"""Check manifest metadata and optional version hashes; never play or modify media."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def inside_file(root, relative):
    if not isinstance(relative, str) or not relative:
        raise ValueError('missing evidence path')
    target = (root / relative).resolve()
    if root not in target.parents or not target.is_file():
        raise ValueError('missing evidence file or path outside archive')
    return target


def inspect(root, manifest, verify_hashes=False):
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(data.get('items'), list):
        raise ValueError('Manifest requires an items array')
    problems, seen, videos, present = [], set(), [], 0
    phases = ('full_transcription_status', 'slide_export_status', 'cross_check_status')
    counts = {p: {} for p in phases}
    for item in data['items']:
        if item.get('type') != '视频':
            continue
        identifier = (item.get('module'), item.get('sequence'))
        if None in identifier or identifier in seen:
            problems.append({'id': identifier, 'problem': 'missing_or_duplicate_id'})
        seen.add(identifier)
        videos.append(identifier)
        path = item.get('recording_file')
        target = None
        if path:
            target = (root / path).resolve()
            if root not in target.parents:
                problems.append({'id': identifier, 'problem': 'outside_archive'})
            elif not target.is_file() or target.stat().st_size == 0:
                problems.append({'id': identifier, 'problem': 'missing_or_empty_formal_file'})
            else:
                present += 1
                for field in ('full_title', 'source_url', 'source_duration_seconds', 'duration_seconds'):
                    value = item.get(field)
                    if not value or (field.endswith('_seconds') and
                                     (not isinstance(value, (int, float)) or value <= 0)):
                        problems.append({'id': identifier, 'problem': 'missing_formal_identity', 'field': field})
        for phase in phases:
            status = item.get(phase, 'pending')
            counts[phase][status] = counts[phase].get(status, 0) + 1
            if status not in ('pending', 'running', 'completed', 'needs_review', 'failed'):
                problems.append({'id': identifier, 'problem': 'invalid_phase', 'phase': phase})
            if status not in ('completed', 'needs_review'):
                continue
            evidence_field = phase.replace('_status', '_evidence')
            try:
                evidence_path = inside_file(root, item.get(evidence_field))
                if any((evidence_path.parent / marker).exists() for marker in
                       ('FAILED.json', '候选索引发布失败.json')):
                    raise ValueError('stage evidence directory has a failure marker')
                evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
                signature = evidence.get('signature', evidence)
                bound_hash = signature.get('input_sha256')
                expected = item.get('formal_sha256')
                if not expected or bound_hash != expected:
                    raise ValueError('evidence does not match registered formal hash')
                if target is None or not target.is_file():
                    raise ValueError('stage evidence has no formal input')
                if signature.get('input_bytes') != target.stat().st_size:
                    raise ValueError('input size changed or not recorded')
                if verify_hashes and digest(target) != expected:
                    raise ValueError('formal content hash changed')
                if status == 'completed' and evidence.get('quality_status') not in ('completed', 'accepted'):
                    raise ValueError('completed phase lacks accepted quality evidence')
                outputs = evidence.get('outputs')
                if not isinstance(outputs, dict) or not outputs:
                    raise ValueError('review/completed phase has no bound output files')
                for name, output_hash in outputs.items():
                    if not isinstance(output_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', output_hash):
                        raise ValueError('invalid derived output SHA256')
                    output = inside_file(evidence_path.parent, name)
                    if verify_hashes and digest(output) != output_hash:
                        raise ValueError('derived output hash changed')
            except (OSError, ValueError, TypeError, AttributeError) as error:
                problems.append({'id': identifier, 'problem': 'invalid_stage_binding',
                                 'phase': phase, 'detail': str(error)})
    return dict(video_count=len(videos), formal_file_count=present, phases=counts,
                problems=problems, verify_hashes=verify_hashes,
                limitations=['no_live_process_check', 'no_content_validation']+
                            ([] if verify_hashes else ['content_hashes_not_recomputed']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-root', required=True)
    parser.add_argument('--manifest')
    parser.add_argument('--verify-hashes', action='store_true',
                        help='Recompute hashes only when capture and heavy media tasks are idle')
    args = parser.parse_args()
    root = Path(args.archive_root).resolve()
    manifest = Path(args.manifest).resolve() if args.manifest else root / '课程清单.json'
    try:
        result = inspect(root, manifest, args.verify_hashes)
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(result['problems']))


if __name__ == '__main__':
    raise SystemExit(main())
