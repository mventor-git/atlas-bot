"""Transplant a logo PNG into a .ots template + fix brand strings.

Fork helper: public templates ship generic; the company fork re-applies
branding with this script.

LIMITATION (verified 2026-09-08): draw:frames authored by odfpy inside
Calc cells do NOT paint in headless PDF export (Calc floats drawings;
odfpy cannot express sheet-anchored placement). To place a logo: open
the template once in LibreOffice, drag the image into the header cell,
save. The filler never touches that cell, so the logo survives fills.
This script's text-rebrand path works fine headless.
"""

Usage:
    python scripts/transplant_logo.py <template.ots> [--logo IMG.png --as NAME]
                                     [--brand-from OLD --brand-to NEW]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Logo transplant + rebrand for .ots templates")
    ap.add_argument("template", help="Path to .ots file (modified in place)")
    ap.add_argument("--logo", default=None, help="PNG to embed as Pictures/<as>")
    ap.add_argument("--as", dest="as_name", default="logo.png", help="Name under Pictures/")
    ap.add_argument("--width", default="4.0cm", help="Frame width")
    ap.add_argument("--height", default="1.38cm", help="Frame height")
    ap.add_argument("--brand-from", default=None)
    ap.add_argument("--brand-to", default=None)
    args = ap.parse_args()

    tpl = Path(args.template)
    work = Path(tempfile.mkdtemp(prefix="ots"))
    try:
        # 1. odfpy DOM pass: brand text + logo frame reference
        from odf.draw import Frame, Image
        from odf.opendocument import load
        from odf.table import Table, TableCell, TableRow
        from odf.text import P

        doc = load(str(tpl))
        # Idempotent: skip when this logo is already embedded
        with zipfile.ZipFile(tpl, "r") as _z:
            if "Pictures/" + args.as_name in _z.namelist():
                print("SKIP (already embedded):", tpl)
                return 0
        if args.brand_from and args.brand_to:
            old_b = args.brand_from.encode("utf-8")
            new_b = args.brand_to.encode("utf-8")
            for p in doc.getElementsByType(P):
                for node in list(p.childNodes):
                    if node.nodeType == 3 and old_b.decode("utf-8") in str(node.data):
                        node.data = str(node.data).replace(
                            args.brand_from, args.brand_to
                        )
        if args.logo:
            tables = doc.getElementsByType(Table)
            row = tables[0].getElementsByType(TableRow)[0]
            cell = row.getElementsByType(TableCell)[0]
            p = P()
            frame = Frame(
                name="Logo", width=args.width, height=args.height,
                x="0cm", y="0cm", anchortype="as-char",
            )
            frame.addElement(Image(href="Pictures/" + args.as_name, type="simple",
                                     show="embed", actuate="onLoad"))
            p.addElement(frame)
            cell.addElement(p)
        doc.save(str(tpl))

        # 2. Zip pass: add the PNG bytes + manifest entry
        if args.logo:
            with zipfile.ZipFile(tpl, "a", zipfile.ZIP_DEFLATED) as z:
                z.write(args.logo, "Pictures/" + args.as_name)
            with zipfile.ZipFile(tpl, "r") as z:
                manifest = z.read("META-INF/manifest.xml").decode("utf-8")
            entry = (
                '<manifest:file-entry manifest:full-path="Pictures/%s" '
                'manifest:media-type="image/png"/>' % args.as_name
            )
            if "Pictures/" + args.as_name not in manifest:
                manifest = manifest.replace(
                    "</manifest:manifest>", " " + entry + "</manifest:manifest>"
                )
            # rewrite zip with updated manifest (mimetype must stay first, stored)
            tmp = work / "new.ots"
            with zipfile.ZipFile(tpl, "r") as zin:
                with zipfile.ZipFile(tmp, "w") as zout:
                    for item in zin.infolist():
                        data = zin.read(item.filename)
                        if item.filename == "META-INF/manifest.xml":
                            data = manifest.encode("utf-8")
                        zout.writestr(item, data)
            shutil.move(str(tmp), str(tpl))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("OK:", tpl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
