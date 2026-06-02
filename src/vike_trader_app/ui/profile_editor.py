"""Broker-profile editor — QDM-style 'Instruments' table with mass-edit.

Launched from the Data Manager. Lets you pick a broker profile, edit each instrument's
tick / pip / step / contract-size (the keystone spec), add/remove instruments, bulk-edit a
selection ('mass edit instruments'), and save back to ``storage/profiles/<name>.json``.

The mutating logic is the pure ``data.instruments`` helpers + ``profile_editor_data`` row
conversion; the public methods (``save``, ``add_row``, ``delete_rows``, ``apply_mass_edit``,
``new_profile``) are dialog-free so they're unit-testable.
"""

from PySide6 import QtCore, QtWidgets

from ..data.instruments import (
    BrokerProfile,
    InstrumentSpec,
    default_spec_for,
    ensure_presets,
    list_profiles,
    load_profile,
    mass_edit_specs,
    preset_profiles,
    save_profile,
)
from . import theme
from .profile_editor_data import COLUMNS, row_to_spec, spec_to_row

_DERIVED_COL = 6  # "Decimals" — read-only, computed from the tick


class ProfileEditorDialog(QtWidgets.QDialog):
    """Edit broker profiles + their instrument specs."""

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self._root = root
        try:
            ensure_presets(root)
        except Exception:  # noqa: BLE001 - read-only storage just means no presets seeded
            pass
        self.setWindowTitle("Broker profiles & instruments")
        self.resize(640, 460)
        layout = QtWidgets.QVBoxLayout(self)

        # profile selector + meta (timezone label, source postfix)
        top = QtWidgets.QFormLayout()
        self._combo = QtWidgets.QComboBox()
        self._combo.addItems(self._profile_names())
        self._combo.currentTextChanged.connect(self._load)
        self._tz = QtWidgets.QLineEdit()
        self._tz.setToolTip("Display label only — data is always stored in UTC")
        self._postfix = QtWidgets.QLineEdit()
        top.addRow("Profile", self._combo)
        top.addRow("Timezone", self._tz)
        top.addRow("Postfix", self._postfix)
        layout.addLayout(top)

        self._table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self._table, 1)

        bar = QtWidgets.QHBoxLayout()
        self.btn_new = QtWidgets.QPushButton("＋ New profile")
        self.btn_add = QtWidgets.QPushButton("＋ Add instrument")
        self.btn_del = QtWidgets.QPushButton("🗑 Remove")
        self.btn_mass = QtWidgets.QPushButton("✎ Mass edit…")
        self.btn_save = QtWidgets.QPushButton("💾 Save")
        self.btn_new.clicked.connect(self._on_new_profile)
        self.btn_add.clicked.connect(lambda: self.add_row())
        self.btn_del.clicked.connect(lambda: self.delete_rows(self._selected_rows()))
        self.btn_mass.clicked.connect(self._on_mass_edit)
        self.btn_save.clicked.connect(self._on_save)
        for b in (self.btn_new, self.btn_add, self.btn_del, self.btn_mass):
            bar.addWidget(b)
        bar.addStretch(1)
        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet(f"color:{theme.TEXT3};font-size:11px;")
        bar.addWidget(self._status)
        bar.addWidget(self.btn_save)
        layout.addLayout(bar)

        if self._combo.count():
            self._load(self._combo.currentText())

    # --- model access ---
    def _profile_names(self) -> list[str]:
        return sorted(set(list_profiles(self._root)) | set(preset_profiles()))

    def _profile(self, name: str) -> BrokerProfile:
        return load_profile(name, self._root) or preset_profiles().get(name) or BrokerProfile(name)

    # --- table population ---
    def _set_row(self, r: int, cells: list[str]) -> None:
        self._table.insertRow(r)
        for c, val in enumerate(cells):
            item = QtWidgets.QTableWidgetItem(val)
            if c == _DERIVED_COL:  # derived decimals -> not editable
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
            self._table.setItem(r, c, item)

    def _load(self, name: str) -> None:
        p = self._profile(name)
        self._tz.setText(p.timezone)
        self._postfix.setText(p.postfix)
        self._table.setRowCount(0)
        for spec in sorted(p.instruments.values(), key=lambda s: s.symbol):
            if spec.symbol:
                self._set_row(self._table.rowCount(), spec_to_row(spec))

    def _row_cells(self, r: int) -> list[str]:
        return [self._table.item(r, c).text() if self._table.item(r, c) else "" for c in range(len(COLUMNS))]

    def _selected_rows(self) -> list[int]:
        return sorted({idx.row() for idx in self._table.selectedIndexes()})

    # --- public (dialog-free) operations ---
    def current_specs(self) -> list[InstrumentSpec]:
        """Every non-blank row parsed into a spec."""
        return [row_to_spec(self._row_cells(r)) for r in range(self._table.rowCount())
                if self._row_cells(r)[0].strip()]

    def add_row(self, spec: InstrumentSpec | None = None) -> None:
        spec = spec or InstrumentSpec("NEW", self._profile(self._combo.currentText()).asset_class)
        self._set_row(self._table.rowCount(), spec_to_row(spec))

    def delete_rows(self, rows: list[int]) -> None:
        for r in sorted(rows, reverse=True):
            self._table.removeRow(r)

    def apply_mass_edit(self, changes: dict, rows: list[int] | None = None) -> None:
        """Apply ``changes`` to the given rows (or the current selection) in place."""
        rows = self._selected_rows() if rows is None else rows
        for r in rows:
            spec = mass_edit_specs([row_to_spec(self._row_cells(r))], changes)[0]
            for c, val in enumerate(spec_to_row(spec)):
                self._table.item(r, c).setText(val)

    def save(self) -> BrokerProfile:
        """Rebuild the selected profile from the table + meta and persist it."""
        base = self._profile(self._combo.currentText())
        specs = self.current_specs()
        prof = BrokerProfile(
            name=self._combo.currentText().strip(),
            timezone=self._tz.text().strip() or "UTC",
            asset_class=base.asset_class,
            postfix=self._postfix.text().strip(),
            description=base.description,
            instruments={s.symbol: s for s in specs},
            default_spec=base.default_spec,
        )
        save_profile(prof, self._root)
        return prof

    def new_profile(self, name: str, asset_class: str = "crypto", timezone: str = "UTC",
                    postfix: str = "") -> BrokerProfile:
        prof = BrokerProfile(name=name, timezone=timezone, asset_class=asset_class, postfix=postfix,
                             default_spec=default_spec_for("", asset_class))
        save_profile(prof, self._root)
        if self._combo.findText(name) < 0:
            self._combo.addItem(name)
        self._combo.setCurrentText(name)
        return prof

    # --- dialog handlers ---
    def _on_new_profile(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "New profile", "Profile name:")
        if ok and name.strip():
            self.new_profile(name.strip())

    def _on_mass_edit(self) -> None:
        rows = self._selected_rows()
        if not rows:
            self._status.setText("select rows first")
            return
        dlg = _MassEditDialog(self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            changes = dlg.changes()
            if changes:
                self.apply_mass_edit(changes, rows)
                self._status.setText(f"edited {len(rows)} row(s)")

    def _on_save(self) -> None:
        prof = self.save()
        self._status.setText(f"saved {prof.name} ({len(prof.instruments)} instruments)")


class _MassEditDialog(QtWidgets.QDialog):
    """Pick which spec fields to overwrite across the selected instruments."""

    _FIELDS = [("tick_size", "Tick"), ("pip_size", "Pip"),
               ("volume_step", "Step"), ("contract_size", "Contract")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mass edit instruments")
        form = QtWidgets.QFormLayout(self)
        self._rows: dict[str, tuple] = {}
        for key, label in self._FIELDS:
            cb = QtWidgets.QCheckBox()
            le = QtWidgets.QLineEdit()
            le.setPlaceholderText("new value")
            row = QtWidgets.QHBoxLayout()
            row.addWidget(cb)
            row.addWidget(le, 1)
            wrap = QtWidgets.QWidget()
            wrap.setLayout(row)
            form.addRow(label, wrap)
            self._rows[key] = (cb, le)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def changes(self) -> dict:
        out = {}
        for key, (cb, le) in self._rows.items():
            if cb.isChecked():
                try:
                    out[key] = float(le.text())
                except ValueError:
                    pass
        return out
