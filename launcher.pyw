"""
Cinder Peak Lookout launcher: double-click (or use the desktop icon) to open the game in its own window.
No console window. Updates itself first, then opens the world. Closing the window saves and shuts down.
Everything it does is written to save/launcher.log in case something goes wrong.
"""
import os
import subprocess
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)
os.makedirs(os.path.join(HERE, "save"), exist_ok=True)
LOG = open(os.path.join(HERE, "save", "launcher.log"), "w", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = LOG  # pythonw has no console; keep a log instead
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def pip_install(pkg):
    py = sys.executable.replace("pythonw.exe", "python.exe")
    print(f"Installing {pkg}...")
    r = subprocess.run([py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", pkg],
                       stdout=LOG, stderr=LOG, creationflags=NO_WINDOW)
    return r.returncode == 0


def ensure(module, pkg):
    try:
        __import__(module)
        return True
    except Exception:
        return pip_install(pkg) and _try_import(module)


def _try_import(module):
    try:
        __import__(module)
        return True
    except Exception:
        traceback.print_exc()
        return False


def splash_error(msg):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "Cinder Peak Lookout", 0x10)
    except Exception:
        print(msg)


def find_edge_or_chrome():
    cands = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    return next((c for c in cands if os.path.isfile(c)), None)


def with_splash(work):
    """Show a small 'starting' window while work() runs (installing, updating, starting the world)."""
    result = {}
    try:
        import tkinter as tk
    except Exception:
        result["v"] = work()
        return result["v"]
    root = tk.Tk()
    root.overrideredirect(True)
    root.configure(bg="#15130f")
    w, h = 380, 120
    root.geometry(f"{w}x{h}+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 2}")
    tk.Label(root, text="Cinder Peak Lookout", fg="#e0a045", bg="#15130f", font=("Georgia", 16, "bold")).pack(pady=(24, 6))
    msg = tk.StringVar(value="Climbing the tower...")
    tk.Label(root, textvariable=msg, fg="#a39a88", bg="#15130f", font=("Segoe UI", 10)).pack()

    def refresh():  # Tk may only be touched from this thread, so poll the latest status text
        if STATUS[0]:
            msg.set(STATUS[0])
        root.after(150, refresh)
    refresh()

    def run():
        try:
            result["v"] = work()
        except Exception:
            traceback.print_exc()
            result["v"] = None
        root.after(0, root.destroy)
    threading.Thread(target=run, daemon=True).start()
    root.mainloop()
    return result.get("v")


STATUS = [None]


def status(text):
    print(text)
    STATUS[0] = text


def setup():
    status("Checking the brain library...")
    ensure("google.genai", "google-genai")
    status("Checking for updates...")
    try:
        import updater
        updater.main()
    except Exception:
        traceback.print_exc()
    marker = os.path.join(HERE, "save", ".no_app_window")
    have_webview = False
    if not os.path.exists(marker):
        status("Preparing the window...")
        have_webview = ensure("webview", "pywebview")
        if not have_webview:
            open(marker, "w").close()  # don't retry the install on every launch
    status("Waking Julian up...")
    import server
    url, cfg, shutdown = server.start(open_browser=False)
    return url, cfg, shutdown, have_webview


def main():
    print("Launcher starting", time.ctime())
    res = with_splash(setup)
    if not res:
        splash_error("The lookout couldn't start. Details are in save\\launcher.log.")
        return
    url, cfg, shutdown, have_webview = res
    import server
    title = cfg["place"]["lookout"]

    # 1) Best: a real app window (pywebview, uses the Edge engine built into Windows)
    if have_webview:
        try:
            import webview
            webview.create_window(title, url, width=1600, height=950, min_size=(1100, 700), background_color="#15130f")
            webview.start(private_mode=False, storage_path=os.path.join(HERE, "save", "webview"))
            shutdown()
            return
        except Exception:
            traceback.print_exc()
            open(os.path.join(HERE, "save", ".no_app_window"), "w").close()

    # 2) Fallback: Edge/Chrome in app mode (its own window, no tabs or address bar)
    exe = find_edge_or_chrome()
    if exe:
        profile = os.path.join(HERE, "save", "appwindow")
        subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1600,950"], creationflags=NO_WINDOW)
    else:
        import webbrowser
        webbrowser.open(url)
    # stay alive while the page is open: it polls the server every second
    time.sleep(20)
    while time.time() - server.LAST_REQUEST[0] < 25:
        time.sleep(2)
    print("Window closed.")
    shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        splash_error("Something went wrong. Details are in save\\launcher.log.")
