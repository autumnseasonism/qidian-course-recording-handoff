"""Construct an explicit X11/Pulse monitor config only; never start capture or configure audio."""
import argparse
import json
import re
import stat
from pathlib import Path

def guard(value,root=None,exists=False):
    p=Path(value)
    if not p.is_absolute():raise ValueError('Explicit target-machine absolute paths required')
    for node in (p,*p.parents):
        try:s=node.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&0x400:raise ValueError('Links/reparse paths rejected')
    p=p.resolve(strict=exists)
    if root and root not in p.parents:raise ValueError('Output/config must be strictly inside root')
    return p

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for n in ('root','output','config','ffmpeg','session-type','display','audio-monitor'):ap.add_argument('--'+n,required=True)
    for n in ('x','y','width','height'):ap.add_argument('--'+n,required=True,type=int)
    a=ap.parse_args()
    if a.session_type!='x11':raise ValueError('This entry supports X11 only; Wayland capture must use a verified allowed tool')
    if not re.fullmatch(r'(?:localhost)?:\d+(?:\.\d+)?',a.display):raise ValueError('Expected explicit local X11 display')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.monitor',a.audio_monitor):raise ValueError('Provide verified output-monitor name, never default/microphone')
    if min(a.x,a.y)<0 or min(a.width,a.height)<=0:raise ValueError('Observed nonnegative origin and positive full-frame size required')
    root=guard(a.root,exists=True);exe=guard(a.ffmpeg,exists=True)
    out=guard(a.output,root);cfg=guard(a.config,root)
    if not root.is_dir() or not exe.is_file() or not out.parent.is_dir() or not cfg.parent.is_dir():raise ValueError('Existing root/parents and executable required')
    if out==cfg or cfg.exists() or out.exists() or out.suffix.lower()!='.mkv':raise ValueError('New separate config and MKV paths required')
    arguments=[str(exe),'-hide_banner','-loglevel','info','-n','-thread_queue_size','512','-f','x11grab',
      '-framerate','25','-video_size',f'{a.width}x{a.height}','-draw_mouse','0','-i',f'{a.display}+{a.x},{a.y}',
      '-thread_queue_size','512','-f','pulse','-i',a.audio_monitor,'-map','0:v:0','-map','1:a:0',
      '-vf','pad=ceil(iw/2)*2:ceil(ih/2)*2','-c:v','libx264','-preset','veryfast','-crf','18',
      '-pix_fmt','yuv420p','-c:a','aac','-b:a','160k']
    value=dict(archive_root=str(root),output=str(out),arguments=arguments,microphone_capture=False,
       status='configuration_only_requires_target_sample',target_session='x11',
       course_audio_verified=False,window_verified=False,start_verified=False,two_x_verified=False)
    with cfg.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
    print(json.dumps({'config':str(cfg),'capture_started':False,'status':value['status']}))

if __name__=='__main__':main()
