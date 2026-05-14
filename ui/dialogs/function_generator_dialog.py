"""
Panel frontal del Generador de Funciones (FGEN), estilo Multisim XFG1.

Es un QDialog no-modal que muestra:
  • Selector de forma de onda (sin / square / triangle)
  • Frecuencia (Hz, kHz, MHz)
  • Amplitud de pico (Vp)
  • Offset DC (V)
  • Duty cycle (sólo activo en cuadrada)

Edita en vivo el `ComponentItem` (tipo 'FGEN'). Los cambios se reflejan
inmediatamente en la simulación: el motor lee los atributos al rearrancar
el live transient o al usar `_apply_to_engine`.
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox, QButtonGroup,
    QRadioButton, QFrame, QWidget,
)
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush, QPainterPath, QFont
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal

from ui.style import COLORS, _qfont

if TYPE_CHECKING:
    from ui.items.component_item import ComponentItem


_FREQ_MULT = {'Hz': 1.0, 'kHz': 1e3, 'MHz': 1e6}


class _WaveformPreview(QWidget):
    """Mini-pantalla que dibuja un período de la onda configurada."""
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumSize(220, 110)
        self._waveform = 'sin'
        self._duty = 0.5
        self._offset_pu = 0.0  # offset normalizado relativo a amplitud, sólo visual

    def set_waveform(self, wf: str, duty: float, offset_pu: float):
        self._waveform = wf
        self._duty = max(0.02, min(0.98, duty))
        self._offset_pu = max(-1.5, min(1.5, offset_pu))
        self.update()

    def paintEvent(self, event):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(8, 8, -8, -8)
        # Fondo
        p.fillRect(self.rect(), QBrush(QColor(COLORS.get('comp_body', '#101820'))))
        # Marco
        p.setPen(QPen(QColor(COLORS.get('panel_brd', '#3a4250')), 1))
        p.drawRect(rect)
        # Eje X (cero) — se desplaza con offset
        cx = rect.center().x()
        cy = rect.center().y() - self._offset_pu * (rect.height() * 0.35)
        p.setPen(QPen(QColor(COLORS.get('grid_line', '#2a3140')), 1, Qt.PenStyle.DotLine))
        p.drawLine(QPointF(rect.left(), cy), QPointF(rect.right(), cy))
        # Forma de onda
        p.setPen(QPen(QColor(COLORS.get('current', '#27ae60')), 1.8))
        path = QPainterPath()
        N = 240
        y_amp = rect.height() * 0.35
        for i in range(N + 1):
            frac = i / N
            x = rect.left() + frac * rect.width()
            if self._waveform == 'square':
                y_norm = 1.0 if frac < self._duty else -1.0
            elif self._waveform == 'triangle':
                y_norm = 1.0 - 4.0 * abs(frac - 0.5)
            else:  # sin
                y_norm = math.sin(2.0 * math.pi * frac)
            y = cy - y_norm * y_amp
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.drawPath(path)


class FunctionGeneratorDialog(QDialog):
    """Panel frontal del FGEN. Trabaja sobre un ComponentItem de tipo 'FGEN'.

    Emite `changed` cada vez que el usuario modifica algún parámetro.
    """
    changed = pyqtSignal()

    def __init__(self, item: 'ComponentItem', parent=None):
        super().__init__(parent)
        self.item = item
        self.setWindowTitle(f"Generador de funciones — {item.name}")
        # No-modal y siempre on top para imitar al instrumento de Multisim.
        self.setModal(False)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Tool)

        self._build_ui()
        self._load_from_item()
        # Tras conectar señales, refrescar la preview con los valores cargados
        self._on_param_changed()

    # ── Construcción de UI ──────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Cabecera con nombre
        header = QLabel(f"<b>{self.item.name}</b> — Function Generator")
        header.setFont(_qfont('Consolas', 10, QFont.Weight.Bold))
        root.addWidget(header)

        # Preview de onda
        self.preview = _WaveformPreview(self)
        root.addWidget(self.preview)

        # ── Waveform (radio buttons en fila, estilo botonera Multisim) ──
        wf_group = QGroupBox("Forma de onda")
        wf_row = QHBoxLayout(wf_group)
        self.btn_sin = QRadioButton("∿  Sinusoidal")
        self.btn_sq  = QRadioButton("⊓⊔ Cuadrada")
        self.btn_tri = QRadioButton("△  Triangular")
        self._wf_group = QButtonGroup(self)
        self._wf_group.addButton(self.btn_sin, 0)
        self._wf_group.addButton(self.btn_sq,  1)
        self._wf_group.addButton(self.btn_tri, 2)
        for b in (self.btn_sin, self.btn_sq, self.btn_tri):
            b.setFont(_qfont('Consolas', 9))
            wf_row.addWidget(b)
        root.addWidget(wf_group)

        # ── Parámetros numéricos ────────────────────────────────────────
        params = QGroupBox("Parámetros")
        form = QFormLayout(params)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Frecuencia + unidad
        freq_row = QHBoxLayout()
        self.sb_freq = QDoubleSpinBox()
        self.sb_freq.setRange(1e-3, 1e6)
        self.sb_freq.setDecimals(3)
        self.sb_freq.setSingleStep(1.0)
        self.cb_freq_unit = QComboBox()
        self.cb_freq_unit.addItems(['Hz', 'kHz', 'MHz'])
        freq_row.addWidget(self.sb_freq)
        freq_row.addWidget(self.cb_freq_unit)
        freq_w = QWidget(); freq_w.setLayout(freq_row)
        form.addRow("Frecuencia:", freq_w)

        # Amplitud (Vp)
        self.sb_amp = QDoubleSpinBox()
        self.sb_amp.setRange(0.0, 1e6)
        self.sb_amp.setDecimals(3)
        self.sb_amp.setSuffix(" V")
        form.addRow("Amplitud (Vp):", self.sb_amp)

        # Offset DC
        self.sb_offset = QDoubleSpinBox()
        self.sb_offset.setRange(-1e6, 1e6)
        self.sb_offset.setDecimals(3)
        self.sb_offset.setSuffix(" V")
        form.addRow("Offset DC:", self.sb_offset)

        # Duty (% — habilitado sólo si waveform == 'square')
        self.sb_duty = QDoubleSpinBox()
        self.sb_duty.setRange(1.0, 99.0)
        self.sb_duty.setDecimals(1)
        self.sb_duty.setSuffix(" %")
        form.addRow("Duty cycle:", self.sb_duty)

        # Fase
        self.sb_phase = QDoubleSpinBox()
        self.sb_phase.setRange(-360.0, 360.0)
        self.sb_phase.setDecimals(1)
        self.sb_phase.setSuffix(" °")
        form.addRow("Fase inicial:", self.sb_phase)

        root.addWidget(params)

        # Cerrar
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(self.close)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

        # ── Conexiones ──────────────────────────────────────────────────
        for w in (self.sb_freq, self.sb_amp, self.sb_offset,
                  self.sb_duty, self.sb_phase):
            w.valueChanged.connect(self._on_param_changed)
        self.cb_freq_unit.currentTextChanged.connect(self._on_param_changed)
        self._wf_group.idToggled.connect(lambda _id, _on: self._on_param_changed())

    # ── Carga / escritura del ComponentItem ─────────────────────────────
    def _load_from_item(self):
        """Carga los valores del item en los widgets. Bloquea señales para
        evitar que cada `setValue`/`setChecked` dispare `_on_param_changed`
        antes de tener un estado coherente cargado."""
        it = self.item
        wf = getattr(it, 'fgen_waveform', 'sin')
        target_btn = {'sin': self.btn_sin, 'square': self.btn_sq,
                      'triangle': self.btn_tri}.get(wf, self.btn_sin)

        widgets = (self.sb_freq, self.sb_amp, self.sb_offset,
                   self.sb_duty, self.sb_phase, self.cb_freq_unit,
                   self.btn_sin, self.btn_sq, self.btn_tri,
                   self._wf_group)  # ← clave: el grupo emite idToggled
                                    #   incluso si los botones tienen
                                    #   signals bloqueadas. Si no lo
                                    #   bloqueamos, target_btn.setChecked()
                                    #   dispara _on_param_changed y este
                                    #   lee los widgets aún vacíos y pisa
                                    #   los atributos del item.
        for w in widgets:
            w.blockSignals(True)
        try:
            # Marcar el radio primero para que `_waveform_id()` siempre
            # tenga un id válido, aunque algo se cuele.
            target_btn.setChecked(True)
            # Frecuencia: elegimos la unidad más cómoda
            f = float(it.frequency)
            if f >= 1e6:
                self.cb_freq_unit.setCurrentText('MHz'); self.sb_freq.setValue(f / 1e6)
            elif f >= 1e3:
                self.cb_freq_unit.setCurrentText('kHz'); self.sb_freq.setValue(f / 1e3)
            else:
                self.cb_freq_unit.setCurrentText('Hz');  self.sb_freq.setValue(f)
            # Amplitud — en el FGEN siempre se interpreta como pico
            if it.ac_mode == 'rms':
                amp = it.value * (2 ** 0.5)
            else:
                amp = it.value
            self.sb_amp.setValue(amp)
            self.sb_offset.setValue(float(getattr(it, 'fgen_offset', 0.0)))
            self.sb_duty.setValue(100.0 * float(getattr(it, 'fgen_duty', 0.5)))
            self.sb_phase.setValue(float(it.phase_deg))
            # Replica la lógica de _on_param_changed (bloqueada en esta carga)
            self.sb_duty.setEnabled(wf == 'square')
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _waveform_id(self) -> str:
        """Id de la waveform actual. Defensivo: si por una transición
        durante construcción no hay radio chequeado, devolvemos 'sin'."""
        return {0: 'sin', 1: 'square', 2: 'triangle'}.get(
            self._wf_group.checkedId(), 'sin')

    def _on_param_changed(self):
        wf = self._waveform_id()
        # Habilitar duty sólo para square
        self.sb_duty.setEnabled(wf == 'square')

        # Escribir al item
        it = self.item
        it.fgen_waveform = wf
        it.fgen_offset   = float(self.sb_offset.value())
        it.fgen_duty     = float(self.sb_duty.value()) / 100.0
        it.phase_deg     = float(self.sb_phase.value())
        it.value         = float(self.sb_amp.value())   # interpretado como peak
        it.ac_mode       = 'peak'
        unit = self.cb_freq_unit.currentText()
        it.frequency = float(self.sb_freq.value()) * _FREQ_MULT.get(unit, 1.0)

        # Preview
        amp = max(self.sb_amp.value(), 1e-12)
        offset_pu = self.sb_offset.value() / amp
        self.preview.set_waveform(wf, self.sb_duty.value() / 100.0, offset_pu)

        # Refresco visual del símbolo en el canvas
        it.update()
        self.changed.emit()

    # ── Cierre ──────────────────────────────────────────────────────────
    def closeEvent(self, event):
        # Limpia la referencia en el item para que el próximo doble-click
        # cree un panel nuevo (en vez de levantar el ya cerrado).
        if getattr(self.item, '_panel_dialog', None) is self:
            self.item._panel_dialog = None
        super().closeEvent(event)
