from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

from .config import WorldConfig
from .core import PhysicsCore
from .hunter import _seed_stream, alive_score, ensure_dir, quick_reason, save_anomaly_bundle
from .observability import (
    classify_activity_level,
    compute_activity_map,
    compute_fingerprints,
    label_clusters_bool,
    match_replications,
)
from .persistence import EventLog, Snapshot


# =========================
# Layer: UI (optional)
# =========================

def run_ui(cfg: WorldConfig, seed: int, run_dir: Path) -> None:
    import importlib
    import importlib.util

    from PySide6.QtCore import Qt, QTimer, QThread, Signal
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget,
        QHBoxLayout, QVBoxLayout, QPushButton,
        QSlider, QLabel, QCheckBox, QGroupBox, QSpinBox,
        QListWidget, QListWidgetItem, QSplitter, QLineEdit,
        QScrollArea
    )

    # Renderer selection (UI-only): prefer PyQtGraph for high-frequency image updates,
    # keep Matplotlib as a compatibility fallback if PyQtGraph is not installed.
    pg = None
    Figure = None
    FigureCanvas = None
    plot_backend = "none"
    if importlib.util.find_spec("pyqtgraph") is not None:
        pg = importlib.import_module("pyqtgraph")
        plot_backend = "pyqtgraph"
    elif importlib.util.find_spec("matplotlib") is not None:
        Figure = importlib.import_module("matplotlib.figure").Figure
        FigureCanvas = importlib.import_module("matplotlib.backends.backend_qtagg").FigureCanvasQTAgg
        plot_backend = "matplotlib"
    else:
        raise RuntimeError("UI plotting backend not available. Install pyqtgraph or matplotlib.")

    class SeedHunter(QThread):
        status = Signal(str)
        found = Signal(str)  # path to summary.json
        stats = Signal(int, int)  # tested, found

        def __init__(self, base_cfg: WorldConfig):
            super().__init__()
            self.base_cfg = base_cfg
            self._running = False
            self._tested = 0
            self._found = 0
            self._rng = np.random.default_rng(int(time.time()) & 0x7FFFFFFF)
            self._tested_seeds_db_path: Optional[Path] = None
            self._tested_seed_conn: Optional[sqlite3.Connection] = None

            # dynamic settings (UI can update)
            self.target_survival = base_cfg.hunter_target_survival_ticks
            self.max_ticks = base_cfg.hunter_max_ticks_per_seed
            self.batch = base_cfg.hunter_seeds_per_batch

        # Structural change: use a compact SQLite cache (single file) instead of ever-growing text logs.
        def _init_seed_cache(self, anomalies_dir: Path) -> None:
            self._tested_seeds_db_path = anomalies_dir / "tested_seeds.sqlite3"
            conn = sqlite3.connect(self._tested_seeds_db_path)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("CREATE TABLE IF NOT EXISTS tested_seeds (seed INTEGER PRIMARY KEY)")

            # Structural change: migrate legacy text log once into SQLite and remove it to save disk space.
            legacy_log = anomalies_dir / "tested_seeds.log"
            if legacy_log.exists():
                for line in legacy_log.read_text(encoding="utf-8").splitlines():
                    val = line.strip()
                    if not val:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO tested_seeds(seed) VALUES (?)",
                        (int(val),),
                    )
                conn.commit()
                legacy_log.unlink(missing_ok=True)

            self._tested_seed_conn = conn

        # Structural change: cache update is in-place (INSERT OR IGNORE) and persists across restarts.
        def _mark_seed_tested(self, seed: int) -> None:
            if self._tested_seed_conn is None:
                return
            self._tested_seed_conn.execute(
                "INSERT OR IGNORE INTO tested_seeds(seed) VALUES (?)",
                (int(seed),),
            )
            self._tested_seed_conn.commit()

        # Structural change: membership check goes directly against persistent cache to avoid re-testing seeds.
        def _seed_was_tested(self, seed: int) -> bool:
            if self._tested_seed_conn is None:
                return False
            row = self._tested_seed_conn.execute(
                "SELECT 1 FROM tested_seeds WHERE seed=? LIMIT 1",
                (int(seed),),
            ).fetchone()
            return row is not None

        # Structural change: generate unique candidate seeds while skipping those already in the persistent cache.
        def _seed_batch_unique(self) -> List[int]:
            unique: List[int] = []
            target = max(1, int(self.batch))
            while len(unique) < target:
                candidate = int(self._rng.integers(0, 2**31 - 1))
                if candidate in unique:
                    continue
                if self._seed_was_tested(candidate):
                    continue
                unique.append(candidate)
            return unique

        # Structural change: explicit cache close keeps sqlite file consistent when hunter stops.
        def _close_seed_cache(self) -> None:
            if self._tested_seed_conn is None:
                return
            self._tested_seed_conn.close()
            self._tested_seed_conn = None

        def stop(self):
            self._running = False

        def run(self):
            self._running = True
            anomalies_dir = Path(self.base_cfg.hunter_anomalies_dir)
            ensure_dir(anomalies_dir)
            self._init_seed_cache(anomalies_dir)
            known_tested = 0
            if self._tested_seed_conn is not None:
                known_tested = int(self._tested_seed_conn.execute("SELECT COUNT(*) FROM tested_seeds").fetchone()[0])

            self.status.emit(f"Hunter: running (known_tested={known_tested})")
            try:
                while self._running:
                    seeds = self._seed_batch_unique()
                    for sd in seeds:
                        if not self._running:
                            break

                        # fresh core per seed (deterministic run)
                        c = WorldConfig(**asdict(self.base_cfg))
                        core = PhysicsCore(c, seed=sd)

                        birth_tick = None
                        birth_snap = None
                        alive_started = False
                        alive_start_tick = None

                        # run loop
                        for _ in range(int(self.max_ticks)):
                            if not self._running:
                                break
                            core.step()

                            # detect "alive" by alive threshold area
                            area_alive, smax = alive_score(core, c.alive_S_threshold)
                            if (not alive_started) and (area_alive > 0):
                                alive_started = True
                                alive_start_tick = core.tick
                                birth_tick = core.tick
                                birth_snap = core.snapshot()

                            # success: survived target ticks since first alive
                            if alive_started and alive_start_tick is not None:
                                if (core.tick - alive_start_tick) >= int(self.target_survival):
                                    last_snap = core.snapshot()
                                    reason = "SURVIVED_TARGET"
                                    out = save_anomaly_bundle(
                                        base_dir=anomalies_dir,
                                        cfg=c,
                                        seed=sd,
                                        birth_snap=birth_snap,
                                        last_snap=last_snap,
                                        survival_ticks=int(core.tick - alive_start_tick),
                                        birth_tick=birth_tick,
                                        reason=reason
                                    )
                                    self._found += 1
                                    self._tested += 1
                                    self._mark_seed_tested(sd)
                                    self.stats.emit(self._tested, self._found)
                                    self.found.emit(str(out / "summary.json"))
                                    self.status.emit(f"Hunter: FOUND seed={sd} survived={core.tick - alive_start_tick}")
                                    break

                            # failure after having been alive: died out (no proto area)
                            if alive_started:
                                area_proto = int((core.S > c.proto_S_threshold).sum())
                                if area_proto == 0:
                                    last_snap = core.snapshot()
                                    reason = quick_reason(core, c)
                                    out = save_anomaly_bundle(
                                        base_dir=anomalies_dir,
                                        cfg=c,
                                        seed=sd,
                                        birth_snap=birth_snap,
                                        last_snap=last_snap,
                                        survival_ticks=int(core.tick - (alive_start_tick or core.tick)),
                                        birth_tick=birth_tick,
                                        reason=reason
                                    )
                                    self._found += 1
                                    self._tested += 1
                                    self._mark_seed_tested(sd)
                                    self.stats.emit(self._tested, self._found)
                                    self.found.emit(str(out / "summary.json"))
                                    self.status.emit(f"Hunter: anomaly seed={sd} reason={reason}")
                                    break

                        # if no anomaly found, just count as tested
                        if self._running:
                            self._tested += 1
                            self._mark_seed_tested(sd)
                            self.stats.emit(self._tested, self._found)

                    # tiny breather so UI stays snappy
                    self.msleep(15)
            finally:
                self._close_seed_cache()
                self.status.emit("Hunter: stopped")

    class GenesisCockpit(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Genesis v1.4+ — Monolith + Hunter + Inbox")

            self.cfg = cfg
            self.core = PhysicsCore(self.cfg, seed=seed)

            self.run_dir = run_dir
            self.eventlog = EventLog(self.run_dir / "eventlog.jsonl")
            self.plot_backend = plot_backend

            self.sim_running = False
            self.timer = QTimer(self)
            self.timer.setInterval(16)
            self.timer.timeout.connect(self.on_timer)
            # Structural stability change: guard against overlapping timer ticks when a previous
            # auto-run frame is still computing, which can otherwise make GUI updates unstable.
            self._timer_busy = False

            # detection state
            self._frame_counter = 0
            self._fp_history: List[ClusterFP] = []
            self._replication_events = 0
            # Structural UI state change: cache the previous S field so activity glow visualizes
            # true inter-step deltas instead of static magnitude.
            self._prev_s_for_activity = self.core.S.copy()
            self._activity_map = np.zeros_like(self.core.S, dtype=np.float32)
            self._activity_score = 0.0

            # hunter
            self.hunter = SeedHunter(self.cfg)
            self.hunter.status.connect(self.on_hunter_status)
            self.hunter.found.connect(self.on_hunter_found)
            self.hunter.stats.connect(self.on_hunter_stats)

            # UI root
            central = QWidget()
            self.setCentralWidget(central)

            # Structural UI change: vertical root layout allows a persistent bottom toggle button
            # while keeping main content inside a resize-aware splitter.
            root = QVBoxLayout(central)

            self.splitter = QSplitter(Qt.Horizontal)
            self.splitter.setChildrenCollapsible(False)
            root.addWidget(self.splitter, 1)

            # Structural UI change: dedicated sidebar panel + scroll area prevents clipping when
            # controls grow and keeps future sidebar extensions safe.
            self.sidebar = QWidget()
            self.sidebar_layout = QVBoxLayout(self.sidebar)
            self.sidebar_layout.setContentsMargins(8, 8, 8, 8)

            self.sidebar_scroll = QScrollArea()
            self.sidebar_scroll.setWidgetResizable(True)
            self.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.sidebar_scroll.setWidget(self.sidebar)

            self.sidebar_panel = QWidget()
            self.sidebar_panel_layout = QVBoxLayout(self.sidebar_panel)
            self.sidebar_panel_layout.setContentsMargins(0, 0, 0, 0)
            self.sidebar_panel_layout.addWidget(self.sidebar_scroll)
            self.sidebar_panel.setMinimumWidth(360)

            self.plot_panel = self._build_plots()
            self.plot_panel.setMinimumWidth(640)

            self.splitter.addWidget(self.sidebar_panel)
            self.splitter.addWidget(self.plot_panel)
            self.splitter.setStretchFactor(0, 0)
            self.splitter.setStretchFactor(1, 1)
            self.splitter.setSizes([420, 1100])

            self._build_sidebar()

            # Structural UI change: persistent footer control keeps sidebar show/hide reachable
            # even when the sidebar is currently hidden.
            footer_bar = QHBoxLayout()
            self.btn_sidebar_toggle = QPushButton("Sidebar ausblenden")
            self.btn_sidebar_toggle.clicked.connect(self.toggle_sidebar)
            footer_bar.addWidget(self.btn_sidebar_toggle)
            footer_bar.addStretch(1)
            root.addLayout(footer_bar)

            # inbox auto-refresh
            self.inbox_timer = QTimer(self)
            self.inbox_timer.setInterval(2000)
            self.inbox_timer.timeout.connect(self.scan_inbox)
            self.inbox_timer.start()

            self.refresh_plots()
            self.scan_inbox()

        def closeEvent(self, event):
            try:
                if self.hunter.isRunning():
                    self.hunter.stop()
                    self.hunter.wait(500)
                self.eventlog.close()
            finally:
                super().closeEvent(event)


        # Structural UI change: toggle sidebar visibility with safe splitter resizing.
        def toggle_sidebar(self):
            if self.sidebar_panel.isVisible():
                self.sidebar_panel.hide()
                self.btn_sidebar_toggle.setText("Sidebar einblenden")
            else:
                self.sidebar_panel.show()
                self.btn_sidebar_toggle.setText("Sidebar ausblenden")
                # Restore readable default ratio after re-showing the sidebar.
                self.splitter.setSizes([420, 1100])

        # -------------------------
        # Sidebar
        # -------------------------
        def _build_sidebar(self):
            box = self.sidebar_layout
            box.addWidget(QLabel("<b>CONTROLS</b>"))

            self.btn_start = QPushButton("START")
            self.btn_start.clicked.connect(self.toggle_start_pause)
            box.addWidget(self.btn_start)

            step_group = QGroupBox("Step")
            # Structural UI change: keep preset step buttons and add a custom tick runner in the
            # same control group so manual stepping paths share one discoverable place.
            step_layout = QVBoxLayout(step_group)
            preset_row = QHBoxLayout()
            for label, n in [("×1", 1), ("×10", 10), ("×100", 100)]:
                b = QPushButton(label)
                b.clicked.connect(lambda _, nn=n: self.step_n(nn))
                preset_row.addWidget(b)
            step_layout.addLayout(preset_row)

            custom_row = QHBoxLayout()
            custom_row.addWidget(QLabel("Custom:"))
            self.spn_custom_ticks = QSpinBox()
            self.spn_custom_ticks.setRange(1, 5_000_000)
            self.spn_custom_ticks.setValue(500)
            custom_row.addWidget(self.spn_custom_ticks)
            self.btn_step_custom = QPushButton("Run")
            self.btn_step_custom.clicked.connect(self.step_custom)
            custom_row.addWidget(self.btn_step_custom)
            step_layout.addLayout(custom_row)
            box.addWidget(step_group)

            box.addWidget(QLabel("Speed (Ticks per Frame):"))
            self.sld_speed = QSlider(Qt.Horizontal)
            # Structural UI change: allow fine-grained auto-run control down to 1 tick/frame and
            # default to the safest low-load value to reduce bursty frame pressure at startup.
            self.sld_speed.setRange(1, 1000)
            self.sld_speed.setValue(1)
            self.sld_speed.valueChanged.connect(self.on_speed_changed)
            box.addWidget(self.sld_speed)
            self.lbl_speed = QLabel("ticks_per_frame: 1")
            box.addWidget(self.lbl_speed)

            reset_group = QGroupBox("World")
            reset_layout = QHBoxLayout(reset_group)
            self.btn_reset = QPushButton("Reset")
            self.btn_new_seed = QPushButton("New Seed")
            self.btn_reset.clicked.connect(self.reset_same_seed)
            self.btn_new_seed.clicked.connect(self.reset_new_seed)
            reset_layout.addWidget(self.btn_reset)
            reset_layout.addWidget(self.btn_new_seed)
            box.addWidget(reset_group)

            snap_group = QGroupBox("Persistence")
            snap_layout = QHBoxLayout(snap_group)
            self.btn_snap = QPushButton("Snapshot Now")
            self.btn_snap.clicked.connect(self.snapshot_now)
            snap_layout.addWidget(self.btn_snap)

            self.spn_snap_every = QSpinBox()
            self.spn_snap_every.setRange(0, 200000)
            self.spn_snap_every.setValue(self.cfg.snapshot_every_ticks)
            self.spn_snap_every.valueChanged.connect(self.on_snap_every_changed)
            snap_layout.addWidget(QLabel("every"))
            snap_layout.addWidget(self.spn_snap_every)
            snap_layout.addWidget(QLabel("ticks"))
            box.addWidget(snap_group)

            overlay_group = QGroupBox("Overlays")
            overlay_layout = QVBoxLayout(overlay_group)
            self.cb_proto = QCheckBox(f"S > {self.cfg.proto_S_threshold} (proto)")
            self.cb_alive = QCheckBox(f"S > {self.cfg.alive_S_threshold} (alive-cand)")
            self.cb_showM = QCheckBox("Show M")
            self.cb_activity_glow = QCheckBox("Activity Glow (ΔS)")
            self.cb_proto.setChecked(True)
            self.cb_alive.setChecked(False)
            self.cb_showM.setChecked(bool(self.cfg.enable_M))
            self.cb_activity_glow.setChecked(True)
            for cb in [self.cb_proto, self.cb_alive, self.cb_showM, self.cb_activity_glow]:
                cb.stateChanged.connect(lambda: self.refresh_plots())
                overlay_layout.addWidget(cb)
            self.lbl_activity = QLabel("activity: stable (0.000)")
            overlay_layout.addWidget(self.lbl_activity)
            box.addWidget(overlay_group)

            det_group = QGroupBox("Detection")
            det_layout = QVBoxLayout(det_group)
            self.cb_detect = QCheckBox("Enable Replicator Detection")
            self.cb_detect.setChecked(self.cfg.detect_enabled)
            self.cb_detect.stateChanged.connect(self.on_detect_toggle)
            det_layout.addWidget(self.cb_detect)
            self.lbl_detect = QLabel("clusters: 0 | replications: 0")
            det_layout.addWidget(self.lbl_detect)
            box.addWidget(det_group)

            # -------- Phase toggles --------
            phase_group = QGroupBox("Phase Toggles")
            phase_layout = QVBoxLayout(phase_group)
            self.cb_enable_m = QCheckBox("Enable M / Mutation Layer")
            self.cb_enable_m.setChecked(self.cfg.enable_M)
            self.cb_enable_m.stateChanged.connect(self.on_m_toggle)
            phase_layout.addWidget(self.cb_enable_m)
            box.addWidget(phase_group)

            # -------- Hunter --------
            hunt_group = QGroupBox("Hunter Agent")
            hunt_layout = QVBoxLayout(hunt_group)

            self.btn_hunt = QPushButton("HUNT: OFF")
            self.btn_hunt.clicked.connect(self.toggle_hunter)
            hunt_layout.addWidget(self.btn_hunt)

            row1 = QHBoxLayout()
            row1.addWidget(QLabel("target_survival"))
            self.spn_target_surv = QSpinBox()
            self.spn_target_surv.setRange(100, 2_000_000)
            self.spn_target_surv.setValue(self.cfg.hunter_target_survival_ticks)
            self.spn_target_surv.valueChanged.connect(self.on_hunter_params)
            row1.addWidget(self.spn_target_surv)
            hunt_layout.addLayout(row1)

            row2 = QHBoxLayout()
            row2.addWidget(QLabel("max_ticks/seed"))
            self.spn_max_ticks = QSpinBox()
            self.spn_max_ticks.setRange(1_000, 5_000_000)
            self.spn_max_ticks.setValue(self.cfg.hunter_max_ticks_per_seed)
            self.spn_max_ticks.valueChanged.connect(self.on_hunter_params)
            row2.addWidget(self.spn_max_ticks)
            hunt_layout.addLayout(row2)

            row3 = QHBoxLayout()
            row3.addWidget(QLabel("batch"))
            self.spn_batch = QSpinBox()
            self.spn_batch.setRange(1, 200)
            self.spn_batch.setValue(self.cfg.hunter_seeds_per_batch)
            self.spn_batch.valueChanged.connect(self.on_hunter_params)
            row3.addWidget(self.spn_batch)
            hunt_layout.addLayout(row3)

            self.lbl_hunt_status = QLabel("Hunter: idle")
            self.lbl_hunt_stats = QLabel("tested: 0 | found: 0")
            hunt_layout.addWidget(self.lbl_hunt_status)
            hunt_layout.addWidget(self.lbl_hunt_stats)
            box.addWidget(hunt_group)

            # -------- Inbox --------
            inbox_group = QGroupBox("Anomaly Inbox (double-click to load)")
            inbox_layout = QVBoxLayout(inbox_group)

            self.txt_filter = QLineEdit()
            self.txt_filter.setPlaceholderText("filter: seed / reason / survived...")
            self.txt_filter.textChanged.connect(self.scan_inbox)
            inbox_layout.addWidget(self.txt_filter)

            self.lst_inbox = QListWidget()
            self.lst_inbox.itemDoubleClicked.connect(self.on_inbox_double_click)
            inbox_layout.addWidget(self.lst_inbox)

            self.btn_refresh_inbox = QPushButton("Refresh Inbox")
            self.btn_refresh_inbox.clicked.connect(self.scan_inbox)
            inbox_layout.addWidget(self.btn_refresh_inbox)

            self.lbl_inbox_detail = QLabel("Select an item…")
            self.lbl_inbox_detail.setWordWrap(True)
            inbox_layout.addWidget(self.lbl_inbox_detail)

            box.addWidget(inbox_group)

            # -------- Footer --------
            self.lbl_tick = QLabel("Tick: 0")
            self.lbl_stats = QLabel("Emax: 0.00 | R1max: 0.00 | Smax: 0.00 | Mμ: 0.00")
            self.lbl_hash = QLabel("hash: -")
            box.addWidget(self.lbl_tick)
            box.addWidget(self.lbl_stats)
            box.addWidget(self.lbl_hash)

            box.addStretch(1)

        # -------------------------
        # Plots
        # -------------------------
        def _build_plots(self) -> QWidget:
            if self.plot_backend == "pyqtgraph":
                # Structural UI change: image panel rendering is now data-item based,
                # so refreshes update only image buffers (faster than full-canvas redraw).
                self.pg_layout = pg.GraphicsLayoutWidget()
                # Structural UI change: dark-mode plot canvas and fixed-grid behavior improve
                # visibility and prevent accidental panning/zooming interactions.
                self.pg_layout.setBackground("k")

                title_style = {"color": "#E6E6E6"}
                self.plotE = self.pg_layout.addPlot(0, 0)
                self.plotR = self.pg_layout.addPlot(0, 1)
                self.plotS = self.pg_layout.addPlot(0, 2)
                self.plotM = self.pg_layout.addPlot(0, 3)
                self.plotE.setTitle("E (Energy)", **title_style)
                self.plotR.setTitle("R1 (Raw)", **title_style)
                self.plotS.setTitle("S (Structure)", **title_style)
                self.plotM.setTitle("M (Proto-Genom)", **title_style)

                for plot in [self.plotE, self.plotR, self.plotS, self.plotM]:
                    plot.setAspectLocked(True)
                    plot.hideAxis("left")
                    plot.hideAxis("bottom")
                    plot.invertY(True)
                    plot.setMouseEnabled(x=False, y=False)
                    plot.hideButtons()

                self.imE = pg.ImageItem(axisOrder="row-major")
                self.imR = pg.ImageItem(axisOrder="row-major")
                self.imS = pg.ImageItem(axisOrder="row-major")
                self.imM = pg.ImageItem(axisOrder="row-major")
                # Structural UI change: dedicated activity overlay item sits above S and renders
                # normalized inter-step growth dynamics without mutating simulation data.
                self.imActivity = pg.ImageItem(axisOrder="row-major")
                self.imActivity.setZValue(20)

                # Structural UI change: dedicated LUTs increase channel contrast readability.


                self.plotE.addItem(self.imE)
                self.plotR.addItem(self.imR)
                self.plotS.addItem(self.imS)
                self.plotS.addItem(self.imActivity)
                self.plotM.addItem(self.imM)

                # Structural UI change: contour overlays become explicit isocurve items
                # on top of S, preserving proto/alive visual toggles.
                self._proto_curve = pg.IsocurveItem(level=0.5, pen=pg.mkPen("c", width=1))
                self._alive_curve = pg.IsocurveItem(level=0.5, pen=pg.mkPen("lime", width=1))
                self.plotS.addItem(self._proto_curve)
                self.plotS.addItem(self._alive_curve)
                self._proto_curve.hide()
                self._alive_curve.hide()

                return self.pg_layout

            self.fig = Figure(figsize=(13, 4), tight_layout=True)
            self.canvas = FigureCanvas(self.fig)

            self.axE = self.fig.add_subplot(1, 4, 1)
            self.axR = self.fig.add_subplot(1, 4, 2)
            self.axS = self.fig.add_subplot(1, 4, 3)
            self.axM = self.fig.add_subplot(1, 4, 4)

            for ax, title in [
                (self.axE, "E (Energy)"),
                (self.axR, "R1 (Raw)"),
                (self.axS, "S (Structure)"),
                (self.axM, "M (Proto-Genom)"),
            ]:
                ax.set_title(title)
                ax.set_xticks([])
                ax.set_yticks([])

            self.imE = self.axE.imshow(self.core.E, cmap="magma", vmin=0, vmax=2)
            self.imR = self.axR.imshow(self.core.R1, cmap="viridis", vmin=0, vmax=1)
            self.imS = self.axS.imshow(self.core.S, cmap="magma", vmin=0, vmax=1)
            self.imM = self.axM.imshow(self.core.M, cmap="viridis", vmin=0, vmax=1)
            # Structural UI change: Matplotlib fallback gets the same activity glow semantics as
            # PyQtGraph by layering a transparent ΔS heatmap over the S panel.
            self.imActivity = self.axS.imshow(self.core.S * 0.0, cmap="cool", vmin=0, vmax=1, alpha=0.0)

            self._cont_proto = None
            self._cont_alive = None
            return self.canvas

        # -------------------------
        # Sim controls
        # -------------------------
        def toggle_start_pause(self):
            self.sim_running = not self.sim_running
            if self.sim_running:
                self.btn_start.setText("PAUSE")
                if not self.timer.isActive():
                    self.timer.start()
            else:
                self.btn_start.setText("START")

        def on_speed_changed(self, val: int):
            self.lbl_speed.setText(f"ticks_per_frame: {val}")

        def on_snap_every_changed(self, val: int):
            self.cfg.snapshot_every_ticks = int(val)

        def on_detect_toggle(self):
            self.cfg.detect_enabled = self.cb_detect.isChecked()

        def step_n(self, n: int):
            self.sim_running = False
            self.btn_start.setText("START")
            self._advance_ticks(int(n))

        def step_custom(self):
            self.step_n(int(self.spn_custom_ticks.value()))

        def _advance_ticks(self, n: int):
            # Structural logic change: centralize stepping side effects so manual and auto paths
            # use exactly the same snapshot/detection/refresh sequence.
            for _ in range(n):
                self.core.step()
                self._auto_snapshot_tick()
            # Structural observability change: compute a normalized ΔS map once per step batch so
            # the activity overlay reflects progression and remains deterministic.
            self._activity_map = compute_activity_map(self._prev_s_for_activity, self.core.S)
            self._activity_score = float(np.mean(self._activity_map))
            self._prev_s_for_activity = self.core.S.copy()
            self.post_step_jobs()
            self.refresh_plots()

        def reset_same_seed(self):
            self.sim_running = False
            self.btn_start.setText("START")
            self.core.reset(same_seed=True)
            self._fp_history.clear()
            self._replication_events = 0
            self._prev_s_for_activity = self.core.S.copy()
            self._activity_map.fill(0.0)
            self._activity_score = 0.0
            self.eventlog.write({"t": self.core.tick, "type": "reset", "same_seed": True, "seed": self.core.seed})
            self.refresh_plots()

        def reset_new_seed(self):
            self.sim_running = False
            self.btn_start.setText("START")
            self.core.reset(same_seed=False)
            self._fp_history.clear()
            self._replication_events = 0
            self._prev_s_for_activity = self.core.S.copy()
            self._activity_map.fill(0.0)
            self._activity_score = 0.0
            self.eventlog.write({"t": self.core.tick, "type": "reset", "same_seed": False, "seed": self.core.seed})
            self.refresh_plots()

        def snapshot_now(self):
            snap = self.core.snapshot()
            path = self.run_dir / "snapshots" / f"tick_{snap.tick:09d}.npz"
            snap.save_npz(path)
            self.eventlog.write({"t": self.core.tick, "type": "snapshot", "path": str(path)})

        def on_m_toggle(self):
            self.cfg.enable_M = self.cb_enable_m.isChecked()
            if not self.cfg.enable_M:
                self.core.M.fill(0.0)
            self.refresh_plots()

        def on_timer(self):
            if not self.sim_running or self._timer_busy:
                return
            self._timer_busy = True
            try:
                n = int(self.sld_speed.value())
                self._advance_ticks(n)
            finally:
                self._timer_busy = False

        def _auto_snapshot_tick(self):
            every = int(self.cfg.snapshot_every_ticks)
            if every > 0 and (self.core.tick % every == 0):
                self.snapshot_now()

        def post_step_jobs(self):
            self._frame_counter += 1
            clusters = 0
            if self.cfg.detect_enabled and (self._frame_counter % max(1, self.cfg.detect_every_n_frames) == 0):
                labels, n_clusters = label_clusters_bool(
                    self.core.S > self.cfg.proto_S_threshold,
                    moore=(self.cfg.neighborhood == "moore"),
                    torus=True,
                )
                clusters = n_clusters
                fps = compute_fingerprints(self.core, labels, n_clusters)
                # Structural note: keep call signature synchronized with match_replications(fps_now, history, dt_ticks, sim_thresh).
                rep = match_replications(
                    fps,
                    self._fp_history,
                    dt_ticks=self.cfg.detect_match_dt_ticks,
                    sim_thresh=self.cfg.detect_similarity_threshold,
                )
                if rep:
                    self._replication_events += len(rep)
                    self.eventlog.write({
                        "t": self.core.tick,
                        "type": "replication_detected",
                        "count": len(rep),
                        "pairs": rep,
                    })
                self._fp_history = fps
            self.lbl_detect.setText(f"clusters: {clusters} | replications: {self._replication_events}")

        def refresh_plots(self):
            if self.plot_backend == "pyqtgraph":
                v_max_E = max(1e-6, float(self.core.E.max()))
                v_max_R1 = max(1e-6, float(self.core.R1.max()))
                v_max_S = max(1e-6, float(self.core.S.max()))

                self.imE.setImage(self.core.E, autoLevels=False)
                self.imR.setImage(self.core.R1, autoLevels=False)
                self.imS.setImage(self.core.S, autoLevels=False)
                self.imM.setImage(self.core.M, autoLevels=False)

                self.imE.setLevels((0.0, v_max_E))
                self.imR.setLevels((0.0, v_max_R1))
                self.imS.setLevels((0.0, v_max_S))
                self.imM.setLevels((0.0, 1.0))

                self.plotM.setVisible(self.cb_showM.isChecked())

                if self.cb_proto.isChecked():
                    self._proto_curve.setData((self.core.S > self.cfg.proto_S_threshold).astype(np.float32))
                    self._proto_curve.show()
                else:
                    self._proto_curve.hide()

                if self.cb_alive.isChecked():
                    self._alive_curve.setData((self.core.S > self.cfg.alive_S_threshold).astype(np.float32))
                    self._alive_curve.show()
                else:
                    self._alive_curve.hide()

                if self.cb_activity_glow.isChecked():
                    glow = np.clip((self._activity_map - 0.08) / 0.92, 0.0, 1.0)
                    rgba = np.zeros((*glow.shape, 4), dtype=np.uint8)
                    rgba[..., 1] = 190
                    rgba[..., 2] = 255
                    rgba[..., 3] = (glow * 170.0).astype(np.uint8)
                    self.imActivity.setImage(rgba, autoLevels=False)
                else:
                    self.imActivity.setImage(np.zeros((*self.core.S.shape, 4), dtype=np.uint8), autoLevels=False)

                self.lbl_tick.setText(f"Tick: {self.core.tick}")
                self.lbl_stats.setText(
                    f"Emax: {self.core.E.max():.3f} | R1max: {self.core.R1.max():.3f} | "
                    f"Smax: {self.core.S.max():.3f} | Mμ: {self.core.M.mean():.3f}"
                )
                self.lbl_hash.setText(f"hash: {self.core.state_hash()}")
                self.lbl_activity.setText(f"activity: {classify_activity_level(self._activity_score)} ({self._activity_score:.3f})")
                return

            self.imE.set_data(self.core.E)
            self.imR.set_data(self.core.R1)
            self.imS.set_data(self.core.S)
            self.imM.set_data(self.core.M)
            self.imM.set_visible(self.cb_showM.isChecked())
            self.axM.set_visible(self.cb_showM.isChecked())

            # autoscale image ranges for readability
            self.imE.set_clim(0.0, max(1e-6, float(self.core.E.max())))
            self.imR.set_clim(0.0, max(1e-6, float(self.core.R1.max())))
            self.imS.set_clim(0.0, max(1e-6, float(self.core.S.max())))
            self.imM.set_clim(0.0, 1.0)

            # clear old overlays safely
            for cont_name in ["_cont_proto", "_cont_alive"]:
                cont = getattr(self, cont_name, None)
                if cont is not None:
                    for coll in cont.collections:
                        coll.remove()
                    setattr(self, cont_name, None)

            if self.cb_proto.isChecked():
                self._cont_proto = self.axS.contour(
                    (self.core.S > self.cfg.proto_S_threshold).astype(np.uint8),
                    levels=[0.5],
                    colors=["cyan"],
                    linewidths=0.7,
                )
            if self.cb_alive.isChecked():
                self._cont_alive = self.axS.contour(
                    (self.core.S > self.cfg.alive_S_threshold).astype(np.uint8),
                    levels=[0.5],
                    colors=["lime"],
                    linewidths=0.9,
                )

            if self.cb_activity_glow.isChecked():
                glow = np.clip((self._activity_map - 0.08) / 0.92, 0.0, 1.0)
                self.imActivity.set_data(self._activity_map)
                self.imActivity.set_alpha(glow * 0.65)
            else:
                self.imActivity.set_alpha(0.0)

            self.lbl_tick.setText(f"Tick: {self.core.tick}")
            self.lbl_stats.setText(
                f"Emax: {self.core.E.max():.3f} | R1max: {self.core.R1.max():.3f} | "
                f"Smax: {self.core.S.max():.3f} | Mμ: {self.core.M.mean():.3f}"
            )
            self.lbl_hash.setText(f"hash: {self.core.state_hash()}")
            self.lbl_activity.setText(f"activity: {classify_activity_level(self._activity_score)} ({self._activity_score:.3f})")
            self.canvas.draw_idle()

        def on_hunter_params(self):
            self.hunter.target_survival = int(self.spn_target_surv.value())
            self.hunter.max_ticks = int(self.spn_max_ticks.value())
            self.hunter.batch = int(self.spn_batch.value())

        def toggle_hunter(self):
            if self.hunter.isRunning():
                self.hunter.stop()
                self.hunter.wait(500)
                self.btn_hunt.setText("HUNT: OFF")
                self.cfg.hunter_enabled = False
                return
            self.on_hunter_params()
            self.hunter.start()
            self.btn_hunt.setText("HUNT: ON")
            self.cfg.hunter_enabled = True

        def on_hunter_status(self, msg: str):
            self.lbl_hunt_status.setText(msg)
            if "stopped" in msg.lower() and self.btn_hunt.text() != "HUNT: OFF":
                self.btn_hunt.setText("HUNT: OFF")

        def on_hunter_found(self, summary_path: str):
            self.scan_inbox()
            self.lbl_hunt_status.setText(f"Hunter found anomaly: {summary_path}")

        def on_hunter_stats(self, tested: int, found: int):
            self.lbl_hunt_stats.setText(f"tested: {tested} | found: {found}")

        def scan_inbox(self):
            base = Path(self.cfg.hunter_anomalies_dir)
            ensure_dir(base)
            q = self.txt_filter.text().strip().lower() if hasattr(self, "txt_filter") else ""

            self.lst_inbox.clear()
            items = []
            for summary in sorted(base.glob('*/summary.json'), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    data = json.loads(summary.read_text(encoding='utf-8'))
                except Exception:
                    continue
                text = (
                    f"seed={data.get('seed')} | reason={data.get('reason')} | "
                    f"surv={data.get('survival_ticks')} | tick={data.get('end_tick')}"
                )
                if q and q not in text.lower():
                    continue
                items.append((text, str(summary)))

            for text, path in items:
                it = QListWidgetItem(text)
                it.setData(Qt.UserRole, path)
                self.lst_inbox.addItem(it)

            if not items:
                self.lbl_inbox_detail.setText("Inbox empty.")

        def on_inbox_double_click(self, item):
            path = Path(str(item.data(Qt.UserRole)))
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                # Structural UI change: prefer the first alive snapshot (birth) when loading inbox items,
                # and only fall back to the end/death snapshot if birth data is unavailable.
                paths = data.get('paths', {})
                preferred_path = paths.get('birth') or paths.get('end')
                if not preferred_path:
                    return
                snap = Snapshot.load_npz(Path(preferred_path))
                self.core.load_snapshot(snap)
                self.cfg = self.core.cfg
                self.cb_enable_m.setChecked(bool(self.cfg.enable_M))
                self._fp_history.clear()
                self._replication_events = 0
                self._prev_s_for_activity = self.core.S.copy()
                self._activity_map = np.zeros_like(self.core.S, dtype=np.float32)
                self._activity_score = 0.0
                self.eventlog.write({"t": self.core.tick, "type": "load_anomaly", "summary": str(path)})
                loaded_kind = "birth" if paths.get('birth') else "end"
                self.lbl_inbox_detail.setText(
                    f"Loaded ({loaded_kind}) seed={data.get('seed')} reason={data.get('reason')} "
                    f"survival={data.get('survival_ticks')}"
                )
                self.refresh_plots()
            except Exception as exc:
                self.lbl_inbox_detail.setText(f"Failed to load: {exc}")

    app = QApplication.instance() or QApplication(sys.argv)
    w = GenesisCockpit()
    w.resize(1600, 700)
    w.show()
    app.exec()
