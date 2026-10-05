"""Whole-file diagnostics and exact sampled evidence; never claims human listening."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def run_logged(command, log_path):
    with log_path.open('x', encoding='utf-8') as log:
        return subprocess.run(command, stdout=log, stderr=log,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--lesson-id', required=True)
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--ffprobe', required=True)
    parser.add_argument('--audio-factor', type=float, choices=(1, 2), default=2)
    args = parser.parse_args()
    source, out = Path(args.input).resolve(), Path(args.output_dir).resolve()
    if out.exists():
        raise FileExistsError('Keep prior checks, use a new evidence directory')
    out.mkdir(parents=True)
    probe = subprocess.run([args.ffprobe,'-v','error','-show_format','-show_streams','-of','json',str(source)],
                           capture_output=True, encoding='utf-8', creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    (out/'probe.stdout.json').write_text(probe.stdout, encoding='utf-8')
    (out/'probe.stderr.txt').write_text(probe.stderr, encoding='utf-8')
    if probe.returncode:
        (out/'检查结果.json').write_text(json.dumps({'status':'failed','probe_exit_code':probe.returncode}),encoding='utf-8')
        return 1
    data = json.loads(probe.stdout)
    duration = float(data['format']['duration'])
    videos = [s for s in data['streams'] if s['codec_type']=='video']
    audios = [s for s in data['streams'] if s['codec_type']=='audio']
    if not videos or not audios or duration<=0:
        raise ValueError('Expected positive video duration and an audio stream')
    decode = run_logged([args.ffmpeg,'-hide_banner','-loglevel','info','-i',str(source),
                         '-vf','blackdetect=d=2.02:pix_th=0.1,freezedetect=n=-50dB:d=8',
                         '-af','volumedetect,silencedetect=n=-45dB:d=1.5','-f','null','NUL'],out/'整文件诊断.txt')
    caption = run_logged([args.ffmpeg,'-hide_banner','-loglevel','info','-i',str(source),'-an','-vf',
                          r'crop=iw:min(120\,ih):0:ih-min(120\,ih),freezedetect=n=-80dB:d=8',
                          '-f','null','NUL'],out/'底部字幕区域诊断.txt')
    text = (out/'整文件诊断.txt').read_text(encoding='utf-8',errors='replace')
    caption_text = (out/'底部字幕区域诊断.txt').read_text(encoding='utf-8',errors='replace')
    events = [line for line in text.splitlines() if any(k in line for k in
               ('black_start:', 'silence_start:', 'silence_end:', 'freeze_start:', 'freeze_end:', 'mean_volume:', 'max_volume:'))]
    samples = []
    for label, frame, start in [('开头',min(6,duration/4),0),
                               ('中间',duration/2,max(0,duration/2-3)),
                               ('结尾',max(0,duration-8),max(0,duration-14))]:
        image, wav = out/f'{label}-固定帧.png', out/f'{label}-恢复原速.wav'
        count = min(12,duration-start)
        codes = [run_logged([args.ffmpeg,'-hide_banner','-loglevel','error','-n','-ss',str(frame),
                             '-i',str(source),'-frames:v','1',str(image)],out/f'{label}-取帧.txt'),
                 run_logged([args.ffmpeg,'-hide_banner','-loglevel','error','-n','-ss',str(start),'-t',str(count),
                             '-i',str(source),'-vn','-af',f'atempo={1/args.audio_factor}',
                             '-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)],out/f'{label}-音频提取.txt')]
        samples.append(dict(label=label,frame_seconds=frame,audio_start_seconds=start,audio_source_seconds=count,
                            image=image.name,audio=wav.name,extraction_exit_codes=codes,
                            actual_visual_check=False,actual_playback_check=False,human_listening=False))
    hash_value = hashlib.sha256()
    with source.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): hash_value.update(block)
    result = dict(lesson_id=args.lesson_id, input=str(source), input_sha256=hash_value.hexdigest(),
                  input_bytes=source.stat().st_size,duration_seconds=duration,streams=data['streams'],
                  decode_exit_code=decode,caption_scan_exit_code=caption,
                  diagnostic_events=events,caption_freeze_events=[l for l in caption_text.splitlines() if 'freeze_start:' in l],
                  samples=samples,status='diagnostics_completed_requires_content_review',human_listening=False,
                  limitations=['threshold_events_require_review','static_slides_are_not_confirmed_stalls',
                               'bottom_region_may_contain_non_caption_content','actual_playback_pending'])
    (out/'检查结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':result['status'],'duration':duration,'decode_exit_code':decode,
                      'caption_scan_exit_code':caption,'samples':len(samples)},ensure_ascii=False),flush=True)
    return int(decode!=0 or caption!=0 or any(c for s in samples for c in s['extraction_exit_codes']))


if __name__=='__main__':
    raise SystemExit(main())
