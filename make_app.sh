#!/bin/bash
# Build MoneyGuilt.app, a launcher for this project, and install it to
# /Applications. Re-run after moving the project folder.
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")" && pwd)"
# The interpreter that actually runs the widget (the project's .venv can't
# load Qt's platform plugin). Override with PYTHON=/path/to/python3.
PYTHON="${PYTHON:-/usr/local/opt/python@3.14/bin/python3}"
APP="$PROJECT/MoneyGuilt.app"
CONTENTS="$APP/Contents"

[ -x "$PYTHON" ] || { echo "Missing $PYTHON: set PYTHON to an interpreter with PyQt5" >&2; exit 1; }

rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"

# Launcher: hands off to Python's own app bundle through Launch Services.
# Running the interpreter from inside this bundle instead loses the menu bar
# icon (checked: even a bare test icon doesn't show), so the widget runs as
# Python.app with our Dock icon. The widget reads its database, stylesheet
# and .env relative to the working directory, and Launch Services doesn't set
# one, so the code chdirs to the project first.
PYTHON_APP="$("$PYTHON" -c 'import sys; print(sys.base_prefix)')/Resources/Python.app"
[ -d "$PYTHON_APP" ] || { echo "Missing $PYTHON_APP" >&2; exit 1; }

cat > "$CONTENTS/MacOS/MoneyGuilt" <<LAUNCHER
#!/bin/bash
# Already running: do nothing, so no second Dock icon appears
pgrep -f "runpy.run_path\\('widget.py'" >/dev/null && exit 0
# The log records which stats the widget can show, i.e. spending habits, so
# keep it readable by the owner only.
umask 077
mkdir -p "\$HOME/Library/Logs"
touch "\$HOME/Library/Logs/MoneyGuilt.log"
chmod 600 "\$HOME/Library/Logs/MoneyGuilt.log"
exec /usr/bin/open -n -a "$PYTHON_APP" \\
    --stdout "\$HOME/Library/Logs/MoneyGuilt.log" \\
    --stderr "\$HOME/Library/Logs/MoneyGuilt.log" \\
    --args -c "import os, sys, runpy; os.chdir('$PROJECT'); sys.path.insert(0, '$PROJECT'); runpy.run_path('widget.py', run_name='__main__')"
LAUNCHER
chmod +x "$CONTENTS/MacOS/MoneyGuilt"

# Icon (drawn by app_icon.py, which the widget also uses for its Dock icon)
ICONSET="$(mktemp -d)/MoneyGuilt.iconset"
mkdir -p "$ICONSET"
PYTHONPATH="$PROJECT" "$PYTHON" - "$ICONSET" <<'PY'
import sys
from PyQt5.QtWidgets import QApplication
from app_icon import draw_icon

app = QApplication([])
out = sys.argv[1]
# iconutil wants these exact names: (pixel size, file name)
FILES = [(16, "icon_16x16"), (32, "icon_16x16@2x"), (32, "icon_32x32"),
         (64, "icon_32x32@2x"), (128, "icon_128x128"), (256, "icon_128x128@2x"),
         (256, "icon_256x256"), (512, "icon_256x256@2x"), (512, "icon_512x512"),
         (1024, "icon_512x512@2x")]
for size, name in FILES:
    draw_icon(size).save(f"{out}/{name}.png")
PY
iconutil -c icns "$ICONSET" -o "$CONTENTS/Resources/MoneyGuilt.icns"

cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Money Guilt</string>
    <key>CFBundleDisplayName</key><string>Money Guilt</string>
    <key>CFBundleIdentifier</key><string>com.moneyguilt.widget</string>
    <key>CFBundleExecutable</key><string>MoneyGuilt</string>
    <key>CFBundleIconFile</key><string>MoneyGuilt</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

rm -rf "/Applications/MoneyGuilt.app"
cp -R "$APP" "/Applications/MoneyGuilt.app"
echo "Installed /Applications/MoneyGuilt.app"
