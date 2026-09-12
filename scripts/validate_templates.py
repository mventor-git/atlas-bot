"""Validate production daily templates (ticket-030).

Deterministic preflight for developers/CI — run before deployment:

    python scripts/validate_templates.py [--config config/config.yaml] [--render]

Checks (per template): file, sheet, header row, header-driven column
contract (workers/craftsmen/helpers/details/totals), day/date cells,
data slots, fill smoke test. --render additionally converts via
headless soffice and asserts page counts stay at baseline (small 1,
medium 2, large 3).

Exit 0 when every template passes (render steps SKIP cleanly when
soffice is absent); exit 1 with actionable lines otherwise.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

BASELINE_PAGES = {"small": 1, "medium": 2, "large": 3}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Validate daily templates.")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--render", action="store_true",
                        help="also render via soffice and check page counts")
    args = parser.parse_args()

    from app.config.loader import ConfigLoader
    from app.libre.validate import TemplateValidator

    try:
        config = ConfigLoader.load(args.config)
    except Exception as e:
        print(f"FAIL config: {e}")
        return 1

    keys = (("small", config.template.small_template),
            ("medium", config.template.medium_template),
            ("large", config.template.large_template))
    validator = TemplateValidator(config)
    failed = False
    for name, path in keys:
        report = validator.check_template(path)
        print(("PASS " if report.passed else "FAIL ") + f"{name}: {path}")
        for failure in report.failures:
            print(f"  FAIL {failure}")
            failed = True
        if not report.passed:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            try:
                out = validator.smoke_fill(path, tmp, config)
                print(f"  PASS {name} fill smoke: {Path(out).name}")
            except Exception as e:
                print(f"  FAIL {name} fill smoke: {e}")
                failed = True
                continue
            if args.render:
                try:
                    from app.libre.pdf import PDFGenerator, find_soffice
                    find_soffice()
                except Exception:
                    print(f"  SKIP {name} render: soffice not available")
                    continue
                pdf = Path(tmp) / f"{name}.pdf"
                PDFGenerator(config).convert_to_pdf(str(out), str(pdf))
                import re
                pages = len(re.findall(rb"/Type\s*/Page[^s]",
                                       pdf.read_bytes()))
                want = BASELINE_PAGES[name]
                if pages != want:
                    print(f"  FAIL {name} render: {pages} pages, "
                          f"baseline {want}")
                    failed = True
                else:
                    print(f"  PASS {name} render: {pages} page(s)")
    print("Templates: " + ("PASS" if not failed else "FAIL"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
