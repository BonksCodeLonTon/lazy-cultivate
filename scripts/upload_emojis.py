"""Upload PNG/GIF/JPG/WebP files in ``emojis/`` as Discord application emojis,
one folder at a time so each batch can be reviewed.

Reads DISCORD_TOKEN from .env and uses the application ID below.

Usage
-----
List the top-level subfolders and counts (no upload):
    python scripts/upload_emojis.py --list

Upload one folder (recurses into its subfolders):
    python scripts/upload_emojis.py "Effect"
    python scripts/upload_emojis.py "đan dược/5"
    python scripts/upload_emojis.py "trang bị"

Dry-run (plan + slug check, no API calls):
    python scripts/upload_emojis.py "Effect" --dry-run

Upload everything (skip the per-folder gate):
    python scripts/upload_emojis.py --all

What it does
------------
* Walks the chosen subfolder for image files.
* Slugifies each filename → ASCII ``[a-z0-9_]`` (Discord requirement).
* Resolves slug collisions by prepending the parent folder slug.
* Auto-downscales any file > 250 KB with Pillow until it fits Discord's 256 KB cap.
* Lists existing application emojis once and skips any whose name already exists.
* POSTs ``application/emojis`` for new ones.
* Appends to ``src/utils/emojis_generated.py`` with ``EMOJI_<SLUG> = "<:name:id>"``
  constants — re-runs merge with whatever's already there.
"""
from __future__ import annotations

import base64
import io
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image

# Force UTF-8 output so Vietnamese folder/filenames print on Windows.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# ── Config ────────────────────────────────────────────────────────────────────
APPLICATION_ID = "1490557962006171669"
EMOJI_DIR = Path(__file__).resolve().parent.parent / "emojis"
OUTPUT_PY = Path(__file__).resolve().parent.parent / "src" / "utils" / "emojis_generated.py"
DISCORD_API = "https://discord.com/api/v10"
MAX_BYTES = 256 * 1024
TARGET_BYTES = 240 * 1024  # leave headroom
IMAGE_EXTS = {".png", ".gif", ".jpg", ".jpeg", ".webp"}


# ── Slugify ───────────────────────────────────────────────────────────────────
_DIACRITIC_MAP = str.maketrans({"đ": "d", "Đ": "d"})


def slugify(name: str) -> str:
    """Return a Discord-safe emoji name: ``[a-z0-9_]``, 2–32 chars."""
    s = name.translate(_DIACRITIC_MAP)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    if len(s) < 2:
        s = f"e_{s}" if s else "emoji"
    return s[:32]


# ── Image fitting ─────────────────────────────────────────────────────────────
def fit_to_limit(path: Path) -> tuple[bytes, str]:
    """Read the file; if oversized (and not animated), downscale until it fits.

    Returns (bytes, mime_type).
    """
    raw = path.read_bytes()
    ext = path.suffix.lower()

    # Animated GIFs: never re-encode (would lose animation), just hand back as-is.
    # If a GIF is over the limit, skip in caller.
    if ext == ".gif":
        return raw, "image/gif"

    if len(raw) <= MAX_BYTES:
        if ext in (".jpg", ".jpeg"):
            return raw, "image/jpeg"
        if ext == ".webp":
            return raw, "image/webp"
        return raw, "image/png"

    # Oversized PNG/JPG/WebP: downscale iteratively until under target.
    img = Image.open(io.BytesIO(raw))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")

    for _ in range(10):
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        if len(data) <= TARGET_BYTES:
            return data, "image/png"
        new_w = max(64, int(img.width * 0.75))
        new_h = max(64, int(img.height * 0.75))
        if (new_w, new_h) == img.size:
            break
        img = img.resize((new_w, new_h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), "image/png"


# ── Discord API ───────────────────────────────────────────────────────────────
def list_existing(token: str) -> dict[str, str]:
    """Return ``{name: id}`` for emojis already attached to this application."""
    url = f"{DISCORD_API}/applications/{APPLICATION_ID}/emojis"
    r = requests.get(url, headers={"Authorization": f"Bot {token}"}, timeout=20)
    r.raise_for_status()
    items = r.json().get("items", [])
    return {it["name"]: it["id"] for it in items}


def upload_one(token: str, name: str, data: bytes, mime: str) -> dict:
    """POST a single emoji; returns the API response on success."""
    b64 = base64.b64encode(data).decode("ascii")
    body = {"name": name, "image": f"data:{mime};base64,{b64}"}
    url = f"{DISCORD_API}/applications/{APPLICATION_ID}/emojis"
    r = requests.post(
        url,
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    if r.status_code == 429:
        retry = float(r.headers.get("Retry-After", "1"))
        time.sleep(retry + 0.2)
        return upload_one(token, name, data, mime)
    if not r.ok:
        raise RuntimeError(f"upload {name!r} failed [{r.status_code}]: {r.text}")
    return r.json()


# ── Walk + plan ───────────────────────────────────────────────────────────────
def collect_files(root: Path) -> list[tuple[Path, str]]:
    """Return ``[(path, slug)]`` with collisions resolved by parent-folder prefix."""
    candidates: list[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            candidates.append(p)

    # First pass: stem only.
    by_slug: dict[str, list[Path]] = {}
    for p in candidates:
        by_slug.setdefault(slugify(p.stem), []).append(p)

    # Resolve collisions: prepend slug of the immediate parent folder.
    resolved: list[tuple[Path, str]] = []
    used: set[str] = set()
    for slug, paths in by_slug.items():
        if len(paths) == 1:
            final = slug
            i = 2
            while final in used:
                final = f"{slug}_{i}"[:32]
                i += 1
            used.add(final)
            resolved.append((paths[0], final))
        else:
            for p in paths:
                parent_slug = slugify(p.parent.name) or "x"
                final = f"{parent_slug}_{slug}"[:32]
                i = 2
                while final in used:
                    final = f"{parent_slug}_{slug}_{i}"[:32]
                    i += 1
                used.add(final)
                resolved.append((p, final))
    return resolved


# ── Output file ───────────────────────────────────────────────────────────────
_EMOJI_LINE_RE = re.compile(r"^\s*[\"\']?(?P<name>[a-z0-9_]+)[\"\']?\s*:\s*[\"\'](?P<val>[^\"\']+)[\"\']")


def read_existing_output() -> dict[str, str]:
    """Parse the previous EMOJIS dict from emojis_generated.py if present."""
    if not OUTPUT_PY.exists():
        return {}
    out: dict[str, str] = {}
    in_block = False
    for line in OUTPUT_PY.read_text(encoding="utf-8").splitlines():
        if line.startswith("EMOJIS"):
            in_block = True
            continue
        if in_block:
            if line.startswith("}"):
                break
            m = _EMOJI_LINE_RE.match(line)
            if m:
                out[m.group("name")] = m.group("val")
    return out


def write_output(mapping: dict[str, str]) -> None:
    """Write ``EMOJI_<SLUG> = "<:name:id>"`` constants for import (merges with prior)."""
    merged = {**read_existing_output(), **mapping}
    OUTPUT_PY.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '"""Auto-generated by ``scripts/upload_emojis.py`` — do not edit by hand."""',
        "from __future__ import annotations",
        "",
        "EMOJIS: dict[str, str] = {",
    ]
    for name in sorted(merged):
        lines.append(f"    {name!r}: {merged[name]!r},")
    lines.append("}")
    lines.append("")
    for name in sorted(merged):
        const = f"EMOJI_{name.upper()}"
        lines.append(f"{const} = {merged[name]!r}")
    lines.append("")
    OUTPUT_PY.write_text("\n".join(lines), encoding="utf-8")
    print(f"  wrote {OUTPUT_PY.relative_to(OUTPUT_PY.parents[2])} ({len(merged)} total entries)")


# ── Main ──────────────────────────────────────────────────────────────────────
def print_usage() -> None:
    print("Usage:")
    print("  python scripts/upload_emojis.py --list")
    print("  python scripts/upload_emojis.py <subfolder> [--dry-run]")
    print("  python scripts/upload_emojis.py --all [--dry-run]")
    print()
    print("Top-level folders:")
    for sub in sorted(EMOJI_DIR.iterdir()):
        if sub.is_dir():
            count = sum(
                1 for p in sub.rglob("*")
                if p.is_file() and p.suffix.lower() in IMAGE_EXTS
            )
            print(f"  {count:4d}  {sub.name}")


def resolve_target(arg: str) -> Path | None:
    """Accept either a path-like arg ('Effect' or 'đan dược/5') and return a real folder."""
    p = (EMOJI_DIR / arg).resolve()
    try:
        p.relative_to(EMOJI_DIR.resolve())
    except ValueError:
        return None
    return p if p.is_dir() else None


def main(argv: list[str]) -> int:
    load_dotenv()
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN not set in .env", file=sys.stderr)
        return 2

    if not argv or argv[0] in {"-h", "--help"}:
        print_usage()
        return 0
    if argv[0] == "--list":
        print_usage()
        return 0

    dry_run = "--dry-run" in argv
    if argv[0] == "--all":
        target = EMOJI_DIR
    else:
        target = resolve_target(argv[0])
        if target is None:
            print(f"Folder not found: emojis/{argv[0]}", file=sys.stderr)
            print_usage()
            return 2

    print(f"Scanning {target}…")
    plan = collect_files(target)
    print(f"  found {len(plan)} image file(s)")
    if not plan:
        return 0

    if dry_run:
        for path, name in plan:
            print(f"  {name:32s} <- {path.relative_to(EMOJI_DIR)}")
        print("\n(dry-run: no API calls made)")
        return 0

    print("Listing existing application emojis…")
    existing = list_existing(token)
    print(f"  {len(existing)} already on the application")

    mapping: dict[str, str] = {}  # name → "<:name:id>"
    uploaded = 0
    skipped = 0
    failed: list[tuple[str, str]] = []

    for path, name in plan:
        if name in existing:
            mapping[name] = f"<:{name}:{existing[name]}>"
            skipped += 1
            continue
        try:
            data, mime = fit_to_limit(path)
            if len(data) > MAX_BYTES:
                failed.append((name, f"still {len(data)} bytes after fit"))
                continue
            resp = upload_one(token, name, data, mime)
            emoji_id = resp["id"]
            mapping[name] = f"<:{name}:{emoji_id}>"
            uploaded += 1
            print(f"  [{uploaded}] {name} <- {path.relative_to(EMOJI_DIR)}")
        except Exception as e:
            failed.append((name, str(e)))
            print(f"  FAIL {name}: {e}", file=sys.stderr)

    print(f"\nUploaded: {uploaded}  Skipped (already present): {skipped}  Failed: {len(failed)}")
    if failed:
        print("Failures:")
        for n, msg in failed[:20]:
            print(f"  - {n}: {msg}")

    write_output(mapping)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
