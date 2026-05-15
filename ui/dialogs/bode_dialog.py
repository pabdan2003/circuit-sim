"""
Diálogo de análisis de Bode / función de transferencia.

Barre la frecuencia entre f_start y f_stop usando el motor MNA
(`solve_ac`) y dibuja H(jω) = V_out / V_in en dos plots matplotlib:
  • Magnitud en dB
  • Fase en grados

Permite ubicar un cursor para leer |H| y ∠H a una frecuencia concreta,
y muestra puntos notables: f en −3 dB y frecuencia de corte estimada.
"""
from __future__ import annotations

from typing import Dict, List, Optional, TYPE_CHECKING

import math
import cmath
import numpy as np

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar

from ui.style import COLORS

if TYPE_CHECKING:
    from ui.items.component_item import ComponentItem


_AC_SOURCE_TYPES = ('VAC', 'FGEN')


class BodeDialog(QDialog):
    """Análisis de Bode: barrido AC + plot |H| (dB) y ∠H (°).

    Toma los componentes de UNA sola hoja activa (la pasada como `scene`).
    Identifica las fuentes AC del circuito, te deja elegir cuál es la
    excitación, qué nodo es la salida y la referencia, y dispara un
    barrido `solve_ac`.
    """

    def __init__(self, scene, colors, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.colors = colors
        self.setWindowTitle('Análisis de Bode / Función de transferencia')
        # No-modal flotante (igual que el resto de instrumentos)
        self.setModal(False)
        self.resize(900, 640)

        # Estado del último cálculo (para el cursor)
        self._freqs: Optional[np.ndarray] = None
        self._H:     Optional[np.ndarray] = None

        self._build_ui()
        self._apply_style()
        self._populate_sources_and_nodes()

    # ── UI ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QHBoxLayout(self)

        # Panel izquierdo: controles + lectura de cursor
        left = QVBoxLayout()
        left.setSpacing(8)

        gb_signal = QGroupBox('Señal de entrada / salida')
        form_signal = QFormLayout(gb_signal)
        self.cb_input = QComboBox()
        self.cb_input.setMinimumWidth(140)
        form_signal.addRow('Fuente AC:', self.cb_input)
        self.cb_out = QComboBox()
        form_signal.addRow('Nodo salida (V_out):', self.cb_out)
        self.cb_ref = QComboBox()
        form_signal.addRow('Nodo referencia:', self.cb_ref)
        left.addWidget(gb_signal)

        gb_sweep = QGroupBox('Barrido')
        form_sweep = QFormLayout(gb_sweep)
        self.sb_fstart = QDoubleSpinBox()
        self.sb_fstart.setRange(1e-3, 1e9)
        self.sb_fstart.setDecimals(3)
        self.sb_fstart.setValue(1.0)
        self.sb_fstart.setSuffix(' Hz')
        form_sweep.addRow('f inicial:', self.sb_fstart)

        self.sb_fstop = QDoubleSpinBox()
        self.sb_fstop.setRange(1e-3, 1e9)
        self.sb_fstop.setDecimals(3)
        self.sb_fstop.setValue(100_000.0)
        self.sb_fstop.setSuffix(' Hz')
        form_sweep.addRow('f final:', self.sb_fstop)

        self.sp_points = QSpinBox()
        self.sp_points.setRange(10, 5000)
        self.sp_points.setValue(400)
        form_sweep.addRow('Puntos:', self.sp_points)

        self.cb_scale = QComboBox()
        self.cb_scale.addItems(['Logarítmica', 'Lineal'])
        form_sweep.addRow('Escala f:', self.cb_scale)

        left.addWidget(gb_sweep)

        btn_row = QHBoxLayout()
        self.btn_run = QPushButton('Calcular')
        self.btn_run.clicked.connect(self._run_sweep)
        btn_row.addWidget(self.btn_run)
        btn_close = QPushButton('Cerrar')
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        left.addLayout(btn_row)

        # Lectura: cursor sobre el gráfico
        gb_cursor = QGroupBox('Cursor (clic sobre el gráfico)')
        form_c = QFormLayout(gb_cursor)
        self.lbl_freq = QLabel('—')
        self.lbl_mag  = QLabel('—')
        self.lbl_phase = QLabel('—')
        self.lbl_freq.setFont(QFont('Consolas', 10, QFont.Weight.Bold))
        self.lbl_mag.setFont(QFont('Consolas', 10, QFont.Weight.Bold))
        self.lbl_phase.setFont(QFont('Consolas', 10, QFont.Weight.Bold))
        form_c.addRow('f:', self.lbl_freq)
        form_c.addRow('|H|:', self.lbl_mag)
        form_c.addRow('∠H:', self.lbl_phase)
        left.addWidget(gb_cursor)

        gb_notable = QGroupBox('Puntos notables')
        v_not = QVBoxLayout(gb_notable)
        self.lbl_dc_gain = QLabel('—')
        self.lbl_fc_3db  = QLabel('—')
        self.lbl_slope   = QLabel('—')
        for w in (self.lbl_dc_gain, self.lbl_fc_3db, self.lbl_slope):
            w.setFont(QFont('Consolas', 9))
        v_not.addWidget(self.lbl_dc_gain)
        v_not.addWidget(self.lbl_fc_3db)
        v_not.addWidget(self.lbl_slope)
        left.addWidget(gb_notable)

        left.addStretch(1)
        left_w = QWidget(); left_w.setLayout(left); left_w.setFixedWidth(280)
        root.addWidget(left_w)

        # Panel derecho: dos plots apilados
        right = QVBoxLayout()
        self.fig = Figure(figsize=(7, 5), tight_layout=True,
                          facecolor=self.colors.get('panel', '#16213e'))
        self.canvas = FigureCanvas(self.fig)
        toolbar = NavigationToolbar(self.canvas, self)
        right.addWidget(toolbar)
        right.addWidget(self.canvas)

        self.ax_mag = self.fig.add_subplot(2, 1, 1)
        self.ax_phase = self.fig.add_subplot(2, 1, 2, sharex=self.ax_mag)
        self._setup_axes()
        self.canvas.mpl_connect('button_press_event', self._on_canvas_click)

        right_w = QWidget(); right_w.setLayout(right)
        root.addWidget(right_w, 1)

    def _setup_axes(self):
        """Configuración inicial (vacía) y estilo de los ejes."""
        bg = self.colors.get('comp_body', '#0e1430')
        grid_col = self.colors.get('panel_brd', '#3a4250')
        text_col = self.colors.get('text', '#e0e0e0')
        for ax in (self.ax_mag, self.ax_phase):
            ax.set_facecolor(bg)
            ax.grid(True, which='both', color=grid_col, alpha=0.4)
            ax.tick_params(colors=text_col, which='both', labelsize=8)
            for spine in ax.spines.values():
                spine.set_color(grid_col)
            ax.title.set_color(text_col)
            ax.yaxis.label.set_color(text_col)
            ax.xaxis.label.set_color(text_col)
        self.ax_mag.set_ylabel('|H| (dB)')
        self.ax_phase.set_ylabel('∠H (°)')
        self.ax_phase.set_xlabel('Frecuencia (Hz)')
        self.ax_mag.set_title('Magnitud')
        self.ax_phase.set_title('Fase')

    def _apply_style(self):
        c = self.colors
        panel = c.get('panel', '#16213e')
        text  = c.get('text', '#e0e0e0')
        body  = c.get('comp_body', '#16213e')
        border = c.get('panel_brd', '#0f3460')
        accent = c.get('component', '#e94560')
        hover  = c.get('comp_sel', '#f5a623')
        self.setStyleSheet(f"""
            QDialog {{ background: {panel}; color: {text}; }}
            QLabel  {{ color: {text}; }}
            QGroupBox {{
                border: 1px solid {border}; border-radius: 4px;
                margin-top: 10px; padding-top: 8px; color: {text};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin; left: 8px; padding: 0 4px;
            }}
            QComboBox, QSpinBox, QDoubleSpinBox {{
                background: {c.get('bg', '#1a1a2e')}; color: {text};
                border: 1px solid {border}; border-radius: 4px; padding: 4px;
            }}
            QPushButton {{
                background: {body}; color: {text};
                border: 1px solid {border}; border-radius: 4px;
                padding: 6px 14px;
            }}
            QPushButton:hover {{ background: {hover}; color: white; }}
        """)

    # ── Población de combos a partir del circuito ────────────────────────
    def _populate_sources_and_nodes(self):
        """Refresca las listas de fuentes AC y nodos a partir de la escena.
        Llamado al abrir el diálogo."""
        # Fuentes AC
        self.cb_input.clear()
        ac_items = [c for c in self.scene.components
                    if c.comp_type in _AC_SOURCE_TYPES]
        for it in ac_items:
            self.cb_input.addItem(f"{it.name} ({it.comp_type})", it)

        # Nodos de la red. Hay dos fuentes:
        #  • Auto-extraídos por extract_netlist (Union-Find sobre cables)
        #  • Manuales puestos por el usuario en node1/node2/node3/node4/node5
        # El simulador prefiere el manual sobre el auto, así que el combo
        # debe ofrecer la unión para no esconder nodos válidos.
        pin_node = self.scene.extract_netlist()
        nets = set(pin_node.values())
        for it in self.scene.components:
            for attr in ('node1', 'node2', 'node3', 'node4', 'node5'):
                v = getattr(it, attr, '') or ''
                v = v.strip()
                if v:
                    nets.add(v)
        nets = sorted(nets)
        if '0' not in nets:
            nets = ['0'] + nets

        self.cb_out.clear()
        self.cb_ref.clear()
        for n in nets:
            self.cb_out.addItem(n)
            self.cb_ref.addItem(n)
        # Default: referencia = '0' (GND), salida = primer net distinto de '0'
        if '0' in nets:
            self.cb_ref.setCurrentText('0')
        if len(nets) > 1:
            for n in nets:
                if n != '0':
                    self.cb_out.setCurrentText(n)
                    break

        if not ac_items:
            QMessageBox.warning(
                self, 'Sin fuentes AC',
                'No se encontraron fuentes VAC o FGEN en el circuito. '
                'Coloca al menos una para realizar el barrido.')
            self.btn_run.setEnabled(False)
        else:
            self.btn_run.setEnabled(True)

    # ── Barrido de frecuencia ────────────────────────────────────────────
    def _run_sweep(self):
        """Ejecuta el AC sweep y dibuja los plots."""
        from ui.scene import build_engine_components_for_item
        from ui.items.component_item import ComponentItem
        from engine import MNASolver, VoltageSourceAC, Resistor

        idx = self.cb_input.currentIndex()
        if idx < 0:
            return
        input_item = self.cb_input.itemData(idx)
        n_out = self.cb_out.currentText().strip()
        n_ref = self.cb_ref.currentText().strip()
        if not n_out:
            return

        f_start = float(self.sb_fstart.value())
        f_stop  = float(self.sb_fstop.value())
        if f_stop <= f_start:
            QMessageBox.warning(self, 'Rango inválido',
                                'f final debe ser mayor que f inicial.')
            return
        points = int(self.sp_points.value())
        scale = 'log' if self.cb_scale.currentText().startswith('Log') else 'linear'

        # Construir lista de componentes para el motor MNA. Reemplazamos
        # la fuente elegida por una VAC unitaria (1 V RMS, 0 °) y ponemos
        # a CERO el resto de fuentes AC, para que H(jω) sea estrictamente
        # el aporte de la fuente elegida.
        pin_node = self.scene.extract_netlist()
        comps = []
        for it in self.scene.components:
            if it.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                continue
            if it.comp_type in ComponentItem.DIGITAL_TYPES:
                continue
            if it.comp_type == 'OSC':
                continue
            # Multímetro como resistencia (igual que en sim normal)
            if it is input_item:
                # Fuente excitadora: 1 V RMS, fase 0 — la salida será H(jω)
                n1 = it.node1.strip() or pin_node.get(f"{it.name}__p1", "")
                n2 = it.node2.strip() or pin_node.get(f"{it.name}__p2", "0")
                comps.append(VoltageSourceAC(
                    it.name, n2, n1, amplitude=1.0, frequency=1.0,
                    phase_deg=0.0, mode='rms'))
                continue
            if it.comp_type in _AC_SOURCE_TYPES:
                # Otras fuentes AC → 0 V (cortocircuito ideal): inyectamos
                # una VAC de amplitud 0 para preservar la topología.
                n1 = it.node1.strip() or pin_node.get(f"{it.name}__p1", "")
                n2 = it.node2.strip() or pin_node.get(f"{it.name}__p2", "0")
                comps.append(VoltageSourceAC(
                    it.name, n2, n1, amplitude=0.0, frequency=1.0,
                    phase_deg=0.0, mode='rms'))
                continue
            # Resto: el helper centralizado (R, C, L, Z, POT, XFMR, …)
            try:
                comps.extend(build_engine_components_for_item(it, pin_node))
            except Exception:
                pass

        if not comps:
            QMessageBox.warning(self, 'Circuito vacío',
                                'No hay componentes simulables.')
            return

        # Verificar que out/ref existen en la red (auto + manuales). Sin
        # los manuales, un circuito sin cables (con nodos tipeados a mano)
        # disparaba "Nodo no encontrado" aunque sí estuvieran conectados.
        all_nets = set(pin_node.values()) | {'0'}
        for it in self.scene.components:
            for attr in ('node1', 'node2', 'node3', 'node4', 'node5'):
                v = (getattr(it, attr, '') or '').strip()
                if v:
                    all_nets.add(v)
        if n_out not in all_nets:
            QMessageBox.warning(self, 'Nodo no encontrado',
                                f'El nodo de salida "{n_out}" no aparece en el circuito.')
            return

        solver = MNASolver()
        res = solver.solve_ac(comps, f_start, f_stop, points=points, scale=scale)
        if not res.get('success'):
            QMessageBox.warning(self, 'Error en el barrido',
                                f"{res.get('error', 'desconocido')}")
            return

        freqs = res['frequencies']
        V = res['voltages']
        V_out = V.get(n_out, np.zeros(points, dtype=complex))
        V_ref = V.get(n_ref, np.zeros(points, dtype=complex)) if n_ref != '0' \
                else np.zeros(points, dtype=complex)
        H = V_out - V_ref   # V_in es 1 V, así que H ≡ V_diff

        self._freqs = freqs
        self._H = H
        self._plot(freqs, H, scale)
        self._update_notable_points(freqs, H)

    def _plot(self, freqs: np.ndarray, H: np.ndarray, scale: str):
        """Dibuja |H| (dB) y ∠H (°) vs frecuencia."""
        self.ax_mag.cla()
        self.ax_phase.cla()
        self._setup_axes()

        mag_lin = np.abs(H)
        # Evita log10(0): usar piso muy bajo para que el plot se mantenga
        mag_lin_safe = np.where(mag_lin > 1e-30, mag_lin, 1e-30)
        mag_db = 20.0 * np.log10(mag_lin_safe)
        phase = np.degrees(np.unwrap(np.angle(H)))

        accent = self.colors.get('component', '#e94560')
        accent2 = self.colors.get('current', '#27ae60')

        if scale == 'log':
            self.ax_mag.semilogx(freqs, mag_db, color=accent, linewidth=1.8)
            self.ax_phase.semilogx(freqs, phase, color=accent2, linewidth=1.8)
        else:
            self.ax_mag.plot(freqs, mag_db, color=accent, linewidth=1.8)
            self.ax_phase.plot(freqs, phase, color=accent2, linewidth=1.8)

        # Línea de referencia −3 dB
        self.ax_mag.axhline(-3.0, linestyle='--', color='#888888',
                            linewidth=0.8, alpha=0.7)
        # Líneas notables: 0°, ±90°
        for ph in (0.0, -90.0, -180.0, 90.0, 180.0):
            if phase.min() <= ph <= phase.max():
                self.ax_phase.axhline(ph, linestyle='--', color='#888888',
                                       linewidth=0.6, alpha=0.5)

        self.ax_mag.set_xlim(freqs[0], freqs[-1])
        self.ax_phase.set_xlim(freqs[0], freqs[-1])
        self.canvas.draw_idle()

    def _update_notable_points(self, freqs: np.ndarray, H: np.ndarray):
        """Lee la ganancia DC, frecuencia de corte −3 dB y pendiente final."""
        mag_db = 20.0 * np.log10(np.maximum(np.abs(H), 1e-30))
        g_dc_db = float(mag_db[0])
        g_dc_lin = 10.0 ** (g_dc_db / 20.0)

        # −3 dB respecto a la ganancia DC: primer cruce
        target = g_dc_db - 3.0
        fc = None
        for i in range(1, len(mag_db)):
            if (mag_db[i-1] - target) * (mag_db[i] - target) <= 0:
                # Interpolación lineal en log f
                lf0 = math.log10(freqs[i-1])
                lf1 = math.log10(freqs[i])
                m0, m1 = mag_db[i-1], mag_db[i]
                t = (target - m0) / (m1 - m0) if m1 != m0 else 0.0
                fc = 10.0 ** (lf0 + t * (lf1 - lf0))
                break

        # Pendiente final (dB/dec) entre los últimos dos puntos
        if freqs[-1] > freqs[-2] > 0:
            slope = (mag_db[-1] - mag_db[-2]) / (
                math.log10(freqs[-1]) - math.log10(freqs[-2]))
        else:
            slope = 0.0

        self.lbl_dc_gain.setText(
            f"Ganancia DC: {g_dc_db:+.3f} dB  ({g_dc_lin:.4g} V/V)")
        if fc is not None:
            self.lbl_fc_3db.setText(f"f_corte (−3 dB): {fc:.4g} Hz")
        else:
            self.lbl_fc_3db.setText("f_corte (−3 dB): no cruzada en el rango")
        self.lbl_slope.setText(f"Pendiente final: {slope:+.1f} dB/dec")

    def _on_canvas_click(self, event):
        """Cuando el usuario hace clic sobre los plots, muestra |H| y ∠H
        en la frecuencia más cercana al cursor."""
        if event.inaxes is None or self._freqs is None or self._H is None:
            return
        x = event.xdata
        if x is None:
            return
        # Buscar el punto más cercano (en log si el eje es log)
        f_target = float(x)
        idx = int(np.argmin(np.abs(self._freqs - f_target)))
        f = float(self._freqs[idx])
        h = self._H[idx]
        mag_db = 20.0 * math.log10(max(abs(h), 1e-30))
        phase = math.degrees(cmath.phase(h))
        # Formato amigable de frecuencia
        if f >= 1e6:    f_str = f"{f/1e6:.4g} MHz"
        elif f >= 1e3:  f_str = f"{f/1e3:.4g} kHz"
        else:           f_str = f"{f:.4g} Hz"
        self.lbl_freq.setText(f_str)
        self.lbl_mag.setText(f"{mag_db:+.3f} dB  ({abs(h):.4g} V/V)")
        self.lbl_phase.setText(f"{phase:+.2f}°")
