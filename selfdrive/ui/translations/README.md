# Translations

The `.ts` files here are the translation sources. They came from the C++/Qt
UI and are still used: the UI is now `selfdrive/ui`, a Qt Widgets UI in
Python, and it loads them at runtime.

## How it works now

- **Compile**: the `lrelease` step in `selfdrive/ui/SConscript` turns each
  `.ts` into a `.qm` next to it (`scons translations`). The C++ UI baked the
  `.qm` files into the binary with `rcc`; the Python UI has no binary to bake
  into, so they are read from disk.
- **Load**: `main.load_translation()` reads the `LanguageSetting` param and
  installs a `QTranslator` for `translations/<stem>.qm`. It must run **before
  the first widget is constructed** — Qt resolves `tr()` when a string is
  used, so a widget built earlier keeps its English text.
- **Fallback**: a missing or unreadable `.qm` warns on stderr and leaves the
  UI in English rather than refusing to start. English is the source
  language and needs no catalogue.

Translation contexts are class names. The Python port kept upstream's class
names and user-facing strings verbatim, so most existing entries still
resolve against the new UI without being re-translated.

## Extraction is the part that is still outstanding

`lupdate` scanned `.cc`/`.h` for `tr(...)`. Nothing currently re-scans the
Python sources, so a **newly added** string will not appear in the `.ts`
files until that is wired up:

- the Python equivalent is `pylupdate5` over `selfdrive/ui/**/*.py`;
- `update_translations.py` still drives the `.ts` files but needs its
  extraction step retargeted, and is not wired into any build today.

Note this only affects extraction. `tr()` on a variable still resolves
correctly at runtime — only the tooling that *finds* strings to translate
needs literals.
