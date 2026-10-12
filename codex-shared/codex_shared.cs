// codex-shared.exe: stand-in for codex.exe behind the VS Code setting
// `chatgpt.cliExecutable`.
//
// `codex ... app-server [flags]` (how the panel starts its backend) runs
// agent-relay\codex_bridge.py on the same stdio, which joins ONE shared
// app-server on the control socket. agent-relay can then start or steer turns
// in the same live threads the panel shows. Every other command passes
// straight through to the extension's bundled codex.exe.
// Build: build.ps1 (csc from .NET Framework 4, no SDK needed).
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;

class CodexShared
{
    static string Bridge { get { return Environment.GetEnvironmentVariable("AGENT_RELAY_BRIDGE")
        ?? Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "codex_bridge.py")); } }

    static string FindCodex()
    {
        string env = Environment.GetEnvironmentVariable("AGENT_RELAY_CODEX_EXE");
        if (!string.IsNullOrEmpty(env)) return env;
        string root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".vscode", "extensions");
        foreach (var d in new DirectoryInfo(root).GetDirectories("openai.chatgpt-*").OrderByDescending(d => d.LastWriteTimeUtc))
        {
            string p = Path.Combine(d.FullName, "bin", "windows-x86_64", "codex.exe");
            if (File.Exists(p)) return p;
        }
        throw new FileNotFoundException("bundled codex.exe not found");
    }

    static string Quote(string a)
    {
        if (a.Length > 0 && a.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return a;
        return "\"" + a.Replace("\"", "\\\"") + "\"";
    }

    static int Run(string exe, string args)
    {
        // no redirection: the child inherits this process's stdin/stdout/stderr
        // (CreateNoWindow would stop the child inheriting the pipes)
        var psi = new ProcessStartInfo(exe, args) { UseShellExecute = false };
        using (var p = Process.Start(psi)) { p.WaitForExit(); return p.ExitCode; }
    }

    static int Main(string[] args)
    {
        int i = Array.IndexOf(args, "app-server");
        bool serve = i >= 0 && args.Skip(i + 1).All(a => a.StartsWith("-"));
        if (serve && Environment.GetEnvironmentVariable("AGENT_RELAY_CODEX_PRIVATE") != "1")
            return Run(Environment.GetEnvironmentVariable("AGENT_RELAY_PYTHON") ?? "python", Quote(Bridge));
        return Run(FindCodex(), string.Join(" ", args.Select(Quote)));
    }
}
