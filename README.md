# Reveal Separation for Krita

Professional screen-print color separation for Krita. Reduces full-color images to a limited set of spot colors (3–12) using advanced CIELAB quantization.

Powered by [pyreveal](https://github.com/electrosaur-labs/pyreveal).

![Krita Reveal UI](https://github.com/electrosaur-labs/krita-reveal/raw/main/docs/images/navigator-preview.png)

## Features

- **CIELAB Sovereignty**: All color math happens in 16-bit Lab space for maximum perceptual accuracy.
- **Archetype Engine**: Automatically matches your image against 26+ separation strategies (Natural, Graphic, Dramatic, etc.).
- **B/W & Grayscale Modes**: Strict 2-color thresholding and linear luminance quantization.
- **Palette Surgeon**:
    - **Interactive Swatches**: Click to isolate, Ctrl+Click to override via Krita color picker.
    - **Smart Merging**: Drag swatches to merge colors.
    - **Smart Removal**: Instantly remove user-added colors; soft-delete original colors.
- **Mechanical Knobs**: 31+ post-processing controls including Despeckle, Shadow Floor, and Trap Width.
- **Production Layers**: Builds a structured fill+mask layer stack ready for output.

## Installation

### Standard Method (Recommended)
1. Download `krita_reveal.zip` from the latest [Release](https://github.com/electrosaur-labs/krita-reveal/releases).
2. Open Krita.
3. Go to **Tools → Scripts → Import Python Plugin from File...**
4. Select the downloaded `.zip` file.
5. Restart Krita.
6. Enable the plugin: **Settings → Configure Krita → Python Plugin Manager → Reveal Separation**.
7. Open the panel: **Settings → Dockers → Reveal Separation**.

### Manual Method
1. Copy the `krita_reveal/` folder and `krita_reveal.desktop` file into your Krita `pykrita` folder:
   - **macOS**: `~/Library/Application Support/krita/pykrita/`
   - **Linux**: `~/.local/share/krita/pykrita/`
   - **Windows**: `%APPDATA%\krita\pykrita\`
2. Restart Krita and enable as described above.

## Usage

1. **Lab Mode**: Document must be in **Lab color mode** (Image → Convert Image Color Space → Lab/Alpha).
2. **Separate**: Click **Separate** in the top action row to generate the initial palette and preview.
3. **Refine**: Adjust knobs or use the Palette Surgeon to tune the separation.
4. **Build**: Click **Separate** (Green button) again to build the actual Krita layers.

## Requirements

- **Krita 5.2+** (tested on 5.2.2)
- **Lab Color Model**: The plugin requires Lab documents to function.
- **Numpy** (Optional but recommended): Improves performance by 10x. The plugin will prompt you if it's missing.

## Development

`pyreveal` core is vendored in `krita_reveal/vendor/pyreveal/`.
To update the core engine:
```bash
cp -r ../pyreveal/pyreveal/* krita_reveal/vendor/pyreveal/
```

## License

Apache-2.0
