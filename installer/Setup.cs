// AKADock installer: a classic Windows setup wizard with no external toolchain.
// Built by installer\build.bat with the C# compiler that ships with Windows.
//
// It installs per user (no administrator rights, no UAC prompt), writes an entry in
// Add/Remove Programs, creates shortcuts, and installs the Microsoft WebView2 Runtime
// if the computer does not already have it -- the application window cannot open without it.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;

static class App
{
    public const string Name = "AKADock";
    public const string Version = "1.1";
    public const string Publisher = "AKADock";
    public const string Summary =
        "Automated Kit for AutoDock. Molecular docking on your own PC. Pick a protein, draw or load compounds, " +
        "and AKADock prepares everything, docks with AutoDock Vina, checks itself by " +
        "redocking the known ligand, and writes a report you can share.";
    public const string RegKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\AKADock";

    // Microsoft's official Evergreen bootstrapper. Small; it downloads the runtime itself.
    public const string WebView2Url = "https://go.microsoft.com/fwlink/p/?LinkId=2124703";

    public static readonly string[] WebView2Keys =
    {
        @"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        @"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    };

    public static bool WebView2Installed()
    {
        foreach (var k in WebView2Keys)
        {
            foreach (var root in new[] { Registry.LocalMachine, Registry.CurrentUser })
                using (var key = root.OpenSubKey(k))
                    if (key != null)
                    {
                        var v = key.GetValue("pv") as string;
                        if (!string.IsNullOrEmpty(v) && v != "0.0.0.0") return true;
                    }
        }
        return false;
    }
}

// The beige wizard look: a white header strip, beige body, and a button row at the bottom.
class Wizard : Form
{
    public static readonly Color Beige = Color.FromArgb(240, 236, 225);
    public static readonly Color Line = Color.FromArgb(198, 190, 174);

    readonly Panel header = new Panel();
    readonly Label title = new Label(), subtitle = new Label();
    protected readonly Panel Body = new Panel();
    protected readonly Button Back = new Button(), Next = new Button(), Cancel = new Button();

    public Wizard()
    {
        Text = App.Name + " " + App.Version + " Setup";
        ClientSize = new Size(620, 460);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Beige;
        Font = new Font("Segoe UI", 9f);
        try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

        header.Dock = DockStyle.Top; header.Height = 64; header.BackColor = Color.White;
        title.Font = new Font("Segoe UI", 11f, FontStyle.Bold);
        title.SetBounds(22, 13, 560, 22); title.BackColor = Color.White;
        subtitle.SetBounds(38, 36, 560, 20); subtitle.ForeColor = Color.FromArgb(80, 80, 80);
        subtitle.BackColor = Color.White;
        header.Controls.Add(title); header.Controls.Add(subtitle);
        header.Paint += (s, e) => e.Graphics.DrawLine(new Pen(Line), 0, header.Height - 1, header.Width, header.Height - 1);

        Body.Dock = DockStyle.Fill; Body.BackColor = Beige; Body.Padding = new Padding(22, 18, 22, 10);

        var foot = new Panel { Dock = DockStyle.Bottom, Height = 54, BackColor = Beige };
        foot.Paint += (s, e) => e.Graphics.DrawLine(new Pen(Line), 0, 0, foot.Width, 0);
        foreach (var b in new[] { Back, Next, Cancel })
        {
            b.Size = new Size(96, 28); b.FlatStyle = FlatStyle.System; b.BackColor = SystemColors.Control;
        }
        Back.Text = "< Back"; Next.Text = "Next >"; Cancel.Text = "Cancel";
        Back.Location = new Point(300, 13); Next.Location = new Point(400, 13); Cancel.Location = new Point(508, 13);
        foot.Controls.AddRange(new Control[] { Back, Next, Cancel });

        Controls.Add(Body); Controls.Add(foot); Controls.Add(header);
        Cancel.Click += (s, e) => OnCancelClicked();
    }

    protected void SetHeader(string t, string s) { title.Text = t; subtitle.Text = s; }

    protected virtual void OnCancelClicked()
    {
        if (MessageBox.Show(this, "Stop installing " + App.Name + "?", App.Name + " Setup",
                MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes)
            Environment.Exit(1);
    }
}

class SetupForm : Wizard
{
    int page = 0;
    bool failed = false;
    bool wantDesktop = true, wantMenu = true;
    public bool Silent;
    string logFile;
    string target = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", App.Name);

    CheckBox agree, desktopShortcut, menuShortcut, launchAfter;
    TextBox folderBox;
    Label statusLabel;
    ProgressBar progress;
    TextBox logBox;

    public SetupForm()
    {
        Back.Click += (s, e) => Show(page - 1);
        Next.Click += (s, e) => OnNext();
        Show(0);
    }

    // Unattended install:  AKADock-Setup.exe /silent [/dir=...] [/nodesktop] [/nomenu] [/log=...]
    SetupForm(string dir, bool desktop, bool menu, string log)
    {
        Silent = true;
        if (!string.IsNullOrEmpty(dir)) target = dir;
        wantDesktop = desktop; wantMenu = menu; logFile = log;
    }

    public static int RunSilent(string dir, bool desktop, bool menu, string log)
    {
        var f = new SetupForm(dir, desktop, menu, log);
        try
        {
            string why;
            if (!PathIsUsable(f.target, out why)) { f.Log("Cannot install there: " + why); return 2; }
            f.DoInstall();
            f.Log("Installed into " + f.target);
            return 0;
        }
        catch (Exception e) { f.Log("FAILED: " + e.Message); return 1; }
    }

    void OnNext()
    {
        if (failed) Environment.Exit(1);
        if (page == 1 && !agree.Checked)
        {
            MessageBox.Show(this, "Please tick the box to accept the terms before continuing.",
                App.Name + " Setup", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        if (page == 2)
        {
            target = folderBox.Text.Trim();
            string why;
            if (!PathIsUsable(target, out why))
            {
                MessageBox.Show(this, why, App.Name + " Setup", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
        }
        if (page == 3) { Show(4); Install(); return; }
        if (page == 4) return;              // installing: Next is disabled
        if (page == 5) { Finish(); return; }
        Show(page + 1);
    }

    static bool PathIsUsable(string path, out string why)
    {
        why = null;
        if (string.IsNullOrWhiteSpace(path)) { why = "Choose a folder to install into."; return false; }
        try
        {
            var full = Path.GetFullPath(path);
            if (!Path.IsPathRooted(full)) { why = "Please give a full path, for example C:\\Programs\\AKADock."; return false; }
            var root = Path.GetPathRoot(full);
            if (!Directory.Exists(root)) { why = "The drive " + root + " does not exist on this computer."; return false; }
            if (File.Exists(full)) { why = "There is already a file with that name."; return false; }
            // a quick write test in the nearest existing parent
            var probe = full;
            while (!Directory.Exists(probe) && Path.GetDirectoryName(probe) != null) probe = Path.GetDirectoryName(probe);
            var test = Path.Combine(probe, ".akadock-write-test");
            File.WriteAllText(test, "x"); File.Delete(test);
            return true;
        }
        catch (Exception e) { why = "That folder cannot be used: " + e.Message; return false; }
    }

    void Show(int n)
    {
        page = Math.Max(0, n);
        Body.Controls.Clear();
        Back.Enabled = page > 0 && page < 4;
        Next.Enabled = true;
        Next.Text = page == 3 ? "Install" : page == 5 ? "Finish" : "Next >";
        Cancel.Enabled = page != 4;

        switch (page)
        {
            case 0: PageWelcome(); break;
            case 1: PageLicense(); break;
            case 2: PageFolder(); break;
            case 3: PageOptions(); break;
            case 4: PageInstalling(); break;
            case 5: PageDone(); break;
        }
    }

    Label Para(string text, int top, int height = 60, bool bold = false)
    {
        var l = new Label
        {
            Text = text,
            AutoSize = false,
            BackColor = Wizard.Beige,
            Font = bold ? new Font(Font, FontStyle.Bold) : Font,
        };
        l.SetBounds(0, top, 556, height);
        Body.Controls.Add(l);
        return l;
    }

    void PageWelcome()
    {
        SetHeader("Welcome to " + App.Name + " " + App.Version,
                  "This will install " + App.Name + " on your computer.");
        Para(App.Summary, 6, 60);
        Para("What gets installed", 78, 20, true);
        Para("Everything needed to run is included: AutoDock Vina, the chemistry libraries, "
             + "a chemical drawing editor and a 3D viewer. No internet connection is needed to dock, "
             + "and nothing you load is sent anywhere.", 100, 60);
        Para("One component may be downloaded", 166, 20, true);
        Para(App.WebView2Installed()
             ? "The Microsoft WebView2 Runtime is already on this computer, so nothing will be downloaded."
             : "The application window needs the Microsoft WebView2 Runtime, which is not on this computer yet. "
               + "Setup will download it from Microsoft and install it. This needs an internet connection.",
             188, 56);
        Para("Close other programs before continuing, then press Next.", 258, 20);
        Para("Installing per user — no administrator rights needed.", 290, 20).ForeColor = Color.FromArgb(90, 90, 90);
    }

    void PageLicense()
    {
        SetHeader("Terms of use", "Please read this before installing.");
        var box = new TextBox
        {
            Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical,
            BackColor = Color.White, Font = new Font("Consolas", 8.5f),
        };
        box.SetBounds(0, 4, 556, 280);
        box.Text = Payload.ReadText("LICENSE.txt").Replace("\n", "\r\n");
        box.Select(0, 0);
        Body.Controls.Add(box);

        agree = new CheckBox
        {
            Text = "I have read and accept these terms, and I understand that docking scores are "
                   + "predictions that must be confirmed by experiment.",
            AutoSize = false, BackColor = Wizard.Beige,
        };
        agree.SetBounds(0, 294, 556, 44);
        Body.Controls.Add(agree);
    }

    void PageFolder()
    {
        SetHeader("Where should " + App.Name + " be installed?",
                  "Choose a folder, or keep the suggested one.");
        Para("Setup will install " + App.Name + " into the folder below. "
             + "To use a different one, type it or press Browse.", 6, 40);

        folderBox = new TextBox { Text = target };
        folderBox.SetBounds(0, 56, 440, 24);
        var browse = new Button { Text = "Browse..." };
        browse.SetBounds(450, 55, 100, 26);
        browse.FlatStyle = FlatStyle.System;
        browse.Click += (s, e) =>
        {
            using (var d = new FolderBrowserDialog { Description = "Choose where to install " + App.Name,
                                                     SelectedPath = SafeExisting(folderBox.Text) })
                if (d.ShowDialog(this) == DialogResult.OK)
                    folderBox.Text = Path.Combine(d.SelectedPath, App.Name);
        };
        Body.Controls.AddRange(new Control[] { folderBox, browse });

        Para("Space needed: about " + Payload.UnpackedMb() + " MB."
             + "  Space available: " + FreeMb(folderBox.Text) + " MB.", 94, 20);
        Para("Your docking runs are saved inside this folder, under 'workspace'. "
             + "Uninstalling asks before deleting them.", 122, 40).ForeColor = Color.FromArgb(90, 90, 90);
    }

    static string SafeExisting(string p)
    {
        try
        {
            var d = Path.GetFullPath(p);
            while (!Directory.Exists(d) && Path.GetDirectoryName(d) != null) d = Path.GetDirectoryName(d);
            return d;
        }
        catch { return Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData); }
    }

    static string FreeMb(string p)
    {
        try { return (new DriveInfo(Path.GetPathRoot(Path.GetFullPath(p))).AvailableFreeSpace / 1024 / 1024).ToString("N0"); }
        catch { return "?"; }
    }

    void PageOptions()
    {
        SetHeader("Ready to install", "Choose the extra tasks, then press Install.");
        Para("Setup will install " + App.Name + " " + App.Version + " into:", 6, 20);
        Para(target, 28, 36, true);

        desktopShortcut = new CheckBox { Text = "Put a shortcut on the Desktop", Checked = true, AutoSize = true };
        desktopShortcut.Location = new Point(0, 76);
        menuShortcut = new CheckBox { Text = "Add it to the Start menu", Checked = true, AutoSize = true };
        menuShortcut.Location = new Point(0, 104);
        desktopShortcut.Checked = wantDesktop; menuShortcut.Checked = wantMenu;
        desktopShortcut.CheckedChanged += (s, e) => wantDesktop = desktopShortcut.Checked;
        menuShortcut.CheckedChanged += (s, e) => wantMenu = menuShortcut.Checked;
        foreach (var c in new[] { desktopShortcut, menuShortcut }) { c.BackColor = Wizard.Beige; Body.Controls.Add(c); }

        if (!App.WebView2Installed())
            Para("Setup will also download and install the Microsoft WebView2 Runtime, which the "
                 + "application window needs. Please stay connected to the internet.", 140, 46).ForeColor =
                 Color.FromArgb(150, 70, 0);
        else
            Para("The Microsoft WebView2 Runtime is already installed.", 140, 22).ForeColor = Color.FromArgb(0, 110, 60);
    }

    void PageInstalling()
    {
        SetHeader("Installing", "Please wait while " + App.Name + " is installed.");
        statusLabel = Para("Starting...", 6, 20);
        progress = new ProgressBar { Style = ProgressBarStyle.Continuous, Maximum = 1000 };
        progress.SetBounds(0, 32, 556, 20);
        logBox = new TextBox
        {
            Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical,
            BackColor = Color.White, Font = new Font("Consolas", 8f),
        };
        logBox.SetBounds(0, 64, 556, 250);
        Body.Controls.Add(progress); Body.Controls.Add(logBox);
        Next.Enabled = false; Back.Enabled = false;
    }

    void Log(string s)
    {
        if (logFile != null) { try { File.AppendAllText(logFile, s + Environment.NewLine); } catch { } }
        if (logBox == null) return;
        if (logBox.InvokeRequired) { logBox.BeginInvoke((Action)(() => Log(s))); return; }
        logBox.AppendText(s + "\r\n");
    }

    void Status(string s, double fraction)
    {
        if (statusLabel == null || progress == null) return;
        if (statusLabel.InvokeRequired) { statusLabel.BeginInvoke((Action)(() => Status(s, fraction))); return; }
        statusLabel.Text = s;
        progress.Value = Math.Max(0, Math.Min(1000, (int)(fraction * 1000)));
    }

    async void Install()
    {
        bool ok = true;
        string error = null;
        try
        {
            await Task.Run(() => DoInstall());
        }
        catch (Exception e)
        {
            ok = false; error = e.Message;
            Log("");
            Log("FAILED: " + e.Message);
        }
        if (ok) { Show(5); return; }

        failed = true;                       // OnNext checks this, so Next becomes "Close"
        Next.Enabled = true; Next.Text = "Close"; Cancel.Enabled = true;
        SetHeader("Installation failed", "You can close this window.");
        MessageBox.Show(this, "Setup could not finish:\n\n" + error +
            "\n\nAnything already copied is in:\n" + target, App.Name + " Setup",
            MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    void DoInstall()
    {
        Log(App.Name + " " + App.Version + " setup");
        Log("Installing into: " + target);
        Directory.CreateDirectory(target);

        Status("Copying files...", 0.02);
        Payload.Extract(target, (done, total, name) =>
        {
            if (done % 25 == 0 || done == total)
            {
                Status("Copying files... " + done + " of " + total, 0.02 + 0.80 * done / Math.Max(1, total));
                Log("  " + name);
            }
        });
        Log("Files copied.");

        Status("Writing the uninstaller...", 0.85);
        Payload.WriteUninstaller(target);
        Log("Uninstaller written.");

        Status("Creating shortcuts...", 0.88);
        var exe = Path.Combine(target, "AKADock.exe");
        if (wantDesktop)
        {
            Shortcut.Create(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                App.Name + ".lnk"), exe, target, App.Name + " — molecular docking");
            Log("Desktop shortcut created.");
        }
        if (wantMenu)
        {
            var menu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), App.Name);
            Directory.CreateDirectory(menu);
            Shortcut.Create(Path.Combine(menu, App.Name + ".lnk"), exe, target, App.Name + " — molecular docking");
            Shortcut.Create(Path.Combine(menu, "Uninstall " + App.Name + ".lnk"),
                Path.Combine(target, "Uninstall.exe"), target, "Remove " + App.Name);
            Log("Start menu entry created.");
        }

        Status("Registering with Windows...", 0.90);
        RegisterInAddRemove();
        Log("Listed in Apps & features.");

        if (!App.WebView2Installed())
        {
            Status("Downloading the Microsoft WebView2 Runtime...", 0.92);
            Log("WebView2 Runtime is missing; downloading it from Microsoft.");
            try
            {
                var boot = Path.Combine(Path.GetTempPath(), "MicrosoftEdgeWebview2Setup.exe");
                ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
                using (var wc = new WebClient()) wc.DownloadFile(App.WebView2Url, boot);
                Status("Installing the Microsoft WebView2 Runtime...", 0.95);
                Log("Running the Microsoft installer...");
                var p = Process.Start(new ProcessStartInfo(boot, "/silent /install") { UseShellExecute = false });
                p.WaitForExit();
                Log(App.WebView2Installed()
                    ? "WebView2 Runtime installed."
                    : "WebView2 installer finished but the runtime was still not detected (exit code "
                      + p.ExitCode + ").");
            }
            catch (Exception e)
            {
                // Not fatal: everything else is installed and the engine itself works.
                Log("Could not install WebView2: " + e.Message);
                Log("AKADock is installed, but its window may not open until WebView2 is present.");
                Log("You can install it by hand from: https://developer.microsoft.com/microsoft-edge/webview2/");
            }
        }
        else Log("WebView2 Runtime already present.");

        Status("Finished.", 1.0);
    }

    void RegisterInAddRemove()
    {
        using (var k = Registry.CurrentUser.CreateSubKey(App.RegKey))
        {
            k.SetValue("DisplayName", App.Name);
            k.SetValue("DisplayVersion", App.Version);
            k.SetValue("Publisher", App.Publisher);
            k.SetValue("InstallLocation", target);
            k.SetValue("DisplayIcon", Path.Combine(target, "AKADock.exe"));
            k.SetValue("UninstallString", "\"" + Path.Combine(target, "Uninstall.exe") + "\"");
            k.SetValue("QuietUninstallString", "\"" + Path.Combine(target, "Uninstall.exe") + "\" /quiet");
            k.SetValue("NoModify", 1, RegistryValueKind.DWord);
            k.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            k.SetValue("EstimatedSize", Payload.UnpackedMb() * 1024, RegistryValueKind.DWord);
            k.SetValue("InstallDate", DateTime.Now.ToString("yyyyMMdd"));
        }
    }

    void PageDone()
    {
        SetHeader("Installation complete", App.Name + " " + App.Version + " is ready to use.");
        Para(App.Name + " has been installed into:", 6, 20);
        Para(target, 28, 36, true);
        Para("The first run takes a few seconds longer while the docking engine starts.", 72, 20);
        launchAfter = new CheckBox { Text = "Run " + App.Name + " now", Checked = true, AutoSize = true,
                                     BackColor = Wizard.Beige };
        launchAfter.Location = new Point(0, 108);
        Body.Controls.Add(launchAfter);
        Para("To remove it later, use Apps & features in Windows settings, or the uninstaller in the "
             + "Start menu.", 146, 40).ForeColor = Color.FromArgb(90, 90, 90);
        Back.Enabled = false; Cancel.Enabled = false; Next.Text = "Finish";
    }

    void Finish()
    {
        if (launchAfter != null && launchAfter.Checked)
        {
            try { Process.Start(new ProcessStartInfo(Path.Combine(target, "AKADock.exe")) { WorkingDirectory = target }); }
            catch (Exception e)
            {
                MessageBox.Show(this, "Installed, but could not start the app:\n\n" + e.Message,
                    App.Name, MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }
        Environment.Exit(0);
    }

    protected override void OnCancelClicked()
    {
        if (page == 5) { Environment.Exit(0); return; }
        base.OnCancelClicked();
    }
}

static class Payload
{
    const string Zip = "AKADock.payload.zip";
    const string Uninstaller = "AKADock.Uninstall.exe";

    static Stream Open(string name)
    {
        var s = Assembly.GetExecutingAssembly().GetManifestResourceStream(name);
        if (s == null) throw new Exception("This setup file is incomplete (missing " + name + ").");
        return s;
    }

    public static string ReadText(string file)
    {
        using (var z = new ZipArchive(Open(Zip), ZipArchiveMode.Read))
        {
            foreach (var e in z.Entries)
                if (e.FullName.EndsWith("installer/" + file, StringComparison.OrdinalIgnoreCase)
                    || e.FullName.EndsWith("installer\\" + file, StringComparison.OrdinalIgnoreCase))
                    using (var r = new StreamReader(e.Open())) return r.ReadToEnd();
        }
        return "(terms file not found in this setup)";
    }

    public static int UnpackedMb()
    {
        long total = 0;
        using (var z = new ZipArchive(Open(Zip), ZipArchiveMode.Read))
            foreach (var e in z.Entries) total += e.Length;
        return (int)(total / 1024 / 1024) + 1;
    }

    public static void Extract(string target, Action<int, int, string> onProgress)
    {
        using (var z = new ZipArchive(Open(Zip), ZipArchiveMode.Read))
        {
            int total = z.Entries.Count, done = 0;
            foreach (var e in z.Entries)
            {
                done++;
                // entries are stored as "AKADock/..."; drop that first folder
                var rel = e.FullName.Replace('/', Path.DirectorySeparatorChar);
                int cut = rel.IndexOf(Path.DirectorySeparatorChar);
                if (cut >= 0) rel = rel.Substring(cut + 1);
                if (rel.Length == 0) continue;
                var dest = Path.Combine(target, rel);
                if (e.Length == 0 && e.Name.Length == 0) { Directory.CreateDirectory(dest); continue; }
                Directory.CreateDirectory(Path.GetDirectoryName(dest));
                using (var inS = e.Open())
                using (var outS = File.Create(dest))
                    inS.CopyTo(outS);
                onProgress(done, total, rel);
            }
        }
    }

    public static void WriteUninstaller(string target)
    {
        using (var s = Open(Uninstaller))
        using (var f = File.Create(Path.Combine(target, "Uninstall.exe")))
            s.CopyTo(f);
    }
}

static class Shortcut
{
    // Uses Windows Script Host through COM, so no extra libraries are needed.
    public static void Create(string linkPath, string targetExe, string workingDir, string description)
    {
        var t = Type.GetTypeFromProgID("WScript.Shell");
        if (t == null) return;
        dynamic shell = Activator.CreateInstance(t);
        dynamic link = shell.CreateShortcut(linkPath);
        link.TargetPath = targetExe;
        link.WorkingDirectory = workingDir;
        link.Description = description;
        link.IconLocation = targetExe + ",0";
        link.Save();
    }
}

static class Program
{
    [STAThread]
    static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        string dir = null, log = null;
        bool silent = false, desktop = true, menu = true;
        foreach (var a in args)
        {
            var low = a.ToLowerInvariant();
            if (low == "/silent" || low == "/s") silent = true;
            else if (low == "/nodesktop") desktop = false;
            else if (low == "/nomenu") menu = false;
            else if (low.StartsWith("/dir=")) dir = a.Substring(5).Trim('"');
            else if (low.StartsWith("/log=")) log = a.Substring(5).Trim('"');
            else if (low == "/?" || low == "/help")
            {
                MessageBox.Show(
                    string.Join(Environment.NewLine, new[] {
                        "AKADock Setup",
                        "",
                        "Run it with no options for the normal installer window.",
                        "",
                        "Unattended install:",
                        "  /silent            install without any window",
                        "  /dir=<folder>      where to install",
                        "  /nodesktop         no desktop shortcut",
                        "  /nomenu            no Start menu entry",
                        "  /log=<file>        write a log file",
                    }),
                    "AKADock Setup", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return 0;
            }
        }
        if (silent) return SetupForm.RunSilent(dir, desktop, menu, log);

        // One setup at a time.
        bool fresh;
        using (var mutex = new Mutex(true, "AKADockSetupMutex", out fresh))
        {
            if (!fresh)
            {
                MessageBox.Show(App.Name + " setup is already running.", App.Name + " Setup",
                    MessageBoxButtons.OK, MessageBoxIcon.Information);
                return 3;
            }
            using (var existing = Registry.CurrentUser.OpenSubKey(App.RegKey))
                if (existing != null)
                {
                    var loc = existing.GetValue("InstallLocation") as string;
                    var ver = existing.GetValue("DisplayVersion") as string;
                    if (MessageBox.Show(
                            App.Name + " " + ver + " is already installed in:\n\n" + loc +
                            "\n\nInstall again over it?", App.Name + " Setup",
                            MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes)
                        return 0;
                }
            Application.Run(new SetupForm());
        }
        return 0;
    }
}
