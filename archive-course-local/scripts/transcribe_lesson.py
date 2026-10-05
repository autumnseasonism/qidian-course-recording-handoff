"""Local full-audio recognition, resumable by immutable source and segment signature."""
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import wave


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.pending')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def timestamp(seconds):
    milliseconds = round(seconds * 1000)
    return f'{milliseconds//3600000:02}:{milliseconds//60000%60:02}:{milliseconds//1000%60:02}.{milliseconds%1000:03}'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True)
    p.add_argument('--output-dir', required=True)
    p.add_argument('--lesson-id', required=True)
    p.add_argument('--title', required=True)
    p.add_argument('--ffmpeg', required=True)
    p.add_argument('--ffprobe', required=True)
    p.add_argument('--model-dir', required=True)
    p.add_argument('--model', default='small')
    p.add_argument('--deps-dir')
    p.add_argument('--audio-factor', type=float, default=2.0)
    p.add_argument('--core-seconds', type=float, default=120.0)
    p.add_argument('--overlap-seconds', type=float, default=2.0)
    p.add_argument('--resume', action='store_true')
    args = p.parse_args()
    if args.audio_factor not in (1.0, 2.0) or args.core_seconds <= 0 or args.overlap_seconds < 0:
        raise ValueError('Supported factors 1/2; positive core and nonnegative overlap required')
    source, destination = Path(args.input).resolve(), Path(args.output_dir).resolve()
    if not source.is_file() or source == destination or source in destination.parents:
        raise ValueError('Expected immutable local video and separate output directory')
    if destination.exists() and not args.resume:
        raise FileExistsError('New output directory required; use resume only for the same signature')
    destination.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(subprocess.check_output([args.ffprobe, '-v', 'error', '-show_format',
                                                   '-show_streams', '-of', 'json', str(source)], encoding='utf-8'))
    duration = float(metadata['format']['duration'])
    if not any(s['codec_type'] == 'audio' for s in metadata['streams']):
        raise ValueError('No audio stream')
    signature = dict(lesson_id=args.lesson_id, input=str(source), input_sha256=digest(source),
                     input_bytes=source.stat().st_size, duration_seconds=duration,
                     audio_factor=args.audio_factor, core_seconds=args.core_seconds,
                     overlap_seconds=args.overlap_seconds, model=args.model,
                     model_dir=str(Path(args.model_dir).resolve()))
    state_file = destination / '处理状态.json'
    if state_file.exists():
        prior = json.loads(state_file.read_text(encoding='utf-8'))
        if prior['signature'] != signature:
            raise ValueError('Source or parameters changed; keep prior output and choose a new directory')
        if prior.get('status') == 'recognition_completed_requires_review':
            if not prior.get('outputs'):
                raise ValueError('Completed output lacks version binding; review it instead of overwriting')
            for name, expected_hash in prior['outputs'].items():
                file = destination / name
                if not file.is_file() or digest(file) != expected_hash:
                    raise ValueError('Completed output changed; preserve it and select a new version')
            print(json.dumps({'status': prior['status'], 'reused_completed_outputs': True,
                              'input_sha256': signature['input_sha256']}, ensure_ascii=False), flush=True)
            return
        if any((destination / n).exists() for n in ('整节时间码逐字稿.json', '整节时间码逐字稿.md')):
            raise ValueError('Unbound final outputs already exist; preserve them and choose a new directory')
        history_index = 1
        while (destination / f'处理状态-恢复前{history_index:02}.json').exists():
            history_index += 1
        write_json(destination / f'处理状态-恢复前{history_index:02}.json', prior)
    state = dict(signature=signature, status='running', human_listening=False, pid=os.getpid(),
                 started_utc=dt.datetime.now(dt.timezone.utc).isoformat(), segments=[])
    write_json(state_file, state)
    if args.deps_dir:
        sys.path.insert(0, str(Path(args.deps_dir).resolve()))
    try:
        import numpy as np
        from faster_whisper import WhisperModel
        model = WhisperModel(args.model, device='cpu', compute_type='int8', cpu_threads=8,
                             download_root=args.model_dir, local_files_only=True)
    except Exception as error:
        state.update(status='failed', error_type=type(error).__name__, failure_phase='model_initialization')
        write_json(state_file, state)
        raise
    merged, issues = [], []
    for index in range(math.ceil(duration / args.core_seconds)):
        core_start = index * args.core_seconds
        core_end = min(duration, core_start + args.core_seconds)
        start = max(0.0, core_start - args.overlap_seconds)
        end = min(duration, core_end + args.overlap_seconds)
        wav = destination / f'{index+1:03}-恢复原速.wav'
        raw_path = destination / f'{index+1:03}-原始识别.json'
        segment_signature = dict(signature=signature, core=[core_start, core_end], input_window=[start, end])
        try:
            if raw_path.exists():
                raw = json.loads(raw_path.read_text(encoding='utf-8'))
                if raw['segment_signature'] != segment_signature:
                    raise ValueError('Cached segment signature mismatch')
            else:
                if wav.exists():
                    try:
                        with wave.open(str(wav)) as previous_audio:
                            valid = ((previous_audio.getnchannels(), previous_audio.getframerate(), previous_audio.getsampwidth())
                                     == (1,16000,2) and abs(previous_audio.getnframes()/16000-(end-start)*args.audio_factor)<0.25)
                    except (wave.Error, EOFError):
                        valid = False
                    if not valid:
                        audio_attempt = 1
                        while (destination / f'{index+1:03}-恢复原速-重试{audio_attempt:02}.wav').exists():
                            audio_attempt += 1
                        wav = destination / f'{index+1:03}-恢复原速-重试{audio_attempt:02}.wav'
                if not wav.exists():
                    attempt = 1
                    while (destination / f'{index+1:03}-解码尝试{attempt:02}.txt').exists():
                        attempt += 1
                    with (destination / f'{index+1:03}-解码尝试{attempt:02}.txt').open('x', encoding='utf-8') as log:
                        subprocess.run([args.ffmpeg, '-hide_banner', '-loglevel', 'info', '-n', '-ss', str(start),
                                        '-t', str(end-start), '-i', str(source), '-vn', '-af',
                                        f'atempo={1/args.audio_factor}', '-ac', '1', '-ar', '16000',
                                        '-c:a', 'pcm_s16le', str(wav)], stdout=log, stderr=log, check=True)
                with wave.open(str(wav)) as audio:
                    if (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) != (1, 16000, 2):
                        raise ValueError('Invalid audio format')
                    audio_seconds = audio.getnframes() / audio.getframerate()
                    if abs(audio_seconds - (end-start)*args.audio_factor) > 0.25:
                        raise ValueError('Decoded duration does not cover the planned window')
                    samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16).astype(np.float32)/32768.0
                recognized, info = model.transcribe(samples, language='zh', beam_size=5, word_timestamps=True)
                original = []
                for seg in recognized:
                    words = [dict(start=start+w.start/args.audio_factor, end=start+w.end/args.audio_factor,
                                  text=w.word, probability=w.probability) for w in (seg.words or [])]
                    original.append(dict(start=start+seg.start/args.audio_factor, end=start+seg.end/args.audio_factor,
                                         text=seg.text, words=words, avg_logprob=seg.avg_logprob,
                                         no_speech_prob=seg.no_speech_prob))
                raw = dict(segment_signature=segment_signature, decoded_audio_seconds=audio_seconds,
                           segments=original, model_language=info.language, audio_file=wav.name,
                           audio_sha256=digest(wav))
                write_json(raw_path, raw)
            for seg in raw['segments']:
                owned = [w for w in seg['words'] if core_start <= (w['start']+w['end'])/2 < core_end]
                if not owned:
                    continue
                row = dict(start=owned[0]['start'], end=owned[-1]['end'], text=''.join(w['text'] for w in owned),
                           source_segment=index+1, words=owned, avg_logprob=seg['avg_logprob'])
                merged.append(row)
                if seg['avg_logprob'] < -0.6 or any(w['probability'] < 0.45 for w in owned):
                    issues.append(dict(kind='recognition_uncertain', start=row['start'], end=row['end'],
                                       segment=index+1, status='needs_review'))
            entry = dict(index=index+1, core_start=core_start, core_end=core_end,
                         input_start=start, input_end=end, decoded_seconds=raw['decoded_audio_seconds'],
                         status='completed', raw_file=raw_path.name)
            if not raw['segments']:
                issues.append(dict(kind='empty_recognition', start=core_start, end=core_end, status='needs_review'))
        except Exception as error:
            entry = dict(index=index+1, core_start=core_start, core_end=core_end, status='failed',
                         error_type=type(error).__name__, error=str(error))
            state['segments'].append(entry)
            state['status'] = 'failed'
            write_json(state_file, state)
            raise
        state['segments'].append(entry)
        write_json(state_file, state)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    merged.sort(key=lambda r: (r['start'], r['end']))
    cursor = 0.0
    for row in merged:
        if row['start'] > cursor + 8:
            issues.append(dict(kind='recognition_gap', start=cursor, end=row['start'], status='needs_review'))
        cursor = max(cursor, row['end'])
    if cursor < duration - 8:
        issues.append(dict(kind='recognition_gap', start=cursor, end=duration, status='needs_review'))
    if digest(source) != signature['input_sha256']:
        state.update(status='failed', failure_phase='input_version_changed')
        write_json(state_file, state)
        raise ValueError('Source changed while processing')
    result = dict(signature=signature, segments=merged, issues=issues, human_listening=False,
                  input_audio_coverage_ratio=1.0, quality_status='needs_review',
                  limitations=['automatic_recognition_not_word_accuracy', 'visual_cross_check_pending',
                               'no_independent_full_source_reference', 'no_human_listening'])
    write_json(destination / '整节时间码逐字稿.json', result)
    header = [f'# {args.lesson_id} {args.title} 自动逐字稿', '',
              '时间以本地正式视频为准。全部音频分段识别已覆盖，文字与疑点仍待校对；未人工听检。', '',
              f'视频：{source}', f'输入SHA256：{signature["input_sha256"]}', '',
              '原始识别、覆盖表和疑点保留，不从识别缺字推断漏录。', '']
    lines = [f'[{timestamp(r["start"])} - {timestamp(r["end"])}] {r["text"]}' for r in merged]
    (destination / '整节时间码逐字稿.md').write_text('\n'.join(header+lines)+'\n', encoding='utf-8')
    write_json(destination / '识别疑点.json', issues)
    with (destination / '分段覆盖表.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(state['segments'][0]))
        writer.writeheader()
        writer.writerows(state['segments'])
    state.update(status='recognition_completed_requires_review', input_audio_coverage_ratio=1.0,
                 ended_utc=dt.datetime.now(dt.timezone.utc).isoformat())
    state['outputs'] = {name: digest(destination/name) for name in
                        ('整节时间码逐字稿.json','整节时间码逐字稿.md','识别疑点.json','分段覆盖表.csv')}
    write_json(state_file, state)
    print(json.dumps({'status': state['status'], 'segments':len(merged), 'issues':len(issues)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
