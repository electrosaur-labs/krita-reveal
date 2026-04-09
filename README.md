# krita-reveal

Krita plugin for screen-print colour separation.

Reduces a Lab-mode image to 3–12 spot colours and builds a fill+mask
layer stack ready for plate output.

Powered by [pyreveal](https://github.com/electrosaur-labs/pyreveal).

## Install

1. Copy `krita_reveal/` and `krita_reveal.desktop` to your pykrita folder:
   - **macOS**: `~/Library/Application Support/krita/pykrita/`
   - **Linux**: `~/.local/share/krita/pykrita/`
   - **Windows**: `%APPDATA%\krita\pykrita\`

2. Restart Krita.

3. Enable the plugin: **Settings → Configure Krita → Python Plugin Manager → Reveal Separation**.

4. Open the dock: **Settings → Dockers → Reveal Separation**.

## Usage

1. Open a **Lab colour mode** document (Image → Convert → Lab).
2. Set the target colour count (3–12).
3. Click **Separate Colors** — palette swatches appear.
4. Click **Build Layers** — one group layer per colour is created.

## Requirements

- Krita 5.x
- Document must be in **Lab colour mode**, any bit depth

## Development

pyreveal is vendored in `krita_reveal/vendor/pyreveal/`.
To update it: `cp -r ../pyreveal/pyreveal krita_reveal/vendor/pyreveal`
