"""3x4 feed grid as a single image — what the Sonnet grader sees.

One composite instead of twelve separate photos: roughly ten times fewer
tokens per profile, while preserving the overall feed impression the
client judges by."""
import io
import os

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GRIDS_DIR = os.environ.get('LEADGEN_GRIDS_DIR', os.path.join(ROOT, 'db', 'grids'))

TILE, COLS, ROWS = 320, 3, 4
BLANK = (235, 235, 235)


def path_for(nick, ref=False):
    name = '%s.jpg' % (nick or '').strip().lstrip('@').lower()
    return os.path.join(GRIDS_DIR, '_ref', name) if ref else os.path.join(GRIDS_DIR, name)


def _tile(data):
    img = Image.open(io.BytesIO(data)).convert('RGB')
    side = min(img.size)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    return img.crop((left, top, left + side, top + side)).resize((TILE, TILE))


def compose(images, path):
    canvas = Image.new('RGB', (TILE * COLS, TILE * ROWS), BLANK)
    placed = 0
    for data in images:
        if placed >= COLS * ROWS:
            break
        if not data:
            continue
        try:
            tile = _tile(data)
        except Exception:
            continue  # broken image -> leave the tile blank instead of failing the task
        canvas.paste(tile, ((placed % COLS) * TILE, (placed // COLS) * TILE))
        placed += 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    canvas.save(path, 'JPEG', quality=82)
    return placed
