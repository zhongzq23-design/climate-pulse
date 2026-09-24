#!/usr/bin/env python3
"""Synchronize generated Climate Pulse products with the isolated production-data branch.

Trusted executable code lives on main. The production-data checkout is intentionally
limited to browser/publication data and report artifacts so automated write access
never needs to modify executable workflow or processing code.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ALLOWED_PATHS = {
    "data/events",
    "data/exposure/population",
    "data/exposure/assets",
    "data/footprints",
    "data/climate/event_timeseries",
    "data/history/daily",
    "data/reports",
    "reports",
    "data/reference/crops/cropgrids_2020",
    "data/reference/climate/cru_ts_4.10",
    "data/reference/landcover/modis_mcd12c1_2024",
    "data/reference/population",
}
STORE_TOP_LEVEL = {".git", "README.md", "data", "reports"}


def normalize_rel(value: str) -> Path:
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts:
        raise RuntimeError(f"Unsafe production-data path: {value}")
    normalized = rel.as_posix().rstrip("/")
    if normalized not in ALLOWED_PATHS:
        raise RuntimeError(f"Path is not authorized for automated production writes: {value}")
    return Path(normalized)


def validate_store_boundary(store: Path) -> None:
    if not store.is_dir():
        raise RuntimeError(f"Production-data checkout is missing: {store}")

    extras = sorted(p.name for p in store.iterdir() if p.name not in STORE_TOP_LEVEL)
    if extras:
        raise RuntimeError(
            "Production-data boundary failed; unexpected top-level entries: "
            + ", ".join(extras)
        )

    for root_name in ("data", "reports"):
        root = store / root_name
        if not root.exists():
            continue
        if root.is_symlink():
            raise RuntimeError(f"Production-data boundary failed; symlink root: {root_name}")
        for path in root.rglob("*"):
            if path.is_symlink():
                raise RuntimeError(
                    "Production-data boundary failed; symlink is not allowed: "
                    + str(path.relative_to(store))
                )


def replace_path(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if src.is_symlink():
        raise RuntimeError(f"Refusing to synchronize symlink: {src}")
    if dst.exists() or dst.is_symlink():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, symlinks=False)
    else:
        shutil.copy2(src, dst)


def sync(action: str, store: Path, paths: list[Path]) -> None:
    validate_store_boundary(store)
    if action == "restore":
        source_root, dest_root = store, ROOT
    elif action == "publish":
        source_root, dest_root = ROOT, store
    else:
        raise RuntimeError(f"Unsupported action: {action}")

    for rel in paths:
        replace_path(source_root / rel, dest_root / rel)

    validate_store_boundary(store)
    print(
        f"PRODUCTION_DATA_SYNC=PASS action={action} "
        f"paths={','.join(p.as_posix() for p in paths)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("restore", "publish"))
    parser.add_argument("--store", default=".production-data")
    parser.add_argument("--paths", nargs="+", required=True)
    args = parser.parse_args()

    store = Path(args.store)
    if not store.is_absolute():
        store = ROOT / store
    paths = [normalize_rel(value) for value in args.paths]
    sync(args.action, store, paths)


if __name__ == "__main__":
    main()
