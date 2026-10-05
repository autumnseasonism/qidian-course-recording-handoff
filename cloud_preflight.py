"""Read-only cloud prerequisites; no software install, login or desktop modification."""
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

root=Path(__file__).resolve().parent
modules={n:importlib.util.find_spec(n) is not None for n in ('numpy','PIL','reportlab','pypdf','pptx','faster_whisper')}
tools={n:shutil.which(n) for n in ('ffmpeg','ffprobe','pdftoppm','soffice')}
versions={}
for n in ('ffmpeg','ffprobe'):
    if tools[n]:
        r=subprocess.run([tools[n],'-version'],capture_output=True,text=True,errors='replace',timeout=10)
        versions[n]={'exit':r.returncode,'first_line':r.stdout.splitlines()[0] if r.stdout else ''}
print(json.dumps({'os':platform.system(),'python':sys.version,'python_executable':sys.executable,
 'modules':modules,'tools':tools,'versions':versions,'bundle_root':str(root),
 'browser_login_verified':False,'desktop_capture_verified':False,'course_audio_isolation_verified':False,
 'microphone_excluded_verified':False,'two_x_verified':False,'recording_start_verified':False,
 'status':'requires_actual_browser_and_sample_validation','notes':['Windows capture arguments must be rebuilt on target',
 'Linux desktop/audio capture untested','Missing ASR/page tools does not necessarily block recording',
 'No installations or credentials loaded']},ensure_ascii=False,indent=2))
