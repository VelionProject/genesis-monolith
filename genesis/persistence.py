from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import numpy as np


# =========================
# Layer: Persistence
# =========================

@dataclass
class Snapshot:
    tick: int
    seed: int
    cfg: Dict
    rng_state: Dict
    meta: Dict
    arrays: Dict[str, np.ndarray]

    def save_npz(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "tick": self.tick,
            "seed": self.seed,
            "cfg": self.cfg,
            "rng_state": self.rng_state,
            "meta": self.meta,
            "arrays": list(self.arrays.keys()),
        }
        np.savez_compressed(path, __meta__=json.dumps(meta), **self.arrays)

    @staticmethod
    def load_npz(path: Path) -> "Snapshot":
        data = np.load(path, allow_pickle=False)
        meta = json.loads(str(data["__meta__"]))
        arrays = {k: data[k] for k in meta["arrays"]}
        return Snapshot(
            tick=int(meta["tick"]),
            seed=int(meta["seed"]),
            cfg=meta["cfg"],
            rng_state=meta["rng_state"],
            meta=meta.get("meta", {}),
            arrays=arrays
        )


class EventLog:
    """Append-only JSONL. Observability only (does not drive the core)."""
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, event: Dict) -> None:
        self._fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass
