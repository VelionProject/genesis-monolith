from __future__ import annotations

import argparse
from pathlib import Path

from .config import WorldConfig
from .headless import run_headless
from .hunter import ensure_dir, now_id
from .ui import run_ui


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Genesis Monolith")
    p.add_argument("--seed", type=int, default=12345)
    p.add_argument("--size", type=int, default=256)
    p.add_argument("--headless", action="store_true")
    p.add_argument("--ticks", type=int, default=20000)
    p.add_argument("--snapshot-every", type=int, default=2000)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = WorldConfig(size=int(args.size), snapshot_every_ticks=int(args.snapshot_every))

    run_dir = ensure_dir(Path(cfg.out_dir) / now_id())
    (run_dir / "snapshots").mkdir(parents=True, exist_ok=True)

    if args.headless:
        run_headless(cfg, seed=int(args.seed), ticks=int(args.ticks), run_dir=run_dir)
    else:
        run_ui(cfg, seed=int(args.seed), run_dir=run_dir)


if __name__ == "__main__":
    main()
