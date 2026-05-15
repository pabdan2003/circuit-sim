"""
Panel frontal del Multímetro (XMM), estilo Multisim.

Es un QDialog no-modal que muestra:
  • Display grande con la lectura instantánea
  • Selector de magnitud (V / A / Ω)
  • Selector de acoplamiento (DC / AC)

Edita en vivo el `ComponentItem` (tipo 'MULTIMETER'). Cuando cambia la
magnitud (V↔A) la resistencia interna del modelo MNA cambia, así que
emitimos `changed` para que la simulación reconstruya el netlist live —
mismo patrón que FunctionGeneratorDialog y OscilloscopeDialog.
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QButtonGroup, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout,
)

from pynode.ui.style import COLORS, _qfont

if TYPE_CHECKING:
    from pynode.ui.items.component_item import ComponentItem


class MultimeterDialog(QDialog):
    """Panel frontal del multímetro.

    Emite `changed` cada vez que el usuario modifica modo o acoplamiento
    (no cuando cambia la lectura: eso lo dispara la simulación)."""
    changed = pyqtSignal()

    def __init__(self, item: 'ComponentItem', parent=None):
        super().__init__(parent)
        self.item = item
        self.setWindowTitle(f"Multímetro — {item.name}")
        # No-modal, flotante, estilo instrumento Multisim
        self.setModal(False)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Tool)

        self._build_ui()
        self._sync_buttons_from_item()
        self._refresh_display()

        # Refresco periódico mientras la ventana esté abierta — necesario
        # para que el display siga el live transient (que actualiza
        # item.meter_reading desde fuera de este diálogo).
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._refresh_display)
        self._timer.start()

    # ── Construcción de UI ──────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Cabecera
        header = QLabel(f"<b>{self.item.name}</b> — Multímetro")
        header.setFont(_qfont('Consolas', 10, QFont.Weight.Bold))
        root.addWidget(header)

        # Display grande
        self.display = QLabel('— — —')
        self.display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display.setMinimumHeight(72)
        self.display.setFont(QFont('Consolas', 24, QFont.Weight.Bold))
        self.display.setStyleSheet(
            f"background: {COLORS.get('bg', '#1a1a2e')};"
            f"color: {COLORS.get('current', '#27ae60')};"
            f"border: 2px solid {COLORS.get('panel_brd', '#0f3460')};"
            f"border-radius: 6px; padding: 8px;"
        )
        root.addWidget(self.display)

        # Selector de magnitud (V / A / Ω)
        gb_qty = QGroupBox('Medir')
        l_qty = QHBoxLayout(gb_qty)
        self.btn_v   = QPushButton('V')
        self.btn_a   = QPushButton('A')
        self.btn_ohm = QPushButton('Ω')
        for b in (self.btn_v, self.btn_a, self.btn_ohm):
            b.setCheckable(True)
            b.setFont(_qfont('Consolas', 12, QFont.Weight.Bold))
            b.setMinimumHeight(36)
            l_qty.addWidget(b)
        self._qty_group = QButtonGroup(self)
        self._qty_group.setExclusive(True)
        self._qty_group.addButton(self.btn_v)
        self._qty_group.addButton(self.btn_a)
        self._qty_group.addButton(self.btn_ohm)
        self.btn_v.clicked.connect(lambda: self._set_quantity('V'))
        self.btn_a.clicked.connect(lambda: self._set_quantity('A'))
        self.btn_ohm.clicked.connect(lambda: self._set_quantity('OHM'))
        root.addWidget(gb_qty)

        # Selector de acoplamiento (DC / AC)
        gb_cpl = QGroupBox('Acoplamiento')
        l_cpl = QHBoxLayout(gb_cpl)
        self.btn_dc = QPushButton('DC')
        self.btn_ac = QPushButton('AC')
        for b in (self.btn_dc, self.btn_ac):
            b.setCheckable(True)
            b.setFont(_qfont('Consolas', 11, QFont.Weight.Bold))
            b.setMinimumHeight(32)
            l_cpl.addWidget(b)
        self._cpl_group = QButtonGroup(self)
        self._cpl_group.setExclusive(True)
        self._cpl_group.addButton(self.btn_dc)
        self._cpl_group.addButton(self.btn_ac)
        self.btn_dc.clicked.connect(lambda: self._set_coupling('DC'))
        self.btn_ac.clicked.connect(lambda: self._set_coupling('AC'))
        root.addWidget(gb_cpl)

        # Pie con info breve
        hint = QLabel(
            '<small>Conecta la punta <b>+</b> (roja) y <b>−</b> (negra) '
            'a los nodos a medir.<br>'
            'V → en paralelo · A → en serie · Ω → circuito sin alimentar.'
            '</small>'
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        btn_close = QPushButton('Cerrar')
        btn_close.clicked.connect(self.close)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    # ── Sincronización item ↔ UI ────────────────────────────────────────
    def _sync_buttons_from_item(self):
        qty = getattr(self.item, 'meter_quantity', 'V')
        cpl = getattr(self.item, 'meter_coupling', 'DC')
        # Bloquear señales: setChecked sobre los botones haría que QButtonGroup
        # emitiera y dispararía _set_quantity/_set_coupling con valores
        # parciales — patrón análogo al fix de FGEN para QButtonGroup.
        for w in (self.btn_v, self.btn_a, self.btn_ohm,
                  self.btn_dc, self.btn_ac,
                  self._qty_group, self._cpl_group):
            w.blockSignals(True)
        try:
            self.btn_v.setChecked(qty == 'V')
            self.btn_a.setChecked(qty == 'A')
            self.btn_ohm.setChecked(qty == 'OHM')
            self.btn_dc.setChecked(cpl == 'DC')
            self.btn_ac.setChecked(cpl == 'AC')
        finally:
            for w in (self.btn_v, self.btn_a, self.btn_ohm,
                      self.btn_dc, self.btn_ac,
                      self._qty_group, self._cpl_group):
                w.blockSignals(False)
        # Ω no tiene acoplamiento DC/AC
        is_ohm = (qty == 'OHM')
        self.btn_dc.setEnabled(not is_ohm)
        self.btn_ac.setEnabled(not is_ohm)

    def _set_quantity(self, q: str):
        if getattr(self.item, 'meter_quantity', None) == q:
            return
        self.item.meter_quantity = q
        self.item.meter_reading_unit_hint = {'V': 'V', 'A': 'A', 'OHM': 'Ω'}.get(q, '')
        # Cambiar de V↔A altera la R interna que ve el solver: el valor
        # anterior deja de tener sentido físico. Lo limpiamos.
        self.item.meter_reading = None
        self._sync_buttons_from_item()
        self.item.update()
        self.changed.emit()
        self._refresh_display()

    def _set_coupling(self, c: str):
        if getattr(self.item, 'meter_coupling', None) == c:
            return
        self.item.meter_coupling = c
        self.item.meter_reading = None
        self._sync_buttons_from_item()
        self.item.update()
        self.changed.emit()
        self._refresh_display()

    def _refresh_display(self):
        v = getattr(self.item, 'meter_reading', None)
        unit = getattr(self.item, 'meter_reading_unit_hint', '')
        if v is None:
            self.display.setText('— — —')
            return
        av = abs(v)
        if av >= 1e6:
            txt = f"{v/1e6:.4f} M{unit}"
        elif av >= 1e3:
            txt = f"{v/1e3:.4f} k{unit}"
        elif av >= 1 or av == 0:
            txt = f"{v:.4f} {unit}"
        elif av >= 1e-3:
            txt = f"{v*1e3:.4f} m{unit}"
        elif av >= 1e-6:
            txt = f"{v*1e6:.4f} μ{unit}"
        else:
            txt = f"{v:.4e} {unit}"
        self.display.setText(txt)

    # ── Cierre ──────────────────────────────────────────────────────────
    def closeEvent(self, event):
        try:
            self._timer.stop()
        except Exception:
            pass
        # Limpia la referencia en el item para que el próximo doble-click
        # cree un panel nuevo (en vez de levantar el ya cerrado). Mismo
        # patrón que FGEN/OSC.
        if getattr(self.item, '_panel_dialog', None) is self:
            self.item._panel_dialog = None
        super().closeEvent(event)
