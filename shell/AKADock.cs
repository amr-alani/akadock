// AKADock - the app window.
// Starts the AKADock engine (python\python.exe run.py, hidden, no console) and shows the app in its own
// maximized window using the Edge engine (WebView2). Closing the window stops the engine and any running Vina.
// Build: shell\build.bat (uses the C# compiler that ships with Windows, .NET Framework 4).
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

static class Program
{
    public static string Root = AppDomain.CurrentDomain.BaseDirectory;
    public static string ShellDir = Path.Combine(Root, "shell");

    [STAThread]
    static void Main()
    {
        bool first;
        var single = new Mutex(true, "AKADock-app-window", out first);
        if (!first) { Native.BringOtherToFront(); return; }
        Native.MakeDpiAware();
        // The WebView2 DLLs live in shell\, not next to the exe.
        AppDomain.CurrentDomain.AssemblyResolve += delegate (object s, ResolveEventArgs e)
        {
            string f = Path.Combine(ShellDir, new AssemblyName(e.Name).Name + ".dll");
            return File.Exists(f) ? Assembly.LoadFrom(f) : null;
        };
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Run();
        GC.KeepAlive(single);
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    static void Run()
    {
        foreach (var need in new[] { Path.Combine(Root, "python", "python.exe"), Path.Combine(Root, "run.py"),
                                     Path.Combine(ShellDir, "Microsoft.Web.WebView2.Core.dll") })
        {
            if (!File.Exists(need))
            {
                MessageBox.Show("Keep the AKADock folder together: AKADock.exe needs the python, app and shell folders next to it.\n\nMissing: " + need,
                                "AKADock", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
        }
        CoreWebView2Environment.SetLoaderDllFolderPath(ShellDir);
        Application.Run(new MainWindow());
    }
}

// ---------------------------------------------------------------- the engine (local server)
class Engine
{
    public int Port;
    public string Base { get { return "http://127.0.0.1:" + Port + "/"; } }
    public string LogPath;
    Process proc;
    StreamWriter log;
    readonly object logLock = new object();

    public void Start()
    {
        string ws = Path.Combine(Program.Root, "workspace");
        Directory.CreateDirectory(ws);
        LogPath = Path.Combine(ws, "akadock.log");
        try { if (File.Exists(LogPath) && new FileInfo(LogPath).Length > 2000000) File.Delete(LogPath); } catch { }
        log = new StreamWriter(LogPath, true, new UTF8Encoding(false));
        log.AutoFlush = true;
        Write("==== AKADock started " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));

        Port = FreePort();
        var psi = new ProcessStartInfo(Path.Combine(Program.Root, "python", "python.exe"),
                                       "-u run.py --no-browser --port " + Port);
        psi.WorkingDirectory = Program.Root;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        psi.RedirectStandardOutput = true;
        psi.RedirectStandardError = true;
        psi.EnvironmentVariables["PYTHONHOME"] = Path.Combine(Program.Root, "python");
        psi.EnvironmentVariables.Remove("PYTHONPATH");
        psi.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
        psi.EnvironmentVariables["PYTHONUTF8"] = "1";
        psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        proc = new Process { StartInfo = psi };
        proc.OutputDataReceived += delegate (object s, DataReceivedEventArgs e) { if (e.Data != null) Write(e.Data); };
        proc.ErrorDataReceived += delegate (object s, DataReceivedEventArgs e) { if (e.Data != null) Write(e.Data); };
        proc.Start();
        Native.KillWithThisApp(proc);   // the engine and Vina stop when the window closes, even after a crash
        proc.BeginOutputReadLine();
        proc.BeginErrorReadLine();
    }

    void Write(string line)
    {
        lock (logLock) { try { log.WriteLine(line); } catch { } }
    }

    public bool Exited { get { return proc == null || proc.HasExited; } }

    public bool Healthy()
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(Base + "api/ping");
            req.Timeout = 2000;
            req.Proxy = null;
            using (var r = (HttpWebResponse)req.GetResponse()) return r.StatusCode == HttpStatusCode.OK;
        }
        catch { return false; }
    }

    public bool AnyRunWorking()
    {
        try
        {
            var wc = new WebClient { Proxy = null, Encoding = Encoding.UTF8 };
            string list = wc.DownloadString(Base + "api/jobs");
            return list.Contains("\"status\":\"running\"") || list.Contains("\"status\":\"queued\"");
        }
        catch { return false; }
    }

    public string LastLines(int n)
    {
        try
        {
            string[] all;
            lock (logLock) { log.Flush(); }
            using (var fs = new FileStream(LogPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            using (var sr = new StreamReader(fs)) all = sr.ReadToEnd().Split('\n');
            int start = Math.Max(0, all.Length - n);
            return string.Join("\n", all, start, all.Length - start).Trim();
        }
        catch { return ""; }
    }

    public void Stop()
    {
        try { if (proc != null && !proc.HasExited) proc.Kill(); } catch { }
        lock (logLock) { try { log.Dispose(); } catch { } }
    }

    static int FreePort()
    {
        var l = new TcpListener(IPAddress.Loopback, 0);
        l.Start();
        int p = ((IPEndPoint)l.LocalEndpoint).Port;
        l.Stop();
        return p;
    }
}

// ---------------------------------------------------------------- windows
class AppWindow : Form
{
    protected WebView2 view = new WebView2();
    protected CoreWebView2Environment env;
    bool full;
    FormBorderStyle oldBorder;
    FormWindowState oldState;

    protected AppWindow()
    {
        Text = "AKADock";
        BackColor = Color.FromArgb(244, 246, 249);
        StartPosition = FormStartPosition.CenterScreen;
        Size = new Size(1280, 800);
        MinimumSize = new Size(1000, 640);
        WindowState = FormWindowState.Maximized;
        try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
        view.Dock = DockStyle.Fill;
        view.DefaultBackgroundColor = BackColor;
    }

    protected async Task InitView(CoreWebView2Environment e)
    {
        env = e;
        await view.EnsureCoreWebView2Async(env);
        var core = view.CoreWebView2;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.AreDevToolsEnabled = Environment.GetEnvironmentVariable("AKADOCK_DEVTOOLS") == "1";
        core.Settings.IsGeneralAutofillEnabled = false;
        core.Settings.IsPasswordAutosaveEnabled = false;
        core.DocumentTitleChanged += delegate { Text = string.IsNullOrEmpty(core.DocumentTitle) || core.DocumentTitle == "AKADock" ? "AKADock" : core.DocumentTitle + " - AKADock"; };
        // Keys pressed inside the page stay in the Edge engine, so the page reports F11 / Esc to the window.
        await core.AddScriptToExecuteOnDocumentCreatedAsync(
            "document.addEventListener('keydown', function (e) {" +
            " if (e.key === 'F11') { e.preventDefault(); window.chrome.webview.postMessage('toggle-fullscreen'); }" +
            " else if (e.key === 'Escape') { window.chrome.webview.postMessage('escape'); } }, true);");
        core.WebMessageReceived += delegate (object s, CoreWebView2WebMessageReceivedEventArgs a)
        {
            string m = a.TryGetWebMessageAsString();
            if (m == "toggle-fullscreen") ToggleFull();
            else if (m == "escape" && full) ToggleFull();
        };
        core.NewWindowRequested += OnNewWindow;
        core.DownloadStarting += OnDownload;
        core.NavigationStarting += delegate (object s, CoreWebView2NavigationStartingEventArgs a)
        {
            // Pages from the internet (links in reports, RCSB, ...) open in the normal browser.
            if (!a.Uri.StartsWith("http://127.0.0.1:") && !a.Uri.StartsWith("about:") && !a.Uri.StartsWith("data:") && !a.Uri.StartsWith("blob:"))
            {
                a.Cancel = true;
                try { Process.Start(a.Uri); } catch { }
            }
        };
    }

    async void OnNewWindow(object sender, CoreWebView2NewWindowRequestedEventArgs e)
    {
        if (!e.Uri.StartsWith("http://127.0.0.1:"))
        {
            e.Handled = true;
            try { Process.Start(e.Uri); } catch { }
            return;
        }
        var deferral = e.GetDeferral();
        try
        {
            var w = new PopupWindow();
            w.Show();
            await w.Ready(env);
            e.NewWindow = w.Core;
            e.Handled = true;
        }
        finally { deferral.Complete(); }
    }

    void OnDownload(object sender, CoreWebView2DownloadStartingEventArgs e)
    {
        var deferral = e.GetDeferral();
        BeginInvoke(new Action(delegate
        {
            try
            {
                using (var dlg = new SaveFileDialog())
                {
                    dlg.FileName = Path.GetFileName(e.ResultFilePath);
                    dlg.InitialDirectory = Path.GetDirectoryName(e.ResultFilePath);
                    string ext = Path.GetExtension(e.ResultFilePath);
                    dlg.Filter = (ext.Length > 1 ? ext.Substring(1).ToUpper() + " file|*" + ext + "|" : "") + "All files|*.*";
                    if (dlg.ShowDialog(this) == DialogResult.OK) { e.ResultFilePath = dlg.FileName; e.Handled = true; }
                    else e.Cancel = true;
                }
            }
            finally { deferral.Complete(); }
        }));
    }

    protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
    {
        if (keyData == Keys.F11) { ToggleFull(); return true; }
        if (keyData == Keys.Escape && full) { ToggleFull(); }
        return base.ProcessCmdKey(ref msg, keyData);
    }

    public bool IsFull { get { return full; } }

    void ToggleFull()
    {
        if (!full)
        {
            oldBorder = FormBorderStyle; oldState = WindowState;
            WindowState = FormWindowState.Normal;   // needed so the borderless window covers the taskbar
            FormBorderStyle = FormBorderStyle.None;
            WindowState = FormWindowState.Maximized;
        }
        else
        {
            FormBorderStyle = oldBorder;
            WindowState = FormWindowState.Normal;
            WindowState = oldState == FormWindowState.Minimized ? FormWindowState.Maximized : oldState;
        }
        full = !full;
    }
}

class PopupWindow : AppWindow
{
    public CoreWebView2 Core { get { return view.CoreWebView2; } }

    public PopupWindow()
    {
        Controls.Add(view);
    }

    public Task Ready(CoreWebView2Environment e) { return InitView(e); }
}

class MainWindow : AppWindow
{
    readonly Engine engine = new Engine();
    readonly Panel splash = new Panel();
    readonly Label status = new Label();
    bool closingConfirmed;

    public MainWindow()
    {
        splash.Dock = DockStyle.Fill;
        splash.BackColor = BackColor;
        var logo = new PictureBox { SizeMode = PictureBoxSizeMode.Zoom, Size = new Size(112, 112) };
        try { logo.Image = new Icon(Path.Combine(Program.ShellDir, "akadock.ico"), 256, 256).ToBitmap(); } catch { }
        var name = new Label { Text = "AKADock", AutoSize = true, Font = new Font("Segoe UI Semibold", 26f), ForeColor = Color.FromArgb(27, 31, 36) };
        status.AutoSize = false; status.TextAlign = ContentAlignment.TopCenter;
        status.Font = new Font("Segoe UI", 11f); status.ForeColor = Color.FromArgb(102, 112, 133);
        status.Text = "Starting…";
        splash.Controls.Add(logo); splash.Controls.Add(name); splash.Controls.Add(status);
        splash.Resize += delegate
        {
            int cx = splash.ClientSize.Width / 2, cy = splash.ClientSize.Height / 2;
            logo.Location = new Point(cx - logo.Width / 2, cy - 150);
            name.Location = new Point(cx - name.Width / 2, cy - 20);
            status.SetBounds(40, cy + 45, Math.Max(100, splash.ClientSize.Width - 80), 260);
        };
        view.Visible = false;
        Controls.Add(view);
        Controls.Add(splash);
        Load += async delegate { await Start(); };
        FormClosing += OnClosing;
    }

    async Task Start()
    {
        Task<CoreWebView2Environment> envTask;
        try
        {
            string data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AKADock", "WebView2");
            envTask = CoreWebView2Environment.CreateAsync(null, data);
        }
        catch (Exception ex) { ShowRuntimeMissing(ex); return; }

        try { engine.Start(); }
        catch (Exception ex) { status.Text = "AKADock could not start its engine:\n" + ex.Message; return; }

        var t0 = DateTime.Now;
        status.Text = "Starting the docking engine…";
        while (!await Task.Run(() => engine.Healthy()))
        {
            if (engine.Exited)
            {
                status.Text = "The docking engine stopped while starting.\n\n" + engine.LastLines(12) + "\n\nFull log: " + engine.LogPath;
                status.TextAlign = ContentAlignment.TopLeft;
                return;
            }
            double s = (DateTime.Now - t0).TotalSeconds;
            if (s > 20) status.Text = "Starting the docking engine…\nThe first start after installing can take a minute while Windows checks the files.";
            if (s > 240) { status.Text = "The docking engine did not start within 4 minutes.\n\nFull log: " + engine.LogPath; return; }
            await Task.Delay(400);
        }

        try { await InitView(await envTask); }
        catch (Exception ex) { ShowRuntimeMissing(ex); return; }
        view.CoreWebView2.NavigationCompleted += delegate
        {
            if (!view.Visible) { view.Visible = true; splash.Visible = false; view.Focus(); }
        };
        view.CoreWebView2.Navigate(engine.Base);
    }

    void ShowRuntimeMissing(Exception ex)
    {
        status.Text = "AKADock needs the Microsoft Edge WebView2 Runtime, which is part of Windows 10 and 11 but seems to be missing or damaged here.\n\n" +
                      "Install it from https://go.microsoft.com/fwlink/p/?LinkId=2124703 and start AKADock again.\n\n(" + ex.Message + ")";
    }

    void OnClosing(object sender, FormClosingEventArgs e)
    {
        if (!closingConfirmed && view.Visible && engine.AnyRunWorking())
        {
            var answer = MessageBox.Show(this, "A docking run is still working. Closing AKADock stops it.\n\nClose anyway?",
                                         "AKADock", MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2);
            if (answer != DialogResult.Yes) { e.Cancel = true; return; }
        }
        closingConfirmed = true;
        foreach (Form f in Application.OpenForms) if (f != this) { try { f.Hide(); } catch { } }
        engine.Stop();
    }
}

// ---------------------------------------------------------------- Windows API bits
static class Native
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr attrs, string name);
    [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length);
    [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr hwnd, int cmd);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr ctx);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();

    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimit
    {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct IoCounters { public ulong R, W, O, RB, WB, OB; }

    [StructLayout(LayoutKind.Sequential)]
    struct ExtendedLimit
    {
        public BasicLimit Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }

    static IntPtr job = IntPtr.Zero;

    public static void KillWithThisApp(Process p)
    {
        try
        {
            if (job == IntPtr.Zero)
            {
                job = CreateJobObject(IntPtr.Zero, null);
                var info = new ExtendedLimit();
                info.Basic.LimitFlags = 0x2000;   // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                int len = Marshal.SizeOf(typeof(ExtendedLimit));
                IntPtr ptr = Marshal.AllocHGlobal(len);
                Marshal.StructureToPtr(info, ptr, false);
                SetInformationJobObject(job, 9, ptr, (uint)len);
                Marshal.FreeHGlobal(ptr);
            }
            AssignProcessToJobObject(job, p.Handle);
        }
        catch { }
    }

    public static void MakeDpiAware()
    {
        try { if (SetProcessDpiAwarenessContext(new IntPtr(-4))) return; } catch { }
        try { SetProcessDPIAware(); } catch { }
    }

    public static void BringOtherToFront()
    {
        var me = Process.GetCurrentProcess();
        foreach (var p in Process.GetProcessesByName(me.ProcessName))
        {
            if (p.Id == me.Id || p.MainWindowHandle == IntPtr.Zero) continue;
            if (IsIconic(p.MainWindowHandle)) ShowWindow(p.MainWindowHandle, 3);   // SW_MAXIMIZE
            SetForegroundWindow(p.MainWindowHandle);
            return;
        }
    }
}
