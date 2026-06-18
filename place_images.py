#!/usr/bin/env python3
"""Stage 1 PoC driver: VLM-proposed image placement for Vivliostyle / VFM manuscripts.

Goal of this stage: find out whether a vision-language model can produce
*useful* image-placement decisions for a paged-media manuscript — BEFORE
investing in any agent (OpenClaw) plumbing.

Design choices baked in:
  * Backend-agnostic. Talks to any OpenAI-compatible /v1/chat/completions
    endpoint with vision support (Ollama, vLLM, hosted APIs). Swap the
    endpoint via env vars — local now, DGX Spark later.
  * Propose, don't commit. The source manuscript is never modified. A
    modified copy, the raw proposals, and a unified diff are written to
    an output directory for human review.
  * VFM syntax is isolated. All VFM/Markdown markup generation lives in
    vfm_render.py so the pipeline never hardcodes the spec.
  * The VLM is asked only for what it is good at: matching image *content*
    to the right place in the text, plus caption/alt text. It is NOT asked
    to judge rendered layout quality — that is left to the human (and to a
    later, optional render-and-inspect step).

Dependencies: Python standard library only. (Vivliostyle CLI is a separate
npm tool, used only for the optional --build step.)
"""
from __future__ import annotations

import argparse
import base64
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

from vfm_render import render_placement  # <- edit that file to match current VFM

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
}
ALLOWED_PLACEMENTS = {"inline", "figure", "figure-full-width", "float"}

SYSTEM_PROMPT = """\
You decide where a single image belongs inside a book manuscript and how it \
should be presented. The manuscript is given as a list of numbered blocks \
(paragraphs and headings). You are shown ONE image.

Choose the block AFTER which the image should be inserted, based on the \
*content* of the image and how well it matches the surrounding text. This is a \
paged-media book: do NOT worry about which physical page the image lands on or \
about exact pixel positions — choose the semantically correct location only.

For "placement", default to "figure" for ordinary photographs and \
illustrations. Use "inline" only for a small icon or glyph that belongs within \
a line of text; use "figure-full-width" only when the image clearly merits a \
full, page-width presentation; use "float" rarely. When unsure, choose \
"figure". Write "caption" and "alt" in the SAME LANGUAGE as the manuscript.

Respond with a SINGLE JSON object and nothing else (no prose, no code fences):

{
  "anchor_block_index": <int>,        // insert AFTER this block
  "placement": "inline" | "figure" | "figure-full-width" | "float",
  "caption": "<short caption, language matching the manuscript, or empty>",
  "alt": "<concise alt text describing the image>",
  "width": "<optional CSS width like \\"100%\\" or \\"60mm\\", or null>",
  "confidence": <float 0..1>,
  "reason": "<one short sentence: why this location>"
}
"""


def data_uri(path: Path) -> str:
    mime = MIME[path.suffix.lower()]
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def split_blocks(md: str) -> list[tuple[int, str]]:
    """Split a manuscript into blank-line-separated blocks, indexed from 0."""
    parts = re.split(r"\n\s*\n", md.strip("\n"))
    return [(i, p.strip()) for i, p in enumerate(p2 for p2 in parts) if p.strip()]


def numbered_manuscript(blocks: list[tuple[int, str]]) -> str:
    return "\n\n".join(f"[block {i}] {text}" for i, text in blocks)


def call_vlm(base_url: str, api_key: str, model: str,
             manuscript_text: str, image_path: Path) -> dict:
    """Ask the VLM for a placement proposal for one image. Returns parsed JSON."""
    body = {
        "model": model,
        "max_tokens": 4096,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text":
                    "Manuscript blocks:\n\n" + manuscript_text +
                    f"\n\nImage filename: {image_path.name}\n"
                    "Return the placement JSON for THIS image only. /no_think"},
                {"type": "image_url", "image_url": {"url": data_uri(image_path)}},
            ]},
        ],
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"VL endpoint error {e.code} for {image_path.name}: "
                         f"{e.read().decode(errors='replace')[:500]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Could not reach VL endpoint at {base_url}: {e.reason}")

    msg = payload["choices"][0]["message"]
    # Reasoning models (Qwen3-VL) may leave `content` empty and put text in
    # `reasoning`. Fall back to it so we can still recover the JSON.
    text = (msg.get("content") or "").strip() or (msg.get("reasoning") or "")
    return _parse_json(text, image_path.name)


def _parse_json(text: str, image_name: str) -> dict:
    """Extract the placement JSON from a model response.

    Qwen3-VL may emit chain-of-thought and/or wrap the answer in prose or code
    fences. Scan for flat JSON objects and return the last one that parses and
    carries the expected key.
    """
    text = re.sub(r"```(?:json)?|```", " ", text)
    for chunk in reversed(re.findall(r"\{[^{}]*\}", text, flags=re.DOTALL)):
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if "anchor_block_index" in obj:
            return obj
    raise SystemExit(
        f"No usable JSON found in response for {image_name}.\n"
        f"First 800 chars:\n{text.strip()[:800]}")


def validate(proposal: dict, n_blocks: int, image_name: str) -> dict:
    idx = proposal.get("anchor_block_index")
    if not isinstance(idx, int) or not (0 <= idx < n_blocks):
        raise SystemExit(f"{image_name}: anchor_block_index {idx!r} out of range "
                         f"[0, {n_blocks - 1}]")
    if proposal.get("placement") not in ALLOWED_PLACEMENTS:
        raise SystemExit(f"{image_name}: bad placement {proposal.get('placement')!r}")
    proposal.setdefault("caption", "")
    proposal.setdefault("alt", proposal.get("caption", ""))
    proposal.setdefault("width", None)
    return proposal


def assemble(blocks: list[tuple[int, str]],
             insertions: dict[int, list[str]]) -> str:
    """Rebuild the manuscript, appending rendered markup after chosen blocks."""
    out: list[str] = []
    for i, text in blocks:
        out.append(text)
        for snippet in insertions.get(i, []):
            out.append(snippet)
    return "\n\n".join(out) + "\n"


def build_modified(blocks: list[tuple[int, str]],
                   placements: list[tuple[dict, Path]],
                   base_dir: Path) -> str:
    """Produce the modified manuscript with image paths relative to base_dir.

    The image reference differs by where the manuscript will live: the copy in
    out/ needs paths relative to out/, while the in-place git branch needs them
    relative to the manuscript's own directory.
    """
    insertions: dict[int, list[str]] = {}
    for prop, img in placements:
        rel = os.path.relpath(img, base_dir)
        snippet = render_placement(prop, rel)
        insertions.setdefault(prop["anchor_block_index"], []).append(snippet)
    return assemble(blocks, insertions)


def in_git_repo(path: Path) -> bool:
    try:
        r = subprocess.run(["git", "-C", str(path.parent),
                            "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def propose_on_branch(manuscript: Path, modified: str,
                      proposals: list) -> tuple[str, str, str] | None:
    """Commit the proposed manuscript on a fresh branch, then return to the
    original branch so the working tree is left exactly as it was found.

    Returns (branch, base_branch, relpath), or None if it was skipped.
    """
    import datetime
    root = Path(_git(manuscript.parent, "rev-parse", "--show-toplevel"))
    if _git(root, "status", "--porcelain"):
        print("\n[git] Uncommitted changes present — skipping branch creation "
              "(commit or stash first, or pass --no-git).")
        return None
    base = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = "proposal/image-placement-" + \
        datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    rel = str(manuscript.resolve().relative_to(root))
    try:
        _git(root, "checkout", "-b", branch)
        manuscript.write_text(modified, encoding="utf-8")
        _git(root, "add", rel)
        summary = "\n".join(
            f"- {p['image']} -> after block {p['anchor_block_index']} "
            f"({p['placement']})" for p in proposals)
        _git(root, "commit", "-m", "propose image placements (PoC)\n\n" + summary)
    finally:
        _git(root, "checkout", base)  # always restore the original branch
    return branch, base, rel


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manuscript", type=Path, help="VFM/Markdown manuscript")
    ap.add_argument("--images", type=Path, required=True, help="directory of images")
    ap.add_argument("--out", type=Path, default=Path("output"), help="output directory")
    ap.add_argument("--build", action="store_true",
                    help="run `vivliostyle build` on the modified copy")
    ap.add_argument("--no-git", action="store_true",
                    help="do not create a proposal branch even if in a repo")
    args = ap.parse_args()

    base_url = os.environ.get("VL_BASE_URL", "http://localhost:11434/v1")
    api_key = os.environ.get("VL_API_KEY", "not-needed")
    model = os.environ.get("VL_MODEL", "qwen3-vl:8b")

    src = args.manuscript.read_text(encoding="utf-8")
    blocks = split_blocks(src)
    manuscript_text = numbered_manuscript(blocks)

    images = sorted(p for p in args.images.iterdir()
                    if p.suffix.lower() in IMAGE_EXTS)
    if not images:
        raise SystemExit(f"No images found in {args.images}")

    print(f"Endpoint: {base_url}  model: {model}")
    print(f"Manuscript: {len(blocks)} blocks   Images: {len(images)}\n")

    proposals = []
    placements: list[tuple[dict, Path]] = []
    for img in images:
        print(f"  → asking about {img.name} ...", flush=True)
        raw = call_vlm(base_url, api_key, model, manuscript_text, img)
        prop = validate(raw, len(blocks), img.name)
        prop["image"] = img.name
        proposals.append(prop)
        placements.append((prop, img))
        print(f"    after block {prop['anchor_block_index']} "
              f"({prop['placement']}, conf={prop.get('confidence')}): "
              f"{prop.get('reason','')}")

    args.out.mkdir(parents=True, exist_ok=True)

    # Make out/ a self-contained bundle: bring the images in as out/images so
    # the modified manuscript can reference them as `images/<name>` with no
    # parent-escaping `../` paths (which Vivliostyle's dev server won't serve).
    img_out = args.out / "images"
    if img_out.is_symlink() or img_out.exists():
        img_out.unlink() if img_out.is_symlink() else shutil.rmtree(img_out)
    shutil.copytree(args.images, img_out)

    # out/ copy: paths relative to out/ (→ images/<name>, via the copy above).
    placements_out = [(prop, img_out / img.name) for prop, img in placements]
    modified = build_modified(blocks, placements_out, args.out)

    (args.out / "proposals.json").write_text(
        json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8")
    modified_path = args.out / (args.manuscript.stem + ".modified.md")
    modified_path.write_text(modified, encoding="utf-8")

    diff = difflib.unified_diff(
        src.splitlines(keepends=True), modified.splitlines(keepends=True),
        fromfile=str(args.manuscript), tofile=str(modified_path))
    (args.out / "changes.diff").write_text("".join(diff), encoding="utf-8")

    print(f"\nWrote:\n  {modified_path}\n  {args.out/'proposals.json'}\n"
          f"  {args.out/'changes.diff'}")
    print(f"Source manuscript ({args.manuscript}) was NOT modified.")

    if in_git_repo(args.manuscript) and not args.no_git:
        modified_inplace = build_modified(blocks, placements,
                                          args.manuscript.parent)
        result = propose_on_branch(args.manuscript, modified_inplace, proposals)
        if result:
            branch, base, rel = result
            print(f"\n[git] Proposal committed on branch '{branch}'; "
                  f"'{base}' left untouched.\n"
                  f"      review:  git diff {base}..{branch} -- {rel}\n"
                  f"      inspect: git switch {branch}    (or open a PR)")

    if args.build:
        pdf = args.out / (args.manuscript.stem + ".pdf")
        print(f"\nBuilding with Vivliostyle CLI → {pdf}")
        try:
            subprocess.run(["vivliostyle", "build", str(modified_path),
                            "-o", str(pdf)], check=True)
            print(f"Built {pdf}")
        except FileNotFoundError:
            print("  vivliostyle CLI not found (npm i -g @vivliostyle/cli). Skipped.")
        except subprocess.CalledProcessError as e:
            print(f"  Build failed (exit {e.returncode}). Inspect the diff/markup.")


if __name__ == "__main__":
    main()
