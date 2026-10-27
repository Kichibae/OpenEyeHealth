"""OpenEyeHealth - a tiny night light for Windows.

Makes your screens warmer (less blue) at night, on a schedule or with a hotkey.

    Ctrl+Alt+N            turn the warm screen on/off
    Ctrl+Alt+Q            quit (screens go back to normal)
    Left-click tray icon  turn the warm screen on/off
    Right-click tray icon menu, including Settings

Double-click this file to start it. It lives in the system tray.
"""
import ctypes
import json
import os
import sys
import threading
import tkinter as tk
import winreg
from ctypes import wintypes
from datetime import datetime, time
from tkinter import messagebox, ttk

APP = "OpenEyeHealth"
PANEL_TITLE = f"{APP} Settings"
NORMAL = (1.0, 1.0, 1.0)
DEFAULTS = {"warmth": 70, "schedule": True, "start": "21:00", "end": "07:00"}
SETTINGS_FILE = os.path.join(os.environ["APPDATA"], APP, "settings.json")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"   # apps that start with Windows

# Ids for hotkeys, menu items and messages from the settings panel.
TOGGLE, QUIT, SETTINGS, SAVED = 1, 2, 3, 4

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
WM_DESTROY, WM_CLOSE, WM_TIMER, WM_HOTKEY = 0x0002, 0x0010, 0x0113, 0x0312
WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
WM_TRAY, WM_PANEL = 0x8001, 0x8002           # messages from our tray icon and settings panel
MF_CHECKED, TPM_RETURNCMD = 0x8, 0x100
NIM_ADD, NIM_DELETE, NIF_MESSAGE, NIF_ICON, NIF_TIP = 0, 2, 1, 2, 4

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
shell32 = ctypes.windll.shell32
gdi32.CreateDCW.restype = wintypes.HDC
gdi32.SetDeviceGammaRamp.argtypes = (wintypes.HDC, ctypes.c_void_p)
gdi32.DeleteDC.argtypes = (wintypes.HDC,)
user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.restype = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
TASKBAR_CREATED = user32.RegisterWindowMessageW("TaskbarCreated")   # sent when Explorer restarts


class DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]


# ---- Screen colors ---------------------------------------------------------

def monitors():
    """Yield the name of every connected monitor, like \\\\.\\DISPLAY1."""
    dev = DISPLAY_DEVICE(cb=ctypes.sizeof(DISPLAY_DEVICE))
    i = 0
    while user32.EnumDisplayDevicesW(None, i, ctypes.byref(dev), 0):
        if dev.StateFlags & 1:  # 1 = attached to the desktop
            yield dev.DeviceName
        i += 1


def set_color(red, green, blue):
    """Dim each color channel on every monitor. (1, 1, 1) = normal."""
    # The "gamma ramp" is Windows' brightness lookup table: 3 channels x 256 levels.
    ramp = (wintypes.WORD * 256 * 3)()
    for channel, strength in enumerate((red, green, blue)):
        for level in range(256):
            ramp[channel][level] = int(level * 257 * strength)  # 257 turns 0-255 into 0-65535
    for name in monitors():
        dc = gdi32.CreateDCW(name, None, None, None)
        if not gdi32.SetDeviceGammaRamp(dc, ramp):
            print(f"Windows refused that color on {name}. Try a lower warmth.")
        gdi32.DeleteDC(dc)


def warmth_color(warmth):
    """Turn warmth (0 = normal, 100 = as warm as Windows allows) into red, green, blue strengths."""
    w = warmth / 100
    return 1.0, 1 - 0.22 * w, 1 - 0.47 * w


# ---- Times -----------------------------------------------------------------

def is_night(now, start, end):
    if start <= end:                       # e.g. 1 AM -> 6 AM, same day
        return start <= now < end
    return now >= start or now < end       # e.g. 9 PM -> 7 AM, crosses midnight


def to_12h(hhmm):
    """ "21:30" -> (9, 30, "PM") """
    t = time.fromisoformat(hhmm)
    return t.hour % 12 or 12, t.minute, "PM" if t.hour >= 12 else "AM"


def from_12h(hour, minute, am_pm):
    """ (9, 30, "PM") -> "21:30". Raises ValueError for times that don't exist. """
    if not 1 <= hour <= 12:
        raise ValueError(f"bad hour {hour}")
    return time(hour % 12 + (12 if am_pm == "PM" else 0), minute).strftime("%H:%M")


# ---- Settings --------------------------------------------------------------

def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            saved = json.load(f)
    except (OSError, ValueError):          # no file yet, or it's broken
        return dict(DEFAULTS)
    # Skip anything unknown or of the wrong type, like leftovers from an older version.
    return {**DEFAULTS, **{k: v for k, v in saved.items() if type(v) is type(DEFAULTS.get(k))}}


def save_settings():
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def launch_command():
    if getattr(sys, "frozen", False):      # running as OpenEyeHealth.exe
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pythonw}" "{os.path.abspath(__file__)}"'


def starts_with_windows():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        try:
            winreg.QueryValueEx(key, APP)
            return True
        except FileNotFoundError:
            return False


def set_starts_with_windows(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, APP, 0, winreg.REG_SZ, launch_command())
        else:
            winreg.DeleteValue(key, APP)


# ---- Settings panel --------------------------------------------------------

def open_settings():
    panel = user32.FindWindowW(None, PANEL_TITLE)
    if panel:
        user32.SetForegroundWindow(panel)     # already open, just bring it to the front
    else:
        # The panel gets its own thread, so hotkeys and the schedule keep working while it's open.
        threading.Thread(target=settings_panel).start()


def time_picker(parent, row, label, hhmm):
    """Hour : minute AM/PM boxes on one row. Returns a function that reads them back as "HH:MM"."""
    hour, minute, am_pm = to_12h(hhmm)
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(24, 12), pady=3)
    boxes = ttk.Frame(parent)
    boxes.grid(row=row, column=1, sticky="w")
    h = ttk.Spinbox(boxes, from_=1, to=12, width=3, wrap=True)
    m = ttk.Spinbox(boxes, from_=0, to=59, width=3, wrap=True, format="%02.0f")
    p = ttk.Spinbox(boxes, values=("AM", "PM"), width=4, wrap=True)
    for box, value in ((h, hour), (m, f"{minute:02}"), (p, am_pm)):
        box.set(value)
    p.state(["readonly"])                     # arrows only, no typing
    h.pack(side="left")
    ttk.Label(boxes, text=":").pack(side="left", padx=3)
    m.pack(side="left")
    p.pack(side="left", padx=(8, 0))
    return lambda: from_12h(int(h.get()), int(m.get()), p.get())


def settings_panel():
    root = tk.Tk()
    root.title(PANEL_TITLE)
    root.resizable(False, False)
    root.iconbitmap(sys.executable)           # same icon as the app
    frame = ttk.Frame(root, padding=20)
    frame.grid()

    ttk.Label(frame, text="Warmth").grid(row=0, column=0, sticky="w")
    warmth = ttk.Scale(frame, from_=0, to=100, value=settings["warmth"], length=190,
                       command=lambda value: set_color(*warmth_color(float(value))))   # live preview
    warmth.grid(row=0, column=1, sticky="ew")

    schedule_on = tk.BooleanVar(value=settings["schedule"])
    ttk.Checkbutton(frame, text="Turn on automatically", variable=schedule_on).grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(16, 4))
    start = time_picker(frame, 2, "Turn on at", settings["start"])
    end = time_picker(frame, 3, "Turn off at", settings["end"])

    startup_on = tk.BooleanVar(value=starts_with_windows())
    ttk.Checkbutton(frame, text="Start with Windows", variable=startup_on).grid(
        row=4, column=0, columnspan=2, sticky="w", pady=(16, 0))

    def close(saved=False):
        root.destroy()
        user32.PostMessageW(hwnd, WM_PANEL, SAVED if saved else 0, 0)   # tell the app to repaint

    def save():
        try:
            start_time, end_time = start(), end()
        except ValueError:
            messagebox.showerror(APP, "That time doesn't look right.\nHours go 1 to 12, minutes 0 to 59.", parent=root)
            return
        settings.update(warmth=round(warmth.get()), schedule=schedule_on.get(), start=start_time, end=end_time)
        save_settings()
        if startup_on.get() != starts_with_windows():
            set_starts_with_windows(startup_on.get())
        close(saved=True)

    buttons = ttk.Frame(frame)
    buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(24, 0))
    ttk.Button(buttons, text="Cancel", command=close).pack(side="left")
    ttk.Button(buttons, text="Save", command=save, default="active").pack(side="left", padx=(8, 0))
    root.bind("<Return>", lambda event: save())
    root.bind("<Escape>", lambda event: close())
    root.protocol("WM_DELETE_WINDOW", close)
    root.eval("tk::PlaceWindow . center")
    root.focus_force()
    root.mainloop()


# ---- The app ---------------------------------------------------------------

settings = load_settings()
warm = False          # is the screen warm right now?
scheduled = None      # what the schedule said last time we looked (None = look again)
hwnd = None           # our hidden window, which gets hotkey, timer and tray messages
tray = NOTIFYICONDATA()


def tick():
    """Follow the schedule, then paint the screens. Runs every minute and after every change."""
    global warm, scheduled
    start, end = time.fromisoformat(settings["start"]), time.fromisoformat(settings["end"])
    night = is_night(datetime.now().time(), start, end)
    if settings["schedule"] and night != scheduled:   # start or end time just passed
        scheduled = warm = night
    # Re-painting every minute also fixes colors that games, sleep or new monitors reset.
    set_color(*(warmth_color(settings["warmth"]) if warm else NORMAL))


def command(cmd):
    """Do what a hotkey, menu item or the settings panel asked for."""
    global warm, scheduled
    if cmd == TOGGLE:
        warm = not warm
    elif cmd == SETTINGS:
        open_settings()
    elif cmd == SAVED:
        scheduled = None                   # settings changed, so follow the schedule again right away
    elif cmd == QUIT:
        user32.DestroyWindow(hwnd)
        return
    tick()                                 # also undoes the panel's preview when it closes


def show_menu():
    """Pop up the tray menu and return the id of the item picked (0 = none)."""
    menu = user32.CreatePopupMenu()
    user32.AppendMenuW(menu, MF_CHECKED if warm else 0, TOGGLE, "Warm screen\tCtrl+Alt+N")
    user32.AppendMenuW(menu, 0, SETTINGS, "Settings...")
    user32.AppendMenuW(menu, 0, QUIT, "Quit\tCtrl+Alt+Q")
    pos = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pos))
    user32.SetForegroundWindow(hwnd)       # lets the menu close when you click somewhere else
    cmd = user32.TrackPopupMenu(menu, TPM_RETURNCMD, pos.x, pos.y, 0, hwnd, None)
    user32.DestroyMenu(menu)
    return cmd


@WNDPROC
def on_message(window, msg, wparam, lparam):
    if msg in (WM_HOTKEY, WM_PANEL):
        command(wparam)
    elif msg == WM_TRAY and lparam == WM_LBUTTONUP:
        command(TOGGLE)
    elif msg == WM_TRAY and lparam == WM_RBUTTONUP:
        command(show_menu())
    elif msg == WM_TIMER:
        tick()
    elif msg == TASKBAR_CREATED:           # Explorer restarted and lost our icon
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(tray))
    elif msg == WM_DESTROY:
        panel = user32.FindWindowW(None, PANEL_TITLE)
        if panel:
            user32.PostMessageW(panel, WM_CLOSE, 0, 0)   # close the settings panel too
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(tray))
        user32.PostQuitMessage(0)
    return user32.DefWindowProcW(window, msg, wparam, lparam)


def main():
    global hwnd
    user32.SetProcessDPIAware()            # sharp text on high-res screens
    window_class = WNDCLASS(lpfnWndProc=on_message, lpszClassName=APP)
    user32.RegisterClassW(ctypes.byref(window_class))
    hwnd = user32.CreateWindowExW(0, APP, APP, 0, 0, 0, 0, 0, None, None, None, None)
    for hotkey, key in ((TOGGLE, "N"), (QUIT, "Q")):
        if not user32.RegisterHotKey(hwnd, hotkey, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord(key)):
            user32.MessageBoxW(None, f"Ctrl+Alt+{key} is taken. Is {APP} already running?", APP, 0x10)
            return

    tray.cbSize = ctypes.sizeof(tray)
    tray.hWnd, tray.uFlags, tray.uCallbackMessage = hwnd, NIF_MESSAGE | NIF_ICON | NIF_TIP, WM_TRAY
    tray.szTip = f"{APP}: click to toggle, right-click for menu"
    icon = wintypes.HICON()
    shell32.ExtractIconExW(sys.executable, 0, None, ctypes.byref(icon), 1)   # small icon of our .exe
    tray.hIcon = icon
    shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(tray))

    user32.SetTimer(hwnd, 1, 60_000, None)  # check the clock once a minute
    tick()
    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        set_color(*NORMAL)


if __name__ == "__main__":
    main()
