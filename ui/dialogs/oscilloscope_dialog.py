"""
Panel frontal del Osciloscopio (estilo Multisim XSC1, 2 canales).

Recibe muestras del live transient vía `push_samples(tr, pin_node)`.
Cada llamada agrega los puntos al buffer circular de cada canal y
re-pinta la pantalla.

Controles:
  • Time/Div   (presets: 1us, 10us, 100us, 1ms, 10ms, 100ms, 1s)
  • V/Div  A,B (1mV → 10V por div)
  • Offset vertical A,B (en divisiones)
  • Trigger: source (A/B), edge (rising/falling), level (V), mode (auto/normal/single)
"""
from __future__ import annotations

import collections
from typing import Optional, Deque, Tuple, TYPE_CHECKING

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QGroupBox, QLabel, QPushButton, QComboBox, QDoubleSpinBox,
    QWidget, QSizePolicy,
)
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush, QPainterPath, QFont
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal

from ui.style import COLORS, _qfont

if TYPE_CHECKING:
    from ui.items.component_item import ComponentItem


# Presets razonables, en segundos por división
_TIME_DIV_PRESETS = [
    (1e-6,   "1 µs"), (2e-6,   "2 µs"), (5e-6,   "5 µs"),
    (10e-6,  "10 µs"), (20e-6,  "20 µs"), (50e-6,  "50 µs"),
    (100e-6, "100 µs"), (200e-6, "200 µs"), (500e-6, "500 µs"),
    (1e-3,   "1 ms"), (2e-3,   "2 ms"), (5e-3,   "5 ms"),
    (10e-3,  "10 ms"), (20e-3,  "20 ms"), (50e-3,  "50 ms"),
    (100e-3, "100 ms"), (200e-3, "200 ms"), (500e-3, "500 ms"),
    (1.0,    "1 s"),
]
_V_DIV_PRESETS = [
    (1e-3, "1 mV"), (2e-3, "2 mV"), (5e-3, "5 mV"),
    (10e-3, "10 mV"), (20e-3, "20 mV"), (50e-3, "50 mV"),
    (100e-3, "100 mV"), (200e-3, "200 mV"), (500e-3, "500 mV"),
    (1.0, "1 V"), (2.0, "2 V"), (5.0, "5 V"),
    (10.0, "10 V"), (20.0, "20 V"), (50.0, "50 V"),
    (100.0, "100 V"),
]

# Cuántos puntos máximos guardar por canal (10 divisiones x ~100 puntos/div)
_BUFFER_MAX = 8000

# Colores de las trazas — estilo Multisim
_TRACE_A_COLOR = QColor(255, 220, 70)    # amarillo
_TRACE_B_COLOR = QColor(80, 200, 255)    # celeste
_SCREEN_BG     = QColor(15, 25, 18)
_GRID_COLOR    = QColor(60, 80, 70)
_AXIS_COLOR    = QColor(110, 140, 120)


class _Screen(QWidget):
    """Pantalla del osciloscopio: grilla 10×8 + dos trazas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        # Buffers (t, v) por canal. deque para coste O(1) por insert.
        self.buf_a: Deque[Tuple[float, float]] = collections.deque(maxlen=_BUFFER_MAX)
        self.buf_b: Deque[Tuple[float, float]] = collections.deque(maxlen=_BUFFER_MAX)
        # Configuración cacheada (refrescada por el diálogo)
        self.time_div = 1e-3
        self.v_div_a  = 1.0
        self.v_div_b  = 1.0
        self.pos_a    = 0.0
        self.pos_b    = 0.0
        # Ventana de tiempo a mostrar (segundos). 10 divisiones.
        # `t_window_end` se actualiza al recibir muestras nuevas.
        self.t_window_end: float = 0.0
        # Trigger (sin implementar todavía — “auto”/scroll por defecto).

    def push(self, t_arr, v_a, v_b):
        """Acepta arrays paralelos. v_a/v_b pueden ser None si el pin
        correspondiente no está conectado (canal apagado para esa muestra)."""
        if t_arr is None or len(t_arr) == 0:
            return
        # Pareamos cada t con cada v
        for i, t in enumerate(t_arr):
            if v_a is not None and i < len(v_a):
                self.buf_a.append((float(t), float(v_a[i])))
            if v_b is not None and i < len(v_b):
                self.buf_b.append((float(t), float(v_b[i])))
        # Avanzar ventana al último tiempo conocido
        last = float(t_arr[-1])
        if last > self.t_window_end:
            self.t_window_end = last
        self.update()

    def clear(self):
        self.buf_a.clear()
        self.buf_b.clear()
        self.t_window_end = 0.0
        self.update()

    # ── Pintado ─────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(2, 2, -2, -2)
        # Fondo
        p.fillRect(rect, QBrush(_SCREEN_BG))
        # Marco
        p.setPen(QPen(_AXIS_COLOR, 1))
        p.drawRect(rect)

        # Grilla: 10 divisiones horizontales × 8 verticales
        DX = rect.width() / 10.0
        DY = rect.height() / 8.0
        p.setPen(QPen(_GRID_COLOR, 1, Qt.PenStyle.DotLine))
        for i in range(1, 10):
            x = rect.left() + i * DX
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for j in range(1, 8):
            y = rect.top() + j * DY
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        # Ejes centrales más marcados
        p.setPen(QPen(_AXIS_COLOR, 1))
        cx = rect.left() + 5 * DX
        cy = rect.top() + 4 * DY
        p.drawLine(QPointF(cx, rect.top()), QPointF(cx, rect.bottom()))
        p.drawLine(QPointF(rect.left(), cy), QPointF(rect.right(), cy))

        # Marcas de tick (small ticks en los ejes centrales)
        p.setPen(QPen(_AXIS_COLOR, 1))
        for i in range(1, 10):
            x = rect.left() + i * DX
            p.drawLine(QPointF(x, cy - 3), QPointF(x, cy + 3))
        for j in range(1, 8):
            y = rect.top() + j * DY
            p.drawLine(QPointF(cx - 3, y), QPointF(cx + 3, y))

        # ── Trazas ──────────────────────────────────────────────────────
        # La ventana de tiempo es [t_end - 10·time_div, t_end].
        t_total = self.time_div * 10.0
        if t_total <= 0:
            return
        t_end = self.t_window_end
        t_start = t_end - t_total

        def draw_trace(buf, v_div, pos_divs, color):
            if v_div <= 0 or len(buf) < 2:
                return
            p.setPen(QPen(color, 1.5))
            path = QPainterPath()
            first = True
            # Recortar a la ventana visible (los muy viejos no se dibujan).
            # Una iteración basta porque buf está ordenado por t.
            for (t, v) in buf:
                if t < t_start:
                    continue
                frac_x = (t - t_start) / t_total
                # Volts: positivo arriba (y decrece hacia arriba en Qt)
                # 4 divisiones positivas y 4 negativas alrededor del centro.
                v_offset = v + pos_divs * v_div  # pos_divs sube la traza
                frac_y = 0.5 - (v_offset / (v_div * 8.0))  # 8 divs totales
                x = rect.left() + frac_x * rect.width()
                y = rect.top() + frac_y * rect.height()
                # Clamp para evitar líneas que salen al infinito si se sale
                # un montón de la escala (sigue siendo informativo).
                if y < rect.top() - 200:   y = rect.top() - 200
                if y > rect.bottom() + 200: y = rect.bottom() + 200
                if first:
                    path.moveTo(x, y); first = False
                else:
                    path.lineTo(x, y)
            p.drawPath(path)

        draw_trace(self.buf_a, self.v_div_a, self.pos_a, _TRACE_A_COLOR)
        draw_trace(self.buf_b, self.v_div_b, self.pos_b, _TRACE_B_COLOR)

        # ── Leyenda superior (Time/Div, V/Div) ──────────────────────────
        p.setPen(QPen(QColor(180, 200, 190), 1))
        p.setFont(_qfont('Consolas', 8))
        info = (f"  T={self._fmt_time(self.time_div)}/div    "
                f"A={self._fmt_volts(self.v_div_a)}/div    "
                f"B={self._fmt_volts(self.v_div_b)}/div")
        p.drawText(rect.adjusted(0, -2, 0, 0), Qt.AlignmentFlag.AlignTop, info)

    @staticmethod
    def _fmt_time(t: float) -> str:
        if t < 1e-3:  return f"{t*1e6:.0f}µs"
        if t < 1.0:   return f"{t*1e3:.0f}ms"
        return f"{t:g}s"

    @staticmethod
    def _fmt_volts(v: float) -> str:
        if v < 1.0:   return f"{v*1e3:.0f}mV"
        return f"{v:g}V"


class OscilloscopeDialog(QDialog):
    """Panel del osciloscopio. No-modal, recibe muestras del live transient."""
    changed = pyqtSignal()

    def __init__(self, item: 'ComponentItem', parent=None):
        super().__init__(parent)
        self.item = item
        self.setWindowTitle(f"Osciloscopio — {item.name}")
        self.setModal(False)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Tool)
        # Tamaño cómodo por defecto, redimensionable
        self.resize(820, 460)

        # Hardware: hilo + configuración persistida en el item.
        self._hw_thread = None
        self._hw_cfg = dict(getattr(item, 'osc_hw_config', {}) or {})

        self._build_ui()
        self._load_from_item()
        self._sync_screen()   # llevar config al widget pantalla

    # ── UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # Pantalla a la izquierda
        self.screen = _Screen(self)
        root.addWidget(self.screen, stretch=1)

        # Panel de controles a la derecha
        right = QVBoxLayout()
        right.setSpacing(6)

        # Time base
        gb_time = QGroupBox("Time base")
        f_time = QFormLayout(gb_time)
        self.cb_time = QComboBox()
        for v, lbl in _TIME_DIV_PRESETS:
            self.cb_time.addItem(lbl, v)
        f_time.addRow("Time/Div:", self.cb_time)
        right.addWidget(gb_time)

        # Canal A
        gb_a = QGroupBox("Canal A (amarillo)")
        f_a = QFormLayout(gb_a)
        self.cb_va = QComboBox()
        for v, lbl in _V_DIV_PRESETS:
            self.cb_va.addItem(lbl, v)
        f_a.addRow("V/Div:", self.cb_va)
        self.sb_pos_a = QDoubleSpinBox()
        self.sb_pos_a.setRange(-4.0, 4.0)
        self.sb_pos_a.setDecimals(2)
        self.sb_pos_a.setSingleStep(0.1)
        self.sb_pos_a.setSuffix(" div")
        f_a.addRow("Posición:", self.sb_pos_a)
        right.addWidget(gb_a)

        # Canal B
        gb_b = QGroupBox("Canal B (celeste)")
        f_b = QFormLayout(gb_b)
        self.cb_vb = QComboBox()
        for v, lbl in _V_DIV_PRESETS:
            self.cb_vb.addItem(lbl, v)
        f_b.addRow("V/Div:", self.cb_vb)
        self.sb_pos_b = QDoubleSpinBox()
        self.sb_pos_b.setRange(-4.0, 4.0)
        self.sb_pos_b.setDecimals(2)
        self.sb_pos_b.setSingleStep(0.1)
        self.sb_pos_b.setSuffix(" div")
        f_b.addRow("Posición:", self.sb_pos_b)
        right.addWidget(gb_b)

        # Trigger (controles visibles pero sin lógica todavía)
        gb_trig = QGroupBox("Trigger (auto)")
        f_trig = QFormLayout(gb_trig)
        self.cb_trig_src = QComboBox(); self.cb_trig_src.addItems(['A', 'B'])
        self.cb_trig_edge = QComboBox(); self.cb_trig_edge.addItems(['rising', 'falling'])
        self.sb_trig_lvl = QDoubleSpinBox()
        self.sb_trig_lvl.setRange(-1e6, 1e6); self.sb_trig_lvl.setSuffix(" V")
        self.sb_trig_lvl.setDecimals(3)
        f_trig.addRow("Source:", self.cb_trig_src)
        f_trig.addRow("Edge:",   self.cb_trig_edge)
        f_trig.addRow("Level:",  self.sb_trig_lvl)
        right.addWidget(gb_trig)

        # Botones
        btn_row = QHBoxLayout()
        self.btn_clear = QPushButton("Limpiar")
        self.btn_clear.clicked.connect(self._on_clear)
        btn_row.addWidget(self.btn_clear)
        self.btn_hw = QPushButton("Hardware…")
        self.btn_hw.setToolTip(
            "Conecta el osciloscopio a un micro (RP2040 / STM32 / …) "
            "vía USB-CDC, o usa Mock device para probar sin hardware.")
        self.btn_hw.clicked.connect(self._on_hardware_button)
        btn_row.addWidget(self.btn_hw)
        btn_row.addStretch(1)
        right.addLayout(btn_row)

        # Etiqueta de estado del HW (cambia de color según conexión)
        self.lbl_hw_status = QLabel('HW desconectado')
        self.lbl_hw_status.setStyleSheet('color: #888888;')
        right.addWidget(self.lbl_hw_status)
        right.addStretch(1)

        root.addLayout(right)

        # Conexiones — sólo después de armar todo
        self.cb_time.currentIndexChanged.connect(self._on_param_changed)
        self.cb_va.currentIndexChanged.connect(self._on_param_changed)
        self.cb_vb.currentIndexChanged.connect(self._on_param_changed)
        self.sb_pos_a.valueChanged.connect(self._on_param_changed)
        self.sb_pos_b.valueChanged.connect(self._on_param_changed)
        self.cb_trig_src.currentTextChanged.connect(self._on_param_changed)
        self.cb_trig_edge.currentTextChanged.connect(self._on_param_changed)
        self.sb_trig_lvl.valueChanged.connect(self._on_param_changed)

    # ── Carga/escritura del item ────────────────────────────────────────
    def _set_combo_to_value(self, combo: QComboBox, target: float):
        """Selecciona el item del combo cuyo dato (float) es más cercano a `target`."""
        best_i = 0
        best_d = float('inf')
        for i in range(combo.count()):
            v = combo.itemData(i)
            d = abs(v - target)
            if d < best_d:
                best_d = d; best_i = i
        combo.setCurrentIndex(best_i)

    def _load_from_item(self):
        it = self.item
        widgets = (self.cb_time, self.cb_va, self.cb_vb, self.sb_pos_a,
                   self.sb_pos_b, self.cb_trig_src, self.cb_trig_edge,
                   self.sb_trig_lvl)
        for w in widgets: w.blockSignals(True)
        try:
            self._set_combo_to_value(self.cb_time, float(it.osc_time_div))
            self._set_combo_to_value(self.cb_va, float(it.osc_v_div_a))
            self._set_combo_to_value(self.cb_vb, float(it.osc_v_div_b))
            self.sb_pos_a.setValue(float(it.osc_pos_a))
            self.sb_pos_b.setValue(float(it.osc_pos_b))
            self.cb_trig_src.setCurrentText(it.osc_trig_source)
            self.cb_trig_edge.setCurrentText(it.osc_trig_edge)
            self.sb_trig_lvl.setValue(float(it.osc_trig_level))
        finally:
            for w in widgets: w.blockSignals(False)

    def _on_param_changed(self):
        it = self.item
        it.osc_time_div = float(self.cb_time.currentData())
        it.osc_v_div_a  = float(self.cb_va.currentData())
        it.osc_v_div_b  = float(self.cb_vb.currentData())
        it.osc_pos_a    = float(self.sb_pos_a.value())
        it.osc_pos_b    = float(self.sb_pos_b.value())
        it.osc_trig_source = self.cb_trig_src.currentText()
        it.osc_trig_edge   = self.cb_trig_edge.currentText()
        it.osc_trig_level  = float(self.sb_trig_lvl.value())
        self._sync_screen()
        self.changed.emit()

    def _sync_screen(self):
        s = self.screen
        s.time_div = float(self.item.osc_time_div)
        s.v_div_a  = float(self.item.osc_v_div_a)
        s.v_div_b  = float(self.item.osc_v_div_b)
        s.pos_a    = float(self.item.osc_pos_a)
        s.pos_b    = float(self.item.osc_pos_b)
        s.update()

    def _on_clear(self):
        self.screen.clear()

    # ── API consumida por MainWindow._push_to_open_instruments ──────────
    def push_samples(self, tr: dict, pin_node: dict):
        """Recibe el último resultado de `solve_transient`. Calcula
        V(A+)−V(A−) y V(B+)−V(B−) y los appendea al buffer."""
        t_arr  = tr.get('time')
        v_dict = tr.get('voltages', {})
        if t_arr is None or not v_dict:
            return

        n_ap = self.item.node1.strip() or pin_node.get(f"{self.item.name}__p1", "")
        n_am = self.item.node2.strip() or pin_node.get(f"{self.item.name}__p2", "")
        n_bp = pin_node.get(f"{self.item.name}__p3", "")
        n_bm = pin_node.get(f"{self.item.name}__p4", "")

        def diff(node_p, node_m):
            # Devuelve list de v(node_p) − v(node_m) por muestra.
            ap = v_dict.get(node_p)
            am = v_dict.get(node_m)
            if ap is None and am is None:
                return None
            n = len(t_arr)
            ap = ap if ap is not None else [0.0] * n
            am = am if am is not None else [0.0] * n
            # Si node_m es '0'/GND, v(0) = 0
            if node_m in ('0', '', 'gnd', 'GND'):
                am = [0.0] * n
            if node_p in ('0', '', 'gnd', 'GND'):
                ap = [0.0] * n
            m = min(len(ap), len(am), n)
            return [ap[i] - am[i] for i in range(m)]

        v_a = diff(n_ap, n_am)
        v_b = diff(n_bp, n_bm)
        self.screen.push(t_arr, v_a, v_b)

    # ── Hardware ────────────────────────────────────────────────────────
    def _on_hardware_button(self):
        """Abre el sub-diálogo de hardware. Si el usuario acepta y hay
        un hilo previo corriendo, lo desconecta primero."""
        from ui.dialogs.hardware_source_dialog import HardwareSourceDialog
        is_connected = self._hw_thread is not None and self._hw_thread.isRunning()
        if is_connected:
            # Botón funciona como "Desconectar" cuando ya hay stream
            self._stop_hw_thread()
            self._set_hw_status(False, msg='HW desconectado')
            self.btn_hw.setText('Hardware…')
            return
        dlg = HardwareSourceDialog(self._hw_cfg, COLORS, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cfg = dlg.get_result()
        if not cfg:
            return
        self._hw_cfg = cfg
        # Persistir en el item para que sobreviva save/load y reopen del panel.
        try:
            self.item.osc_hw_config = dict(cfg)
        except Exception:
            pass
        self._start_hw_thread(cfg)

    def _start_hw_thread(self, cfg: dict):
        from engine.hw_stream import HardwareStreamThread
        self.screen.clear()
        self._hw_thread = HardwareStreamThread(cfg, parent=self)
        self._hw_thread.samples_received.connect(self._on_hw_samples)
        self._hw_thread.error_occurred.connect(self._on_hw_error)
        self._hw_thread.connection_state.connect(self._on_hw_state)
        self._hw_thread.start()
        self.btn_hw.setText('Desconectar HW')

    def _stop_hw_thread(self):
        if self._hw_thread is None:
            return
        try:
            self._hw_thread.stop()
        except Exception:
            pass
        self._hw_thread = None

    def _on_hw_samples(self, ts: list, va: list, vb: list):
        """Empuja a la pantalla las muestras del HW como si vinieran del sim."""
        self.screen.push(ts, va, vb)

    def _on_hw_state(self, connected: bool):
        if connected:
            self._set_hw_status(True, msg='HW conectado')
        else:
            self._set_hw_status(False, msg='HW desconectado')
            self.btn_hw.setText('Hardware…')

    def _on_hw_error(self, msg: str):
        self._set_hw_status(False, msg=f'Error HW: {msg}')
        self.btn_hw.setText('Hardware…')

    def _set_hw_status(self, connected: bool, msg: str):
        color = '#27ae60' if connected else '#888888'
        if msg.startswith('Error'):
            color = '#e94560'
        self.lbl_hw_status.setStyleSheet(f'color: {color};')
        self.lbl_hw_status.setText(msg)

    # ── Cierre ──────────────────────────────────────────────────────────
    def closeEvent(self, event):
        # Importante: parar el hilo HW antes de soltar el diálogo para no
        # dejar el puerto serie abierto ni que un sample llegue a un Qt
        # widget ya destruido (crash).
        self._stop_hw_thread()
        if getattr(self.item, '_panel_dialog', None) is self:
            self.item._panel_dialog = None
        super().closeEvent(event)
