#!/usr/bin/env python3
"""The agent door: lets a program (an AI agent, a test script) drive the emulator and see its screen.

A small HTTP server on this computer only (127.0.0.1), closed unless asked for, and needing a token.
The window opens it from More > "AI agent control"; without a window:

  python3 emulator/agent_door.py                 # headless: the emulator with no window, door open

Where it listens and its token are written to <work>/agent.json for the connector (agent_mcp.py).

  GET  /status                         state, message, firmware, whether the screen is up (screen_up: the
                                       firmware has drawn its first picture) or off (screen_off: its idle
                                       timer switched it off; the wheel then sets the volume and touches
                                       are ignored until the key "centre", the Q2's power key, wakes it)
  POST /load     {"firmware": path, "fresh": false}      a Windows path (C:\\...) is fine under WSL
  POST /power    {"on": true|false}
  POST /restart
  POST /tap      {"x": 0-374, "y": 0-319}
  POST /swipe    {"x1","y1","x2","y2","ms": 300}
  POST /key      {"name": centre|return|play|next|prev}
  POST /wheel    {"ticks": n}           positive is down/next
  GET  /screenshot[?device=1]           PNG of the screen (375x320), or of the whole player
  GET  /steps                           what was done so far, as a q2emu --script (replayable)
  GET  /log                             the end of the firmware's own log

Every request carries the token: header "Authorization: Bearer <token>" or "?token=<token>".
Answers are JSON ({"ok": true, ...} or {"ok": false, "error": ...}), except /screenshot."""
import io
import json
import os
import pathlib
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

KEYS = ('centre', 'return', 'play', 'next', 'prev')
SCREEN = (375, 320)


def linux_path(path):
    """A Windows path (C:\\Users\\...) as Linux names it under WSL; anything else unchanged."""
    path = str(path)
    if len(path) > 2 and path[1] == ':' and path[2] in '\\/':
        try:
            out = subprocess.run(['wslpath', '-u', path], capture_output=True, text=True, timeout=5).stdout.strip()
            return out or path
        except (OSError, subprocess.SubprocessError):
            pass
    return path


class Door:
    """The server, around a FirmwareRunner. on_input(kind, data), if given, is told of each input (the
    window lights up the touch or the button); player(screen) draws the whole player for ?device=1."""

    def __init__(self, runner, on_input=None, player=None, port=0):
        self.runner, self.on_input, self.player = runner, on_input, player
        self.token = secrets.token_urlsafe(16)
        self.steps, self._step_at = [], None
        door = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self, code, body, kind='application/json'):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method):
                url = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                given = self.headers.get('Authorization', '').removeprefix('Bearer ').strip() or query.get('token', '')
                if not secrets.compare_digest(given, door.token):
                    return self._answer(401, {'ok': False, 'error': 'missing or wrong token'})
                body = {}
                if method == 'POST':
                    raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                    try:
                        body = json.loads(raw) if raw.strip() else {}
                    except ValueError:
                        return self._answer(400, {'ok': False, 'error': 'the request body is not JSON'})
                try:
                    result = door.do(method, url.path, {**query, **body})
                except Refused as e:
                    return self._answer(e.code, {'ok': False, 'error': str(e)})
                except Exception as e:  # a bad request must not take the door down
                    return self._answer(500, {'ok': False, 'error': f'{type(e).__name__}: {e}'})
                if isinstance(result, bytes):
                    return self._answer(200, result, 'image/png')
                self._answer(200, {'ok': True, **result})

            def do_GET(self):
                self._handle('GET')

            def do_POST(self):
                self._handle('POST')

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)  # this computer only
        self.port = self.server.server_address[1]
        self.file = runner.work / 'agent.json'
        runner.work.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps({'port': self.port, 'token': self.token, 'pid': os.getpid()}))
        self.file.chmod(0o600)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        try:
            if json.loads(self.file.read_text()).get('pid') == os.getpid():
                self.file.unlink()
        except (OSError, ValueError):
            pass

    # -- What a request does --

    def status(self):
        r = self.runner
        r.poll()
        up = False
        if r.state == 'running':
            frame = r.frame()
            up = frame is not None and frame.getbbox() is not None and r.started()
        return {'state': r.state, 'message': r.message, 'hint': r.hint, 'screen_up': up, 'screen_off': r.screen_off(),
                'firmware': str(r.firmware) if r.firmware else None, 'loading': r.loading,
                'screen_size': list(SCREEN), 'keys': list(KEYS)}

    def _step(self, text):
        now = time.time()
        if self._step_at is not None and now - self._step_at >= 0.1:
            self.steps.append(f'wait {now - self._step_at:.1f}')
        self.steps.append(text)
        self._step_at = now

    def _input(self, kind, data, action, step):
        if self.runner.state != 'running':
            raise Refused(409, f'the player is not running (state: {self.runner.state}); load a firmware or power on first')
        self._step(step)
        if self.on_input:
            self.on_input(kind, data)
        self.runner.send(action)
        return {'did': step}

    def do(self, method, path, a):
        r = self.runner
        if path == '/status':
            return self.status()
        if path == '/screenshot':
            frame = r.frame() if r.state == 'running' else None
            if frame is None:
                raise Refused(409, f'there is no screen to show (state: {r.state}; {r.message})')
            if str(a.get('device', '')).lower() in ('1', 'true', 'yes') and self.player:
                frame = self.player(frame)
            out = io.BytesIO()
            frame.save(out, 'PNG')
            return out.getvalue()
        if path == '/steps':
            return {'script': ';\n'.join(self.steps)}
        if path == '/log':
            try:
                with open(r.work / 'demo.log', 'rb') as f:
                    f.seek(0, 2)
                    f.seek(max(0, f.tell() - 6000))
                    text = f.read().decode('utf-8', 'replace')
            except OSError:
                text = ''
            return {'log': text}
        if method != 'POST':
            raise Refused(404, f'no such page: {path}')
        if path == '/load':
            firmware = a.get('firmware') or (str(r.firmware) if r.firmware else None)
            if not firmware:
                raise Refused(400, 'say which firmware: {"firmware": "path/to/update.tar"}')
            firmware = pathlib.Path(linux_path(firmware))
            if not firmware.is_file():
                raise Refused(400, f'no such file: {firmware}')
            if not r.load(firmware, fresh=bool(a.get('fresh'))):
                raise Refused(409, 'a firmware is already being loaded; wait for it')
            self.steps, self._step_at = [], None
            return {'loading': str(firmware)}
        if path == '/power':
            if a.get('on', True):
                if r.state in ('running', 'loading'):
                    return {'state': r.state}
                if not r.firmware:
                    raise Refused(409, 'no firmware has been loaded yet; use /load')
                r.load(r.firmware)
                return {'loading': str(r.firmware)}
            if not r.power_off():
                raise Refused(409, 'a firmware is being loaded; wait for it')
            return {'state': r.state}
        if path == '/restart':
            if r.state != 'running':
                raise Refused(409, f'the player is not running (state: {r.state})')
            r.load(r.firmware)
            return {'loading': str(r.firmware)}
        if path == '/tap':
            x, y = self._point(a, 'x', 'y')
            return self._input('touch', (x, y), lambda q2: q2.tap(x, y), f'tap {x} {y}')
        if path == '/swipe':
            x1, y1 = self._point(a, 'x1', 'y1')
            x2, y2 = self._point(a, 'x2', 'y2')
            ms = max(50, min(5000, int(a.get('ms', 300))))
            return self._input('touch', (x2, y2), lambda q2: q2.swipe(x1, y1, x2, y2, ms), f'swipe {x1} {y1} {x2} {y2} {ms}')
        if path == '/key':
            name = {'center': 'centre', 'back': 'return'}.get(a.get('name'), a.get('name'))  # either spelling
            if name not in KEYS:
                raise Refused(400, f'key name must be one of: {", ".join(KEYS)}')
            return self._input('key', name, lambda q2: q2.key(name), f'key {name}')
        if path == '/wheel':
            ticks = int(a.get('ticks', 0))
            if not ticks or abs(ticks) > 50:
                raise Refused(400, 'ticks must be a whole number from -50 to 50, not 0 (positive is down/next)')
            return self._input('wheel', ticks, lambda q2: q2.wheel(ticks), f'wheel {ticks}')
        raise Refused(404, f'no such page: {path}')

    @staticmethod
    def _point(a, kx, ky):
        try:
            x, y = int(a[kx]), int(a[ky])
        except (KeyError, TypeError, ValueError):
            raise Refused(400, f'give {kx} and {ky} as whole numbers')
        if not (0 <= x < SCREEN[0] and 0 <= y < SCREEN[1]):
            raise Refused(400, f'({x}, {y}) is off the screen: x is 0-{SCREEN[0] - 1}, y is 0-{SCREEN[1] - 1}')
        return x, y


class Refused(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


def main():
    """Headless: the emulator with no window and the door open, until interrupted."""
    import argparse
    import signal
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from emulator_gui import FirmwareRunner, player
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--work', type=pathlib.Path, default=pathlib.Path.home() / '.cache/q2emu')
    ap.add_argument('--port', type=int, default=0, help='0 (default) picks a free one')
    ap.add_argument('--firmware', type=pathlib.Path, help='load this firmware straight away')
    a = ap.parse_args()
    if os.geteuid():
        sys.exit('q2emu needs root: chroot, mounts and binfmt_misc')
    runner = FirmwareRunner(a.work)
    door = Door(runner, port=a.port, player=lambda screen: player(screen, 'Orange', backdrop=None))
    print(f'agent door open: http://127.0.0.1:{door.port}  (token in {door.file})', flush=True)
    if a.firmware:
        runner.load(a.firmware)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.wait(0.5):
        runner.poll()
    door.close()
    runner.close()


if __name__ == '__main__':
    main()
