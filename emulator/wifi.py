"""A pretend Wi-Fi for the firmware, with the PC's own internet behind it.

On the Q2 the firmware talks to a helper program, wpa_supplicant, which works the Wi-Fi chip: it asks it
which networks are in the air, to join one, and whether it is connected. There is no Wi-Fi chip here, so
this answers in wpa_supplicant's place, on the socket the firmware looks for it on: one network is in the
air, "Emulator Wi-Fi", open, always in reach. The connection itself is real: q2emu gives the box a network
interface named wlan0 that leads to the PC's internet (see Q2 in q2emu.py), so what the firmware downloads
over "Wi-Fi", it downloads.

The firmware's own wpa_supplicant and udhcpc are replaced by stand-ins while this is in use (q2emu.prepare):
they only have to exist, be started and be killed by name."""
import os, pathlib, re, socket, threading, time

SSID = 'Emulator Wi-Fi'
BSSID, MAC = '02:51:32:00:00:01', '02:51:32:00:00:02'
ADDRESS, DNS = '10.0.2.100', '10.0.2.3'     # what slirp4netns gives the box
CONF = 'mnt/data/wpa_supplicant.conf'       # the firmware's saved networks, as wpa_supplicant keeps them
SOCKET = 'var/run/wpa_supplicant/wlan0'
HEADER = 'ctrl_interface=/var/run/wpa_supplicant\nap_scan=1\nupdate_config=1\n'


def _inside(root, path):
    """A path of the box, as this side of the box reaches it (the rootfs's links are relative, or absolute
    ones meant for inside it)."""
    here = pathlib.Path(root)
    for part in pathlib.PurePosixPath(path).parts:
        if part in ('/', '.'): continue
        here = here / part
        for _ in range(8):
            if not here.is_symlink(): break
            target = os.readlink(here)
            here = (pathlib.Path(root) / target.lstrip('/')) if target.startswith('/') else (here.parent / target)
    return pathlib.Path(os.path.normpath(here))


class Wifi:
    """wpa_supplicant's control socket, answered. start() and stop() are all q2emu needs of it."""

    def __init__(self, root, log=None):
        self.root = pathlib.Path(root)
        self.log = log                    # a file for what the firmware asked: new requests are found, not guessed
        self.networks = {}                # id -> {'ssid': '"..."', 'key_mgmt': ..., 'disabled': '0' or '1'}
        self.current = None               # the id of the network joined
        self.listeners = set()            # the sockets that asked for events (ATTACH)
        self.sock = None
        self._stop = False
        self._read_conf()
        for i, net in self.networks.items():   # as wpa_supplicant does at its start: joins what it knows and finds
            if self._ours(net) and net.get('disabled') != '1':
                self.current = i
                break

    # -- the saved networks --

    def _ours(self, net):
        return net.get('ssid') in (f'"{SSID}"', SSID.encode().hex())

    def _read_conf(self):
        try: text = _inside(self.root, CONF).read_text(errors='replace')
        except OSError: return
        for i, block in enumerate(re.findall(r'network=\{(.*?)\}', text, re.S)):
            self.networks[i] = dict(re.findall(r'^\s*(\w+)=(.*?)\s*$', block, re.M))

    def _write_conf(self):
        path = _inside(self.root, CONF)
        try: head = path.read_text(errors='replace').split('network={')[0].strip() + '\n'
        except OSError: head = HEADER
        if 'update_config' not in head: head += 'update_config=1\n'
        blocks = ''.join('\nnetwork={\n' + ''.join(f'\t{k}={v}\n' for k, v in net.items()) + '}\n'
                         for net in self.networks.values())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(head + blocks)
        except OSError: pass

    # -- the socket --

    def start(self):
        path = _inside(self.root, SOCKET)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._bind()
        threading.Thread(target=self._serve, daemon=True).start()

    def _bind(self):
        if self.sock: self.sock.close()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.unlink(missing_ok=True)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(str(self._path))
        self.sock.settimeout(0.5)

    def stop(self):
        self._stop = True
        try: self.sock.close()
        except (OSError, AttributeError): pass
        try: self._path.unlink(missing_ok=True)
        except (OSError, AttributeError): pass
        try: self.log.close()
        except (OSError, AttributeError): pass

    def _serve(self):
        while not self._stop:
            try: data, sender = self.sock.recvfrom(4096)
            except socket.timeout:
                # the firmware clears /var/run/wpa_supplicant when it switches Wi-Fi off: be there again for the next on
                if not self._stop and not self._path.exists():
                    try: self._bind()
                    except OSError: pass
                continue
            except OSError:
                if self._stop: return
                time.sleep(0.2)
                continue
            if not sender: continue
            command = data.decode(errors='replace').strip()
            try: reply = self.answer(command, sender)
            except Exception as e:  # one odd request must not end the Wi-Fi
                reply = 'FAIL\n'
                self._note(f'!! {command!r}: {e}')
            self._note(f'{command}  ->  {reply.strip()[:120]!r}')
            self._send(sender, reply)

    def _send(self, to, text):
        """To a socket of the box, named as the box names it (/tmp/wpa_ctrl_PID-N)."""
        if isinstance(to, bytes): to = to.decode(errors='replace')
        try: self.sock.sendto(text.encode(), str(_inside(self.root, to)))
        except OSError: self.listeners.discard(to)

    def _event(self, text):
        for to in list(self.listeners): self._send(to, '<3>' + text)

    def _note(self, line):
        if self.log:
            try: self.log.write(f'{time.strftime("%H:%M:%S")} {line}\n'); self.log.flush()
            except (OSError, ValueError): pass

    # -- the answers --

    @property
    def connected(self):
        return self.current is not None

    def _join(self, i):
        net = self.networks.get(i)
        if net is None: return 'FAIL\n'
        net.pop('disabled', None)
        if self._ours(net):
            self.current = i
            self._event(f'CTRL-EVENT-CONNECTED - Connection to {BSSID} completed [id={i} id_str=]')
        else:
            self.current = None
            self._event('CTRL-EVENT-NETWORK-NOT-FOUND ')
        return 'OK\n'

    def _leave(self):
        if self.current is not None:
            self.current = None
            self._event(f'CTRL-EVENT-DISCONNECTED bssid={BSSID} reason=3 locally_generated=1')

    def answer(self, command, sender=None):
        word, _, rest = command.partition(' ')
        if word == 'PING': return 'PONG\n'
        if word == 'ATTACH': self.listeners.add(sender); return 'OK\n'
        if word == 'DETACH': self.listeners.discard(sender); return 'OK\n'
        if word in ('STATUS', 'STATUS-VERBOSE'):
            if not self.connected:
                return f'wpa_state={"SCANNING" if self.networks else "INACTIVE"}\naddress={MAC}\n'
            return (f'bssid={BSSID}\nfreq=2437\nssid={SSID}\nid={self.current}\nmode=station\npairwise_cipher=NONE\n'
                    f'group_cipher=NONE\nkey_mgmt=NONE\nwpa_state=COMPLETED\nip_address={ADDRESS}\naddress={MAC}\n')
        if word == 'SCAN':
            threading.Timer(0.6, self._event, ('CTRL-EVENT-SCAN-RESULTS ',)).start()
            return 'OK\n'
        if word == 'SCAN_RESULTS':
            return f'bssid / frequency / signal level / flags / ssid\n{BSSID}\t2437\t-42\t[ESS]\t{SSID}\n'
        if word == 'BSS':
            return f'bssid={BSSID}\nfreq=2437\nlevel=-42\nflags=[ESS]\nssid={SSID}\n' if rest.strip() in ('0', BSSID, 'FIRST') else ''
        if word == 'SIGNAL_POLL':
            return 'RSSI=-42\nLINKSPEED=72\nNOISE=9999\nFREQUENCY=2437\n' if self.connected else 'FAIL\n'
        if word == 'LIST_NETWORKS':
            rows = ''.join(f'{i}\t{net.get("ssid", "").strip(chr(34))}\tany\t'
                           f'{"[CURRENT]" if i == self.current else "[DISABLED]" if net.get("disabled") == "1" else ""}\n'
                           for i, net in self.networks.items())
            return 'network id / ssid / bssid / flags\n' + rows
        if word == 'ADD_NETWORK':
            i = max(self.networks, default=-1) + 1
            self.networks[i] = {'disabled': '1'}
            return f'{i}\n'
        if word == 'SET_NETWORK':
            i, key, value = (rest.split(' ', 2) + ['', ''])[:3]
            if not i.isdigit() or int(i) not in self.networks: return 'FAIL\n'
            self.networks[int(i)][key] = value
            return 'OK\n'
        if word == 'GET_NETWORK':
            i, key = (rest.split(' ', 1) + [''])[:2]
            value = self.networks.get(int(i), {}).get(key) if i.isdigit() else None
            return f'{value}\n' if value is not None else 'FAIL\n'
        if word in ('SELECT_NETWORK', 'ENABLE_NETWORK'):
            if rest.strip() == 'all':
                for i, net in self.networks.items():
                    if self._ours(net): return self._join(i)
                return 'OK\n'
            return self._join(int(rest)) if rest.strip().isdigit() else 'FAIL\n'
        if word in ('REASSOCIATE', 'RECONNECT', 'REATTACH'):
            for i, net in self.networks.items():
                if self._ours(net) and net.get('disabled') != '1': return self._join(i)
            return 'OK\n'
        if word == 'DISCONNECT': self._leave(); return 'OK\n'
        if word == 'DISABLE_NETWORK':
            ids = list(self.networks) if rest.strip() == 'all' else [int(rest)] if rest.strip().isdigit() else []
            for i in ids:
                if i in self.networks: self.networks[i]['disabled'] = '1'
                if i == self.current: self._leave()
            return 'OK\n'
        if word == 'REMOVE_NETWORK':
            ids = list(self.networks) if rest.strip() == 'all' else [int(rest)] if rest.strip().isdigit() else []
            for i in ids:
                if i == self.current: self._leave()
                self.networks.pop(i, None)
            return 'OK\n'
        if word == 'SAVE_CONFIG': self._write_conf(); return 'OK\n'
        if word == 'TERMINATE': self._leave(); return 'OK\n'
        if word in ('IFNAME',): return 'wlan0\n'
        if word in ('INTERFACES',): return 'wlan0\n'
        return 'OK\n'  # settings and the like (AP_SCAN, SET, AUTOSCAN, SCAN_INTERVAL...): accepted
