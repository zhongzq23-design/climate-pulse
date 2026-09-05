#!/usr/bin/env python3
"""Compatibility stub for the retired drought-population enrichment step.

Climate Pulse no longer presents drought population exposure. Drought exposure is
now built by ``enrich_event_footprints.py`` + ``enrich_asset_exposure.py`` and is
expressed as mapped area plus land, forest and crop-area overlap when the relevant
reference rasters are available.

This file remains only so old local commands fail safely rather than silently
re-introducing the previous population metric.
"""


def main() -> None:
    print(
        "Drought population exposure is retired. "
        "Run scripts/enrich_event_footprints.py followed by "
        "scripts/enrich_asset_exposure.py instead."
    )


if __name__ == "__main__":
    main()
