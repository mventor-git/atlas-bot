"""Check if the Excel template has images and if openpyxl preserves them."""
import sys
from pathlib import Path

_root = Path(__file__).parent.parent.resolve()
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import openpyxl
from app.config.loader import ConfigLoader

config = ConfigLoader.load()
template_path = config.template_path
print(f"Template: {template_path}")
print(f"Exists: {template_path.exists()}")

wb = openpyxl.load_workbook(filename=str(template_path), data_only=False)
ws = wb.active
print(f"\nSheet: {ws.title}")

# Check images
images = ws._images
print(f"Images found by openpyxl: {len(images)}")
for i, img in enumerate(images):
    print(f"  Image {i}:")
    print(f"    Anchor: {img.anchor}")
    print(f"    Width: {img.width}, Height: {img.height}")
    if hasattr(img, '_src'):
        print(f"    Source: {img._src}")

# Try saving and re-checking
test_path = Path("exports/preview/test_image_check.xlsx")
test_path.parent.mkdir(parents=True, exist_ok=True)
wb.save(str(test_path))
wb.close()

# Reload the saved file
wb2 = openpyxl.load_workbook(filename=str(test_path), data_only=False)
ws2 = wb2.active
images2 = ws2._images
print(f"\nImages after openpyxl save: {len(images2)}")
for i, img in enumerate(images2):
    print(f"  Image {i}: anchor={img.anchor}")

wb2.close()
test_path.unlink(missing_ok=True)

# Check for any drawing objects (charts, etc.)
import zipfile
with zipfile.ZipFile(template_path, 'r') as z:
    drawing_files = [f for f in z.namelist() if 'drawing' in f.lower()]
    media_files = [f for f in z.namelist() if 'media' in f.lower()]
    print(f"\nDrawing XML files in original: {drawing_files}")
    print(f"Media files in original: {media_files}")
    
with zipfile.ZipFile(test_path, 'r') as z:
    drawing_files2 = [f for f in z.namelist() if 'drawing' in f.lower()]
    media_files2 = [f for f in z.namelist() if 'media' in f.lower()]
    print(f"\nDrawing XML files after save: {drawing_files2}")
    print(f"Media files after save: {media_files2}")
