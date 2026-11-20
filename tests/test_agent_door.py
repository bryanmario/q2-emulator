"""The agent door and its MCP connector, without root: a stand-in firmware behind a real door, real HTTP,
and the connector run as a separate program speaking MCP over its stdin/stdout."""
import base64, io, json, os, pathlib, subprocess, sys, tempfile, time, urllib.error, urllib.request
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'emulator'))
FIX = __import__('pathlib').Path(__file__).resolve().parent / 'fixtures'
import agent_door, emulator_gui as g
from PIL import Image

ok = lambda c, what: print(('PASS ' if c else 'FAIL ') + what)
tmp = pathlib.Path(tempfile.mkdtemp())
HOME = Image.open(FIX/'screen-home.png').convert('RGB').resize(g.SCREEN_SIZE)
OTHER = Image.open(FIX/'screen-other.png').convert('RGB').resize(g.SCREEN_SIZE)

class FakeProc:
    returncode = None
    def poll(s): return s.returncode
class FakeQ2:
    started = lambda s: True; screen_off = lambda s: getattr(s, 'off', False)
    def __init__(s, r, l): s.proc = FakeProc(); s.got = []; s.pic = HOME
    def stop(s): s.proc.returncode = -9
    def frame(s): return s.pic
    def key(s, n): s.got.append(('key', n))
    def wheel(s, t): s.got.append(('wheel', t))
    def tap(s, x, y): s.got.append(('tap', x, y)); s.pic = OTHER if s.pic is HOME else HOME
    def swipe(s, *a): s.got.append(('swipe', *a))
g.unpack = lambda fw, r: ((r/'release/bin').mkdir(parents=True), (r/'mnt/data').mkdir(parents=True))
g._patch_serial_check = lambda p: None; g.build_shim = lambda r, w: None; g.prepare = lambda *a, **k: None; g.Q2 = FakeQ2
fw = tmp/'dev-test'/'update.tar'; fw.parent.mkdir(); fw.write_bytes(b'firmware')

runner = g.FirmwareRunner(tmp/'work')
seen = []
door = agent_door.Door(runner, on_input=lambda kind, data: seen.append((kind, data)),
                       player=lambda screen: g.player(screen, 'Orange', backdrop=None))
base = f'http://127.0.0.1:{door.port}'

def call(method, path, body=None, token=door.token):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else (b'{}' if method == 'POST' else None),
                                 method=method, headers={'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (raw if r.headers['Content-Type'].startswith('image/') else json.loads(raw))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

# -- the door over HTTP --
info = json.loads((tmp/'work/agent.json').read_text())
ok(info['port'] == door.port and info['token'] == door.token and oct((tmp/'work/agent.json').stat().st_mode)[-3:] == '600',
   'agent.json names the port and token, readable by its owner only')
ok(door.server.server_address[0] == '127.0.0.1', 'the door listens on this computer only')
ok(call('GET', '/status', token='wrong')[0] == 401 and call('POST', '/tap', {'x': 1, 'y': 1}, token='')[0] == 401, 'no token, no entry')
code, st = call('GET', '/status')
ok(code == 200 and st['state'] == 'idle' and st['screen_size'] == [375, 320], f'status when idle: {st["state"]}')
ok(call('POST', '/tap', {'x': 10, 'y': 10})[0] == 409 and call('GET', '/screenshot')[0] == 409, 'input and screenshot are refused while nothing runs')
ok(call('POST', '/load', {'firmware': str(tmp/'nope.tar')})[0] == 400, 'a firmware file that is not there is refused')
code, r = call('POST', '/load', {'firmware': str(fw)})
for _ in range(100):
    if call('GET', '/status')[1]['screen_up']: break
    time.sleep(0.05)
st = call('GET', '/status')[1]
ok(code == 200 and st['state'] == 'running' and st['screen_up'] and st['firmware'] == str(fw), 'load starts the firmware; status says running, screen up')
code, png = call('GET', '/screenshot')
shot = Image.open(io.BytesIO(png))
ok(code == 200 and shot.size == (375, 320), f'screenshot is the 375x320 screen: {shot.size}')
code, png = call('GET', '/screenshot?device=1')
ok(Image.open(io.BytesIO(png)).size == g.DEVICE_SIZE, 'screenshot?device=1 shows the whole player')
ok(call('POST', '/tap', {'x': 500, 'y': 10})[0] == 400 and call('POST', '/key', {'name': 'volume'})[0] == 400
   and call('POST', '/wheel', {'ticks': 0})[0] == 400, 'off-screen taps, unknown keys and a zero wheel turn are refused, with a reason')
call('POST', '/tap', {'x': 80, 'y': 290}); time.sleep(0.3)
call('POST', '/key', {'name': 'return'}); call('POST', '/wheel', {'ticks': -2})
call('POST', '/swipe', {'x1': 100, 'y1': 250, 'x2': 100, 'y2': 80}); time.sleep(0.3)
ok(runner.q2.got == [('tap', 80, 290), ('key', 'return'), ('wheel', -2), ('swipe', 100, 250, 100, 80, 300)], f'inputs reach the firmware in order: {runner.q2.got}')
ok(seen[0] == ('touch', (80, 290)) and ('key', 'return') in seen, 'the window is told of each input (to light it up)')
script = call('GET', '/steps')[1]['script']
ok('tap 80 290' in script and 'key return' in script and 'wheel -2' in script and 'swipe 100 250 100 80 300' in script and 'wait' in script,
   'steps come back as a replayable script: ' + script.replace('\n', ' '))
ok(call('POST', '/power', {'on': False})[1]['state'] == 'off' and call('GET', '/status')[1]['state'] == 'off', 'power off')
call('POST', '/power', {'on': True})
for _ in range(100):
    if call('GET', '/status')[1]['screen_up']: break
    time.sleep(0.05)
ok(call('GET', '/status')[1]['state'] == 'running', 'power on starts the last firmware again')

# -- the connector, as its own program speaking MCP --
mcp = subprocess.Popen([sys.executable, '-I', str(pathlib.Path(agent_door.__file__).parent/'agent_mcp.py')],
                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=dict(os.environ, Q2EMU_WORK=str(tmp/'work')))
n = [0]
def rpc(method, params=None):
    n[0] += 1
    mcp.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': n[0], 'method': method, 'params': params or {}}) + '\n'); mcp.stdin.flush()
    return json.loads(mcp.stdout.readline())
def tool(name, **args):
    return rpc('tools/call', {'name': name, 'arguments': args})['result']
init = rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '0'}})
ok(init['result']['serverInfo']['name'] == 'q2-emulator' and 'tools' in init['result']['capabilities'], 'connector answers initialize')
mcp.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n'); mcp.stdin.flush()
tools = [t['name'] for t in rpc('tools/list')['result']['tools']]
ok({'status', 'load_firmware', 'screenshot', 'tap', 'key', 'wheel', 'swipe', 'wait', 'steps'} <= set(tools), f'tools offered: {", ".join(tools)}')
st = json.loads(tool('status')['content'][0]['text'])
ok(st['state'] == 'running' and st['screen_up'], 'connector finds the open door and reads the status')
before = runner.q2.pic
res = tool('tap', x=80, y=290)
img = [c for c in res['content'] if c['type'] == 'image'][0]
after = Image.open(io.BytesIO(base64.b64decode(img['data'])))
ok(runner.q2.got[-1] == ('tap', 80, 290) and after.size == (375, 320) and img['mimeType'] == 'image/png', 'tap through the connector returns the screen afterwards')
ok(after.tobytes() != before.tobytes(), 'and that screen is the new one')
bad = tool('tap', x=900, y=5)
ok(bad.get('isError') and 'off the screen' in bad['content'][0]['text'], 'a bad request comes back as a plain error the agent can read')
ok('tap 80 290' in tool('steps')['content'][0]['text'], 'steps through the connector')
ok(rpc('no/such')['error']['code'] == -32601, 'an unknown method is answered, not ignored')
mcp.stdin.close(); mcp.wait(timeout=10)
ok(runner.state == 'running', 'the connector leaving does not stop a door it did not open')

door.close(); runner.close()
ok(not (tmp/'work/agent.json').exists(), 'closing the door removes agent.json')
try: urllib.request.urlopen(base + '/status', timeout=2); ok(False, 'the door is closed')
except Exception: ok(True, 'the door is closed')
