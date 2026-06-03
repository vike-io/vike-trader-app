"""Historical Providers panel: enable (checkbox) + priority (drag order) + a Load-Data testbed.

Mirrors Wealth-Lab's Historical Providers tab. The order/flags persist to storage/providers.json
and drive the provider fallback chain used by Auto routing. Public ops are dialog-free for tests.

Part 4: each row now has an auto-built settings sub-form (QFormLayout in a group box) driven by
``provider_settings.fields_for``. Values are persisted back into each ProviderEntry.settings and
saved immediately on every edit. An in-memory ``_settings_map`` keyed by provider name tracks the
current values so ``current_config()`` can reconstruct the full entries.

W3-B: "Symbol Mappings…" button opens ``_SymbolMappingsDialog`` for per-provider symbol rewrites.
"""

import time

from PySide6 import QtCore, QtWidgets

from ..data.provider_settings import FieldSpec, fields_for
from ..data.providers_config import (
    DEFAULT_ORDER,
    ProviderEntry,
    ProvidersConfig,
    load_providers_config,
    save_providers_config,
)
from ..data.symbol_mappings import (
    MappingRule,
    SymbolMappings,
    load_mappings,
    save_mappings,
)

_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"]

_MAPPING_COLUMNS = ["Provider", "From (pattern)", "To (replacement)", "Regex"]


class _SymbolMappingsDialog(QtWidgets.QDialog):
    """Editor for per-provider symbol mappings (literal or regex).

    Exposes dialog-free helpers for tests:
    - ``current_mappings() -> SymbolMappings``
    - ``set_rows(list[tuple[str, str, str, bool]])``
    """

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self._root = root
        self.setWindowTitle("Symbol Mappings")
        self.resize(620, 380)

        layout = QtWidgets.QVBoxLayout(self)

        self._table = QtWidgets.QTableWidget(0, len(_MAPPING_COLUMNS))
        self._table.setHorizontalHeaderLabels(_MAPPING_COLUMNS)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.Stretch
        )
        layout.addWidget(self._table, 1)

        btn_row = QtWidgets.QHBoxLayout()
        self._btn_add = QtWidgets.QPushButton("Add Rule")
        self._btn_remove = QtWidgets.QPushButton("Remove Rule")
        self._btn_add.clicked.connect(self._add_row)
        self._btn_remove.clicked.connect(self._remove_row)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_remove)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Load existing mappings
        m = load_mappings(root)
        self.set_rows([(r.provider, r.pattern, r.replacement, r.is_regex) for r in m.rules])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_row(self, provider: str = "", pattern: str = "",
                 replacement: str = "", is_regex: bool = False) -> int:
        row = self._table.rowCount()
        self._table.insertRow(row)

        # Column 0: Provider combo
        combo = QtWidgets.QComboBox()
        combo.addItems(DEFAULT_ORDER)
        combo.setEditable(True)
        if provider:
            idx = combo.findText(provider)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setEditText(provider)
        self._table.setCellWidget(row, 0, combo)

        # Column 1: From pattern
        from_edit = QtWidgets.QLineEdit(pattern)
        self._table.setCellWidget(row, 1, from_edit)

        # Column 2: To replacement
        to_edit = QtWidgets.QLineEdit(replacement)
        self._table.setCellWidget(row, 2, to_edit)

        # Column 3: Regex checkbox (centred)
        chk = QtWidgets.QCheckBox()
        chk.setChecked(is_regex)
        cell_widget = QtWidgets.QWidget()
        cell_layout = QtWidgets.QHBoxLayout(cell_widget)
        cell_layout.addWidget(chk)
        cell_layout.setAlignment(QtCore.Qt.AlignCenter)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        self._table.setCellWidget(row, 3, cell_widget)
        # Store checkbox reference for easy retrieval
        cell_widget._chk = chk
        return row

    def _remove_row(self) -> None:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        for row in sorted(rows, reverse=True):
            self._table.removeRow(row)

    def _on_accept(self) -> None:
        save_mappings(self.current_mappings(), self._root)
        self.accept()

    # ------------------------------------------------------------------
    # Public dialog-free API (used by tests)
    # ------------------------------------------------------------------

    def set_rows(self, rows: list[tuple]) -> None:
        """Replace all rows with ``[(provider, pattern, replacement, is_regex), ...]``."""
        self._table.setRowCount(0)
        for provider, pattern, replacement, is_regex in rows:
            self._add_row(provider, pattern, replacement, bool(is_regex))

    def current_mappings(self) -> SymbolMappings:
        """Return a ``SymbolMappings`` reflecting the current table state."""
        rules = []
        for row in range(self._table.rowCount()):
            combo = self._table.cellWidget(row, 0)
            provider = combo.currentText().strip() if combo else ""
            from_edit = self._table.cellWidget(row, 1)
            pattern = from_edit.text().strip() if from_edit else ""
            to_edit = self._table.cellWidget(row, 2)
            replacement = to_edit.text().strip() if to_edit else ""
            chk_container = self._table.cellWidget(row, 3)
            is_regex = chk_container._chk.isChecked() if chk_container else False
            if provider and pattern:
                rules.append(MappingRule(provider, pattern, replacement, is_regex))
        return SymbolMappings(rules)


class ProvidersPanel(QtWidgets.QWidget):
    """Reorderable, checkable provider list + testbed + per-provider settings form.

    Emits a one-line testbed report string.
    """

    testbed_result = QtCore.Signal(str)

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self._root = root
        # In-memory settings map: {provider_name: {key: value}}
        self._settings_map: dict[str, dict] = {}
        # Currently displayed settings widgets: {field_name: widget}
        self._settings_widgets: dict[str, QtWidgets.QWidget] = {}
        self._settings_provider: str | None = None  # which provider the form shows

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Historical Providers — check to enable, drag to prioritize"))

        self._list = QtWidgets.QListWidget()
        self._list.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self._list.model().rowsMoved.connect(lambda *_: self._persist())
        self._list.itemChanged.connect(lambda *_: self._persist())
        self._list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self._list, 1)

        # --- Per-provider settings group box (auto-built form) ---
        self._settings_group = QtWidgets.QGroupBox("Provider Settings")
        self._settings_form_layout = QtWidgets.QFormLayout(self._settings_group)
        self._settings_group.setVisible(False)
        layout.addWidget(self._settings_group)

        cfg = load_providers_config(root)
        self._populate(cfg)

        # --- Load-Data testbed ---
        tb = QtWidgets.QHBoxLayout()
        self._tb_symbol = QtWidgets.QLineEdit()
        self._tb_symbol.setPlaceholderText("symbol e.g. BTCUSDT")
        self._tb_interval = QtWidgets.QComboBox()
        self._tb_interval.addItems(_INTERVALS)
        self.btn_load = QtWidgets.QPushButton("Load Data")
        self.btn_load.clicked.connect(self._on_load)
        self.btn_symbol_mappings = QtWidgets.QPushButton("\U0001f517 Symbol Mappings…")
        self.btn_symbol_mappings.clicked.connect(self._on_symbol_mappings)
        tb.addWidget(self._tb_symbol, 1)
        tb.addWidget(self._tb_interval)
        tb.addWidget(self.btn_load)
        tb.addWidget(self.btn_symbol_mappings)
        layout.addLayout(tb)

    def _populate(self, cfg: ProvidersConfig) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for p in cfg.providers:
            item = QtWidgets.QListWidgetItem(p.name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if p.enabled else QtCore.Qt.Unchecked)
            self._list.addItem(item)
            # Seed the settings map from the loaded config (overwrite if already present)
            self._settings_map[p.name] = dict(p.settings) if p.settings else {}
        self._list.blockSignals(False)

    def _on_row_changed(self, row: int) -> None:
        """Called when the user selects a different provider row; rebuild the settings form."""
        if row < 0:
            self._settings_group.setVisible(False)
            return
        name = self._list.item(row).text()
        self._build_settings_form(name)

    def _build_settings_form(self, name: str) -> None:
        """Rebuild the QFormLayout for ``name``'s fields. Pre-fill from _settings_map."""
        # Clear old widgets
        while self._settings_form_layout.rowCount() > 0:
            self._settings_form_layout.removeRow(0)
        self._settings_widgets.clear()
        self._settings_provider = name

        specs = fields_for(name)
        if not specs:
            self._settings_group.setVisible(False)
            return

        current = self._settings_map.get(name, {})
        self._settings_group.setTitle(f"Settings — {name}")
        self._settings_group.setVisible(True)

        for spec in specs:
            widget = self._make_widget(spec, current.get(spec.name, spec.default))
            label = QtWidgets.QLabel(spec.name)
            if spec.hint:
                label.setToolTip(spec.hint)
                widget.setToolTip(spec.hint)
            self._settings_form_layout.addRow(label, widget)
            self._settings_widgets[spec.name] = widget
            # Connect change signal to persist immediately
            self._connect_widget_signal(spec, widget)

    def _make_widget(self, spec: FieldSpec, value: object) -> QtWidgets.QWidget:
        if spec.kind == "float":
            w = QtWidgets.QDoubleSpinBox()
            w.setRange(0.0, 3600.0)
            w.setDecimals(3)
            w.setSingleStep(0.1)
            w.setValue(float(value) if value else 0.0)
            return w
        if spec.kind == "int":
            w = QtWidgets.QSpinBox()
            w.setRange(0, 1_000_000)
            w.setValue(int(value) if value else 0)
            return w
        if spec.kind == "bool":
            w = QtWidgets.QCheckBox()
            w.setChecked(bool(value))
            return w
        if spec.kind == "choice" and spec.choices:
            w = QtWidgets.QComboBox()
            w.addItems(spec.choices)
            idx = spec.choices.index(value) if value in spec.choices else 0
            w.setCurrentIndex(idx)
            return w
        # default: "str"
        w = QtWidgets.QLineEdit()
        w.setText(str(value) if value else "")
        return w

    def _connect_widget_signal(self, spec: FieldSpec, widget: QtWidgets.QWidget) -> None:
        if spec.kind == "float":
            widget.valueChanged.connect(lambda v, n=spec.name: self._on_setting_changed(n, v))
        elif spec.kind == "int":
            widget.valueChanged.connect(lambda v, n=spec.name: self._on_setting_changed(n, v))
        elif spec.kind == "bool":
            widget.toggled.connect(lambda v, n=spec.name: self._on_setting_changed(n, v))
        elif spec.kind == "choice":
            widget.currentTextChanged.connect(lambda v, n=spec.name: self._on_setting_changed(n, v))
        else:  # str
            widget.textChanged.connect(lambda v, n=spec.name: self._on_setting_changed(n, v))

    def _on_setting_changed(self, field_name: str, value: object) -> None:
        """Called on any widget edit; update the in-memory map and persist."""
        if self._settings_provider is None:
            return
        if self._settings_provider not in self._settings_map:
            self._settings_map[self._settings_provider] = {}
        self._settings_map[self._settings_provider][field_name] = value
        self._persist()

    def current_config(self) -> ProvidersConfig:
        entries = []
        for i in range(self._list.count()):
            it = self._list.item(i)
            name = it.text()
            settings = dict(self._settings_map.get(name, {}))
            entries.append(ProviderEntry(name, it.checkState() == QtCore.Qt.Checked, settings))
        return ProvidersConfig(entries)

    def current_order(self) -> list[str]:
        return [self._list.item(i).text() for i in range(self._list.count())]

    def enabled_names(self) -> list[str]:
        return self.current_config().enabled_in_order()

    def set_enabled(self, name: str, enabled: bool) -> None:
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it.text() == name:
                it.setCheckState(QtCore.Qt.Checked if enabled else QtCore.Qt.Unchecked)
                return

    # --- dialog-free setting accessors (for tests) ---

    def provider_settings(self, name: str) -> dict:
        """Return the current in-memory settings dict for ``name`` (copy)."""
        return dict(self._settings_map.get(name, {}))

    def set_provider_setting(self, name: str, key: str, value: object) -> None:
        """Programmatically set a single setting key for ``name`` and persist."""
        if name not in self._settings_map:
            self._settings_map[name] = {}
        self._settings_map[name][key] = value
        # If the form is showing this provider, update the widget too
        if self._settings_provider == name and key in self._settings_widgets:
            widget = self._settings_widgets[key]
            widget.blockSignals(True)
            if isinstance(widget, QtWidgets.QDoubleSpinBox):
                widget.setValue(float(value))
            elif isinstance(widget, QtWidgets.QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QtWidgets.QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QtWidgets.QComboBox):
                idx = widget.findText(str(value))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
            elif isinstance(widget, QtWidgets.QLineEdit):
                widget.setText(str(value))
            widget.blockSignals(False)
        self._persist()

    def _persist(self) -> None:
        save_providers_config(self.current_config(), self._root)

    def run_testbed(self, symbol: str, interval: str, fetch=None) -> None:
        """Try to load ``symbol`` via the chain; emit a one-line report. ``fetch`` injectable for tests."""
        if fetch is None:
            from ..data.provider_chain import fetch_for

            def fetch(sym, iv):
                now = int(time.time() * 1000)
                return fetch_for(sym, iv, now - 7 * 86_400_000, now, root=self._root)

        try:
            bars, used = fetch(symbol, interval)
        except Exception as exc:  # noqa: BLE001 - report failures, don't crash
            self.testbed_result.emit(f"{symbol} {interval}: error — {exc}")
            return
        if bars:
            self.testbed_result.emit(f"{symbol} {interval}: {len(bars)} bars via {used}")
        else:
            self.testbed_result.emit(f"{symbol} {interval}: no data from any enabled provider")

    def _on_load(self) -> None:
        sym = self._tb_symbol.text().strip()
        if sym:
            self.run_testbed(sym, self._tb_interval.currentText())

    def _on_symbol_mappings(self) -> None:
        dlg = _SymbolMappingsDialog(self._root, parent=self)
        dlg.exec()

    def open_symbol_mappings_dialog(self) -> "_SymbolMappingsDialog":
        """Return a (non-modal) dialog instance for tests — does not exec."""
        return _SymbolMappingsDialog(self._root, parent=self)
