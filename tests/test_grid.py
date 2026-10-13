import io

from PIL import Image

from leadgen.qualify import grid


def jpeg(color, size=(400, 600)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, 'JPEG')
    return buf.getvalue()


def test_compose_full_grid(tmp_path):
    path = str(tmp_path / 'g.jpg')
    placed = grid.compose([jpeg((200, 0, 0))] * 12, path)
    img = Image.open(path)
    assert placed == 12
    assert img.size == (960, 1280)
    assert img.getpixel((10, 10))[0] > 150


def test_compose_short_feed_leaves_blank_cells(tmp_path):
    path = str(tmp_path / 'g.jpg')
    assert grid.compose([jpeg((0, 0, 200))] * 2, path) == 2
    img = Image.open(path)
    assert img.getpixel((960 - 10, 1280 - 10)) == img.getpixel((960 - 20, 1280 - 20))


def test_compose_skips_missing_and_broken_images(tmp_path):
    path = str(tmp_path / 'g.jpg')
    assert grid.compose([None, b'not an image', jpeg((0, 200, 0))], path) == 1


def test_compose_creates_directory(tmp_path):
    path = str(tmp_path / 'deep' / 'dir' / 'g.jpg')
    grid.compose([jpeg((1, 2, 3))], path)
    assert Image.open(path).size == (960, 1280)


def test_path_for(monkeypatch, tmp_path):
    monkeypatch.setattr(grid, 'GRIDS_DIR', str(tmp_path))
    assert grid.path_for('Ivan.Chef') == str(tmp_path / 'ivan.chef.jpg')
    assert grid.path_for('chef_amber_', ref=True) == str(tmp_path / '_ref' / 'chef_amber_.jpg')
