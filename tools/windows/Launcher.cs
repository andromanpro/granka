using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("Гранка")]
[assembly: AssemblyDescription("Локальный редактор статей")]
[assembly: AssemblyCompany("androman")]
[assembly: AssemblyProduct("Гранка")]
[assembly: AssemblyVersion("__VERSION__.0")]
[assembly: AssemblyFileVersion("__VERSION__.0")]

internal static class Launcher {
    // Windows command-line quoting, including trailing slashes and embedded quotes.
    private static string Quote(string value) {
        var output = new StringBuilder("\"");
        int slashes = 0;
        foreach (char ch in value) {
            if (ch == '\\') { slashes++; continue; }
            if (ch == '"') { output.Append('\\', slashes * 2 + 1); output.Append(ch); }
            else { output.Append('\\', slashes); output.Append(ch); }
            slashes = 0;
        }
        output.Append('\\', slashes * 2); output.Append('"');
        return output.ToString();
    }

    [STAThread]
    private static int Main(string[] args) {
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string script = Path.Combine(root, "app", "start-granka.py");
            string python = Path.Combine(root, "runtime", "pythonw.exe");
            if (!File.Exists(python) || !File.Exists(script))
                throw new FileNotFoundException("Файлы Гранки не найдены. Распакуй весь архив или повтори установку.");
            string arguments = "-B " + Quote(script);
            foreach (string arg in args) arguments += " " + Quote(arg);
            var info = new ProcessStartInfo(python, arguments);
            info.WorkingDirectory = Path.Combine(root, "app");
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            var process = Process.Start(info);
            process.WaitForExit();
            return process.ExitCode;
        } catch (Exception error) {
            if (Array.IndexOf(args, "--quiet") < 0)
                MessageBox.Show(error.Message, "Гранка", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
