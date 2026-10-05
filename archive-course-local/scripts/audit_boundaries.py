"""Audit raw overlapping recognition windows; never modify/deduplicate transcripts."""
import argparse
import datetime
import hashlib
import json
import math
import stat
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Finite numeric timestamps required')
    return value


def reject_links(path):
    for node in (path, *path.parents):
        try:
            metadata = node.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 1024:
            raise ValueError('Linked/reparse paths rejected')


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
        raise ValueError('Outside authorized root')
    return path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True)
    p.add_argument('--source-dir', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    root = authorized_root(a.root)
    folder, output = bound(root, a.source_dir), bound(root, a.output)
    if output.exists() or not output.parent.is_dir():
        raise ValueError('New report file in existing directory required')
    identities = {}

    def read(name):
        file = bound(folder, name)
        data = file.read_bytes()
        identities[str(file)] = {'sha256': sha(data), 'bytes': len(data)}
        return data

    state = json.loads(read('处理状态.json').decode('utf-8-sig'))
    result = json.loads(read('整节时间码逐字稿.json').decode('utf-8-sig'))
    if state.get('status') != 'recognition_completed_requires_review' or state.get('signature') != result.get('signature'):
        raise ValueError('Completed original transcription and matching signatures required')
    if not isinstance(state.get('segments'), list) or not state['segments'] or not isinstance(result.get('segments'), list):
        raise ValueError('Invalid segment schema')
    expected = {'整节时间码逐字稿.json', '整节时间码逐字稿.md', '识别疑点.json', '分段覆盖表.csv'}
    if not isinstance(state.get('outputs'), dict) or set(state['outputs']) != expected:
        raise ValueError('Original transcription output bindings required')
    for name, digest in state['outputs'].items():
        data = read(name)
        if sha(data) != digest:
            raise ValueError('Bound output changed: ' + name)
    raws, rows, flat = [], [], []
    previous_end = 0
    for wi, entry in enumerate(state['segments']):
        if not isinstance(entry, dict) or entry.get('index') != wi + 1 or entry.get('status') != 'completed':
            raise ValueError('Ordered completed windows required')
        name = f'{wi+1:03}-原始识别.json'
        if entry.get('raw_file') != name:
            raise ValueError('Unexpected raw filename')
        raw = json.loads(read(name).decode('utf-8-sig'))
        sig = raw.get('segment_signature', {})
        core = [number(entry['core_start']), number(entry['core_end'])]
        window = [number(entry['input_start']), number(entry['input_end'])]
        if (core[0] != previous_end or core[1] <= core[0] or window[0] > core[0] or window[1] < core[1]
                or sig.get('signature') != state['signature'] or sig.get('core') != core or sig.get('input_window') != window):
            raise ValueError('Invalid signature or noncontiguous windows')
        previous_end = core[1]
        if not isinstance(raw.get('segments'), list):
            raise ValueError('Raw segments array required')
        refs = []
        for si, seg in enumerate(raw['segments']):
            if not isinstance(seg.get('text'), str) or not isinstance(seg.get('words'), list):
                raise ValueError('Raw text/words required')
            number(seg['avg_logprob'])
            owned = []
            for j, word in enumerate(seg['words']):
                start, end = number(word['start']), number(word['end'])
                number(word['probability'])
                if end < start or start < window[0] or end > window[1] + 0.25 or not isinstance(word.get('text'), str):
                    raise ValueError('Invalid raw word')
                keep = core[0] <= (start + end) / 2 < core[1]
                refs.append({'ref': [wi, si, j], 'raw_file': name, 'raw_sha256': identities[str(folder/name)]['sha256'],
                             'word': word, 'sentence': seg['text'], 'owned': keep})
                if keep:
                    owned.append(word)
            if owned:
                rows.append(dict(start=owned[0]['start'], end=owned[-1]['end'], text=''.join(w['text'] for w in owned),
                                 source_segment=wi+1, words=owned, avg_logprob=seg['avg_logprob']))
        raws.append(raw)
        flat.append(refs)
    if previous_end != number(state['signature']['duration_seconds']):
        raise ValueError('Core coverage does not reach declared duration')
    rows.sort(key=lambda r: (r['start'], r['end']))
    if rows != result['segments']:
        raise ValueError('Formal rows cannot be exactly rebuilt from raw ownership')
    owned_words = [w for r in rows for w in r['words']]
    found = []
    for wi in range(len(raws) - 1):
        boundary = raws[wi]['segment_signature']['core'][1]
        excluded = [[x for x in flat[k] if not x['owned'] and x['word']['start'] <= boundary <= x['word']['end']
                     and x['word']['end'] > x['word']['start']] for k in (wi, wi+1)]
        pairs = []
        for left in excluded[0]:
            for right in excluded[1]:
                x, y = left['word'], right['word']
                if (x['text'] and x['text'] == y['text'] and left['sentence'] == right['sentence']
                        and max(x['start'], y['start']) < min(x['end'], y['end'])):
                    exists = any(w['text'] == x['text'] and max(w['start'], x['start']) < min(w['end'], x['end'])
                                 for w in owned_words)
                    if not exists:
                        pairs.append({'boundary_seconds': boundary, 'left': left, 'right': right})
        for pair in pairs:
            unique = (sum(x['left']['ref'] == pair['left']['ref'] for x in pairs) == 1
                      and sum(x['right']['ref'] == pair['right']['ref'] for x in pairs) == 1)
            pair.update(status='needs_source_review' if unique else 'ambiguous_do_not_restore', unique_pair=unique)
            found.append(pair)
    for name, identity in identities.items():
        if sha(Path(name).read_bytes()) != identity['sha256']:
            raise ValueError('Source changed while auditing')
    report = {'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'lesson_id': state['signature']['lesson_id'], 'source_signature': state['signature'],
              'source_text_identities': identities, 'originals_unchanged': True, 'baseline_rows_exact': True,
              'candidates': found, 'quality_status': 'needs_review', 'human_listening': False,
              'limitations': ['Strict identical sentence/text overlapping double exclusions only',
                              'No automatic repair or deduplication; true repetitions preserved',
                              'Does not prove audio word accuracy or identify all timing drift']}
    with output.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    if json.loads(output.read_text(encoding='utf-8')) != report:
        raise ValueError('Output readback failed')
    print(json.dumps({'candidates': len(found), 'output': str(output), 'originals_unchanged': True}))


if __name__ == '__main__':
    main()
