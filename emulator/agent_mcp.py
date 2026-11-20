#!/usr/bin/env python3
"""The connector that lets an AI agent (Claude and others) use the emulator: an MCP server on stdin/stdout.

It talks to the agent door (agent_door.py). If an emulator window has its door open, the agent drives
that window, so a person can watch; otherwise this starts the emulator with no window by itself.

Registering it with an agent, on Windows with the packaged emulator:
  command: wsl.exe
  args:    -d Q2Emulator -u root -e python3 /opt/q2-emulator/emulator/agent_mcp.py
From a source checkout on Linux:  sudo python3 emulator/agent_mcp.py

No dependencies beyond Python: the MCP messages (JSON-RPC, one per line) are handled here."""
import base64
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
WORK = pathlib.Path(os.environ.get('Q2EMU_WORK') or pathlib.Path.home() / '.cache/q2emu')
SETTLE = 0.7  # after an input, before the screenshot that goes back with it

TOOLS = [
    ('status', 'Whether the player is idle, loading, running, off or stopped, which firmware is loaded, and '
               'whether its screen is up. Call this first.', {}, []),
    ('load_firmware', 'Load a firmware file (an update.tar, or a ZIP holding one) and start it. Takes about 10-20 '
                      'seconds: poll status until screen_up is true. The same file keeps its saved settings; '
                      'fresh=true starts it like the first time (language choice first).',
     {'path': {'type': 'string', 'description': 'The firmware file. A Windows path (C:\\...) is fine.'},
      'fresh': {'type': 'boolean', 'description': 'Erase the settings this firmware saved. Default false.'}}, ['path']),
    ('screenshot', 'The screen as a PNG, 375 wide by 320 high. Coordinates for tap and swipe are pixels of '
                   'this picture, (0,0) at the top left.',
     {'device': {'type': 'boolean', 'description': 'Show the whole player around the screen. Default false.'}}, []),
    ('tap', 'Touch the screen at a point. Returns the screen shortly after.',
     {'x': {'type': 'integer'}, 'y': {'type': 'integer'}}, ['x', 'y']),
    ('swipe', 'Drag a finger across the screen, e.g. to scroll a list. Returns the screen shortly after.',
     {'x1': {'type': 'integer'}, 'y1': {'type': 'integer'}, 'x2': {'type': 'integer'}, 'y2': {'type': 'integer'},
      'ms': {'type': 'integer', 'description': 'How long the drag takes. Default 300.'}}, ['x1', 'y1', 'x2', 'y2']),
    ('key', "Press one of the player's buttons: centre (select), return (back), play (play/pause), next, prev. "
            'Returns the screen shortly after.',
     {'name': {'type': 'string', 'enum': ['centre', 'return', 'play', 'next', 'prev']}}, ['name']),
    ('wheel', 'Turn the wheel by a number of clicks: positive moves down/next, negative up/previous. '
              'Returns the screen shortly after.', {'ticks': {'type': 'integer'}}, ['ticks']),
    ('wait', 'Let time pass (animations, loading), then return the screen.',
     {'seconds': {'type': 'number', 'description': 'Up to 30.'}}, ['seconds']),
    ('power', 'Switch the player off, or on again with the last firmware.', {'on': {'type': 'boolean'}}, ['on']),
    ('restart', 'Switch the player off and on again. Its settings are kept.', {}, []),
    ('steps', 'Everything done so far as a script (wait/tap/key/wheel/swipe lines) that replays it: the '
              'reproduction steps for a bug report.', {}, []),
    ('firmware_log', "The end of the firmware's own log, for when something stops or misbehaves.", {}, []),
]


class Door:
    """The agent door this connector uses: an open one if there is one, else a headless emulator of its own."""

    def __init__(self):
        self.proc = None
        self.url = self.token = None

    def _read(self):
        try:
            info = json.loads((WORK / 'agent.json').read_text())
            self.url, self.token = f'http://127.0.0.1:{info["port"]}', info['token']
            return True
        except (OSError, ValueError, KeyError):
            return False

    def _alive(self):
        try:
            self.call('GET', '/status', retry=False)
            return True
        except Exception:
            return False

    def ensure(self):
        if self.url and self._alive():
            return
        if self._read() and self._alive():
            return
        if self.proc is None or self.proc.poll() is not None:
            self.proc = subprocess.Popen([sys.executable, str(HERE / 'agent_door.py'), '--work', str(WORK)],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(100):
            time.sleep(0.1)
            if self.proc.poll() is not None:
                raise RuntimeError('the emulator could not start: ' + self.proc.stderr.read().decode(errors='replace').strip()[-400:])
            if self._read() and self._alive():
                return
        raise RuntimeError('the emulator did not answer in time')

    def call(self, method, path, body=None, retry=True):
        if retry:
            self.ensure()
        data = json.dumps(body).encode() if body is not None else (b'{}' if method == 'POST' else None)
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=60) as answer:
                raw, kind = answer.read(), answer.headers.get('Content-Type', '')
        except urllib.error.HTTPError as e:
            try:
                raise RuntimeError(json.loads(e.read()).get('error', str(e)))
            except ValueError:
                raise RuntimeError(str(e))
        return raw if kind.startswith('image/') else json.loads(raw)

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


door = Door()


def text(value):
    return {'type': 'text', 'text': value if isinstance(value, str) else json.dumps(value, indent=1)}


def picture(device=False):
    png = door.call('GET', '/screenshot' + ('?device=1' if device else ''))
    return {'type': 'image', 'data': base64.b64encode(png).decode(), 'mimeType': 'image/png'}


def after(result):
    """An input's answer: what was done, and the screen once it has settled."""
    time.sleep(SETTLE)
    return [text(result.get('did', 'done')), picture()]


def run_tool(name, a):
    if name == 'status':
        return [text({k: v for k, v in door.call('GET', '/status').items() if k != 'ok'})]
    if name == 'load_firmware':
        door.call('POST', '/load', {'firmware': a['path'], 'fresh': bool(a.get('fresh'))})
        return [text('Loading. Poll status until screen_up is true (about 10-20 seconds), then take a screenshot.')]
    if name == 'screenshot':
        return [picture(bool(a.get('device')))]
    if name == 'tap':
        return after(door.call('POST', '/tap', {'x': a['x'], 'y': a['y']}))
    if name == 'swipe':
        return after(door.call('POST', '/swipe', a))
    if name == 'key':
        return after(door.call('POST', '/key', {'name': a['name']}))
    if name == 'wheel':
        return after(door.call('POST', '/wheel', {'ticks': a['ticks']}))
    if name == 'wait':
        time.sleep(max(0, min(30, float(a.get('seconds', 1)))))
        return [picture()]
    if name == 'power':
        return [text(door.call('POST', '/power', {'on': bool(a.get('on', True))}))]
    if name == 'restart':
        return [text(door.call('POST', '/restart'))]
    if name == 'steps':
        return [text(door.call('GET', '/steps')['script'] or '(nothing done yet)')]
    if name == 'firmware_log':
        return [text(door.call('GET', '/log')['log'][-4000:] or '(empty)')]
    raise RuntimeError(f'unknown tool: {name}')


def handle(message):
    method, params = message.get('method'), message.get('params') or {}
    if method == 'initialize':
        return {'protocolVersion': params.get('protocolVersion', '2024-11-05'),
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'q2-emulator', 'version': '1.0'},
                'instructions': 'Drives a Shanling Q2 music player emulator running real firmware. Start with '
                                'status; load_firmware if nothing is loaded; then look with screenshot and act '
                                'with tap, key, wheel and swipe. Each action returns the screen afterwards.'}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': [{'name': n, 'description': d,
                           'inputSchema': {'type': 'object', 'properties': props, 'required': need}}
                          for n, d, props, need in TOOLS]}
    if method == 'tools/call':
        try:
            return {'content': run_tool(params.get('name'), params.get('arguments') or {})}
        except Exception as e:
            return {'content': [text(f'Error: {e}')], 'isError': True}
    raise LookupError(method)


def main():
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if 'id' not in message:  # a notification: nothing to answer
                continue
            try:
                reply = {'jsonrpc': '2.0', 'id': message['id'], 'result': handle(message)}
            except LookupError:
                reply = {'jsonrpc': '2.0', 'id': message['id'],
                         'error': {'code': -32601, 'message': f'method not found: {message.get("method")}'}}
            sys.stdout.write(json.dumps(reply) + '\n')
            sys.stdout.flush()
    finally:
        door.close()


if __name__ == '__main__':
    main()
