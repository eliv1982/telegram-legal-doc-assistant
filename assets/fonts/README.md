# Bundled font: PT Sans

The PDF checklist (`services/checklist_generator.py`) is drawn with **PT Sans** (Regular and Bold). It is bundled so that
the Cyrillic text of the checklist renders the same on every machine and never depends on fonts installed on the host.

| File | Style |
|------|-------|
| `PT_Sans-Web-Regular.ttf` | Regular |
| `PT_Sans-Web-Bold.ttf` | Bold |
| `OFL.txt` | The SIL Open Font License 1.1 with the copyright notice of the font |

- **Designer / copyright:** ParaType Ltd. (<http://www.paratype.com/public>), with Reserved Font Names "PT Sans", "PT Serif" and "ParaType".
- **License:** [SIL Open Font License, Version 1.1](OFL.txt). It allows bundling and embedding the unmodified font in software and
  documents. The font files here are **unmodified** copies, so the Reserved Font Names are kept as they are.
- **Source:** <https://github.com/google/fonts/tree/main/ofl/ptsans> (`Version 2.003W OFL` in the font's own name table).
  The git blob hashes of the downloaded files matched the ones in the repository's tree listing.

| File | SHA-256 |
|------|---------|
| `PT_Sans-Web-Regular.ttf` | `9cc831490532009bae2b3ce0d39c62adfc889060beb421593bfd9d2396d0f10a` |
| `PT_Sans-Web-Bold.ttf` | `3128bd5ecf01816e59a23d54c57a7a6b14615b07db53ff277c77376010265b05` |
| `OFL.txt` | `2758cf7a872827f39661cf8cc24188113c030447aefb5ca7145993650076ca8c` |

All three are kept byte-for-byte on every platform (`.gitattributes` turns line-ending conversion and whitespace
checks off for them; the upstream licence text has trailing spaces and a BOM).

**Coverage.** Latin, Cyrillic, digits and the typographic punctuation the checklist needs (`« » № — – … ₽`). It has **no**
checkbox glyph (U+2610) and no emoji; the checkbox is drawn as a vector shape, and the renderer replaces a character the font
cannot draw with `?` instead of letting it become an empty box.
