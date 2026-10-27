# OpenEyeHealth

A small night light for Windows 11. At night it makes your screen warmer (less blue), which is a lot easier on your eyes.
It sits in the system tray and turns on by itself at night, or whenever you press Ctrl+Alt+N.

## Download

Grab `OpenEyeHealth.exe` from the [Releases](../../releases) page and run it. There's nothing to install.

The exe isn't signed, so Windows may say "Windows protected your PC" the first time. Click **More info**, then **Run anyway**.

## Using it

- **Ctrl+Alt+N** or left-click the tray icon: turn the warm screen on or off
- **Ctrl+Alt+Q**: quit, and your colors go back to normal
- **Right-click the tray icon → Settings** to pick how warm it gets, when it turns on and off (any time, down to the minute), and whether it starts with Windows. Dragging the warmth slider previews it live.

Settings are saved in `%APPDATA%\OpenEyeHealth\settings.json`.

Can't find the icon? Windows probably hid it behind the little ^ arrow next to the clock. You can drag it out onto the taskbar.

## Running from source

You need Python 3. Double-click `nightlight.pyw`, or run:

```bash
python nightlight.pyw
```

The whole app is that one file, and it only uses Python's standard library.

## Building the exe

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --name OpenEyeHealth --icon openeyehealth.ico nightlight.pyw
```

The exe ends up in `dist/`.

## How it works

Windows keeps a "gamma ramp" for each monitor, a lookup table for how bright red, green and blue should be.
OpenEyeHealth turns blue down (and green a little), then puts it back when you quit.
Windows won't accept anything much warmer than the "High" setting.

## Tests

```bash
python test_nightlight.py
```

## Contributing

Issues and pull requests are welcome. Please try to keep it small and simple.

## License

[MIT](LICENSE)
