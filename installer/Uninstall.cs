// AKADock uninstaller. Copied into the install folder by Setup.exe.
// Removes the program, its shortcuts and its Windows registration -- and asks before touching
// the user's own docking runs, which live in the 'workspace' folder inside the install folder.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

static class U
{
    public const string Name = "AKADock";
    public const string RegKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\AKADock";

    public static string Here
    {
        get { return Path.GetDirectoryName(Application.ExecutablePath); }
    }

    public static bool AppIsRunning()
    {
        var here = Here;
        return Process.GetProcessesByName("AKADock").Any(p =>
        {
            try { return p.MainModule.FileName.StartsWith(here, StringComparison.OrdinalIgnoreCase); }
            catch { return false; }
        });
    }

    public static long WorkspaceSize(string ws)
    {
        try
        {
            return new DirectoryInfo(ws).EnumerateFiles("*", SearchOption.AllDirectories).Sum(f => f.Length);
        }
        catch { return 0; }
    }
}

class UninstallForm : Form
{
    readonly CheckBox keepData = new CheckBox();
    readonly TextBox log = new TextBox();
    readonly Button remove = new Button(), close = new Button();
    readonly Label head = new Label(), detail = new Label();

    public UninstallForm()
    {
        Text = "Uninstall " + U.Name;
        ClientSize = new Size(560, 380);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(240, 236, 225);
        Font = new Font("Segoe UI", 9f);
        try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

        head.Font = new Font("Segoe UI", 11f, FontStyle.Bold);
        head.SetBounds(20, 18, 520, 24);
        head.Text = "Remove " + U.Name + " from this computer?";
        detail.SetBounds(20, 46, 520, 40);
        detail.Text = "This removes the program from:\n" + U.Here;

        var ws = Path.Combine(U.Here, "workspace");
        long bytes = Directory.Exists(ws) ? U.WorkspaceSize(ws) : 0;
        keepData.SetBounds(20, 96, 520, 44);
        keepData.AutoSize = false;
        keepData.BackColor = BackColor;
        keepData.Checked = true;
        keepData.Text = bytes > 0
            ? string.Format("Keep my docking runs and saved proteins ({0:N0} MB in 'workspace'). "
                            + "Untick to delete them as well.", bytes / 1024 / 1024)
            : "Keep the 'workspace' folder if it holds anything.";

        log.SetBounds(20, 150, 520, 150);
        log.Multiline = true; log.ReadOnly = true; log.ScrollBars = ScrollBars.Vertical;
        log.BackColor = Color.White; log.Font = new Font("Consolas", 8f);
        log.Visible = false;

        remove.SetBounds(316, 320, 110, 30); remove.Text = "Uninstall"; remove.FlatStyle = FlatStyle.System;
        close.SetBounds(436, 320, 104, 30); close.Text = "Cancel"; close.FlatStyle = FlatStyle.System;
        remove.Click += (s, e) => Run();
        close.Click += (s, e) => Environment.Exit(0);

        Controls.AddRange(new Control[] { head, detail, keepData, log, remove, close });
    }

    void Say(string s)
    {
        log.Visible = true;
        log.AppendText(s + "\r\n");
        Application.DoEvents();
    }

    void Run()
    {
        if (U.AppIsRunning())
        {
            MessageBox.Show(this, U.Name + " is still open. Please close it first, then try again.",
                "Uninstall " + U.Name, MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }
        remove.Enabled = false; close.Enabled = false; keepData.Enabled = false;
        var keep = keepData.Checked;
        Remove(keep, Say);

        var ws2 = Path.Combine(U.Here, "workspace");
        var leftover2 = keep && Directory.Exists(ws2);
        MessageBox.Show(this,
            U.Name + " has been removed." +
            (leftover2 ? "\n\nYour docking runs were kept in:\n" + ws2 : ""),
            "Uninstall " + U.Name, MessageBoxButtons.OK, MessageBoxIcon.Information);
        Cleanup.SelfDelete(U.Here, leftover2);
        Environment.Exit(0);
    }

    // The removal itself, shared by the window and by /quiet.
    public static void Remove(bool keep, Action<string> Say)
    {
        Say("Removing shortcuts...");
        TryDelete(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), U.Name + ".lnk"));
        var menu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), U.Name);
        if (Directory.Exists(menu)) { try { Directory.Delete(menu, true); } catch { } }

        Say("Removing the Windows registration...");
        try { Registry.CurrentUser.DeleteSubKeyTree(U.RegKey, false); } catch { }

        Say(keep ? "Removing program files (keeping your workspace)..." : "Removing all files...");
        var here = U.Here;
        var ws = Path.Combine(here, "workspace");
        foreach (var dir in Directory.GetDirectories(here))
        {
            if (keep && string.Equals(dir, ws, StringComparison.OrdinalIgnoreCase)) continue;
            try { Directory.Delete(dir, true); } catch (Exception e) { Say("  could not remove " + Path.GetFileName(dir) + ": " + e.Message); }
        }
        foreach (var file in Directory.GetFiles(here))
        {
            if (string.Equals(file, Application.ExecutablePath, StringComparison.OrdinalIgnoreCase)) continue;
            TryDelete(file);
        }

        Say("Done.");
    }

    static void TryDelete(string path)
    {
        try { if (File.Exists(path)) File.Delete(path); } catch { }
    }
}

static class Cleanup
{
    // The uninstaller cannot delete itself while it is running: a tiny batch file does it after exit.
    public static void SelfDelete(string folder, bool keepFolder)
    {
        var bat = Path.Combine(Path.GetTempPath(), "akadock_cleanup.bat");
        var exe = Application.ExecutablePath;
        var lines = new[]
        {
            "@echo off",
            "ping 127.0.0.1 -n 3 >nul",
            "del /f /q \"" + exe + "\" >nul 2>&1",
            keepFolder ? "rem keeping the folder: the workspace is still inside"
                       : "rd /s /q \"" + folder + "\" >nul 2>&1",
            "del /f /q \"%~f0\" >nul 2>&1",
        };
        File.WriteAllLines(bat, lines);
        Process.Start(new ProcessStartInfo("cmd.exe", "/c \"" + bat + "\"")
        {
            WindowStyle = ProcessWindowStyle.Hidden,
            CreateNoWindow = true,
            UseShellExecute = false,
        });
    }
}

static class Program
{
    [STAThread]
    static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        bool quiet = false, deleteData = false;
        foreach (var a in args)
        {
            var low = a.ToLowerInvariant();
            if (low == "/quiet" || low == "/silent" || low == "/s") quiet = true;
            else if (low == "/deletedata") deleteData = true;
        }

        bool fresh;
        using (new Mutex(true, "AKADockUninstallMutex", out fresh))
        {
            if (!fresh) return 3;
            if (quiet)
            {
                // Used by Windows' own "uninstall quietly" entry. Keeps the user's runs unless told not to.
                if (U.AppIsRunning()) return 4;
                var here = U.Here;
                try { UninstallForm.Remove(!deleteData, s => { }); }
                catch (Exception) { return 1; }
                Cleanup.SelfDelete(here, !deleteData && Directory.Exists(Path.Combine(here, "workspace")));
                return 0;
            }
            Application.Run(new UninstallForm());
        }
        return 0;
    }
}
