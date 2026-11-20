"""What the window does with the firmware at its start: none the first time, the last one by itself
afterwards, and a plain word if that file is gone."""
import pathlib, sys, tempfile
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'emulator'))
import emulator_gui as g
loads = []
g.FirmwareRunner.load = lambda self, fw, **k: (loads.append(str(fw)), setattr(self, 'firmware', pathlib.Path(fw)), True)[2]
t = pathlib.Path(tempfile.mkdtemp()); fw = t/'dev-test'/'update.tar'; fw.parent.mkdir(); fw.write_bytes(b'x')
gui = g.EmulatorGUI(work=t/'w'); s = str(gui._power_btn.cget('state')); txt = gui.status.cget('text')
print('PASS' if s == 'disabled' and not loads and 'Open firmware' in txt else 'FAIL', 'first start: no firmware, and it says to open one:', s, loads, '|', txt)
gui.runner.close(); gui.window.destroy()
g.Settings(t/'w').set('firmware', str(fw))
gui = g.EmulatorGUI(work=t/'w')
print('PASS' if loads == [str(fw)] else 'FAIL', 'later starts: the last firmware loads by itself:', loads)
gui.runner.close(); gui.window.destroy()
fw.unlink(); loads.clear()
gui = g.EmulatorGUI(work=t/'w'); txt = gui.status.cget('text')
print('PASS' if not loads and "isn't where it was" in txt and 'Firmware' in txt else 'FAIL', 'the last firmware is gone: nothing loads, and it says to open it again |', txt)
gui.runner.close(); gui.window.destroy()