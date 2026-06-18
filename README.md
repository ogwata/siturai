# siturai — Vivliostyle image-placement PoC (Stage 1)

English ・ [日本語](README.ja.md)

A minimal experiment to find out whether a vision-language model (VLM) can
produce **useful image-placement decisions** for a VFM/Markdown manuscript.
No agent (OpenClaw) plumbing yet. We confirm here that placement quality is
good enough, *before* investing in agent machinery in Stage 2.

The name `siturai` (しつらひ) comes from an old Japanese word meaning "to
arrange and place something fittingly for its setting" — matching what this
tool does: placing each image where it belongs in the text.

## What it does

Given a manuscript (split into blocks) and a folder of images, it asks the VLM,
per image, for:

- which block to insert after (**semantic matching of image content to text**)
- a placement kind (inline / figure / figure-full-width / float)
- caption, alt text, and a width hint

It receives this as structured JSON, converts it to VFM, inserts it into a
**copy** of the manuscript, and emits a diff. Optionally it builds the result
with the Vivliostyle CLI.

The VLM is asked only for what it is good at (semantic matching and wording).
It is **not** asked to judge whether the rendered layout looks good — a human
does that.

## Design commitments

- **Backend-agnostic** — any OpenAI-compatible `/v1` endpoint with vision
  support works. Local now (Ollama), the DGX Spark vLLM later, by changing
  environment variables only.
- **Propose, don't commit** — the source manuscript is never modified. It only
  writes a modified copy, `proposals.json`, and `changes.diff` to `output/`.
  Inside a Git repo, the proposal is committed on a fresh branch
  (`proposal/image-placement-...`) and the original branch is restored untouched.
- **VFM syntax is isolated in `vfm_render.py`** — the actual insertion markup
  lives only there. That one file is the only thing to edit to match the
  current VFM spec.

## Layout

```
siturai/
├── place_images.py        Main driver (backend-agnostic)
├── vfm_render.py          VFM-syntax isolation module (the only VFM-aware file)
├── config.example.env     Environment-variable template (copy to .env)
├── sample/
│   ├── manuscript.md      Sample manuscript "四季の庭園" (The Four-Season Garden)
│   └── images/            Sample images (spring/summer/autumn/winter)
├── LICENSE                MIT
└── README.md
```

## Setup

```bash
# 1) Local VL (recommended: run reliably on your machine)
ollama pull qwen3-vl:8b
ollama serve            # skip if already running

# 2) Configure
cp config.example.env .env
# edit .env (defaults to local Ollama / qwen3-vl:8b)
set -a; source .env; set +a

# 3) Run (add --build to render too)
python place_images.py sample/manuscript.md --images sample/images --build
```

Vivliostyle CLI (for builds, optional): `npm i -g @vivliostyle/cli`

Python standard library only. No pip install needed.

### Pointing at the Spark (vLLM)

Change `.env` only — no code change:

```
VL_BASE_URL=http://edgexpert-b0a9.tailXXXXXX.ts.net:8001/v1
VL_MODEL=Qwen/Qwen3-VL-8B-Instruct
```

## Usage

```
python place_images.py <manuscript.md> --images <image-dir> [--out output] [--build] [--no-git]
```

- `--out` output directory (default `output`)
- `--build` build the modified copy to PDF with the Vivliostyle CLI
- `--no-git` do not create a proposal branch even inside a Git repo

Running writes the following to `output/`. **The source manuscript is left
unchanged.**

- `<manuscript>.modified.md` — the copy with images inserted
- `proposals.json` — the VLM's raw placement proposals
- `changes.diff` — the diff against the original
- `images/` — images copied in so the bundle is self-contained

## How to read the results (evaluation rubric)

Look at `output/proposals.json` and the built PDF, and judge:

1. **Anchor validity** — did each image land in the section matching its
   content? (A spring photo -> the "spring" section, etc. This is the core
   pass/fail of the PoC.)
2. **Wording quality** — are the captions/alt text usable as-is, or do they
   need editing?
3. **Placement-kind validity** — are figure / full-width choices reasonable?
4. **Build soundness** — does the inserted manuscript still build as valid VFM?

The sample manuscript "四季の庭園" is a short text with four seasonal sections.
Drop in seasonal photos and judge placement accuracy by whether each image
lands in the correct season's paragraph.

## Notes

- The sample manuscript "四季の庭園" and the four sample images
  (spring/summer/autumn/winter) were written and photographed by Ogata Katsuhiro.
- `place_images.py` / `vfm_render.py` include code generated with AI coding
  assistance (Claude).
- The VLM used (Qwen3-VL family) and third-party software such as the
  Vivliostyle CLI are subject to their respective licenses.
