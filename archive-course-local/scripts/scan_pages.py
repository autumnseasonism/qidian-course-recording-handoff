"""Read an immutable local video; propose page candidates, never assert completeness."""
import argparse
import bisect
import hashlib
import json
import math
import os
import stat
import subprocess
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def guarded_path(value, root=None, must_exist=False):
    p = Path(value)
    if not p.is_absolute():
        raise ValueError('Paths must be explicit and absolute')
    # Inspect supplied lexical ancestors before resolving. Windows reparse
    # points include junctions, including on Python versions without is_junction.
    for ancestor in (p, *p.parents):
        try:
            metadata = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise ValueError('Symlink/junction ancestors are not supported')
        if ancestor != p and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError('A path ancestor is not a directory')
    real = p.resolve(strict=must_exist)
    if root is not None and (real == root or root not in real.parents):
        raise ValueError('Input/output must be strictly inside workspace root')
    return real


def file_identity(file):
    metadata = file.stat()
    return (metadata.st_dev, metadata.st_ino, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'output-dir', 'ffmpeg', 'ffprobe', 'lesson-id', 'workspace-root'):
        ap.add_argument('--' + name, required=True)
    args = ap.parse_args()
    root = guarded_path(args.workspace_root, must_exist=True)
    if not root.is_dir():
        raise ValueError('Workspace root must be an existing directory')
    source = guarded_path(args.input, root, must_exist=True)
    out = guarded_path(args.output_dir, root)
    ffmpeg = guarded_path(args.ffmpeg, must_exist=True)
    ffprobe = guarded_path(args.ffprobe, must_exist=True)
    if not source.is_file() or out.exists() or not ffmpeg.is_file() or not ffprobe.is_file():
        raise ValueError('Input/tools must be regular files; output directory must be new')
    # The exact-frame script route needs modern FFmpeg file-backed option syntax.
    version = subprocess.run([str(ffmpeg), '-version'], capture_output=True, check=True).stdout.decode('utf-8', errors='replace').splitlines()[0]
    import re
    match = re.search(r'ffmpeg version (\d+)', version)
    if not match or int(match.group(1)) < 9:
        raise ValueError('This scanner requires verified FFmpeg 9+ file-backed filter syntax; no silent fallback')
    # Capture immutable evidence BEFORE either source probe. Do not later adopt
    # a replacement file as the signature merely because its probes succeeded.
    source_identity = file_identity(source)
    source_hash = sha(source)
    if file_identity(source) != source_identity:
        raise ValueError('Source changed while collecting initial identity/hash')

    def verify_source():
        if guarded_path(args.workspace_root, must_exist=True) != root:
            raise ValueError('Workspace root changed during scan')
        if guarded_path(args.input, root, must_exist=True) != source or not source.is_file():
            raise ValueError('Source path changed during scan')
        if file_identity(source) != source_identity or sha(source) != source_hash or file_identity(source) != source_identity:
            raise ValueError('Source differs from pre-probe identity/hash')
        if guarded_path(args.output_dir, root) != out:
            raise ValueError('Output path changed during scan')

    probe = subprocess.run([str(ffprobe), '-v', 'error', '-show_streams',
                            '-show_format', '-of', 'json', str(source)],
                           capture_output=True, check=True)
    info = json.loads(probe.stdout)
    v = next(s for s in info['streams'] if s['codec_type'] == 'video')
    numerator, denominator = map(int, v['avg_frame_rate'].split('/'))
    if numerator <= 0 or denominator <= 0:
        raise ValueError('Invalid frame rate ratio')
    fps = numerator / denominator
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('Invalid finite frame rate')
    duration = float(info['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Invalid finite duration')
    timing_probe = subprocess.run([str(ffprobe), '-v', 'error', '-select_streams', 'v:0',
                                  '-show_frames', '-show_entries', 'frame=best_effort_timestamp_time',
                                  '-of', 'json', str(source)], capture_output=True, check=True)
    timing = json.loads(timing_probe.stdout)['frames']
    pts = [float(f['best_effort_timestamp_time']) for f in timing]
    if not pts or any(not math.isfinite(t) or t < 0 or t >= duration for t in pts):
        raise ValueError('Invalid finite frame timestamps or duration coverage')
    if any(current <= previous for previous, current in zip(pts, pts[1:])):
        raise ValueError('Frame timestamps must be strictly increasing')
    first_pts = pts[0]
    relative_cfr_max_error = max(abs((t - first_pts) - n / fps) for n, t in enumerate(pts))
    if relative_cfr_max_error > .0001:
        raise ValueError('Only verified constant-frame-rate videos relative to first actual PTS are supported')
    signature = dict(input=str(source), input_sha256=source_hash,
                     input_bytes=source_identity[2], duration_seconds=duration,
                     lesson_id=args.lesson_id, fps=fps, width=v['width'], height=v['height'])
    # Reject changed sources or path boundaries before the first output write.
    verify_source()
    if out.exists():
        raise ValueError('Output directory appeared after preflight')
    out.mkdir(parents=True)
    events, last, i = [], None, 0
    with (out / '全帧扫描日志.txt').open('wb') as log:
        command = [str(ffmpeg), '-hide_banner', '-loglevel', 'warning', '-i', str(source),
                   '-map', '0:v:0', '-an', '-vf', 'scale=192:108', '-fps_mode', 'passthrough',
                   '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1']
        p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log)
        try:
            while True:
                data = p.stdout.read(192 * 108)
                if not data:
                    break
                if len(data) != 192 * 108:
                    raise RuntimeError('Truncated raw frame')
                if i >= len(pts):
                    raise ValueError('Decoded frame count exceeds complete PTS count')
                frame = np.frombuffer(data, dtype=np.uint8).reshape(108, 192)[:94].astype(np.int16)
                if last is not None:
                    delta = np.abs(frame - last)
                    mean = float(delta.mean())
                    changed = float((delta > 24).mean())
                    if mean >= 1.0 and changed >= .02:
                        events.append(dict(seconds=pts[i], frame_index=i, mean_delta=round(mean, 5),
                                           changed_fraction=round(changed, 5)))
                last = frame
                i += 1
        except BaseException:
            if p.poll() is None:
                p.terminate()
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            raise
        finally:
            p.stdout.close()
        if p.wait() != 0:
            raise RuntimeError('FFmpeg full scan failed')
    if i != len(pts):
        raise ValueError('Actual decoded frame count differs from complete PTS count')
    # N/fps is a nominal frame-span length, not an absolute source timestamp.
    # Include the first actual PTS when comparing the nominal end with duration;
    # keep observed first/last PTS separate from that CFR end estimate.
    nominal_decoded_seconds = i / fps
    nominal_coverage_end = first_pts + nominal_decoded_seconds
    coverage_tolerance = max(1 / fps, .1)
    if abs(nominal_coverage_end - duration) > coverage_tolerance:
        raise ValueError('Decoded nominal coverage end including first PTS does not match duration')
    frame_coverage = dict(first_actual_pts_seconds=first_pts,
                          last_actual_pts_seconds=pts[-1],
                          actual_pts_span_seconds=pts[-1] - first_pts,
                          first_pts_offset_seconds=first_pts,
                          nominal_decoded_duration_seconds=nominal_decoded_seconds,
                          nominal_coverage_end_seconds=nominal_coverage_end,
                          last_frame_nominal_end_seconds=pts[-1] + 1 / fps,
                          source_duration_seconds=duration,
                          nominal_end_minus_duration_seconds=nominal_coverage_end - duration,
                          duration_comparison_tolerance_seconds=coverage_tolerance,
                          policy='first/last PTS are observed; frame-span/end are CFR nominal estimates')
    verify_source()
    # Verified only after actual full decode count agrees with complete PTS data.
    (out / '帧时间戳核验.json').write_text(json.dumps(dict(
        verified=True, frame_count=len(pts), fps=fps, first_pts=first_pts, last_pts=pts[-1],
        first_pts_offset_seconds=first_pts, cfr_basis='relative_to_first_actual_pts',
        maximum_error_seconds=relative_cfr_max_error,
        relative_cfr_max_error_seconds=relative_cfr_max_error,
        zero_based_max_error_seconds=max(abs(t - n / fps) for n, t in enumerate(pts)),
        frame_coverage=frame_coverage), indent=2), encoding='utf-8')
    requests = []
    def request(seconds, basis, exact_frame=None):
        if not math.isfinite(seconds):
            raise ValueError('Non-finite candidate request')
        bounded = max(0, min(duration, seconds))
        # First actual frame at/after a request; clamp end requests to last frame.
        # Rounded request values are retained only as requests, never as PTS.
        frame_index = exact_frame if exact_frame is not None else min(bisect.bisect_left(pts, bounded), len(pts) - 1)
        if type(frame_index) is not int or not 0 <= frame_index < len(pts):
            raise ValueError('Invalid candidate frame index')
        requests.append(dict(requested_seconds=bounded, frame_index=frame_index,
                             actual_seconds=pts[frame_index], basis=basis))
    for t in np.arange(0, duration, 4):
        request(round(float(t), 3), 'regular_interval')
    request(round(max(0, duration - .08), 3), 'end_request')
    previous_event = -10
    for event in events:
        t = event['seconds']
        # Keep every detected transition frame so brief states are candidates.
        request(t, 'detected_transition', event['frame_index'])
        if t - previous_event >= .4:
            for offset in (-.08, .48):
                request(round(max(0, min(duration - .04, t + offset)), 3), 'transition_offset')
            previous_event = t
    by_frame = {}
    for item in requests:
        by_frame.setdefault(item['frame_index'], []).append(item)
    frames_dir = out / '候选原图'
    frames_dir.mkdir()
    selected_frames = sorted(by_frame)
    if not selected_frames:
        raise ValueError('No validated candidate frame indices')
    # Compact consecutive ordinals into ranges without dropping any selected
    # frame. A script file avoids Windows command-line length limits.
    ranges = []
    first = last = selected_frames[0]
    for frame_index in selected_frames[1:]:
        if frame_index == last + 1:
            last = frame_index
        else:
            ranges.append((first, last))
            first = last = frame_index
    ranges.append((first, last))
    terms = [f'eq(n,{first})' if first == last else f'between(n,{first},{last})'
             for first, last in ranges]
    # FFmpeg eval.c bounds expression-node depth. A flat sum is parsed as a
    # left-deep tree; pairwise parenthesized sums retain every range/order while
    # reducing depth from linear to logarithmic (no extra media decode).
    balanced_terms = list(terms)
    while len(balanced_terms) > 1:
        balanced_terms = [f'({balanced_terms[index]}+{balanced_terms[index + 1]})'
                          if index + 1 < len(balanced_terms) else balanced_terms[index]
                          for index in range(0, len(balanced_terms), 2)]
    selection_expression = balanced_terms[0]
    filter_script = out / '候选帧选择.fffilter'
    with filter_script.open('x', encoding='utf-8', newline='\n') as script:
        script.write("select='" + selection_expression + "'\n")
    batch_log = out / '候选批量导出日志.txt'
    # One full decode selects exact zero-based n ordinals in their source order.
    # No -ss request or output-rate conversion participates in frame selection.
    # FFmpeg 9 support for these script/fps options must be verified separately.
    with batch_log.open('xb') as log:
        subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'warning', '-n',
                        '-i', str(source), '-map', '0:v:0', '-an',
                        '-/filter:v', str(filter_script),
                        '-fps_mode', 'passthrough', '-start_number', '1',
                        str(frames_dir / '%08d.png')],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    expected_names = [f'{index:08d}.png' for index in range(1, len(selected_frames) + 1)]
    actual_names = sorted(file.name for file in frames_dir.iterdir())
    if actual_names != expected_names:
        raise ValueError('Batch exported frame count/names differ from selected frame list; see export log/script')
    candidates = []
    for index, frame_index in enumerate(selected_frames, 1):
        actual_seconds = pts[frame_index]
        target = frames_dir / expected_names[index - 1]
        if not target.is_file() or target.stat().st_size == 0:
            raise ValueError('A selected batch frame is missing or empty; diagnostics are preserved')
        candidates.append(dict(index=index, seconds=actual_seconds,
                               frame_index=frame_index, actual_seconds=actual_seconds,
                               requests=by_frame[frame_index],
                               image=str(target.relative_to(out)), sha256=sha(target)))
    verify_source()
    sheets = []
    for start in range(0, len(candidates), 18):
        selected = candidates[start:start + 18]
        sheet = Image.new('RGB', (1200, 6 * 260), '#eeeeee')
        draw = ImageDraw.Draw(sheet)
        for local, record in enumerate(selected):
            x, y = (local % 3) * 400, (local // 3) * 260
            with Image.open(out / record['image']) as full:
                full.thumbnail((396, 224))
                sheet.paste(full, (x + 2, y + 24))
            draw.text((x + 5, y + 4), f"{record['index']:03d}  {record['seconds']:.3f}s", fill='black')
        sheet_path = out / f'候选总览-{start // 18 + 1:02d}.png'
        sheet.save(sheet_path)
        sheets.append(sheet_path.name)
    result = dict(schema_version=1, signature=signature, ffmpeg_version=version,
                  status='candidates_require_visual_review',
                  constant_frame_rate_verified=True, pts_count=len(pts),
                  cfr_basis='relative_to_first_actual_pts',
                  first_pts_offset_seconds=first_pts,
                  relative_cfr_max_error_seconds=relative_cfr_max_error,
                  frame_coverage=frame_coverage,
                  candidate_time_policy='actual_verified_pts; requests_map_to_first_frame_at_or_after_then_clamp_last',
                  candidate_export=dict(method='single_decode_exact_frame_select',
                                        expression_shape='balanced_pairwise_addition',
                                        expression_range_terms=len(terms),
                                        selected_frames=len(selected_frames), exported_frames=len(candidates),
                                        filter_script=filter_script.name, log=batch_log.name),
                  decoded_frames=i, decoded_seconds=nominal_decoded_seconds,
                  decoded_seconds_basis='nominal_frame_count_divided_by_fps_excludes_start_offset',
                  scan_width=192, scan_height=108,
                  analysis_region='top 94/108 rows; full saved images are never cropped',
                  regular_interval_seconds=4, events=events, candidates=candidates, sheets=sheets,
                  limitations=['Thresholds may miss small or low contrast changes',
                               'Bottom-region changes depend on regular frames and visual review',
                               'Detected transitions are retained; thresholds may still miss brief animation states',
                               'This is not proof of slide completeness'])
    verify_source()
    index_path = out / '候选索引.json'
    staged_index = out / ('.候选索引.' + uuid.uuid4().hex + '.candidate')
    with staged_index.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2))
    staged_identity = file_identity(staged_index)[:4]
    staged_hash = sha(staged_index)
    index_published = False
    try:
        verify_source()
        os.link(staged_index, index_path)
        index_published = True
        # Include source replacement during index publication in the guard.
        verify_source()
    except BaseException as error:
        rollback = 'not_published'
        if index_published:
            try:
                guarded_path(index_path, root, must_exist=True)
                if file_identity(index_path)[:4] == staged_identity and sha(index_path) == staged_hash and file_identity(index_path)[:4] == staged_identity:
                    index_path.unlink()
                    rollback = 'removed_unchanged_owned_index'
                else:
                    rollback = 'preserved_changed_or_replaced_index'
            except (OSError, ValueError):
                rollback = 'preserved_on_rollback_uncertainty'
        try:
            guarded_path(out, root, must_exist=True)
            with (out / '候选索引发布失败.json').open('x', encoding='utf-8') as diagnostic:
                diagnostic.write(json.dumps(dict(status='failed', success=False,
                                                  error=str(error), rollback=rollback),
                                            ensure_ascii=False, indent=2))
        except (OSError, ValueError):
            pass
        raise
    else:
        try:
            staged_index.unlink()
        except OSError:
            pass  # Verified index remains valid; keep private staging diagnostic.
    print(json.dumps(dict(frames=i, events=len(events), candidates=len(candidates), sheets=len(sheets)), ensure_ascii=False))


if __name__ == '__main__':
    main()
