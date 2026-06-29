"""Alias entrypoint for the LIVE smoke test (see `scripts/smoke_live.py`).

`make smoke` runs `python -m scripts.smoke`; INTERFACES.md names the script
`smoke.py`. The canonical implementation lives in `smoke_live.py`; this module
just forwards so both `make smoke` and `python -m scripts.smoke_live` work.
"""

from __future__ import annotations

from scripts.smoke_live import main

if __name__ == "__main__":
    main()
