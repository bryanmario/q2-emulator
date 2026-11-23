// Q2Emulator.exe: the double-click that starts the emulator on Windows.
//
// The emulator itself is a Linux program. This launcher hides that: it keeps a small private Linux
// (a WSL distribution named Q2Emulator, made from data\linux.tar.gz the first time) with the emulator
// inside it (copied from data\app whenever data\version.txt changes), and starts it there as root, so
// nobody is asked for a password or sees a terminal. A firmware file dropped on the .exe is opened.
//
// The download is this one file: the data folder is packed inside it (build.ps1 embeds linux.tar.gz,
// app.zip and version.txt) and is unpacked beside the .exe the first time, or after an update; where
// that folder cannot be written, into the user's own application data instead.
//
// Compiled by packaging\build.ps1 with the C# compiler every Windows has (.NET Framework 4, so C# 5).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

static class Launcher
{
    const string Distro = "Q2Emulator";
    const string Title = "Q2 Emulator";
    static string Data, Home;
    static Form splash;
    static Label line;

    [STAThread]
    static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Data = Path.Combine(Path.GetDirectoryName(Application.ExecutablePath), "data");
        Home = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Q2Emulator");
        if (args.Length > 0 && args[0] == "--uninstall") return Uninstall();
        string firmware = args.Length > 0 && File.Exists(args[0]) ? Path.GetFullPath(args[0]) : null;

        MakeSplash();
        int code = 1;
        Thread work = new Thread(delegate()
        {
            try { code = Run(firmware); }
            catch (Exception e) { CloseSplash(); Tell("Q2 Emulator could not start.\n\n" + e.Message, MessageBoxIcon.Error); }
            finally { CloseSplash(); }
        });
        work.SetApartmentState(ApartmentState.STA);
        splash.Shown += delegate { work.Start(); };
        Application.Run(splash);
        work.Join();
        return code;
    }

    // -- The steps --

    static int Run(string firmware)
    {
        if (!Unpack())
        {
            CloseSplash();
            Tell("Q2 Emulator could not unpack its files.\n\nMove Q2Emulator.exe to a folder of your own "
                 + "(Documents, for example) and open it again.", MessageBoxIcon.Error);
            return 2;
        }
        Say("Checking Windows...");
        if (!HasWsl())
        {
            CloseSplash();
            OfferWslSetup();
            return 3;
        }
        bool fresh = false;
        if (!HasDistro())
        {
            Say("Setting up (first time only, about a minute)...");
            string dir = Path.Combine(Home, "linux");
            Directory.CreateDirectory(dir);
            string output;
            int result = Wsl("--import " + Distro + " " + Quote(dir) + " " + Quote(Path.Combine(Data, "linux.tar.gz")) + " --version 2",
                             null, out output);
            if (result != 0)
            {
                CloseSplash();
                Tell(ExplainSetupFailure(output), MessageBoxIcon.Error);
                return 4;
            }
            fresh = true;
        }

        string wanted = File.Exists(Path.Combine(Data, "version.txt")) ? File.ReadAllText(Path.Combine(Data, "version.txt")).Trim() : "";
        string marker = Path.Combine(Home, "app-version.txt");
        string have = File.Exists(marker) ? File.ReadAllText(marker).Trim() : null;
        if (fresh || have != wanted)
        {
            Say(have == null ? "Installing the emulator..." : "Updating the emulator...");
            Dictionary<string, string> env = new Dictionary<string, string>();
            env["Q2APP"] = Path.Combine(Data, "app");
            string output;
            // install.sh (in the app): the copy into /opt/q2-emulator, and the drive lock (the PC's drives
            // read-only in the box). It answers 3 when the drives are still mounted writable, from before
            // the lock: this Linux is restarted so that the emulator only ever starts with them locked.
            int result = Wsl("-d " + Distro + " -u root -e sh -c \"exec sh \\\"$Q2APP/install.sh\\\" \\\"$Q2APP\\\"\"",
                             env, out output);
            if (result == 3)
            {
                string ignored;
                result = Wsl("--terminate " + Distro, null, out ignored);
            }
            if (result != 0)
            {
                CloseSplash();
                Tell("Q2 Emulator could not be installed into its private Linux.\n\n" + Tail(output), MessageBoxIcon.Error);
                return 5;
            }
            Directory.CreateDirectory(Home);
            File.WriteAllText(marker, wanted);
        }

        Say("Starting Q2 Emulator...");
        ProcessStartInfo start = new ProcessStartInfo("wsl.exe",
            "-d " + Distro + " -u root --cd /opt/q2-emulator -e sh -c "
            + "\"exec python3 emulator/emulator_gui.py ${Q2FW:+--firmware \\\"$Q2FW\\\"}\"");
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.RedirectStandardError = true;
        if (firmware != null) AddEnv(start, "Q2FW", firmware);
        AddEnv(start, "Q2EXE", Application.ExecutablePath);  // for More > "Remove Q2 Emulator from this PC"
        Process emulator = Process.Start(start);
        StringBuilder errors = new StringBuilder();
        Thread drain = new Thread(delegate() { errors.Append(Decode(ReadAll(emulator.StandardError.BaseStream))); });
        drain.Start();

        // The starting window stays until the emulator's own is on screen (half a minute at most), so the
        // one follows the other without a gap; it has no taskbar button, so the taskbar only ever shows one.
        for (int i = 0; i < 200 && !emulator.WaitForExit(150) && !WindowUp(); i++) { }
        CloseSplash();
        emulator.WaitForExit();
        drain.Join(2000);
        CloseSplash();
        if (emulator.ExitCode != 0 && HasDistro())  // (no private Linux any more: it was uninstalled from its own menu)
        {
            string text = errors.ToString();
            if (text.Contains("no display name") || text.Contains("couldn't connect to display"))
                Tell("Q2 Emulator could not open its window.\n\nIt needs Windows 11, or Windows 10 with the current "
                     + "version of WSL (run \"wsl --update\" once).", MessageBoxIcon.Error);
            else
                Tell("Q2 Emulator stopped unexpectedly.\n\n" + Tail(text), MessageBoxIcon.Error);
        }
        return emulator.ExitCode;
    }

    // Uninstall.cmd: take the private Linux off this PC again.
    static int Uninstall()
    {
        if (!HasDistro() && !Directory.Exists(Home))
        {
            Tell("Q2 Emulator has nothing installed on this PC. You can simply delete its folder.", MessageBoxIcon.Information);
            return 0;
        }
        DialogResult answer = MessageBox.Show(
            "Remove Q2 Emulator's private Linux from this PC?\n\nThe firmware it had loaded and that firmware's "
            + "settings go with it. Your firmware files, music, screenshots and videos are not touched.",
            Title, MessageBoxButtons.YesNo, MessageBoxIcon.Question);
        if (answer != DialogResult.Yes) return 1;
        string output;
        if (HasDistro() && Wsl("--unregister " + Distro, null, out output) != 0)
        {
            Tell("It could not be removed. Close Q2 Emulator first, then try again.\n\n" + Tail(output), MessageBoxIcon.Error);
            return 6;
        }
        try { if (Directory.Exists(Home)) Directory.Delete(Home, true); } catch (Exception) { }
        try { if (Embedded("version.txt") != null && Directory.Exists(Data)) Directory.Delete(Data, true); } catch (Exception) { }
        Tell("Removed. To finish, delete Q2Emulator.exe.", MessageBoxIcon.Information);
        return 0;
    }

    // -- The data folder, packed inside this file --

    static Stream Embedded(string name)
    {
        return Assembly.GetExecutingAssembly().GetManifestResourceStream(name);
    }

    // Make sure Data holds this build's files, unpacking them if need be. Without anything packed inside
    // (a build that ships its data folder beside it), the folder only has to be there.
    static bool Unpack()
    {
        string wanted;
        using (Stream v = Embedded("version.txt"))
        {
            if (v == null)
                return File.Exists(Path.Combine(Data, "linux.tar.gz")) && Directory.Exists(Path.Combine(Data, "app"));
            wanted = new StreamReader(v).ReadToEnd().Trim();
        }
        foreach (string place in new string[] { Data, Path.Combine(Home, "data") })
        {
            Data = place;
            if (Holds(wanted)) return true;
        }
        Say("Unpacking (first time only)...");
        foreach (string place in new string[] { Path.Combine(Path.GetDirectoryName(Application.ExecutablePath), "data"),
                                                Path.Combine(Home, "data") })
        {
            Data = place;
            try
            {
                Directory.CreateDirectory(Data);
                string version = Path.Combine(Data, "version.txt"), app = Path.Combine(Data, "app");
                if (File.Exists(version)) File.Delete(version);   // written last: a half-unpacked folder is unpacked again
                if (Directory.Exists(app)) Directory.Delete(app, true);
                using (Stream from = Embedded("linux.tar.gz"))
                using (FileStream to = File.Create(Path.Combine(Data, "linux.tar.gz")))
                    from.CopyTo(to);
                using (ZipArchive zip = new ZipArchive(Embedded("app.zip"), ZipArchiveMode.Read))
                    zip.ExtractToDirectory(app);
                File.WriteAllText(Path.Combine(Data, "Uninstall.cmd"),
                                  "@start \"\" \"" + Application.ExecutablePath + "\" --uninstall\r\n");
                File.WriteAllText(version, wanted);
                return true;
            }
            catch (UnauthorizedAccessException) { }   // a folder that cannot be written (Program Files, a read-only disk)
            catch (IOException) { }
        }
        return false;
    }

    static bool Holds(string wanted)
    {
        string version = Path.Combine(Data, "version.txt");
        return File.Exists(version) && File.ReadAllText(version).Trim() == wanted
            && File.Exists(Path.Combine(Data, "linux.tar.gz")) && Directory.Exists(Path.Combine(Data, "app"));
    }

    static bool HasWsl()
    {
        try { string o; return Wsl("--version", null, out o) == 0; }
        catch (Exception) { return false; }
    }

    static bool HasDistro()
    {
        using (RegistryKey lxss = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Lxss"))
        {
            if (lxss == null) return false;
            foreach (string name in lxss.GetSubKeyNames())
                using (RegistryKey key = lxss.OpenSubKey(name))
                    if (key != null && Distro.Equals(key.GetValue("DistributionName") as string, StringComparison.OrdinalIgnoreCase))
                        return true;
        }
        return false;
    }

    static void OfferWslSetup()
    {
        DialogResult answer = MessageBox.Show(
            "Q2 Emulator runs on a part of Windows called \"Windows Subsystem for Linux\", which is not turned on "
            + "on this PC yet.\n\nTurn it on now? Windows will ask for permission. When it finishes, restart your PC "
            + "and open Q2 Emulator again.", Title, MessageBoxButtons.YesNo, MessageBoxIcon.Information);
        if (answer != DialogResult.Yes) return;
        try
        {
            ProcessStartInfo install = new ProcessStartInfo("wsl.exe", "--install --no-distribution");
            install.Verb = "runas";
            install.UseShellExecute = true;
            Process p = Process.Start(install);
            p.WaitForExit();
            Tell("Almost done. Restart your PC, then open Q2 Emulator again.", MessageBoxIcon.Information);
        }
        catch (Exception)
        {
            Tell("The setup was cancelled or could not run.\n\nYou can also do it by hand: open the Start menu, type "
                 + "PowerShell, choose \"Run as administrator\", and run:  wsl --install --no-distribution", MessageBoxIcon.Warning);
        }
    }

    static string ExplainSetupFailure(string output)
    {
        string lower = output.ToLowerInvariant();
        if (lower.Contains("0x80370102") || lower.Contains("hyperv") || lower.Contains("virtualization") || lower.Contains("virtual machine platform"))
            return "Q2 Emulator needs a setting called \"virtualization\" that is switched off on this PC.\n\n"
                 + "It is turned on in the PC's startup settings (BIOS/UEFI), usually under the name Intel VT-x, "
                 + "Intel Virtualization Technology, AMD-V or SVM Mode. Microsoft's step-by-step guide:\n"
                 + "https://aka.ms/enablevirtualization\n\nThen open Q2 Emulator again.";
        return "Q2 Emulator could not set up its private Linux.\n\n" + Tail(output);
    }

    // -- Running wsl.exe --

    static int Wsl(string arguments, Dictionary<string, string> env, out string output)
    {
        ProcessStartInfo start = new ProcessStartInfo("wsl.exe", arguments);
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.RedirectStandardOutput = true;
        start.RedirectStandardError = true;
        if (env != null) foreach (KeyValuePair<string, string> pair in env) AddEnv(start, pair.Key, pair.Value);
        Process p = Process.Start(start);
        byte[] err = null;
        Thread drain = new Thread(delegate() { err = ReadAll(p.StandardError.BaseStream); });
        drain.Start();
        byte[] std = ReadAll(p.StandardOutput.BaseStream);
        p.WaitForExit();
        drain.Join();
        output = (Decode(std) + "\n" + Decode(err)).Trim();
        return p.ExitCode;
    }

    // A Windows path handed to Linux as an environment variable: WSL turns it into the Linux path ("/p").
    static void AddEnv(ProcessStartInfo start, string name, string windowsPath)
    {
        start.EnvironmentVariables[name] = windowsPath;
        string list = start.EnvironmentVariables["WSLENV"];
        start.EnvironmentVariables["WSLENV"] = (string.IsNullOrEmpty(list) ? "" : list + ":") + name + "/p";
    }

    static byte[] ReadAll(Stream stream)
    {
        using (MemoryStream all = new MemoryStream())
        {
            stream.CopyTo(all);
            return all.ToArray();
        }
    }

    // wsl.exe's own messages are UTF-16; what Linux programs print is UTF-8.
    static string Decode(byte[] bytes)
    {
        if (bytes == null || bytes.Length == 0) return "";
        int zeros = 0;
        for (int i = 1; i < bytes.Length; i += 2) if (bytes[i] == 0) zeros++;
        return (zeros > bytes.Length / 4 ? Encoding.Unicode : Encoding.UTF8).GetString(bytes).Replace("\0", "");
    }

    static string Quote(string path) { return "\"" + path + "\""; }

    static string Tail(string text)
    {
        string[] lines = text.Trim().Split('\n');
        int from = Math.Max(0, lines.Length - 12);
        return string.Join("\n", lines, from, lines.Length - from).Trim();
    }

    // -- The emulator's window, as Windows sees it --

    delegate bool EachWindow(IntPtr window, IntPtr unused);
    [DllImport("user32.dll")] static extern bool EnumWindows(EachWindow each, IntPtr unused);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr window, StringBuilder text, int max);

    // Is a window titled as the emulator's (emulator_gui.py: "Shanling Q2 Emulator...") on screen?
    static bool WindowUp()
    {
        bool found = false;
        try
        {
            EnumWindows(delegate(IntPtr window, IntPtr unused)
            {
                if (!IsWindowVisible(window)) return true;
                StringBuilder text = new StringBuilder(64);
                GetWindowText(window, text, 64);
                if (!text.ToString().StartsWith("Shanling Q2 Emulator")) return true;
                found = true;
                return false;
            }, IntPtr.Zero);
        }
        catch (Exception) { }
        return found;
    }

    // -- The small "starting" window --

    static void MakeSplash()
    {
        splash = new Form();
        splash.Text = Title;
        splash.FormBorderStyle = FormBorderStyle.None;
        splash.StartPosition = FormStartPosition.CenterScreen;
        splash.ClientSize = new Size(380, 110);
        splash.BackColor = Color.FromArgb(0x16, 0x1d, 0x2b);
        splash.ShowInTaskbar = false;  // the emulator's own window brings the taskbar's one button
        splash.TopMost = true;         // and without a button, it must not get lost behind other windows
        try { splash.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { }
        Label name = new Label();
        name.Text = Title;
        name.ForeColor = Color.FromArgb(0xee, 0xf2, 0xfa);
        name.Font = new Font("Segoe UI", 15f, FontStyle.Bold);
        name.SetBounds(24, 22, 332, 34);
        line = new Label();
        line.Text = "Starting...";
        line.ForeColor = Color.FromArgb(0x94, 0xa3, 0xc2);
        line.Font = new Font("Segoe UI", 10f);
        line.SetBounds(26, 62, 332, 26);
        splash.Controls.Add(name);
        splash.Controls.Add(line);
    }

    static void Say(string text)
    {
        try { if (splash != null && !splash.IsDisposed) splash.BeginInvoke((MethodInvoker)delegate { line.Text = text; }); }
        catch (Exception) { }
    }

    static void CloseSplash()
    {
        try { if (splash != null && !splash.IsDisposed) splash.Invoke((MethodInvoker)delegate { splash.Close(); }); }
        catch (Exception) { }
    }

    static void Tell(string text, MessageBoxIcon icon)
    {
        MessageBox.Show(text, Title, MessageBoxButtons.OK, icon);
    }
}
