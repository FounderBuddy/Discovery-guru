#!/usr/bin/env python3
"""Write every packet contract to contracts/ as JSON Schema, from the pydantic models in sdp_common.

    python scripts/gen_schemas.py          # regenerate
    python scripts/gen_schemas.py --check  # fail if contracts/ is out of date (used in CI)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "libs" / "sdp_common"))

from sdp_common.contracts import EVENT_TYPES, PACKETS  # noqa: E402
from sdp_common.events import CloudEvent  # noqa: E402


def outputs() -> dict[Path, str]:
    out: dict[Path, str] = {}
    dump = lambda schema: json.dumps(schema, indent=2, sort_keys=True) + "\n"
    out[ROOT / "contracts" / "cloudevent.schema.json"] = dump(CloudEvent.model_json_schema())
    for name, model in PACKETS.items():
        out[ROOT / "contracts" / "packets" / f"{name}.schema.json"] = dump(model.model_json_schema())
    for etype, model in EVENT_TYPES.items():
        schema = model.model_json_schema()
        schema["description"] = f"The `data` payload of a {etype} CloudEvent."
        out[ROOT / "contracts" / "events" / f"{etype}.schema.json"] = dump(schema)
    return out


def main() -> None:
    check = "--check" in sys.argv
    stale = []
    for path, text in outputs().items():
        if check:
            if not path.exists() or path.read_text() != text:
                stale.append(path.relative_to(ROOT))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if stale:
        print("Contracts out of date:", *stale, sep="\n  ")
        sys.exit(1)
    print("contracts ok" if check else f"wrote {len(outputs())} schemas")


if __name__ == "__main__":
    main()
