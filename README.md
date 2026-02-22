# genesis-monolith

Deterministische 2D-Weltsimulation mit Fokus auf **Chemie → Strukturbildung → Replikation → Agenten-Suche**.

Das Projekt kombiniert einen numerischen Simulationskern mit einer interaktiven Cockpit-UI (PySide6) und einem Headless-Modus für reproduzierbare Runs, Snapshots und Analysen.

---

## Inhalte

- [Features](#features)
- [Architektur](#architektur)
- [Projektstruktur](#projektstruktur)
- [Voraussetzungen](#voraussetzungen)
- [Installation](#installation)
- [Schnellstart](#schnellstart)
- [CLI-Optionen](#cli-optionen)
- [Tests & Qualitätssicherung](#tests--qualitätssicherung)
- [Determinismus & Snapshots](#determinismus--snapshots)
- [Entwicklungshinweise](#entwicklungshinweise)
- [Roadmap (kurz)](#roadmap-kurz)
- [Lizenz](#lizenz)

---

## Features

- Deterministischer `PhysicsCore` (bei gleicher Config/Seed reproduzierbar).
- Persistente Snapshots (`.npz`) inkl. RNG-State für Replay/Fork.
- Headless-Ausführung für lange Experimente.
- UI-Cockpit mit Live-Visualisierung der Felder (`E`, `R1`, `R2`, `S`, `T`, `M`).
- Activity-Overlay (`ΔS`) mit Zustandslabels (`stable`, `moderate`, `active`).
- Hunter-Agent (Seed-Suche) mit Anomaly-Inbox.
- Observability-Helfer für Cluster/Fingerprints/Matching.

---

## Architektur

Das Projekt wurde in ein Paket modularisiert, um Logik und Darstellung sauber zu trennen:

- `genesis/config.py` – Simulations-/Runtime-Konfiguration (`WorldConfig`)
- `genesis/core.py` – numerischer Kern (`PhysicsCore`)
- `genesis/persistence.py` – Snapshot/Eventlog (`Snapshot`, `EventLog`)
- `genesis/observability.py` – Clustering, Fingerprints, Aktivitätsmetriken
- `genesis/hunter.py` – Hunter-/Anomaly-Helfer
- `genesis/ui.py` – UI-Cockpit (`run_ui`)
- `genesis/headless.py` – Headless-Runner (`run_headless`)
- `genesis/cli.py` – CLI-Parsing + Main-Entry (`main`)

`Monolith.py` bleibt als **Kompatibilitäts-Fassade** erhalten und exportiert die öffentlichen Symbole weiter.

---

## Projektstruktur

```text
.
├── Monolith.py
├── genesis/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── core.py
│   ├── headless.py
│   ├── hunter.py
│   ├── observability.py
│   ├── persistence.py
│   └── ui.py
├── tests/
├── docs/
├── CHANGELOG.md
└── README.md
```

---

## Voraussetzungen

- Python 3.10+
- Für Core/Tests:
  - `numpy`
  - `pytest`
- Für UI:
  - `PySide6`
  - `pyqtgraph` **oder** `matplotlib`

---

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install numpy pytest PySide6 pyqtgraph matplotlib
```

> Hinweis: Für reine Headless-/Core-Arbeit reichen `numpy` und `pytest`.

---

## Schnellstart

### UI starten

```bash
python Monolith.py
# alternativ (Modulstart)
python -m genesis.ui
# alternativ (direkte Datei, jetzt kompatibel)
python genesis/ui.py
```

### Headless starten

```bash
python Monolith.py --headless --seed 12345 --ticks 200000 --snapshot-every 5000
```

Outputs landen standardmäßig unter `runs/<timestamp>/`.

---

## CLI-Optionen

Aktuell verfügbar:

- `--seed <int>`
- `--size <int>`
- `--headless`
- `--ticks <int>`
- `--snapshot-every <int>`

Die CLI bleibt bewusst kompakt; Fokus ist eine GUI-first-Erfahrung mit optionalen Profi-CLI-Workflows.

---

## Tests & Qualitätssicherung

Empfohlene lokale Checks:

```bash
# Syntax-/Import-Integrität
python -m py_compile Monolith.py genesis/*.py

# Test-Suite
python -m pytest -q
```

Aktuelle Testabdeckung umfasst u. a.:

- Determinismus (`test_determinism.py`)
- Snapshot-Roundtrip (`test_snapshot.py`)
- numerische Stabilität (`test_no_nan.py`)
- Mass-/Bounds-Sanity (`test_mass.py`)
- Activity-Overlay-Helfer (`test_activity_overlay.py`)

---

## Determinismus & Snapshots

- `PhysicsCore.state_hash()` liefert eine deterministische Zustands-Checksumme.
- `Snapshot` speichert Felder + Config + RNG-State.
- Geladene Snapshots können als Fork-Basis weiterlaufen.

Damit lassen sich Runs reproduzierbar vergleichen und Anomalien sauber analysieren.

---

## Entwicklungshinweise

- Trennung von Domänenlogik (Core) und Präsentation (UI) beibehalten.
- Neue Features möglichst zuerst im Core/Helper isolieren, dann UI anbinden.
- Bei strukturellen Änderungen stets `CHANGELOG.md` aktualisieren.
- Öffentliche API über `Monolith.py`/`genesis.__init__` stabil halten, um externe Imports nicht zu brechen.

---

## Roadmap (kurz)

- Replay-/Sweep-CLI für erweiterte Experimentsteuerung
- Ausbau der Overlay-Roadmap (Stress-/Flow-Layer)
- weitere Integrations-/Regressionschecks für UI-nahe Workflows

---

## Lizenz

Siehe [`LICENSE`](./LICENSE).
