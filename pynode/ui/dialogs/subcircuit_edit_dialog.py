"""
Diálogos para subcircuitos:

  • PortEditDialog            — editar nombre/dirección de un PORT.
  • SubcircuitAppearanceDialog — editar apariencia de una instancia SUBCKT
                                 (label, colores, lado de cada pin).
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QComboBox, QPushButton, QLabel, QColorDialog, QScrollArea, QWidget,
)
from PyQt6.QtGui import QColor


class PortEditDialog(QDialog):
    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = item
        self.setWindowTitle("Puerto de subcircuito")
        lay = QFormLayout(self)
        self.ed_name = QLineEdit(item.port_name or item.name)
        self.cb_dir = QComboBox()
        self.cb_dir.addItems(['in', 'out', 'bidir'])
        self.cb_dir.setCurrentText(item.port_dir or 'in')
        lay.addRow("Nombre del pin:", self.ed_name)
        lay.addRow("Dirección:", self.cb_dir)
        row = QHBoxLayout()
        ok = QPushButton("Aceptar"); ca = QPushButton("Cancelar")
        ok.clicked.connect(self.accept); ca.clicked.connect(self.reject)
        row.addWidget(ok); row.addWidget(ca)
        lay.addRow(row)

    def apply(self):
        self.item.port_name = self.ed_name.text().strip() or 'IN'
        self.item.port_dir = self.cb_dir.currentText()
        self.item.update()


class _ColorButton(QPushButton):
    def __init__(self, value, fallback="#cccccc"):
        super().__init__()
        self.value = value or ''
        self.fallback = fallback
        self._refresh()
        self.clicked.connect(self._pick)

    def _refresh(self):
        shown = self.value or self.fallback
        self.setText(self.value or "(tema)")
        self.setStyleSheet(f"background:{shown};")

    def _pick(self):
        init = QColor(self.value) if self.value else QColor(self.fallback)
        c = QColorDialog.getColor(init, self, "Color")
        if c.isValid():
            self.value = c.name()
            self._refresh()


class SubcircuitAppearanceDialog(QDialog):
    SIDES = ['left', 'right', 'top', 'bottom']

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = item
        self.setWindowTitle(f"Apariencia IC — {item.subckt_name or item.name}")
        self.resize(380, 420)
        root = QVBoxLayout(self)

        form = QFormLayout()
        self.ed_label = QLineEdit(item.ic_label or item.subckt_name)
        form.addRow("Texto del IC:", self.ed_label)
        self.btn_body = _ColorButton(item.ic_body_color, "#3776ab")
        self.btn_text = _ColorButton(item.ic_text_color, "#ffd43b")
        form.addRow("Color cuerpo:", self.btn_body)
        form.addRow("Color texto:", self.btn_text)
        root.addLayout(form)

        root.addWidget(QLabel("Disposición de pines:"))
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); self.pin_form = QFormLayout(inner)
        self._pin_combos = []
        for p in (item.ic_pins or []):
            cb = QComboBox(); cb.addItems(self.SIDES)
            cb.setCurrentText(p.get('side', 'left'))
            self._pin_combos.append(cb)
            self.pin_form.addRow(p.get('name', '?'), cb)
        scroll.setWidget(inner)
        root.addWidget(scroll)

        row = QHBoxLayout()
        ok = QPushButton("Aceptar"); ca = QPushButton("Cancelar")
        ok.clicked.connect(self.accept); ca.clicked.connect(self.reject)
        row.addWidget(ok); row.addWidget(ca)
        root.addLayout(row)

    def apply(self):
        self.item.ic_label = self.ed_label.text().strip()
        self.item.ic_body_color = self.btn_body.value
        self.item.ic_text_color = self.btn_text.value
        for cb, p in zip(self._pin_combos, self.item.ic_pins or []):
            p['side'] = cb.currentText()
        self.item.prepareGeometryChange()
        self.item.update()
        if self.item.scene() and hasattr(self.item.scene(),
                                         'update_wires_for_component'):
            self.item.scene().update_wires_for_component(self.item)
