"""Randomizer tab — configure and run the full game randomizer."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

from . import backend
from .widgets import SeedEntry, OutputPicker, ProgressFrame, LogBox


# Default item pool: vanilla counts per item type
# These match what the solver's randomizer_groups define as "stable"
DEFAULT_ITEM_POOL = {
    # Powers (12 total)
    "power_water": 2,
    "power_water_a3": 1,
    "power_air": 3,
    "power_earth": 3,
    "power_fire": 3,
    # Fragments (15 total)
    "frag_air_1": 1,
    "frag_air_2": 1,
    "frag_air_3": 1,
    "frag_water_1": 1,
    "frag_water_2": 1,
    "frag_water_3": 1,
    "frag_fire_1": 1,
    "frag_fire_2": 1,
    "frag_fire_3": 1,
    "frag_earth_1": 1,
    "frag_earth_2": 1,
    "frag_earth_3": 1,
    "frag_life_1": 1,
    "frag_life_2": 1,
    "frag_life_3": 1,
}

# Default gem weights (used as relative weights, not absolute counts)
# Equal weights = vanilla shuffle behavior
DEFAULT_GEM_WEIGHTS = {
    "diamond": 1,
    "emerald": 1,
    "sapphire": 1,
    "ruby": 1,
    "obsidian": 1,
}

# Friendly display names for item categories
ITEM_CATEGORIES = {
    "Gems (weight)": [
        "diamond", "emerald", "sapphire", "ruby", "obsidian",
    ],
    "Powers": [
        "power_water", "power_water_a3", "power_air",
        "power_earth", "power_fire",
    ],
    "Disc Fragments": [
        "frag_air_1", "frag_air_2", "frag_air_3",
        "frag_water_1", "frag_water_2", "frag_water_3",
        "frag_fire_1", "frag_fire_2", "frag_fire_3",
        "frag_earth_1", "frag_earth_2", "frag_earth_3",
        "frag_life_1", "frag_life_2", "frag_life_3",
    ],
}

# Human-readable labels for items
ITEM_LABELS = {
    "diamond": "Diamond",
    "emerald": "Emerald",
    "sapphire": "Sapphire",
    "ruby": "Ruby",
    "obsidian": "Obsidian",
    "power_water": "Water Power",
    "power_water_a3": "Water Power (A3)",
    "power_air": "Air Power",
    "power_earth": "Earth Power",
    "power_fire": "Fire Power",
    "frag_air_1": "Air Fragment 1",
    "frag_air_2": "Air Fragment 2",
    "frag_air_3": "Air Fragment 3",
    "frag_water_1": "Water Fragment 1",
    "frag_water_2": "Water Fragment 2",
    "frag_water_3": "Water Fragment 3",
    "frag_fire_1": "Fire Fragment 1",
    "frag_fire_2": "Fire Fragment 2",
    "frag_fire_3": "Fire Fragment 3",
    "frag_earth_1": "Earth Fragment 1",
    "frag_earth_2": "Earth Fragment 2",
    "frag_earth_3": "Earth Fragment 3",
    "frag_life_1": "Life Fragment 1",
    "frag_life_2": "Life Fragment 2",
    "frag_life_3": "Life Fragment 3",
}

# Gem type names (for separating gem weights from major item counts)
GEM_TYPES = {"diamond", "emerald", "sapphire", "ruby", "obsidian"}

# Max items allowed per type (the game has 27 major item slots total)
MAX_POOL_SLOTS = 27


class RandomizerTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._thread = None
        self._pool_vars: dict[str, tk.IntVar] = {}
        self._pool_expanded = False
        self._build()

    def _build(self):
        # -- Seed row --
        seed_frame = ttk.Frame(self)
        seed_frame.pack(fill=tk.X, padx=10, pady=(10, 5))
        self.seed = SeedEntry(seed_frame)
        self.seed.pack(side=tk.LEFT)

        # -- Output path --
        self.output = OutputPicker(self)
        self.output.pack(fill=tk.X, padx=10, pady=5)

        # -- Category checkboxes --
        cat_frame = ttk.LabelFrame(self, text="Randomize Categories")
        cat_frame.pack(fill=tk.X, padx=10, pady=5)

        self._vars = {}
        categories = [
            ("do_major", "Major Items (fragments, powers, town powers)", True),
            ("do_keys", "Keys (shuffled within elemental realm)", True),
            ("do_gems", "Gems (diamond/emerald/sapphire/ruby per-level)", True),
            ("do_barriers", "Barriers (element vulnerability)", True),
            ("do_connections", "Level Connections (randomize exits between levels)", False),
            ("do_qol", "QoL Patches (disable popups, pickup animations)", True),
        ]
        for key, label, default in categories:
            var = tk.BooleanVar(value=default)
            self._vars[key] = var
            row = ttk.Frame(cat_frame)
            row.pack(anchor=tk.W, padx=10, pady=2)
            ttk.Checkbutton(row, text=label, variable=var).pack(side=tk.LEFT)
            # Add warning labels for keys and barriers
            if key in ("do_keys", "do_barriers", "do_connections"):
                warn = ttk.Label(row, text="\u26A0 may cause unsolvable seeds",
                                 foreground="#CC8800", font=("Segoe UI", 8))
                warn.pack(side=tk.LEFT, padx=(6, 0))

        # -- Obsidian cost --
        obsidian_frame = ttk.Frame(cat_frame)
        obsidian_frame.pack(anchor=tk.W, padx=10, pady=(4, 2))
        ttk.Label(obsidian_frame, text="Obsidian cost per temple lock:").pack(
            side=tk.LEFT)
        self._obsidian_cost = tk.IntVar(value=10)
        ttk.Spinbox(obsidian_frame, from_=1, to=100,
                     textvariable=self._obsidian_cost, width=4).pack(
            side=tk.LEFT, padx=(5, 5))
        ttk.Label(obsidian_frame, text="(default: 10 = locks at 10,20,...100)",
                  foreground="gray", font=("Segoe UI", 8)).pack(side=tk.LEFT)

        # -- Item Pool Editor (collapsible) --
        self._pool_frame = ttk.LabelFrame(self, text="Item Pool (click to expand)")
        self._pool_frame.pack(fill=tk.X, padx=10, pady=5)

        # Toggle bar
        toggle_frame = ttk.Frame(self._pool_frame)
        toggle_frame.pack(fill=tk.X, padx=5, pady=2)

        self._use_custom_pool = tk.BooleanVar(value=False)
        ttk.Checkbutton(toggle_frame, text="Use custom item pool",
                         variable=self._use_custom_pool,
                         command=self._on_pool_toggle).pack(side=tk.LEFT)

        self._pool_total_label = ttk.Label(toggle_frame, text="")
        self._pool_total_label.pack(side=tk.RIGHT, padx=5)

        self._reset_btn = ttk.Button(toggle_frame, text="Reset to Defaults",
                                      command=self._reset_pool)
        self._reset_btn.pack(side=tk.RIGHT, padx=5)

        # Scrollable item grid (initially hidden)
        self._pool_content = ttk.Frame(self._pool_frame)
        # Don't pack yet — shown on toggle

        self._build_pool_editor()
        self._update_pool_total()

        # -- Build button --
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)

        self._build_btn = ttk.Button(btn_frame, text="Build Randomized ISO",
                                     command=self._on_build, style="Accent.TButton")
        self._build_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.progress = ProgressFrame(btn_frame)
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # -- Log output --
        self.log = LogBox(self, height=14)
        self.log.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))

    def _build_pool_editor(self):
        """Build the item pool spinbox grid inside _pool_content."""
        # Use a canvas for scrolling if needed
        canvas = tk.Canvas(self._pool_content, height=220, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self._pool_content, orient=tk.VERTICAL,
                                   command=canvas.yview)
        inner = ttk.Frame(canvas)

        inner.bind("<Configure>",
                    lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Bind mousewheel
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel, add="+")

        # Build columns: category headers + item rows with spinboxes
        row_idx = 0
        for cat_name, items in ITEM_CATEGORIES.items():
            is_gem_category = cat_name.startswith("Gems")

            # Category header
            header_frame = ttk.Frame(inner)
            header_frame.grid(row=row_idx, column=0, columnspan=4,
                              sticky=tk.W, padx=5, pady=(8, 2))
            ttk.Label(header_frame, text=cat_name,
                      font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
            if is_gem_category:
                ttk.Label(header_frame,
                          text="  (relative weights — 0 = excluded from pool)",
                          foreground="gray",
                          font=("Segoe UI", 8)).pack(side=tk.LEFT)
            row_idx += 1

            for item_id in items:
                label_text = ITEM_LABELS.get(item_id, item_id)

                if is_gem_category:
                    default_val = DEFAULT_GEM_WEIGHTS.get(item_id, 0)
                    max_val = 999  # weights can be large
                    vanilla_label = f"(default: {default_val})"
                else:
                    default_val = DEFAULT_ITEM_POOL.get(item_id, 0)
                    max_val = MAX_POOL_SLOTS
                    vanilla_label = f"(vanilla: {default_val})"

                ttk.Label(inner, text=label_text, width=22).grid(
                    row=row_idx, column=0, sticky=tk.W, padx=(20, 5), pady=1)

                var = tk.IntVar(value=default_val)
                var.trace_add("write", lambda *_: self._update_pool_total())
                self._pool_vars[item_id] = var

                spin = ttk.Spinbox(inner, from_=0, to=max_val,
                                    textvariable=var, width=5)
                spin.grid(row=row_idx, column=1, padx=5, pady=1)

                # Show vanilla/default for reference
                ttk.Label(inner, text=vanilla_label,
                          foreground="gray").grid(
                    row=row_idx, column=2, sticky=tk.W, padx=5, pady=1)

                row_idx += 1

    def _on_pool_toggle(self):
        """Show/hide the item pool editor."""
        if self._use_custom_pool.get():
            self._pool_content.pack(fill=tk.BOTH, padx=5, pady=5)
            self._pool_frame.config(text="Item Pool (custom)")
        else:
            self._pool_content.pack_forget()
            self._pool_frame.config(text="Item Pool (click to expand)")

    def _reset_pool(self):
        """Reset all pool spinboxes to vanilla defaults."""
        for item_id, var in self._pool_vars.items():
            if item_id in GEM_TYPES:
                var.set(DEFAULT_GEM_WEIGHTS.get(item_id, 0))
            else:
                var.set(DEFAULT_ITEM_POOL.get(item_id, 0))

    def _update_pool_total(self):
        """Update the total item count label (gems excluded — they're weights)."""
        total = 0
        for item_id, var in self._pool_vars.items():
            if item_id in GEM_TYPES:
                continue  # gems are weights, not slot consumers
            try:
                total += var.get()
            except (tk.TclError, ValueError):
                pass
        color = "#CC0000" if total > MAX_POOL_SLOTS else ""
        self._pool_total_label.config(
            text=f"Major items: {total} / {MAX_POOL_SLOTS} slots",
            foreground=color)

    def _get_obsidian_cost(self) -> int | None:
        """Return custom obsidian cost, or None if using default (10)."""
        try:
            val = self._obsidian_cost.get()
        except (tk.TclError, ValueError):
            return None
        return val if val != 10 else None

    def _get_item_pool(self) -> dict[str, int] | None:
        """Return the custom item pool dict, or None if using defaults.

        Includes both major item counts and gem weights in a single dict.
        The CLI separates them: gem type keys (diamond/emerald/etc.) are
        used as weighted distribution for step 4, everything else feeds
        the solver's forward-fill in step 2.
        """
        if not self._use_custom_pool.get():
            return None
        pool = {}
        for item_id, var in self._pool_vars.items():
            try:
                count = var.get()
            except (tk.TclError, ValueError):
                count = 0
            if count > 0:
                pool[item_id] = count
        return pool

    def _on_build(self):
        iso_path = self.app.get_iso_path()
        if not iso_path or not iso_path.exists():
            messagebox.showerror("Error", "Please select a valid game ISO first.")
            return

        output_path = self.output.get_path()
        if not output_path:
            output_path = iso_path.with_name("Azurik_randomized.iso")
            self.output.set_path(output_path)

        if iso_path.resolve() == output_path.resolve():
            messagebox.showerror("Error", "Output path must differ from input ISO.")
            return

        issues = backend.check_prerequisites()
        if issues:
            messagebox.showerror("Missing Prerequisites", "\n".join(issues))
            return

        # Validate custom pool
        item_pool = self._get_item_pool()
        if item_pool:
            # Split into major items vs gem weights for validation
            major_total = sum(v for k, v in item_pool.items()
                              if k not in GEM_TYPES)
            gem_total = sum(v for k, v in item_pool.items()
                            if k in GEM_TYPES)
            if major_total > MAX_POOL_SLOTS:
                messagebox.showwarning(
                    "Item Pool Warning",
                    f"Custom pool has {major_total} major items but only "
                    f"{MAX_POOL_SLOTS} placement slots exist. Extra items "
                    f"will have nowhere to go and the seed will likely be "
                    f"unsolvable.")
            if major_total == 0 and gem_total == 0:
                messagebox.showerror("Error", "Custom item pool is empty.")
                return

        # Gather entity editor edits (if any)
        config_edits = None
        entity_tab = self.app.tab_entity
        edit_count = entity_tab.get_edit_count()
        if edit_count > 0:
            config_edits = entity_tab.get_pending_mod()

        # Disable button during build
        self._build_btn.config(state=tk.DISABLED)
        self.log.clear()
        if config_edits:
            self.log.append(f"Including {edit_count} entity editor edits\n")
        self.progress.start("Building randomized ISO...")

        seed = self.seed.get_seed()
        self._pending_force = False

        def on_output(line):
            self.after(0, self.log.append, line)
            # Detect unsolvable message to offer force-build
            if "ERROR: Could not find solvable placement" in line:
                self._pending_force = True

        def on_done(result):
            def _finish():
                if self._pending_force and not result.success:
                    # Offer the user a choice to force-build
                    answer = messagebox.askyesno(
                        "Unsolvable Seed",
                        "No solvable placement was found for this seed.\n\n"
                        "This can happen with custom item pools or certain "
                        "category combinations.\n\n"
                        "Build the ISO anyway? (The game may not be completable)",
                        icon="warning",
                    )
                    if answer:
                        self._pending_force = False
                        self.log.append("\nRetrying with --force...\n")
                        self.progress.start("Rebuilding (forced)...")
                        self._thread = backend.run_randomizer(
                            iso_path=iso_path,
                            output_path=output_path,
                            seed=seed,
                            do_major=self._vars["do_major"].get(),
                            do_keys=self._vars["do_keys"].get(),
                            do_gems=self._vars["do_gems"].get(),
                            do_barriers=self._vars["do_barriers"].get(),
                            do_connections=self._vars["do_connections"].get(),
                            do_qol=self._vars["do_qol"].get(),
                            item_pool=item_pool,
                            obsidian_cost=self._get_obsidian_cost(),
                            config_edits=config_edits,
                            force_unsolvable=True,
                            on_output=on_output,
                            on_done=on_done,
                        )
                        return  # Don't re-enable button yet
                    else:
                        self.progress.stop("Build cancelled — seed unsolvable")
                        self._build_btn.config(state=tk.NORMAL)
                        return

                self.progress.stop(
                    f"Done! Seed: {seed}" if result.success
                    else "Build failed — check log"
                )
                self._build_btn.config(state=tk.NORMAL)
                if result.success:
                    self.app.state.last_seed = seed
                    self.app.state.last_output = result.output_path
            self.after(0, _finish)

        self._thread = backend.run_randomizer(
            iso_path=iso_path,
            output_path=output_path,
            seed=seed,
            do_major=self._vars["do_major"].get(),
            do_keys=self._vars["do_keys"].get(),
            do_gems=self._vars["do_gems"].get(),
            do_barriers=self._vars["do_barriers"].get(),
            do_connections=self._vars["do_connections"].get(),
            do_qol=self._vars["do_qol"].get(),
            item_pool=item_pool,
            obsidian_cost=self._get_obsidian_cost(),
            config_edits=config_edits,
            force_unsolvable=False,
            on_output=on_output,
            on_done=on_done,
        )

    def auto_fill_output(self, iso_path: Path | None):
        """Called when ISO path changes."""
        self.output.auto_fill(iso_path)
