"""Run one configured local capture. UI and audio isolation remain caller checks."""
import argparse
import datetime as dt
import json
from pathlib import Path
import subprocess
import time


def save_state(path, state):
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    pending.replace(path)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config')
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    root, output = Path(config['archive_root']).resolve(), Path(config['output']).resolve()
    if root not in output.parents or not root.is_dir() or not output.parent.is_dir():
        raise ValueError('Output must be inside the explicit existing authorized archive')
    if config.get('microphone_capture') is not False:
        raise ValueError('Configuration must explicitly exclude microphone capture')
    arguments = config['arguments']
    if not isinstance(arguments, list) or not arguments or not all(isinstance(a, str) for a in arguments):
        raise ValueError('Expected an executable and individual argument strings')
    state_path, log_path, stop_path = [output.with_suffix(s) for s in
                                      ('.录制状态.json', '.录制日志.txt', '.停止录制')]
    if any(p.exists() for p in (output, state_path, log_path, stop_path)):
        raise FileExistsError('Use a new recording name; prior evidence is immutable')
    state = dict(output=str(output), config=str(config_path), started_utc=now(),
                 status='starting', microphone_capture=False)
    save_state(state_path, state)
    child = None
    try:
        with log_path.open('x', encoding='utf-8') as log:
            child = subprocess.Popen(arguments + [str(output)], stdin=subprocess.PIPE,
                                     stdout=log, stderr=log,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            state.update(pid=child.pid, status='recording')
            save_state(state_path, state)
            while child.poll() is None:
                if not stop_path.exists():
                    time.sleep(0.5)
                    continue
                state['stop_requested_utc'] = now()
                try:
                    child.communicate(input=b'q', timeout=30)
                    state['stop_method'] = 'q'
                except subprocess.TimeoutExpired:
                    state.update(failure_reason='stop_timeout',
                                 stop_method='owned_child_killed_after_30s')
                    child.kill()
                    child.communicate(timeout=10)
                break
            state['exit_code'] = child.returncode
            state['status'] = ('saved_requires_verification'
                               if child.returncode == 0 and not state.get('failure_reason') else 'failed')
    except Exception as error:
        state.update(status='failed', failure_reason=state.get('failure_reason', 'runner_exception'),
                     error_type=type(error).__name__)
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        state['exit_code'] = child.returncode if child is not None else None
    finally:
        state['ended_utc'] = now()
        state['output_bytes'] = output.stat().st_size if output.exists() else 0
        save_state(state_path, state)
    print(json.dumps(state, ensure_ascii=False), flush=True)
    return 0 if state['status'] == 'saved_requires_verification' else 1


if __name__ == '__main__':
    raise SystemExit(main())
