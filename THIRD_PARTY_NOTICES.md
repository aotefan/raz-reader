# Third-party components and notices

Original RAZ Reader source and original example text/vector artwork are distributed under [Apache-2.0](LICENSE). Each third-party component retains its own license. This document identifies bundled code and optional external components; it does not license any user's books or audio.

## Included browser components

- **Mozilla PDF.js 6.4.299**, Apache-2.0, copyright Mozilla Foundation and contributors. The official legacy generic viewer is under `web/vendor/pdfjs/`; original license headers and `LICENSE` are retained. The exact source commit and distribution checksum are recorded in [UPSTREAM.json](UPSTREAM.json). Runtime parsing/rendering builds are unchanged; viewer HTML is adapted for the application and the upstream example PDF is omitted. Matching full source is available at the pinned upstream commit; no nested repository or upstream PDF test corpus is bundled.
- **canvas-confetti 1.9.4**, ISC, by its upstream contributors. The vendored module and original license are under `web/vendor/canvas-confetti/`.
- PDF.js includes components with independent licenses: Adobe CMaps, Foxit standard fonts, Liberation fonts, ICC data, qcms, OpenJPEG, JBIG2 and Brotli. Preserve the notices in `web/vendor/pdfjs/web/` and `THIRD_PARTY_LICENSES/`. In particular, Liberation's font license includes its specific exception; it is not simply Apache-2.0.

## Optional installed components — not bundled as binaries or model weights

- **OpenPronounce 0.3.0**, MIT, copyright Jean-François Lépine. Source revision `74bc17ea406e6f057f15eea0e70951e1931af868` is pinned in the requirements. RAZ Reader uses the phoneme APIs and adapted weighted scoring, not the upstream online TTS/full-score path. See `THIRD_PARTY_LICENSES/OpenPronounce-MIT.txt`.
- **Phonemizer** (GPL-3.0), **eSpeak NG** (GPL-3.0), and **Levenshtein** (GPL-2.0-or-later) are part of the optional speech toolchain. Phonemizer and Levenshtein license texts are retained under `THIRD_PARTY_LICENSES/`. Their licenses are not replaced by the application's license; binary/container distributors must account for the actual components they distribute.
- The optional **facebook/wav2vec2-lv-60-espeak-cv-ft** model is downloaded from the revision pinned in `scripts/install_assessment_model.py`; its upstream model card identifies Apache-2.0. The installer retains the model card locally. Weights are not committed.
- **ECDICT** is downloaded from pinned revision `bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b`; upstream declares MIT. Its original license is retained. The data incorporates multiple sources: this project does not independently clear every underlying entry or redistribute the database.
- **Argos English-to-Chinese 1.9 / OPUS-MT** is an optional separately downloaded model. Its retained model README identifies CC-BY 4.0 and credits Jörg Tiedemann and Santhosh Thottingal, “OPUS-MT — Building open translation services for the World,” EAMT 2020. CTranslate2 is MIT; SentencePiece is Apache-2.0; their notices are retained.
- **Tesseract** is an external Apache-2.0 OCR tool. **Poppler**, **qpdf**, **FFmpeg**, and **eSpeak NG** are separately installed tools, not copied executables in this repository. Their applicable licenses and, for FFmpeg, build options matter when distributing a binary product.
- **ReportLab**, **Pillow**, and **charset-normalizer** are installed for original example generation. **Playwright** is a development dependency. Python/npm manifests pin the used dependencies; their source is not copied into application code here.

## Media exclusions

No RAZ PDFs, covers, page captures, narration, user microphone recordings, trained weights, dictionary database, commercial encouragement voice files or CC0 sound candidate collection is bundled. Original example PDFs and local system-TTS audio are generated only in ignored directories. The repository does not redistribute system voice output.

The documentation screenshots show the application's own original text and artwork, not commercial book content. Their source and regeneration command are documented in [docs/screenshots/README.md](docs/screenshots/README.md).
