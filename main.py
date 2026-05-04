"""
CircuitSim — Simulador de circuitos open source
GUI principal con canvas drag-and-drop, PyQt6
"""

import sys
import math
import json
import os
import re
import html
from itertools import combinations
from typing import Optional, List, Dict, Tuple

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGraphicsScene, QGraphicsView, QGraphicsItem, QGraphicsLineItem,
    QGraphicsEllipseItem, QGraphicsTextItem, QGraphicsRectItem,
    QToolBar, QLabel, QDockWidget, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QSplitter, QDialog,
    QLineEdit, QDialogButtonBox, QMessageBox, QStatusBar,
    QGraphicsPathItem, QPushButton, QComboBox, QDoubleSpinBox,
    QGroupBox, QTextEdit, QFileDialog, QCheckBox, QFormLayout,
    QListWidget, QTabWidget, QInputDialog, QStyledItemDelegate
)
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath, QPolygonF,
    QAction
)
from PyQt6.QtCore import (
    Qt, QPointF, QRectF, QLineF, pyqtSignal, QObject, QSize
)

# Motor MNA
sys.path.insert(0, os.path.dirname(__file__))
from engine import Resistor, VoltageSource, VoltageSourceAC, CurrentSource, Capacitor, Inductor
from engine import Diode, BJT, MOSFET, OpAmp, Impedance, MNASolver
from circuit_analyzer import (
    CircuitAnalyzer, ImplicitBridgeDetector,
    LOGIC_STANDARDS, DEFAULT_STANDARD, AnalysisFlags,
)
from themes import ThemeManager, DEFAULT_THEME_ID
from ui.component_metadata import (
    COMPONENT_NODE_LABELS,
    DEFAULT_NODE_LABELS,
    DIGITAL_FLIPFLOP_TYPES,
    DIGITAL_GATE_TYPES,
    FOUR_PIN_NODE_LABELS,
)
from ui.dialogs.component_dialog import ComponentDialog
from ui.dialogs.component_picker_dialog import ComponentPickerDialog
from ui.dialogs.power_triangle_dialog import PowerTriangleDialog
from ui.dialogs.settings_dialog import SettingsDialog


# ══════════════════════════════════════════════════════════════
# CONSTANTES DE ESTILO
# ══════════════════════════════════════════════════════════════
GRID_SIZE   = 20
COMP_W      = 60
COMP_H      = 30
PIN_RADIUS  = 4

# Estándar lógico digital usado en TODA la app (no es configurable por UI).
DEFAULT_LOGIC_STANDARD = 'CMOS_5V'

# Diccionario MUTABLE de colores de la app — se mantiene como un objeto
# único para que todos los módulos (paint events, stylesheets) lean
# siempre los valores actuales tras un cambio de tema en vivo.
COLORS: Dict[str, str] = {}

# Manager global de temas — carga la selección guardada y aplica al iniciar.
THEME_MANAGER = ThemeManager()
THEME_MANAGER.refresh()


def apply_theme_to_colors(theme_id: str) -> str:
    """
    Reemplaza in-place el contenido de COLORS con la paleta del tema.
    Si el id no existe, cae al tema por defecto.

    Returns:
        El id del tema realmente aplicado.
    """
    meta   = THEME_MANAGER.get_theme_meta(theme_id)
    if meta is None:
        theme_id = DEFAULT_THEME_ID
    palette = THEME_MANAGER.load_theme(theme_id)
    COLORS.clear()
    COLORS.update(palette)
    return theme_id


# Carga inicial: lee la selección persistida (o 'dark' por defecto) y
# rellena COLORS antes de que cualquier widget se construya.
_INITIAL_THEME_ID = apply_theme_to_colors(THEME_MANAGER.load_selection())


# ══════════════════════════════════════════════════════════════
# ÍTEM DE COMPONENTE EN EL CANVAS
# ══════════════════════════════════════════════════════════════
class ComponentItem(QGraphicsItem):
    """
    Representación visual de un componente en el canvas.
    Soporta drag, selección y doble-click para editar propiedades.
    """

    COMP_TYPES = ['R', 'POT', 'V', 'VAC', 'I', 'C', 'L', 'Z', 'GND', 'NODE',
                  'D', 'LED', 'BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP',
                  'XFMR', 'BRIDGE',
                  # ── Digital ──
                  'AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR',
                  'DFF', 'JKFF', 'TFF', 'SRFF',
                  'MUX2', 'COUNTER',
                  'ADC_BRIDGE', 'DAC_BRIDGE', 'COMPARATOR', 'PWM',
                  'CLK',
                  # ── Inter-hoja ──
                  'NET_LABEL_IN', 'NET_LABEL_OUT']

    # Tipos analógicos con 4 terminales (necesitan p3 y p4)
    FOUR_PIN_TYPES = {'XFMR', 'BRIDGE'}

    # Tipos de flip-flop con SET/RESET (4 inputs lógicos + Q,Qn)
    FLIPFLOP_TYPES = {'DFF', 'JKFF', 'TFF', 'SRFF'}

    # Tipos que pertenecen al dominio digital (no se pasan al MNA)
    DIGITAL_TYPES = {
        'AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR',
        'DFF', 'JKFF', 'TFF', 'SRFF',
        'MUX2', 'COUNTER',
        'ADC_BRIDGE', 'DAC_BRIDGE', 'COMPARATOR', 'PWM',
        'LOGIC_STATE', 'CLK',
    }

    def __init__(self, comp_type: str, name: str, value: float = 0.0,
                 unit: str = '', node1: str = '', node2: str = '', node3: str = ''):
        super().__init__()
        self.comp_type = comp_type
        self.name = name
        self.value = value
        self.unit = unit
        self.node1 = node1
        self.node2 = node2
        self.node3 = node3
        # Atributos extra para fuente AC
        self.frequency: float = 60.0    # Hz
        self.phase_deg: float = 0.0     # grados
        self.ac_mode:   str   = 'rms'   # 'rms' o 'peak'
        self.result_voltage: Optional[float] = None
        self._angle = 0  # rotación en grados (0, 90, 180, 270)
        # Estado LED
        self.led_color: str  = 'red'   # color del LED
        self.led_on:    bool = False    # encendido si conduce suficiente corriente
        # Atributos para impedancia genérica
        self.z_mode:   str   = 'rect'   # 'rect' o 'phasor'
        self.z_real:   float = 100.0    # Ω (parte real)
        self.z_imag:   float = 0.0      # Ω (parte imag)
        self.z_mag:    float = 100.0    # Ω (magnitud fasorial)
        self.z_phase:  float = 0.0      # ° (fase fasorial)

        # ── Atributos para componentes digitales ────────────────────────────
        # Puerta: número de entradas (AND/OR/etc.)
        self.dig_inputs:   int   = 2
        # Flip-flop / contador: bits de salida
        self.dig_bits:     int   = 1
        # ADC/DAC: resolución y Vref
        self.dig_bits_adc: int   = 8
        self.dig_vref:     float = 3.3
        # Señal de reloj (nombre de net digital)
        self.dig_clk:      str   = 'CLK'
        # Retardo de propagación (ns)
        self.dig_tpd_ns:   float = 1.0
        # Nodo analógico que conecta al MNA (ADC/DAC/Comparador)
        self.dig_analog_node: str = ''
        # Nodos de entradas extra (entrada 3, 4, ... N) para puertas multi-entrada
        self.dig_input_nodes: list = []   # ['net_A', 'net_B', ...]

        # ── Atributos analógicos extendidos ─────────────────────────────────
        # Potenciómetro: posición del cursor (0.0 a 1.0). El valor base se
        # guarda en self.value (R_total).  R_efectiva = value * pot_wiper.
        self.pot_wiper: float = 0.5
        # Transformador: relación de transformación (n=N1/N2) y corriente máx
        self.xfmr_ratio: float = 2.0          # primario:secundario (n)
        self.xfmr_imax:  float = 1.0          # corriente nominal del primario (A)
        # Puente rectificador: tensión directa de cada diodo (informativa)
        self.bridge_vf: float = 0.7
        # Cuarto nodo para componentes de 4 terminales
        self.node4: str = ''

        # Etiqueta de net label inalámbrico
        self.sheet_label: str = ''

        # ── CLK (reloj digital) ─────────────────────────────────────────────
        # Si está corriendo (oscilando), el timer global lo conmuta a la frecuencia
        # configurada; en caso contrario se comporta como un LOGIC_STATE manual.
        self.clk_running: bool = False

        # ── Estado de memoria de flip-flops ─────────────────────────────────
        # Refleja la salida Q actual del FF (0 ó 1) para visualizarla
        # con un círculo en el centro durante la simulación.
        self.dig_q_state: int = 0

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)

    def rotate_90(self):
        """Rota el componente 90° en sentido horario."""
        self._angle = (self._angle + 90) % 360
        self.setRotation(self._angle)
        self.update()

    def pin_positions_scene(self) -> Tuple[QPointF, QPointF]:
        """Retorna posición de los pines en coordenadas de ESCENA (considera rotación)."""
        p1_local, p2_local = self.pin_positions()
        return self.mapToScene(p1_local), self.mapToScene(p2_local)

    # ── Geometría ──────────────────────────────
    def boundingRect(self) -> QRectF:
        if self.comp_type == 'GND':
            return QRectF(-20, -5, 40, 30)
        if self.comp_type == 'NODE':
            return QRectF(-8, -8, 16, 16)
        if self.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
            # Tamaño similar a GND. Flecha de 30 px y etiqueta encima.
            return QRectF(-18, -16, 36, 28)
        # Flip-flops: cuerpo + cables horizontales + pines SET/RESET arriba/abajo
        if self.comp_type in self.FLIPFLOP_TYPES:
            hw_f = COMP_W // 2
            hh_f = COMP_H // 2 + 8
            return QRectF(-hw_f - 14, -hh_f - 18,
                          (hw_f + 14) * 2, (hh_f + 18) * 2)
        # Puertas: bounding rect dinámico según altura real
        if self.comp_type in ('AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR',
                               'COMPARATOR', 'PWM', 'MUX2'):
            gw, gh, step, n = self._gate_geometry()
            margin = 20
            return QRectF(-gw - 10 - margin, -gh - margin,
                          (gw + 10) * 2 + margin * 2, gh * 2 + margin * 2)
        if self.comp_type == 'XFMR':
            # 80×80, primario izq y secundario der
            return QRectF(-50, -45, 100, 100)
        if self.comp_type == 'BRIDGE':
            # Diamante 80×80
            return QRectF(-50, -50, 100, 100)
        return QRectF(-COMP_W//2 - 10, -COMP_H//2 - 20, COMP_W + 20, COMP_H + 40)

    def pin_positions(self) -> Tuple[QPointF, QPointF]:
        """Retorna posición de los pines principales en coordenadas locales."""
        hw = COMP_W // 2
        hh = COMP_H // 2
        if self.comp_type == 'GND':
            return QPointF(0, -5), QPointF(0, -5)
        if self.comp_type in ('BJT_NPN', 'BJT_PNP'):
            return QPointF(hw + 10, -hh - 6), QPointF(hw + 10, hh + 6)
        if self.comp_type in ('NMOS', 'PMOS'):
            return QPointF(hw + 10, -hh - 6), QPointF(hw + 10, hh + 6)
        if self.comp_type == 'OPAMP':
            hh_op = hh + 6
            return QPointF(hw + 10, 0), QPointF(-hw - 10, hh_op // 2)
        # ── Transformador: p1=PRI+ (sup-izq), p2=PRI- (inf-izq) ─────────
        if self.comp_type == 'XFMR':
            return QPointF(-50, -25), QPointF(-50, 25)
        # ── Puente rectificador (diamante):
        #     p1 = AC1 (izq),  p2 = AC2 (der)
        #     p3 = DC+ (sup),  p4 = DC− (inf)
        if self.comp_type == 'BRIDGE':
            return QPointF(-50, 0), QPointF(50, 0)
        # ── Puertas lógicas: usar _gate_geometry para coincidir exactamente ──
        if self.comp_type in ('AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR',
                               'COMPARATOR', 'PWM'):
            gw, gh, step, n = self._gate_geometry()
            y0 = self._gate_pin_ys()[0]  # posición exacta del primer pin
            return QPointF(gw + 10, 0), QPointF(-gw - 10, y0)
        # ── Flip-flops ───────────────────────────────────────────────────
        if self.comp_type in ('DFF', 'JKFF', 'TFF', 'SRFF'):
            hw_f = COMP_W // 2
            hh_f = COMP_H // 2 + 8
            return QPointF(hw_f + 10, -(hh_f // 2)), QPointF(-hw_f - 10, -(hh_f // 2))
        # ── Contador: p1=Q0 (der-arriba), p2=CLK (izq-centro) ───────────
        if self.comp_type == 'COUNTER':
            hw_f = COMP_W // 2
            hh_f = COMP_H // 2 + 8
            return QPointF(hw_f + 10, -(hh_f // 2)), QPointF(-hw_f - 10, 0)
        # ── MUX2: p1=salida (der), p2=I0 (izq-arriba) ───────────────────
        if self.comp_type == 'MUX2':
            gw, gh, step, _ = self._gate_geometry()
            ys = self._gate_pin_ys()
            return QPointF(gw + 10, 0), QPointF(-gw - 10, ys[0])
        if self.comp_type == 'LOGIC_STATE':
            hw2 = COMP_W // 2
            return QPointF(hw2 + 10, 0), QPointF(hw2 + 10, 0)  # p1=salida, p2=dummy
        if self.comp_type == 'CLK':
            hw2 = COMP_W // 2
            return QPointF(hw2 + 10, 0), QPointF(hw2 + 10, 0)  # p1=salida, p2=dummy
        if self.comp_type == 'NET_LABEL_IN':
            # Pin en la CABEZA de la flecha (lado derecho): ─►●
            return QPointF(15, 0), QPointF(15, 0)
        if self.comp_type == 'NET_LABEL_OUT':
            # Pin en la COLA de la flecha (lado izquierdo): ●─►
            return QPointF(-15, 0), QPointF(-15, 0)
        return QPointF(-hw - 10, 0), QPointF(hw + 10, 0)

    def pin3_position(self) -> QPointF:
        """
        Tercer pin:
          BJT/MOSFET  → Base/Gate   (izq-centro)
          OpAmp       → Entrada+    (izq-arriba)
          Puertas 2+  → segunda entrada (izq, segundo cable)
          Flip-flops  → CLK         (izq-abajo)
          MUX2        → I1          (izq-centro)
        """
        hw = COMP_W // 2
        hh = COMP_H // 2
        if self.comp_type in ('BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS'):
            return QPointF(-hw - 10, 0)
        if self.comp_type == 'OPAMP':
            hh_op = hh + 6
            return QPointF(-hw - 10, -(hh_op // 2))
        # Puertas con 2+ entradas: segundo cable de entrada
        if self.comp_type in ('AND', 'OR', 'NAND', 'NOR', 'XOR', 'COMPARATOR'):
            gw, gh, step, n = self._gate_geometry()
            ys = self._gate_pin_ys()
            if n >= 2:
                return QPointF(-gw - 10, ys[1])  # posición exacta del segundo pin
            return QPointF(0, 0)
        # Flip-flops: CLK (izq-abajo)
        if self.comp_type in ('DFF', 'JKFF', 'TFF', 'SRFF'):
            hw_f = COMP_W // 2
            hh_f = COMP_H // 2 + 8
            return QPointF(-hw_f - 10, hh_f // 2)
        # MUX2: I1 (izq, segundo cable)
        if self.comp_type == 'MUX2':
            gw, gh, step, _ = self._gate_geometry()
            ys = self._gate_pin_ys()
            return QPointF(-gw - 10, ys[1] if len(ys) > 1 else 0)
        # Transformador: p3 = SEC+ (sup-der)
        if self.comp_type == 'XFMR':
            return QPointF(50, -25)
        # Puente: p3 = DC+ (sup)
        if self.comp_type == 'BRIDGE':
            return QPointF(0, -50)
        return QPointF(0, 0)

    def pin3_position_scene(self) -> QPointF:
        return self.mapToScene(self.pin3_position())

    def pin4_position(self) -> QPointF:
        """Cuarto pin.

          XFMR        → SEC− (inferior derecho)
          BRIDGE      → DC−  (inferior)
          DFF/JKFF/TFF/SRFF → SET (parte superior, arriba del centro)
        """
        if self.comp_type == 'XFMR':
            return QPointF(50, 25)
        if self.comp_type == 'BRIDGE':
            return QPointF(0, 50)
        if self.comp_type in self.FLIPFLOP_TYPES:
            hh_f = COMP_H // 2 + 8
            return QPointF(0, -hh_f - 10)
        return QPointF(0, 0)

    def pin4_position_scene(self) -> QPointF:
        return self.mapToScene(self.pin4_position())

    def pin5_position(self) -> QPointF:
        """Quinto pin (sólo flip-flops): RESET en la parte inferior."""
        if self.comp_type in self.FLIPFLOP_TYPES:
            hh_f = COMP_H // 2 + 8
            return QPointF(0, hh_f + 10)
        return QPointF(0, 0)

    def pin5_position_scene(self) -> QPointF:
        return self.mapToScene(self.pin5_position())

    def pin6_position(self) -> QPointF:
        """Sexto pin (sólo flip-flops): salida complementada Q̄ (derecha-abajo)."""
        if self.comp_type in self.FLIPFLOP_TYPES:
            hw_f = COMP_W // 2
            hh_f = COMP_H // 2 + 8
            return QPointF(hw_f + 10, hh_f // 2)
        return QPointF(0, 0)

    def pin6_position_scene(self) -> QPointF:
        return self.mapToScene(self.pin6_position())

    def all_pin_positions_scene(self) -> list:
        """Retorna todos los pines activos del componente en coordenadas de escena."""
        p1, p2 = self.pin_positions_scene()
        pins = [p1, p2]
        # Pines adicionales según tipo
        if self.comp_type in ('BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP'):
            pins.append(self.pin3_position_scene())
        elif self.comp_type in ('AND', 'OR', 'NAND', 'NOR', 'XOR', 'COMPARATOR'):
            gw, gh, step, n = self._gate_geometry()
            ys = self._gate_pin_ys()
            for y in ys[1:]:   # primer pin ya incluido como p2
                pins.append(self.mapToScene(QPointF(-gw - 10, y)))
        elif self.comp_type in self.FLIPFLOP_TYPES:
            pins.append(self.pin3_position_scene())  # CLK / 2da entrada
            pins.append(self.pin4_position_scene())  # SET
            pins.append(self.pin5_position_scene())  # RESET
            pins.append(self.pin6_position_scene())  # Q̄
        elif self.comp_type == 'MUX2':
            pins.append(self.pin3_position_scene())
        elif self.comp_type in self.FOUR_PIN_TYPES:
            pins.append(self.pin3_position_scene())
            pins.append(self.pin4_position_scene())
        return pins

    # ── Dibujo ──────────────────────────────────
    def paint(self, painter: QPainter, option, widget):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        selected = self.isSelected()
        body_color  = QColor(COLORS['comp_sel'] if selected else COLORS['comp_body'])
        line_color  = QColor(COLORS['comp_sel'] if selected else COLORS['component'])
        text_color  = QColor(COLORS['text'])

        pen_body = QPen(line_color, 2)
        pen_wire = QPen(QColor(COLORS['wire']), 2)
        pen_pin  = QPen(QColor(COLORS['pin']),  2)

        if self.comp_type == 'GND':
            self._draw_gnd(painter, pen_body)
        elif self.comp_type == 'NODE':
            self._draw_node(painter, line_color)
        elif self.comp_type == 'R':
            self._draw_resistor(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'POT':
            self._draw_potentiometer(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'C':
            self._draw_capacitor(painter, pen_body, pen_wire)
        elif self.comp_type == 'L':
            self._draw_inductor(painter, pen_body, pen_wire)
        elif self.comp_type in ('V', 'I', 'VAC'):
            self._draw_source(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'D':
            self._draw_diode(painter, pen_body, pen_wire)
        elif self.comp_type == 'LED':
            self._draw_led(painter, pen_body, pen_wire)
        elif self.comp_type in ('BJT_NPN', 'BJT_PNP'):
            self._draw_bjt(painter, pen_body, pen_wire)
        elif self.comp_type in ('NMOS', 'PMOS'):
            self._draw_mosfet(painter, pen_body, pen_wire)
        elif self.comp_type == 'OPAMP':
            self._draw_opamp(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'Z':
            self._draw_impedance(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'XFMR':
            self._draw_transformer(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'BRIDGE':
            self._draw_bridge_rectifier(painter, pen_body, pen_wire, body_color)
        # ── Digital ──────────────────────────────────────────────────────
        elif self.comp_type in ('AND', 'NAND', 'OR', 'NOR', 'XOR', 'NOT'):
            self._draw_ansi_gate(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'DFF':
            self._draw_flipflop(painter, pen_body, pen_wire, body_color, 'DFF')
        elif self.comp_type == 'JKFF':
            self._draw_flipflop(painter, pen_body, pen_wire, body_color, 'JKFF')
        elif self.comp_type == 'TFF':
            self._draw_flipflop(painter, pen_body, pen_wire, body_color, 'TFF')
        elif self.comp_type == 'SRFF':
            self._draw_flipflop(painter, pen_body, pen_wire, body_color, 'SRFF')
        elif self.comp_type == 'CLK':
            self._draw_clk(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'ADC_BRIDGE':
            self._draw_adc_dac(painter, pen_body, pen_wire, body_color, is_adc=True)
        elif self.comp_type == 'DAC_BRIDGE':
            self._draw_adc_dac(painter, pen_body, pen_wire, body_color, is_adc=False)
        elif self.comp_type == 'COMPARATOR':
            self._draw_digital_gate(painter, pen_body, pen_wire, body_color, 'CMP')
        elif self.comp_type == 'PWM':
            self._draw_digital_gate(painter, pen_body, pen_wire, body_color, 'PWM')
        elif self.comp_type == 'COUNTER':
            self._draw_counter(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'MUX2':
            self._draw_mux(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'LOGIC_STATE':
            self._draw_logic_state(painter, pen_body, pen_wire, body_color)
        elif self.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
            self._draw_sheet_connector(painter, pen_body, pen_wire, body_color)

        # Nombre y valor
        self._draw_labels(painter, text_color)

        # Pines — los dispositivos de 3 terminales dibujan sus propios pines
        # internamente con etiquetas; solo dibujar pines genéricos para el resto
        three_terminal = ('BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP',
                          'NET_LABEL_IN', 'NET_LABEL_OUT',
                          'DFF', 'JKFF', 'TFF', 'SRFF')
        if self.comp_type not in three_terminal:
            for pin in self.pin_positions():
                painter.setPen(pen_pin)
                painter.setBrush(QBrush(QColor(COLORS['pin'])))
                painter.drawEllipse(pin, PIN_RADIUS, PIN_RADIUS)

    def _draw_resistor(self, painter, pen_body, pen_wire, body_color):
        hw = COMP_W // 2
        hh = COMP_H // 2
        # Cables de conexión
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-hw, 0))
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        # Cuerpo (rectángulo)
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, COMP_W, COMP_H))
        # Símbolo zigzag interno
        painter.setPen(QPen(QColor(COLORS['component']), 1.5))
        pts = []
        steps = 6
        for i in range(steps + 1):
            x = -hw + i * (COMP_W / steps)
            y = (hh * 0.6) if i % 2 == 0 else -(hh * 0.6)
            pts.append(QPointF(x, y))
        for i in range(len(pts) - 1):
            painter.drawLine(pts[i], pts[i+1])

    def _draw_potentiometer(self, painter, pen_body, pen_wire, body_color):
        """Resistor + flecha diagonal que lo atraviesa (cursor variable)."""
        # Primero el resistor base
        self._draw_resistor(painter, pen_body, pen_wire, body_color)
        hw = COMP_W // 2
        hh = COMP_H // 2

        # Flecha diagonal con la inclinación según la posición del cursor.
        # wiper=0  → flecha apuntando a la izq;  wiper=1 → a la derecha.
        w        = max(0.0, min(1.0, float(self.pot_wiper)))
        # Punto inicial: izq-inferior, punta: cruza el cuerpo en diagonal
        arrow_pen = QPen(QColor(COLORS['comp_sel']), 2.2)
        painter.setPen(arrow_pen)
        x_start = -hw + 4
        y_start = hh + 8
        # X de la punta varía con el wiper para visualizar la posición
        x_tip   = -hw + 6 + (COMP_W - 12) * w
        y_tip   = -hh - 8
        # Línea principal
        painter.drawLine(QPointF(x_start, y_start), QPointF(x_tip, y_tip))
        # Cabeza de la flecha (triángulo)
        import math as _m
        dx = x_tip - x_start; dy = y_tip - y_start
        L  = _m.hypot(dx, dy) or 1.0
        ux, uy = dx/L, dy/L
        px, py = -uy, ux
        sz = 7
        head = QPolygonF([
            QPointF(x_tip, y_tip),
            QPointF(x_tip - sz*ux + sz*0.45*px, y_tip - sz*uy + sz*0.45*py),
            QPointF(x_tip - sz*ux - sz*0.45*px, y_tip - sz*uy - sz*0.45*py),
        ])
        painter.setBrush(QColor(COLORS['comp_sel']))
        painter.drawPolygon(head)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Indicador del % (pequeño, debajo)
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.setFont(QFont('Consolas', 6))
        painter.drawText(QRectF(-hw, hh + 14, COMP_W, 10),
                         Qt.AlignmentFlag.AlignCenter, f"{w*100:.0f}%")

    def _draw_transformer(self, painter, pen_body, pen_wire, body_color):
        """
        Transformador con dos bobinas verticales (primario izq, secundario der)
        y dos líneas verticales centrales que representan el núcleo de hierro.
        """
        import math as _m
        # ── Cables a los 4 pines ──────────────────────────────────────────
        painter.setPen(pen_wire)
        # Primario (izq): bobina entre y=-25 y y=25 a x=-30
        painter.drawLine(QPointF(-50, -25), QPointF(-30, -25))   # p1 → top de bobina
        painter.drawLine(QPointF(-50,  25), QPointF(-30,  25))   # p2 → bot de bobina
        # Secundario (der): bobina a x=30
        painter.drawLine(QPointF(30, -25), QPointF(50, -25))     # p3 → top
        painter.drawLine(QPointF(30,  25), QPointF(50,  25))     # p4 → bot

        # ── Bobinas (semicírculos apilados) ───────────────────────────────
        painter.setPen(QPen(QColor(COLORS['component']), 1.8))
        # Primario: 4 lazos a la izquierda (abren hacia la derecha)
        path_p = QPainterPath()
        path_p.moveTo(-30, -25)
        for i in range(4):
            cy = -25 + i*12 + 6
            # Semicírculo de radio 6 abriendo hacia la derecha (+90 a -90)
            path_p.arcTo(QRectF(-36, cy - 6, 12, 12), 90, -180)
        painter.drawPath(path_p)
        # Secundario: 4 lazos a la derecha (abren hacia la izquierda)
        path_s = QPainterPath()
        path_s.moveTo(30, -25)
        for i in range(4):
            cy = -25 + i*12 + 6
            path_s.arcTo(QRectF(24, cy - 6, 12, 12), 90, 180)
        painter.drawPath(path_s)

        # ── Núcleo de hierro: dos líneas verticales paralelas ─────────────
        painter.setPen(QPen(QColor(COLORS['text']), 1.4))
        painter.drawLine(QPointF(-3, -28), QPointF(-3, 28))
        painter.drawLine(QPointF( 3, -28), QPointF( 3, 28))

        # ── Etiqueta de relación ──────────────────────────────────────────
        painter.setFont(QFont('Consolas', 7))
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        # Mostrar n = ratio
        n   = self.xfmr_ratio
        # Formato pretty para 1:n o n:1
        if n >= 1:
            label = f"{n:.1f}:1"
        else:
            label = f"1:{1/n:.1f}"
        painter.drawText(QRectF(-30, 32, 60, 10),
                         Qt.AlignmentFlag.AlignCenter, label)
        # Indicar polaridad con un punto en la parte superior de cada bobina
        painter.setPen(QPen(QColor(COLORS['component']), 1))
        painter.setBrush(QColor(COLORS['component']))
        painter.drawEllipse(QPointF(-22, -30), 1.8, 1.8)
        painter.drawEllipse(QPointF( 22, -30), 1.8, 1.8)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_bridge_rectifier(self, painter, pen_body, pen_wire, body_color):
        """
        Puente rectificador en disposición de diamante con 4 diodos:

                    DC+ (top)
                     /\\
                    /  \\
              D3 ↗      ↘ D1
                /        \\
        AC1 ──┤          ├── AC2
                \\        /
              D4 ↘      ↗ D2
                    \\/
                    DC− (bottom)
        """
        # ── Pines ─────────────────────────────────────────────────────────
        # AC1 (izq), AC2 (der), DC+ (sup), DC- (inf)
        # Conectores cortos a los vértices del diamante
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-50, 0),  QPointF(-30, 0))   # AC1
        painter.drawLine(QPointF(30, 0),   QPointF(50, 0))    # AC2
        painter.drawLine(QPointF(0, -50),  QPointF(0, -30))   # DC+
        painter.drawLine(QPointF(0,  30),  QPointF(0,  50))   # DC−

        # ── Diamante ──────────────────────────────────────────────────────
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        diamond = QPolygonF([
            QPointF(0, -30),   # top  (DC+)
            QPointF(30,  0),   # right (AC2)
            QPointF(0,  30),   # bot  (DC-)
            QPointF(-30, 0),   # left (AC1)
        ])
        painter.drawPolygon(diamond)

        # ── 4 diodos dentro del diamante ──────────────────────────────────
        # Cada diodo: triángulo + raya. Los 4 apuntan hacia DC+ (excepto los
        # de DC- que apuntan desde DC- hacia los nodos AC).
        painter.setPen(QPen(QColor(COLORS['component']), 1.5))
        painter.setBrush(QBrush(QColor(COLORS['component'])))

        def draw_diode_arrow(painter, p_from, p_to):
            """Dibuja un diodo orientado de p_from → p_to dentro del puente."""
            dx = p_to.x() - p_from.x()
            dy = p_to.y() - p_from.y()
            import math as _m
            L  = _m.hypot(dx, dy) or 1.0
            ux, uy = dx/L, dy/L
            px, py = -uy, ux
            # Centro del diodo
            cx = (p_from.x() + p_to.x()) / 2
            cy = (p_from.y() + p_to.y()) / 2
            # Triángulo (apuntando a p_to)
            sz = 5
            tri = QPolygonF([
                QPointF(cx + sz*ux,            cy + sz*uy),
                QPointF(cx - sz*ux + sz*0.7*px, cy - sz*uy + sz*0.7*py),
                QPointF(cx - sz*ux - sz*0.7*px, cy - sz*uy - sz*0.7*py),
            ])
            painter.drawPolygon(tri)
            # Raya en la cabeza del triángulo (cátodo)
            tip_x = cx + sz*ux
            tip_y = cy + sz*uy
            painter.drawLine(
                QPointF(tip_x + 0.7*sz*px, tip_y + 0.7*sz*py),
                QPointF(tip_x - 0.7*sz*px, tip_y - 0.7*sz*py))

        # Brazos del puente: AC1→DC+ (D1), AC2→DC+ (D2), DC-→AC1 (D3), DC-→AC2 (D4)
        draw_diode_arrow(painter, QPointF(-30, 0), QPointF(0, -30))   # D1: AC1→DC+
        draw_diode_arrow(painter, QPointF(30, 0),  QPointF(0, -30))   # D2: AC2→DC+
        draw_diode_arrow(painter, QPointF(0, 30),  QPointF(-30, 0))   # D3: DC-→AC1
        draw_diode_arrow(painter, QPointF(0, 30),  QPointF(30, 0))    # D4: DC-→AC2
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Etiquetas de pines
        painter.setFont(QFont('Consolas', 6))
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.drawText(QRectF(-50, -8, 18, 10),  Qt.AlignmentFlag.AlignCenter, '~')
        painter.drawText(QRectF(32,  -8, 18, 10),  Qt.AlignmentFlag.AlignCenter, '~')
        painter.drawText(QRectF(-12, -50, 24, 10), Qt.AlignmentFlag.AlignCenter, '+')
        painter.drawText(QRectF(-12,  40, 24, 10), Qt.AlignmentFlag.AlignCenter, '−')

    def _draw_capacitor(self, painter, pen_body, pen_wire):
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-COMP_W//2 - 10, 0), QPointF(-8, 0))
        painter.drawLine(QPointF(8, 0), QPointF(COMP_W//2 + 10, 0))
        painter.setPen(pen_body)
        painter.drawLine(QPointF(-8, -COMP_H//2), QPointF(-8, COMP_H//2))
        painter.drawLine(QPointF(8, -COMP_H//2), QPointF(8, COMP_H//2))

    def _draw_inductor(self, painter, pen_body, pen_wire):
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-COMP_W//2 - 10, 0), QPointF(-COMP_W//2, 0))
        painter.drawLine(QPointF(COMP_W//2, 0), QPointF(COMP_W//2 + 10, 0))
        painter.setPen(pen_body)
        path = QPainterPath()
        path.moveTo(-COMP_W//2, 0)
        for i in range(4):
            cx = -COMP_W//2 + i * 15
            path.arcTo(QRectF(cx, -10, 15, 20), 180, -180)
        painter.drawPath(path)

    def _draw_source(self, painter, pen_body, pen_wire, body_color):
        r = COMP_H // 2 + 2
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-COMP_W//2 - 10, 0), QPointF(-r, 0))
        painter.drawLine(QPointF(r, 0), QPointF(COMP_W//2 + 10, 0))
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawEllipse(QPointF(0, 0), r, r)
        # Símbolo + / − / ~  o flecha
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        if self.comp_type == 'V':
            painter.drawText(QRectF(4, -r+4, r-4, r*2-8), Qt.AlignmentFlag.AlignCenter, '+')
        elif self.comp_type == 'VAC':
            # Onda sinusoidal dentro del círculo
            path = QPainterPath()
            path.moveTo(-r*0.5, 0)
            for i in range(1, 21):
                t = i / 20.0
                x = -r*0.5 + t * r
                y = -r*0.35 * math.sin(t * 2 * math.pi)
                path.lineTo(x, y)
            painter.drawPath(path)
        else:
            # Flecha de corriente
            painter.drawLine(QPointF(-8, 0), QPointF(8, 0))
            painter.drawLine(QPointF(4, -5), QPointF(8, 0))
            painter.drawLine(QPointF(4, 5), QPointF(8, 0))

    def _draw_impedance(self, painter, pen_body, pen_wire, body_color):
        hw = COMP_W // 2
        hh = COMP_H // 2
        # Cables
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-hw, 0))
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        # Cuerpo: rectángulo vacío (solo borde)
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, COMP_W, COMP_H))

    def _draw_gnd(self, painter, pen_body):
        painter.setPen(pen_body)
        painter.drawLine(QPointF(0, -5), QPointF(0, 5))
        for i, w in enumerate([20, 14, 8]):
            y = 5 + i * 5
            painter.drawLine(QPointF(-w//2, y), QPointF(w//2, y))

    def _draw_node(self, painter, color):
        painter.setPen(QPen(color, 1))
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QPointF(0, 0), 5, 5)

    def _draw_diode(self, painter, pen_body, pen_wire):
        hw = COMP_W // 2
        # Cables
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-hw + 8, 0))
        painter.drawLine(QPointF(hw - 8, 0), QPointF(hw + 10, 0))
        # Triángulo (ánodo → cátodo)
        painter.setPen(pen_body)
        painter.setBrush(QBrush(QColor(COLORS['comp_body'])))
        triangle = [QPointF(-hw + 8, -12), QPointF(-hw + 8, 12), QPointF(hw - 8, 0)]
        from PyQt6.QtGui import QPolygonF
        painter.drawPolygon(QPolygonF(triangle))
        # Línea del cátodo
        painter.drawLine(QPointF(hw - 8, -12), QPointF(hw - 8, 12))

    def _draw_led(self, painter, pen_body, pen_wire):
        """Dibuja LED: apagado=gris oscuro con tinte, encendido=color sólido brillante + glow + rayos."""
        from PyQt6.QtGui import QPolygonF, QRadialGradient
        hw = COMP_W // 2

        selected       = self.isSelected()
        led_on         = getattr(self, 'led_on', False)
        led_color_name = getattr(self, 'led_color', 'red')

        # Color sólido encendido / color apagado (gris con tinte)
        color_on = {
            'red':    QColor(255,  60,  60),
            'green':  QColor( 80, 255,  80),
            'blue':   QColor( 80, 160, 255),
            'yellow': QColor(255, 240,  60),
            'white':  QColor(255, 255, 255),
            'orange': QColor(255, 170,  30),
        }
        color_off = {
            'red':    QColor( 80,  30,  30),
            'green':  QColor( 25,  70,  25),
            'blue':   QColor( 25,  35,  90),
            'yellow': QColor( 80,  75,  20),
            'white':  QColor( 70,  70,  80),
            'orange': QColor( 80,  50,  20),
        }
        on_col  = color_on.get(led_color_name, QColor(255, 40, 40))
        off_col = color_off.get(led_color_name, QColor(60, 30, 30))
        body_col = on_col if led_on else off_col

        # ── Cables ───────────────────────────────────────────────────────
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-hw + 8, 0))
        painter.drawLine(QPointF(hw - 8,   0), QPointF(hw + 10, 0))

        # ── Glow halo cuando encendido ───────────────────────────────────
        if led_on:
            painter.setPen(Qt.PenStyle.NoPen)
            for radius, alpha in [(30, 30), (24, 55), (18, 90), (13, 130)]:
                gc = QColor(on_col)
                gc.setAlpha(alpha)
                painter.setBrush(QBrush(gc))
                painter.drawEllipse(QPointF(0, 0), radius, radius)

        # ── Cuerpo (triángulo relleno) ───────────────────────────────────
        # Borde: naranja si seleccionado, claro si encendido, normal si apagado
        if selected:
            outline_col = QColor(COLORS['comp_sel'])
        elif led_on:
            outline_col = on_col.lighter(160)
        else:
            outline_col = off_col.lighter(170)
        outline_pen = QPen(outline_col, 2)
        painter.setPen(outline_pen)

        triangle = [QPointF(-hw + 8, -12), QPointF(-hw + 8, 12), QPointF(hw - 8, 0)]
        if led_on:
            # Relleno con gradiente radial centrado en la punta (ánodo) para efecto brillante
            grad = QRadialGradient(QPointF(0, 0), hw)
            bright = QColor(on_col)
            bright.setAlpha(255)
            center_col = bright.lighter(180)   # núcleo casi blanco
            center_col.setAlpha(255)
            grad.setColorAt(0.0, center_col)
            grad.setColorAt(0.6, bright)
            edge_col = QColor(on_col)
            edge_col.setAlpha(200)
            grad.setColorAt(1.0, edge_col)
            painter.setBrush(QBrush(grad))
        else:
            painter.setBrush(QBrush(body_col))
        painter.drawPolygon(QPolygonF(triangle))

        # Línea del cátodo
        cathode_col = outline_col.lighter(120) if led_on else outline_col
        painter.setPen(QPen(cathode_col, 2))
        painter.drawLine(QPointF(hw - 8, -12), QPointF(hw - 8, 12))

        # ── Flechas de emisión de luz (siempre visibles) ──────────────────
        tip_x = hw - 8
        if led_on:
            arrow_col = on_col.lighter(150)
            arrow_alpha = 255
            arrow_width = 2.0
        elif selected:
            arrow_col = QColor(COLORS['comp_sel'])
            arrow_alpha = 200
            arrow_width = 1.5
        else:
            # Apagado: flechas tenues para indicar que ES un LED
            arrow_col = off_col.lighter(200)
            arrow_alpha = 120
            arrow_width = 1.2
        arrow_col.setAlpha(arrow_alpha)
        ray_pen = QPen(arrow_col, arrow_width, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap)
        painter.setPen(ray_pen)

        # Rayo 1 — diagonal hacia arriba-derecha
        painter.drawLine(QPointF(tip_x + 2,  -8), QPointF(tip_x + 14, -20))
        # Punta de flecha rayo 1
        painter.drawLine(QPointF(tip_x + 14, -20), QPointF(tip_x + 9, -18))
        painter.drawLine(QPointF(tip_x + 14, -20), QPointF(tip_x + 12, -14))

        # Rayo 2 — más vertical
        painter.drawLine(QPointF(tip_x + 6,  -6), QPointF(tip_x + 10, -20))
        # Punta de flecha rayo 2
        painter.drawLine(QPointF(tip_x + 10, -20), QPointF(tip_x + 6,  -17))
        painter.drawLine(QPointF(tip_x + 10, -20), QPointF(tip_x + 13, -16))

    def _draw_bjt(self, painter, pen_body, pen_wire):
        hw = COMP_W // 2
        hh = COMP_H // 2
        is_npn = (self.comp_type == 'BJT_NPN')

        # Círculo del cuerpo
        painter.setPen(pen_body)
        painter.setBrush(QBrush(QColor(COLORS['comp_body'])))
        painter.drawEllipse(QPointF(0, 0), hh + 4, hh + 4)

        # Base (izquierda): pin3 = (-hw-10, 0)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-8, 0))
        # Barra vertical de base
        painter.drawLine(QPointF(-8, -hh + 2), QPointF(-8, hh - 2))

        # Colector (der-arriba): p1 = (hw+10, -hh-6)
        painter.drawLine(QPointF(-8, -(hh - 2) // 2), QPointF(hw + 10, -hh - 6))
        # Emisor (der-abajo): p2 = (hw+10, hh+6)
        painter.drawLine(QPointF(-8,  (hh - 2) // 2), QPointF(hw + 10,  hh + 6))

        # Flecha en el emisor
        painter.setPen(pen_body)
        ex1 = hw + 10
        ey1 = hh + 6
        # Punto medio del cable del emisor para colocar la flecha
        mx = (-8 + ex1) // 2
        my = ((hh - 2) // 2 + ey1) // 2
        dx = ex1 - (-8)
        dy = ey1 - (hh - 2) // 2
        length = (dx**2 + dy**2) ** 0.5
        if length > 0:
            ux, uy = dx / length, dy / length   # vector unitario
            perp_x, perp_y = -uy, ux            # perpendicular
            tip_x = mx + ux * 6
            tip_y = my + uy * 6
            if is_npn:
                # Flecha apuntando hacia afuera (salida del emisor)
                painter.drawLine(QPointF(tip_x - ux*8 + perp_x*4,
                                         tip_y - uy*8 + perp_y*4),
                                  QPointF(tip_x, tip_y))
                painter.drawLine(QPointF(tip_x - ux*8 - perp_x*4,
                                         tip_y - uy*8 - perp_y*4),
                                  QPointF(tip_x, tip_y))
            else:
                # Flecha apuntando hacia adentro (PNP)
                base_x = mx - ux * 2
                base_y = my - uy * 2
                painter.drawLine(QPointF(base_x + perp_x*4, base_y + perp_y*4),
                                  QPointF(base_x, base_y))
                painter.drawLine(QPointF(base_x - perp_x*4, base_y - perp_y*4),
                                  QPointF(base_x, base_y))

        # Pines con etiquetas B / C / E
        font = QFont('Consolas', 7, QFont.Weight.Bold)
        painter.setFont(font)
        pin_color = QColor(COLORS['pin'])

        pin_data = [
            (QPointF(hw + 10, -hh - 6), 'C'),   # Colector
            (QPointF(hw + 10,  hh + 6), 'E'),   # Emisor
            (QPointF(-hw - 10, 0),      'B'),   # Base
        ]
        for pos, label in pin_data:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(pos, PIN_RADIUS, PIN_RADIUS)
            # Etiqueta al lado del pin
            painter.setPen(QPen(QColor(COLORS['text']), 1))
            offset_x = 6 if pos.x() > 0 else -14
            offset_y = -8 if pos.y() < 0 else 2
            if abs(pos.x()) < 5:  # pin central
                offset_x = 6
                offset_y = -8
            painter.drawText(QRectF(pos.x() + offset_x, pos.y() + offset_y, 14, 10),
                             Qt.AlignmentFlag.AlignLeft, label)

    def _draw_mosfet(self, painter, pen_body, pen_wire):
        hw = COMP_W // 2
        hh = COMP_H // 2
        is_nmos = (self.comp_type == 'NMOS')

        # Círculo del cuerpo
        painter.setPen(pen_body)
        painter.setBrush(QBrush(QColor(COLORS['comp_body'])))
        painter.drawEllipse(QPointF(0, 0), hh + 4, hh + 4)

        # Gate cable (izquierda → placa)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-10, 0))

        # Placa del gate
        painter.setPen(pen_body)
        painter.drawLine(QPointF(-8, -hh + 4), QPointF(-8, hh - 4))

        # Canal con gap de óxido
        gap = 4
        painter.drawLine(QPointF(-8 + gap, -hh + 4), QPointF(-8 + gap, -3))
        painter.drawLine(QPointF(-8 + gap,  3),       QPointF(-8 + gap,  hh - 4))

        # Drain (der-arriba) y Source (der-abajo)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-8 + gap, -(hh - 4) // 2), QPointF(hw + 10, -hh - 6))
        painter.drawLine(QPointF(-8 + gap,  (hh - 4) // 2), QPointF(hw + 10,  hh + 6))

        # Flecha de canal (N: hacia canal, P: alejándose)
        painter.setPen(pen_body)
        ax = -8 + gap + 8
        if is_nmos:
            painter.drawLine(QPointF(ax - 6, 0), QPointF(ax, 0))
            painter.drawLine(QPointF(ax - 4, -3), QPointF(ax, 0))
            painter.drawLine(QPointF(ax - 4,  3), QPointF(ax, 0))
        else:
            painter.drawLine(QPointF(ax, 0), QPointF(ax - 6, 0))
            painter.drawLine(QPointF(ax - 2, -3), QPointF(ax - 6, 0))
            painter.drawLine(QPointF(ax - 2,  3), QPointF(ax - 6, 0))

        # Pines con etiquetas G / D / S
        font = QFont('Consolas', 7, QFont.Weight.Bold)
        painter.setFont(font)
        pin_color = QColor(COLORS['pin'])

        pin_data = [
            (QPointF(hw + 10, -hh - 6), 'D',  6, -8),
            (QPointF(hw + 10, -hh - 6), 'D',  6, -8),
            (QPointF(hw + 10,  hh + 6), 'S',  6,  2),
            (QPointF(-hw - 10, 0),      'G', -14, -8),
        ]
        for pos, label, ox, oy in pin_data:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(pos, PIN_RADIUS, PIN_RADIUS)
            painter.setPen(QPen(QColor(COLORS['text']), 1))
            painter.drawText(QRectF(pos.x() + ox, pos.y() + oy, 14, 10),
                             Qt.AlignmentFlag.AlignLeft, label)

    def _draw_opamp(self, painter, pen_body, pen_wire, body_color):
        hw = COMP_W // 2
        hh = COMP_H // 2 + 6
        from PyQt6.QtGui import QPolygonF

        # Triángulo del op-amp
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        triangle = [QPointF(-hw, -hh), QPointF(-hw, hh), QPointF(hw, 0)]
        painter.drawPolygon(QPolygonF(triangle))

        # Cables: salida (der), entrada+ (izq-arriba), entrada- (izq-abajo)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(hw, 0),          QPointF(hw + 10, 0))
        painter.drawLine(QPointF(-hw - 10, -hh // 2), QPointF(-hw, -hh // 2))
        painter.drawLine(QPointF(-hw - 10,  hh // 2), QPointF(-hw,  hh // 2))

        # Símbolos + y − dentro del triángulo
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        painter.drawText(QRectF(-hw + 4, -hh + 4,  12, 12), Qt.AlignmentFlag.AlignCenter, '+')
        painter.drawText(QRectF(-hw + 4,  hh - 16, 12, 12), Qt.AlignmentFlag.AlignCenter, '−')

        # Pines con etiquetas
        font = QFont('Consolas', 7, QFont.Weight.Bold)
        painter.setFont(font)
        pin_color = QColor(COLORS['pin'])

        pin_data = [
            (QPointF(hw + 10, 0),          'OUT',  6,  -4),
            (QPointF(-hw - 10, -hh // 2),  'V+',  -20,  -8),
            (QPointF(-hw - 10,  hh // 2),  'V−',  -20,   2),
        ]
        for pos, label, ox, oy in pin_data:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(pos, PIN_RADIUS, PIN_RADIUS)
            painter.setPen(QPen(QColor(COLORS['text']), 1))
            painter.drawText(QRectF(pos.x() + ox, pos.y() + oy, 28, 10),
                             Qt.AlignmentFlag.AlignLeft, label)


    # ──────────────────────────────────────────────────────────────────────
    # Dibujo de componentes digitales
    # ──────────────────────────────────────────────────────────────────────

    def _gate_geometry(self):
        """
        Retorna (hw, hh, step, n).
        step = GRID_SIZE = 20px siempre.
        Pines centrados en y=0: y_i = -(n-1)*step//2 + i*step
        hh = max(|y_i|) + step
        """
        n    = max(self.dig_inputs, 1)
        hw   = COMP_W // 2                     # 30 → salida en x=40
        step = GRID_SIZE                        # 20px entre pines
        hh   = (n - 1) * step // 2 + step      # altura mínima para contener pines
        return hw, hh, step, n

    def _gate_pin_ys(self):
        """Posiciones y de los pines de entrada, centradas en 0."""
        _, _, step, n = self._gate_geometry()
        return [-(n - 1) * step // 2 + i * step for i in range(n)]

    def _draw_digital_gate(self, painter, pen_body, pen_wire, body_color, label: str):
        """Cuerpo rectangular de puerta lógica con etiqueta central.

        Se usa para COMPARATOR y PWM (no tienen símbolo ANSI clásico).
        Las puertas booleanas (AND/OR/NOT/NAND/NOR/XOR) usan _draw_ansi_gate.
        """
        hw, hh, step, n = self._gate_geometry()
        # Cuerpo
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRoundedRect(QRectF(-hw, -hh, hw * 2, hh * 2), 4, 4)
        # Etiqueta
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        font = QFont('Consolas', 8, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(-hw, -hh, hw * 2, hh * 2),
                         Qt.AlignmentFlag.AlignCenter, label)
        # Cables: entradas izquierda, salida derecha
        painter.setPen(pen_wire)
        pin_ys = self._gate_pin_ys()
        for y in pin_ys:
            painter.drawLine(QPointF(-hw - 10, y), QPointF(-hw, y))
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        # Pines
        pin_color = QColor(COLORS['pin'])
        for y in pin_ys:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(QPointF(-hw - 10, y), PIN_RADIUS, PIN_RADIUS)
        painter.drawEllipse(QPointF(hw + 10, 0), PIN_RADIUS, PIN_RADIUS)

    # ──────────────────────────────────────────────────────────────────────
    # Puertas con simbología ANSI/IEEE estándar
    # ──────────────────────────────────────────────────────────────────────
    def _draw_ansi_gate(self, painter, pen_body, pen_wire, body_color):
        """
        Dibuja la puerta lógica usando la simbología distintiva ANSI/IEEE
        (la "tradicional" americana):

            AND  / NAND  → forma de D (rect + semielipse a la derecha)
            OR   / NOR   → forma de escudo (back cóncavo + curvas frontales
                           que confluyen en una punta)
            XOR          → como OR + curva paralela cóncava extra a la entrada
            NOT          → triángulo apuntando a la derecha

        Las versiones invertidas (NAND, NOR, NOT) llevan un círculo de
        inversión (bubble) en la salida.

        El bounding [-hw,hw] × [-hh,hh] coincide con _gate_geometry() para
        que las posiciones de los pines (y por tanto de los cables y la
        bounding-rect del componente) sigan siendo válidas.
        """
        hw, hh, step, n = self._gate_geometry()
        ct          = self.comp_type
        bubble_d    = 7                       # diámetro del bubble de inversión
        has_bubble  = ct in ('NAND', 'NOR', 'NOT')
        # Si lleva bubble, el cuerpo termina antes para dejarle hueco;
        # el bubble queda entre body_right y x=hw (donde nace el cable de salida).
        body_right  = hw - bubble_d if has_bubble else hw
        body_w      = body_right - (-hw)      # ancho del cuerpo

        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))

        # ── Cuerpo según el tipo ──────────────────────────────────────────
        if ct in ('AND', 'NAND'):
            # Forma de D: mitad izquierda rectangular + semielipse derecha.
            path = QPainterPath()
            flat_w     = body_w * 0.5
            flat_x_end = -hw + flat_w
            arc_a      = body_w - flat_w     # = body_w * 0.5
            path.moveTo(-hw, -hh)
            path.lineTo(flat_x_end, -hh)
            # Semi-elipse: 90° (12 o'clock) sweeping -180° (clockwise) → derecha
            path.arcTo(flat_x_end - arc_a, -hh,
                       2 * arc_a, 2 * hh,
                       90, -180)
            path.lineTo(-hw, hh)
            path.closeSubpath()
            painter.drawPath(path)

        elif ct in ('OR', 'NOR', 'XOR'):
            # Forma de escudo OR: back cóncavo + dos curvas que confluyen
            # en una punta a la derecha.
            back_bulge  = body_w * 0.25      # cuán adentro entra la curva trasera
            front_pull  = body_w * 0.55      # control de las curvas frontales
            path = QPainterPath()
            path.moveTo(-hw, -hh)            # esquina superior trasera
            # Curva superior hasta la punta
            path.quadTo(-hw + front_pull, -hh,
                        body_right, 0)
            # Curva inferior desde la punta
            path.quadTo(-hw + front_pull,  hh,
                        -hw, hh)
            # Curva trasera cóncava (bulge a la derecha)
            path.quadTo(-hw + back_bulge, 0,
                        -hw, -hh)
            path.closeSubpath()
            painter.drawPath(path)

            if ct == 'XOR':
                # Curva extra paralela al back, desplazada hacia la izquierda.
                xor_offset = 5
                xor_path   = QPainterPath()
                xor_path.moveTo(-hw - xor_offset, -hh)
                xor_path.quadTo(-hw - xor_offset + back_bulge, 0,
                                -hw - xor_offset,  hh)
                # Sólo trazo, sin relleno
                painter.strokePath(xor_path, pen_body)

        elif ct == 'NOT':
            # Triángulo equilátero apuntando a la derecha
            path = QPainterPath()
            path.moveTo(-hw, -hh)
            path.lineTo(-hw,  hh)
            path.lineTo(body_right, 0)
            path.closeSubpath()
            painter.drawPath(path)

        # ── Bubble de inversión (NAND / NOR / NOT) ────────────────────────
        if has_bubble:
            # El borde derecho del bubble toca x=hw (donde sale el cable).
            bubble_cx = body_right + bubble_d / 2
            painter.drawEllipse(QPointF(bubble_cx, 0),
                                bubble_d / 2, bubble_d / 2)

        # ── Cables de conexión ────────────────────────────────────────────
        painter.setPen(pen_wire)
        pin_ys = self._gate_pin_ys()

        # Para AND/NAND/NOT el lateral es vertical → cable termina en x=-hw.
        # Para OR/NOR/XOR el back es cóncavo (curva Bezier cuadrática), por
        # lo que x varía según y. Cada cable debe terminar EXACTAMENTE sobre
        # la curva — si se queda corto deja un hueco; si se pasa, "atraviesa"
        # el cuerpo y se ve mal.
        #
        # Bezier cuadrático con extremos (-hw, ±hh) y control (-hw+back_bulge, 0):
        #   y(t) = hh·(2t − 1)        →  t = (y + hh)/(2hh)
        #   x(t) = -hw + 2t(1−t)·back_bulge
        if ct in ('OR', 'NOR', 'XOR'):
            # Para XOR los cables conectan a la curva EXTERIOR (más a la izq).
            outer_offset = 5 if ct == 'XOR' else 0
            back_bulge_eff = body_w * 0.25     # mismo back_bulge que el path
            for y in pin_ys:
                t      = (y + hh) / (2 * hh) if hh > 0 else 0.5
                back_x = -hw - outer_offset + 2 * t * (1 - t) * back_bulge_eff
                painter.drawLine(QPointF(-hw - 10, y), QPointF(back_x, y))
        else:
            for y in pin_ys:
                painter.drawLine(QPointF(-hw - 10, y), QPointF(-hw, y))

        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))

        # ── Pines (puntos de conexión) ────────────────────────────────────
        pin_color = QColor(COLORS['pin'])
        painter.setPen(QPen(pin_color, 2))
        painter.setBrush(QBrush(pin_color))
        for y in pin_ys:
            painter.drawEllipse(QPointF(-hw - 10, y), PIN_RADIUS, PIN_RADIUS)
        painter.drawEllipse(QPointF(hw + 10, 0), PIN_RADIUS, PIN_RADIUS)

    def _draw_flipflop(self, painter, pen_body, pen_wire, body_color, ff_type: str):
        """Flip-flop con etiquetas específicas por tipo, SET/RESET y círculo de memoria.

        Layout común:
            - Cuerpo rectangular con título (DFF / JKFF / TFF / SRFF)
            - p1 = Q       (derecha-arriba)
            - p2 = Entrada principal (izquierda-arriba): D / J / T / S
            - p3 = Entrada secundaria (izquierda-abajo): CLK / K / R
            - p4 = SET     (arriba)
            - p5 = RESET   (abajo)
            - Qn dibujada como salida derecha-abajo (sin pin externo)
            - Círculo central muestra el estado actual de Q (dig_q_state)
        """
        hw, hh = COMP_W // 2, COMP_H // 2 + 8       # 30, 23
        # ── Cuerpo ────────────────────────────────────────────────────────
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, hw * 2, hh * 2))

        # ── Etiqueta del tipo (parte superior) ────────────────────────────
        title = {'DFF': 'D-FF', 'JKFF': 'JK-FF',
                 'TFF': 'T-FF', 'SRFF': 'SR-FF'}[ff_type]
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        font = QFont('Consolas', 7, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(-hw, -hh + 1, hw * 2, 11),
                         Qt.AlignmentFlag.AlignCenter, title)

        # ── Pines izquierda (entrada principal arriba, secundaria abajo) ──
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, -hh // 2), QPointF(-hw, -hh // 2))
        painter.drawLine(QPointF(-hw - 10,  hh // 2), QPointF(-hw,  hh // 2))
        # Pines derecha (Q arriba, Qn abajo)
        painter.drawLine(QPointF(hw, -hh // 2), QPointF(hw + 10, -hh // 2))
        painter.drawLine(QPointF(hw,  hh // 2), QPointF(hw + 10,  hh // 2))
        # Pines verticales: SET arriba, RESET abajo
        painter.drawLine(QPointF(0, -hh), QPointF(0, -hh - 10))
        painter.drawLine(QPointF(0,  hh), QPointF(0,  hh + 10))

        # ── Símbolo de reloj (triángulo) en el pin de CLK ─────────────────
        # SRFF y JKFF no tienen CLK como pin físico (SRFF asíncrono;
        # JKFF usa el net global dig_clk).
        if ff_type in ('DFF', 'TFF'):
            painter.setPen(QPen(QColor(COLORS['component']), 1.5))
            cy = hh // 2
            painter.drawLine(QPointF(-hw, cy - 5), QPointF(-hw + 6, cy))
            painter.drawLine(QPointF(-hw + 6, cy), QPointF(-hw, cy + 5))

        # ── Etiquetas de pin internas ─────────────────────────────────────
        labels = {
            'DFF':  ('D',  'CLK'),
            'JKFF': ('J',  'K'),     # K en lugar de CLK como secundaria
            'TFF':  ('T',  'CLK'),
            'SRFF': ('S',  'R'),
        }[ff_type]
        # Para JKFF, la entrada CLK aparece etiquetada también: el flip-flop JK
        # usa j/k/clk pero solo tenemos 2 pines de entrada laterales. Convención:
        # p2 = J (arriba), p3 = K (abajo); CLK se asume en el net p3 también
        # (la simulación lo enruta vía dig_clk como nombre de net global).
        font2 = QFont('Consolas', 6)
        painter.setFont(font2)
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.drawText(QRectF(-hw + 2, -hh // 2 - 8, 18, 10),
                         Qt.AlignmentFlag.AlignLeft, labels[0])
        painter.drawText(QRectF(-hw + 2,  hh // 2 - 8, 22, 10),
                         Qt.AlignmentFlag.AlignLeft, labels[1])
        painter.drawText(QRectF(hw - 14, -hh // 2 - 8, 14, 10),
                         Qt.AlignmentFlag.AlignRight, 'Q')
        painter.drawText(QRectF(hw - 14,  hh // 2 - 8, 18, 10),
                         Qt.AlignmentFlag.AlignRight, 'Q̄')
        # Etiquetas SET / RESET (encima/debajo del cuerpo, junto a sus pines)
        painter.drawText(QRectF(2, -hh - 12, 28, 10),
                         Qt.AlignmentFlag.AlignLeft, 'S')
        painter.drawText(QRectF(2,  hh + 2, 28, 10),
                         Qt.AlignmentFlag.AlignLeft, 'R')

        # ── Círculo central de memoria ────────────────────────────────────
        # Verde brillante = Q vale 1, gris oscuro = Q vale 0.
        q = 1 if int(getattr(self, 'dig_q_state', 0)) else 0
        mem_r = 7
        mem_color = QColor('#27ae60') if q else QColor(COLORS['comp_body']).darker(125)
        mem_border = QColor(COLORS['component'])
        painter.setPen(QPen(mem_border, 1.5))
        painter.setBrush(QBrush(mem_color))
        painter.drawEllipse(QPointF(0, 1), mem_r, mem_r)
        # Dígito interno (1 ó 0) en blanco
        painter.setPen(QPen(QColor('white' if q else '#7f8c8d'), 1))
        font_q = QFont('Consolas', 8, QFont.Weight.Bold)
        painter.setFont(font_q)
        painter.drawText(QRectF(-mem_r, 1 - mem_r, mem_r * 2, mem_r * 2),
                         Qt.AlignmentFlag.AlignCenter, str(q))

        # ── Puntos de pin ─────────────────────────────────────────────────
        pin_color = QColor(COLORS['pin'])
        painter.setPen(QPen(pin_color, 2))
        painter.setBrush(QBrush(pin_color))
        for px, py in [(-hw - 10, -hh // 2), (-hw - 10, hh // 2),
                       (hw + 10, -hh // 2), (hw + 10, hh // 2),
                       (0, -hh - 10), (0, hh + 10)]:
            painter.drawEllipse(QPointF(px, py), PIN_RADIUS, PIN_RADIUS)

    def _draw_clk(self, painter, pen_body, pen_wire, body_color):
        """Reloj digital: cuadrado con onda cuadrada y dígito 0/1 grande.

        Doble-click: conmuta manualmente (como LOGIC_STATE).
        Ctrl+K (con el componente seleccionado): activa/desactiva oscilación
        automática a la frecuencia configurada en Herramientas → Frecuencia CLK.
        """
        hw = COMP_W // 2
        hh = COMP_H // 2
        state = int(self.value) & 1

        # Color de fondo según estado y modo
        if self.clk_running:
            col_off = QColor('#2980b9')   # azul (oscilando, parte LOW)
            col_on  = QColor('#3498db')
        else:
            col_off = QColor('#7f8c8d')   # gris (manual, parte LOW)
            col_on  = QColor('#bdc3c7')
        fill = col_on if state else col_off

        painter.setPen(pen_body)
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(QRectF(-hw, -hh, hw * 2, hh * 2), 4, 4)

        # Onda cuadrada como icono central (esquina superior izquierda)
        painter.setPen(QPen(QColor('white'), 1.5))
        wx0 = -hw + 4
        wy0 = -hh + 4
        wave_h = 8
        path = QPainterPath()
        path.moveTo(wx0,        wy0 + wave_h)
        path.lineTo(wx0 + 4,    wy0 + wave_h)
        path.lineTo(wx0 + 4,    wy0)
        path.lineTo(wx0 + 10,   wy0)
        path.lineTo(wx0 + 10,   wy0 + wave_h)
        path.lineTo(wx0 + 16,   wy0 + wave_h)
        path.lineTo(wx0 + 16,   wy0)
        path.lineTo(wx0 + 22,   wy0)
        painter.drawPath(path)

        # Dígito grande del estado
        font_big = QFont('Consolas', 18, QFont.Weight.Bold)
        painter.setFont(font_big)
        painter.setPen(QPen(QColor('white'), 2))
        painter.drawText(QRectF(-hw, -hh + 4, hw * 2, hh * 2),
                         Qt.AlignmentFlag.AlignCenter, str(state))

        # Pin de salida (derecha)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        pin_color = QColor(COLORS['pin'])
        painter.setPen(QPen(pin_color, 2))
        painter.setBrush(QBrush(pin_color))
        painter.drawEllipse(QPointF(hw + 10, 0), PIN_RADIUS, PIN_RADIUS)

    def _draw_adc_dac(self, painter, pen_body, pen_wire, body_color, is_adc: bool):
        """Bloque ADC o DAC con flecha de conversión y datos de configuración."""
        hw, hh = COMP_W // 2, COMP_H // 2 + 6
        # Cuerpo
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, hw * 2, hh * 2))
        # Etiqueta principal
        lbl = 'ADC' if is_adc else 'DAC'
        font = QFont('Consolas', 9, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        painter.drawText(QRectF(-hw, -hh, hw * 2, hh * 2),
                         Qt.AlignmentFlag.AlignCenter, lbl)
        # Flecha de conversión
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        font2 = QFont('Consolas', 6)
        painter.setFont(font2)
        if is_adc:
            painter.drawText(QRectF(-hw + 2, 4, hw * 2 - 4, 12),
                             Qt.AlignmentFlag.AlignCenter,
                             f'{self.dig_bits_adc}b {self.dig_vref:.1f}V')
        else:
            painter.drawText(QRectF(-hw + 2, 4, hw * 2 - 4, 12),
                             Qt.AlignmentFlag.AlignCenter,
                             f'{self.dig_bits_adc}b {self.dig_vref:.1f}V')
        # Pins: izquierda=analógico, derecha=digital
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-hw, 0))  # analógico
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))    # digital
        pin_color = QColor(COLORS['pin'])
        for px in [-hw - 10, hw + 10]:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(QPointF(px, 0), PIN_RADIUS, PIN_RADIUS)
        # Etiquetas de pin
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.setFont(font2)
        painter.drawText(QRectF(-hw - 24, -6, 20, 10), Qt.AlignmentFlag.AlignRight, 'A')
        painter.drawText(QRectF(hw + 4, -6, 20, 10), Qt.AlignmentFlag.AlignLeft, 'D')

    def _draw_logic_state(self, painter, pen_body, pen_wire, body_color):
        """Botón de estado lógico: cuadrado con 1/0 grande, un pin de salida."""
        hw = COMP_W // 2
        hh = COMP_H // 2
        state = int(self.value)   # 0 o 1
        # Cuerpo — color según estado
        col_on  = QColor('#27ae60')   # verde = HIGH
        col_off = QColor('#c0392b')   # rojo  = LOW
        fill = col_on if state else col_off
        painter.setPen(pen_body)
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(QRectF(-hw, -hh, hw * 2, hh * 2), 6, 6)
        # Dígito grande
        font_big = QFont('Consolas', 22, QFont.Weight.Bold)
        painter.setFont(font_big)
        painter.setPen(QPen(QColor('white'), 2))
        painter.drawText(QRectF(-hw, -hh, hw * 2, hh * 2),
                         Qt.AlignmentFlag.AlignCenter, str(state))
        # Pin de salida (derecha)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        pin_color = QColor(COLORS['pin'])
        painter.setPen(QPen(pin_color, 2))
        painter.setBrush(QBrush(pin_color))
        painter.drawEllipse(QPointF(hw + 10, 0), PIN_RADIUS, PIN_RADIUS)

    def _draw_counter(self, painter, pen_body, pen_wire, body_color):
        """Contador binario N-bit."""
        hw, hh = COMP_W // 2, COMP_H // 2 + 6
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, hw * 2, hh * 2))
        font = QFont('Consolas', 7, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        bits_lbl = f'CNT {self.dig_bits}b'
        painter.drawText(QRectF(-hw, -hh, hw * 2, hh * 2),
                         Qt.AlignmentFlag.AlignCenter, bits_lbl)
        # CLK pin izquierda, salidas derecha
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-hw, 0))
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        pin_color = QColor(COLORS['pin'])
        for px in [-hw - 10, hw + 10]:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(QPointF(px, 0), PIN_RADIUS, PIN_RADIUS)

    def _draw_mux(self, painter, pen_body, pen_wire, body_color):
        """MUX 2:1."""
        hw, hh = COMP_W // 2, COMP_H // 2 + 4
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, hw * 2, hh * 2))
        font = QFont('Consolas', 7, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        painter.drawText(QRectF(-hw, -hh, hw * 2, hh * 2),
                         Qt.AlignmentFlag.AlignCenter, 'MUX 2:1')
        painter.setPen(pen_wire)
        for y in [-hh // 2, hh // 2]:
            painter.drawLine(QPointF(-hw - 10, y), QPointF(-hw, y))
        painter.drawLine(QPointF(-hw // 2, hh + 4), QPointF(-hw // 2, hh + 10))  # sel
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        pin_color = QColor(COLORS['pin'])
        for px, py in [(-hw - 10, -hh // 2), (-hw - 10, hh // 2),
                       (-hw // 2, hh + 10), (hw + 10, 0)]:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(QPointF(px, py), PIN_RADIUS, PIN_RADIUS)

    def _draw_sheet_connector(self, painter, pen_body, pen_wire, body_color):
        """Net label como flecha pequeña del mismo color que GND, apuntando a la derecha.

        INPUT  (entrada):  ─►●     pin en la CABEZA (extremo derecho)
        OUTPUT (salida):   ●─►     pin en la COLA   (extremo izquierdo)
        """
        is_input = self.comp_type == 'NET_LABEL_IN'
        label = self.sheet_label or self.name

        # Mismo color que GND: pen_body (line_color = COLORS['component'])
        arrow_pen = pen_body
        arrow_color = arrow_pen.color()
        tip_sz = 6

        # Flecha de ~30 px de largo (similar al ancho de GND)
        tail_x = -15
        head_x =  15
        pin_x  = head_x if is_input else tail_x

        # ── Línea + cabeza de la flecha ───────────────────────────────────
        painter.setPen(arrow_pen)
        painter.setBrush(QBrush(arrow_color))
        head_base_x = head_x - tip_sz
        painter.drawLine(QPointF(tail_x, 0), QPointF(head_base_x, 0))
        arrow = QPolygonF([
            QPointF(head_x, 0),                          # punta
            QPointF(head_base_x, -tip_sz * 0.55),
            QPointF(head_base_x,  tip_sz * 0.55),
        ])
        painter.drawPolygon(arrow)

        # ── Etiqueta encima de la flecha ──────────────────────────────────
        font = QFont('Consolas', 7, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(COLORS['text']), 1))
        text_rect = QRectF(tail_x - 4, -15, (head_x - tail_x) + 8, 11)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
            label,
        )

        # ── Pin de conexión ───────────────────────────────────────────────
        pin_color = QColor(COLORS['pin'])
        painter.setPen(QPen(pin_color, 2))
        painter.setBrush(QBrush(pin_color))
        painter.drawEllipse(QPointF(pin_x, 0), PIN_RADIUS, PIN_RADIUS)

    def _draw_labels(self, painter, text_color):
        if self.comp_type in ('GND', 'NODE', 'NET_LABEL_IN', 'NET_LABEL_OUT'):
            return
        font = QFont('Consolas', 8)
        painter.setFont(font)
        painter.setPen(QPen(text_color))

        # Nombre arriba
        name_rect = QRectF(-COMP_W//2, -COMP_H//2 - 18, COMP_W, 16)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, self.name)

        # Valor abajo
        if self.value != 0:
            val_str = self._format_value()
            val_rect = QRectF(-COMP_W//2, COMP_H//2 + 2, COMP_W, 16)
            painter.setPen(QPen(QColor(COLORS['text_dim'])))
            painter.drawText(val_rect, Qt.AlignmentFlag.AlignCenter, val_str)

        # Resultado de simulación
        if self.result_voltage is not None:
            res_str = f"{self.result_voltage:.3f}V"
            res_rect = QRectF(-COMP_W//2, COMP_H//2 + 16, COMP_W, 16)
            painter.setPen(QPen(QColor(COLORS['voltage'])))
            painter.drawText(res_rect, Qt.AlignmentFlag.AlignCenter, res_str)

    def _format_value(self) -> str:
        if self.comp_type == 'Z':
            if self.z_mode == 'rect':
                real = self.z_real
                imag = self.z_imag
                if abs(imag) < 1e-12:
                    return f"{real:.3g}Ω"
                sign = '+' if imag >= 0 else '-'
                return f"{real:.3g}{sign}{abs(imag):.3g}jΩ"
            else:
                return f"{self.z_mag:.3g}∠{self.z_phase:.1f}°Ω"
        v = self.value
        if abs(v) >= 1e6:
            return f"{v/1e6:.2g}M{self.unit}"
        elif abs(v) >= 1e3:
            return f"{v/1e3:.2g}k{self.unit}"
        elif abs(v) >= 1:
            return f"{v:.2g}{self.unit}"
        elif abs(v) >= 1e-3:
            return f"{v*1e3:.2g}m{self.unit}"
        elif abs(v) >= 1e-6:
            return f"{v*1e6:.2g}μ{self.unit}"
        return f"{v:.2g}{self.unit}"

    # ── Snap a grid ──────────────────────────────
    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            x = round(value.x() / GRID_SIZE) * GRID_SIZE
            y = round(value.y() / GRID_SIZE) * GRID_SIZE
            return QPointF(x, y)
        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            # NUEVO: Notificar a la escena para actualizar cables
            if self.scene() and hasattr(self.scene(), 'update_wires_for_component'):
                self.scene().update_wires_for_component(self)
        return super().itemChange(change, value)

# ══════════════════════════════════════════════════════════════
# ÍTEM DE CABLE (WIRE)
# ══════════════════════════════════════════════════════════════
class WireItem(QGraphicsLineItem):
    def __init__(self, p1: QPointF, p2: QPointF,
                 start_comp=None, start_pin_idx=0,
                 end_comp=None, end_pin_idx=0):
        super().__init__(QLineF(p1, p2))
        self.setPen(QPen(QColor(COLORS['wire']), 2, Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.RoundCap))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        # NUEVO: Referencias a componentes conectados
        self.start_comp = start_comp
        self.start_pin_idx = start_pin_idx
        self.end_comp = end_comp
        self.end_pin_idx = end_pin_idx
    
    def update_from_pins(self):
        """Actualiza la línea según posición actual de los pines"""
        p1 = self.line().p1()
        p2 = self.line().p2()
        
        if self.start_comp and self.start_comp.scene():
            pins = self.start_comp.all_pin_positions_scene()
            if 0 <= self.start_pin_idx < len(pins):
                p1 = pins[self.start_pin_idx]
        
        if self.end_comp and self.end_comp.scene():
            pins = self.end_comp.all_pin_positions_scene()
            if 0 <= self.end_pin_idx < len(pins):
                p2 = pins[self.end_pin_idx]
        
        self.setLine(QLineF(p1, p2))

    def paint(self, painter, option, widget):
        if self.isSelected():
            self.setPen(QPen(QColor(COLORS['wire_sel']), 2.5))
        else:
            self.setPen(QPen(QColor(COLORS['wire']), 2, Qt.PenStyle.SolidLine,
                             Qt.PenCapStyle.RoundCap))
        super().paint(painter, option, widget)
        
# ══════════════════════════════════════════════════════════════
# ESCENA DEL CIRCUITO
# ══════════════════════════════════════════════════════════════
class CircuitScene(QGraphicsScene):
    component_selected   = pyqtSignal(object)
    status_message       = pyqtSignal(str)
    logic_state_toggled  = pyqtSignal(object)   # emitido cuando LOGIC_STATE cambia

    def __init__(self):
        super().__init__()
        self.setSceneRect(-1000, -1000, 2000, 2000)
        self.setBackgroundBrush(QBrush(QColor(COLORS['bg'])))

        self.components: List[ComponentItem] = []
        self.wires: List[WireItem] = []

        self._wire_start: Optional[QPointF] = None
        self._wire_preview: Optional[WireItem] = None
        self._mode = 'select'   # 'select' | 'wire' | 'place_{tipo}'

        self._comp_counter: Dict[str, int] = {}

        # Estado para arrastre grupal (mover circuito + cables como una unidad)
        self._group_drag_active: bool = False
        self._group_drag_start_pos: Optional[QPointF] = None
        self._group_drag_wires: List[dict] = []

    # ── Grid (dibujado en drawBackground para que sea independiente del zoom) ──
    def drawBackground(self, painter: QPainter, rect: QRectF):
        super().drawBackground(painter, rect)

        left   = int(math.floor(rect.left()  / GRID_SIZE)) * GRID_SIZE
        right  = int(math.ceil(rect.right()  / GRID_SIZE)) * GRID_SIZE
        top    = int(math.floor(rect.top()   / GRID_SIZE)) * GRID_SIZE
        bottom = int(math.ceil(rect.bottom() / GRID_SIZE)) * GRID_SIZE

        pen_minor = QPen(QColor(COLORS['grid_line']), 0)
        pen_minor.setCosmetic(True)
        pen_minor.setStyle(Qt.PenStyle.DotLine)

        pen_major = QPen(QColor(COLORS['grid_line']), 0)
        pen_major.setCosmetic(True)
        pen_major.setStyle(Qt.PenStyle.SolidLine)

        lines_minor = []
        lines_major = []
        x = left
        while x <= right:
            idx = round(x / GRID_SIZE)
            line = QLineF(x, rect.top(), x, rect.bottom())
            if idx % 5 == 0:
                lines_major.append(line)
            else:
                lines_minor.append(line)
            x += GRID_SIZE

        y = top
        while y <= bottom:
            idx = round(y / GRID_SIZE)
            line = QLineF(rect.left(), y, rect.right(), y)
            if idx % 5 == 0:
                lines_major.append(line)
            else:
                lines_minor.append(line)
            y += GRID_SIZE

        painter.setPen(pen_minor)
        painter.drawLines(lines_minor)
        painter.setPen(pen_major)
        painter.drawLines(lines_major)

    # ── Punto de unión (junction dot) ───────────
    def drawForeground(self, painter: QPainter, rect: QRectF):
        super().drawForeground(painter, rect)

        # Cuenta extremos LIBRES de cables (sin componente conectado) por
        # posición snapeada. Cuando concurren más de 3 en un mismo punto
        # se dibuja un dot tipo pin para indicar la unión cable-cable.
        SNAP = 5
        counts: Dict[Tuple[int, int], int] = {}
        positions: Dict[Tuple[int, int], QPointF] = {}
        for w in self.wires:
            line = w.line()
            if w.start_comp is None:
                p = line.p1()
                key = (round(p.x() / SNAP) * SNAP, round(p.y() / SNAP) * SNAP)
                counts[key] = counts.get(key, 0) + 1
                positions.setdefault(key, p)
            if w.end_comp is None:
                p = line.p2()
                key = (round(p.x() / SNAP) * SNAP, round(p.y() / SNAP) * SNAP)
                counts[key] = counts.get(key, 0) + 1
                positions.setdefault(key, p)

        if not counts:
            return

        color = QColor(COLORS['pin'])
        painter.setPen(QPen(color, 2))
        painter.setBrush(QBrush(color))
        for key, n in counts.items():
            if n > 3:
                painter.drawEllipse(positions[key], PIN_RADIUS, PIN_RADIUS)

    # ── Modo ────────────────────────────────────
    def set_mode(self, mode: str):
        self._mode = mode
        if not mode.startswith('place') and mode != 'wire':
            if self._wire_preview:
                self.removeItem(self._wire_preview)
                self._wire_preview = None
            self._wire_start = None

    def _snap_to_pin_or_grid(self, pos: QPointF, threshold: float = 16.0) -> QPointF:
        """
        Si el cursor está a menos de `threshold` px de cualquier pin,
        retorna la posición exacta del pin. Si no, snapea a grilla.
        """
        best_dist = threshold
        best_pt   = None
        for comp in self.components:
            for pt in comp.all_pin_positions_scene():
                dx = pos.x() - pt.x()
                dy  = pos.y() - pt.y()
                d  = (dx*dx + dy*dy) ** 0.5
                if d  < best_dist:
                    best_dist = d
                    best_pt   = pt
        if best_pt is not None:
            return best_pt
        return QPointF(round(pos.x()/GRID_SIZE)*GRID_SIZE,
                        round(pos.y()/GRID_SIZE)*GRID_SIZE)

    def _find_component_at_pin(self, pos: QPointF, threshold: float = 16.0):
        """Encuentra el componente y índice de pin más cercano a pos"""
        best_dist = threshold
        best_comp = None
        best_pin_idx = 0
        
        for comp in self.components:
            for idx, pt in enumerate(comp.all_pin_positions_scene()):
                dx = pos.x() - pt.x()
                dy = pos.y() - pt.y()
                d = (dx*dx + dy*dy) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best_comp = comp
                    best_pin_idx = idx
        
        return best_comp, best_pin_idx if best_comp else (None, 0)

    # ── Colocar componente ───────────────────────
    def place_component(self, comp_type: str, pos: QPointF,
                        name: str = '', value: float = 0.0, unit: str = '',
                        node1: str = '', node2: str = '', node3: str = '') -> ComponentItem:
        if not name:
            # NET_LABEL_IN y NET_LABEL_OUT comparten prefijo "NL" → mismo contador
            # para evitar colisiones de nombre (NL1 vs NL1) que rompen Union-Find.
            counter_key = 'NET_LABEL' if comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT') else comp_type
            count = self._comp_counter.get(counter_key, 0) + 1
            self._comp_counter[counter_key] = count
            prefixes = {'R': 'R', 'V': 'V', 'I': 'I', 'C': 'C', 'L': 'L',
                        'GND': 'GND', 'NODE': 'N', 'LOGIC_STATE': 'LS',
                        'AND': 'AND', 'OR': 'OR', 'NOT': 'NOT', 'NAND': 'NAND',
                        'NOR': 'NOR', 'XOR': 'XOR',
                        'DFF': 'DFF', 'JKFF': 'JKFF',
                        'TFF': 'TFF', 'SRFF': 'SRFF',
                        'COUNTER': 'CNT', 'MUX2': 'MUX',
                        'CLK': 'CLK',
                        'NET_LABEL_IN': 'NL', 'NET_LABEL_OUT': 'NL'}
            name = f"{prefixes.get(comp_type, comp_type)}{count}"

        units = {'R': 'Ω', 'V': 'V', 'VAC': 'V', 'I': 'A', 'C': 'F', 'L': 'H',
                 'D': 'A', 'LED': 'A', 'BJT_NPN': 'hFE', 'BJT_PNP': 'hFE',
                 'NMOS': 'A/V²', 'PMOS': 'A/V²', 'OPAMP': 'V/V'}
        if not unit:
            unit = units.get(comp_type, '')

        # NOTA: el default de LED es 0.0 para que el `Is` lo determine el COLOR
        # (ver build_engine_components_for_item).  Si el usuario escribe un Is
        # positivo en la propiedad, se usa ese valor en lugar del preset.
        defaults = {'R': 1000.0, 'POT': 10_000.0, 'V': 5.0, 'VAC': 120.0,
                    'I': 0.001, 'C': 1e-6, 'L': 1e-3,
                    'D': 1e-14, 'LED': 0.0, 'BJT_NPN': 100.0, 'BJT_PNP': 100.0,
                    'NMOS': 1e-3, 'PMOS': 1e-3, 'OPAMP': 1e5,
                    'XFMR': 1.0, 'BRIDGE': 0.7,
                    'LOGIC_STATE': 0.0, 'CLK': 0.0,
                    'NET_LABEL_IN': 0.0, 'NET_LABEL_OUT': 0.0}
        _stateful = ('LOGIC_STATE', 'CLK', 'NET_LABEL_IN', 'NET_LABEL_OUT')
        if value == 0.0 and comp_type not in _stateful:
            value = defaults.get(comp_type, 1.0)
        elif comp_type in _stateful:
            value = defaults.get(comp_type, 0.0)

        item = ComponentItem(comp_type, name, value, unit, node1, node2, node3)
        if comp_type == 'NOT':
            item.dig_inputs = 1
        if comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
            # El sheet_label es el nombre de red inalámbrico.
            # Por defecto 'NET' para que el usuario lo renombre a algo significativo.
            # Dos net labels con el mismo sheet_label quedan eléctricamente unidos.
            item.sheet_label = 'NET'
        snap_x = round(pos.x() / GRID_SIZE) * GRID_SIZE
        snap_y = round(pos.y() / GRID_SIZE) * GRID_SIZE
        item.setPos(snap_x, snap_y)
        self.addItem(item)
        self.components.append(item)
        return item

    # ── Eventos de mouse ────────────────────────

    def _snap_to_pin_or_grid_with_comp(self, pos: QPointF, threshold: float = 16.0):
        """Igual que _snap_to_pin_or_grid, pero devuelve también (comp, pin_idx)."""
        best_dist = threshold
        best_pt = None
        best_comp = None
        best_pin_idx = 0
        
        for comp in self.components:
            for idx, pt in enumerate(comp.all_pin_positions_scene()):
                dx = pos.x() - pt.x()
                dy = pos.y() - pt.y()
                d = (dx*dx + dy*dy) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best_pt = pt
                    best_comp = comp
                    best_pin_idx = idx
                    
        if best_pt is not None:
            return best_pt, best_comp, best_pin_idx
        return QPointF(round(pos.x()/GRID_SIZE)*GRID_SIZE,
                       round(pos.y()/GRID_SIZE)*GRID_SIZE), None, 0

    def mousePressEvent(self, event):
        pos = event.scenePos()

        if self._mode.startswith('place_'):
            comp_type = self._mode.split('_', 1)[1]
            self.place_component(comp_type, pos)
            self.status_message.emit(f"Componente {comp_type} colocado en ({pos.x():.0f}, {pos.y():.0f})")
            return

        if self._mode == 'wire':
            snap, comp, pin_idx = self._snap_to_pin_or_grid_with_comp(pos)
            
            if self._wire_start is None:
                # Buscar componente/pin en posición inicial
                start_comp, start_pin = self._find_component_at_pin(snap)
                self._wire_start = snap
                self._wire_start_comp = start_comp
                self._wire_start_pin = start_pin or 0
                self._wire_preview = WireItem(snap, snap)
                self.addItem(self._wire_preview)
            else:
                # Finalizar cable
                end_comp, end_pin = self._find_component_at_pin(snap)
                wire = WireItem(
                    self._wire_start, snap,
                    start_comp=self._wire_start_comp,
                    start_pin_idx=self._wire_start_pin,
                    end_comp=end_comp,
                    end_pin_idx=end_pin or 0
                )
                self.addItem(wire)
                self.wires.append(wire)
                # Preparar para siguiente cable
                self._wire_start = snap
                self._wire_start_comp = end_comp
                self._wire_start_pin = end_pin or 0
                if self._wire_preview:
                    self.removeItem(self._wire_preview)
                self._wire_preview = WireItem(snap, snap)
                self.addItem(self._wire_preview)
                self._wire_start = snap
                self._wire_start_comp = comp
                self._wire_start_pin = pin_idx
                self.status_message.emit("Cable colocado")
            return

        super().mousePressEvent(event)

        # Preparar arrastre grupal: si el usuario hace click sobre un ítem
        # ya seleccionado en modo 'select', registramos los cables que deben
        # trasladarse manualmente junto con los componentes seleccionados.
        self._group_drag_active = False
        self._group_drag_start_pos = None
        self._group_drag_wires = []
        if (self._mode == 'select'
                and event.button() == Qt.MouseButton.LeftButton):
            selected_items = self.selectedItems()
            clicked_items  = self.items(pos)
            clicked_selected = next(
                (it for it in clicked_items if it in selected_items), None)
            if clicked_selected is not None:
                selected_comps = {
                    it for it in selected_items if isinstance(it, ComponentItem)}
                wires_in_sel = {
                    it for it in selected_items if isinstance(it, WireItem)}
                wires_to_track = set(wires_in_sel)
                for w in self.wires:
                    if w.start_comp in selected_comps or w.end_comp in selected_comps:
                        wires_to_track.add(w)

                # Mapa de uniones libres entre cables: posición (snap) →
                # lista de (cable, extremo). Solo extremos sin componente
                # asociado pueden formar junction "cable-cable".
                SNAP = 5
                def _jkey(p: QPointF):
                    return (round(p.x() / SNAP) * SNAP,
                            round(p.y() / SNAP) * SNAP)
                junction_map: Dict[Tuple[int, int], List[Tuple[WireItem, str]]] = {}
                for w in self.wires:
                    if w.start_comp is None:
                        junction_map.setdefault(_jkey(w.line().p1()), []) \
                            .append((w, 'p1'))
                    if w.end_comp is None:
                        junction_map.setdefault(_jkey(w.line().p2()), []) \
                            .append((w, 'p2'))

                tracked_by_wire: Dict[WireItem, dict] = {}
                for w in wires_to_track:
                    line = w.line()
                    p1_free = (w.start_comp is None)
                    p2_free = (w.end_comp is None)
                    p1_in_sel = (w.start_comp in selected_comps)
                    p2_in_sel = (w.end_comp in selected_comps)
                    wire_selected = w in wires_in_sel
                    # Trasladamos a mano cualquier extremo libre cuyo cable
                    # forme parte del grupo (ya sea por estar seleccionado
                    # o por tener su otro extremo unido a un comp del grupo).
                    translate_p1 = p1_free and (wire_selected or p2_in_sel)
                    translate_p2 = p2_free and (wire_selected or p1_in_sel)
                    if translate_p1 or translate_p2:
                        tracked_by_wire[w] = {
                            'p1': QPointF(line.p1()),
                            'p2': QPointF(line.p2()),
                            'translate_p1': translate_p1,
                            'translate_p2': translate_p2,
                        }

                # Propagación por uniones: si un extremo libre se traslada,
                # todos los demás extremos libres en esa misma posición
                # deben moverse con él para que la unión no se rompa.
                changed = True
                while changed:
                    changed = False
                    moving_keys = set()
                    for w, info in tracked_by_wire.items():
                        if info['translate_p1']:
                            moving_keys.add(_jkey(info['p1']))
                        if info['translate_p2']:
                            moving_keys.add(_jkey(info['p2']))
                    for key in moving_keys:
                        for (w, end) in junction_map.get(key, []):
                            if w not in tracked_by_wire:
                                line = w.line()
                                tracked_by_wire[w] = {
                                    'p1': QPointF(line.p1()),
                                    'p2': QPointF(line.p2()),
                                    'translate_p1': False,
                                    'translate_p2': False,
                                }
                            info = tracked_by_wire[w]
                            if end == 'p1' and not info['translate_p1']:
                                info['translate_p1'] = True
                                changed = True
                            if end == 'p2' and not info['translate_p2']:
                                info['translate_p2'] = True
                                changed = True

                tracked = [{'wire': w, **info}
                           for w, info in tracked_by_wire.items()]
                if tracked or selected_comps:
                    self._group_drag_active = True
                    self._group_drag_start_pos = QPointF(pos)
                    self._group_drag_wires = tracked

        # Emitir componente seleccionado
        items = self.selectedItems()
        if items and isinstance(items[0], ComponentItem):
            self.component_selected.emit(items[0])
        else:
            self.component_selected.emit(None)

    def mouseMoveEvent(self, event):
        if self._mode == 'wire' and self._wire_start and self._wire_preview:
            pos  = event.scenePos()
            snap = self._snap_to_pin_or_grid(pos)
            self._wire_preview.setLine(QLineF(self._wire_start, snap))
        super().mouseMoveEvent(event)

        # Trasladar extremos libres de cables durante un arrastre grupal,
        # snapeando el delta a la grilla para mantener alineación con pines.
        if self._group_drag_active and self._group_drag_wires \
                and self._group_drag_start_pos is not None:
            delta = event.scenePos() - self._group_drag_start_pos
            dx = round(delta.x() / GRID_SIZE) * GRID_SIZE
            dy = round(delta.y() / GRID_SIZE) * GRID_SIZE
            for info in self._group_drag_wires:
                wire = info['wire']
                p1 = info['p1']
                p2 = info['p2']
                new_p1 = QPointF(p1.x() + dx, p1.y() + dy) if info['translate_p1'] else QPointF(p1)
                new_p2 = QPointF(p2.x() + dx, p2.y() + dy) if info['translate_p2'] else QPointF(p2)
                wire.setLine(QLineF(new_p1, new_p2))
                # Si algún extremo está unido a un componente, dejar que el
                # pin actual gobierne esa coordenada.
                if not info['translate_p1'] or not info['translate_p2']:
                    wire.update_from_pins()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._group_drag_active:
            self._group_drag_active = False
            self._group_drag_start_pos = None
            self._group_drag_wires = []

    def mouseDoubleClickEvent(self, event):
        items = self.items(event.scenePos())
        for item in items:
            if isinstance(item, ComponentItem):
                if item.comp_type == 'LOGIC_STATE':
                    # Toggle 0↔1 con doble-click
                    item.value = 0.0 if item.value else 1.0
                    item.update()
                    self.logic_state_toggled.emit(item)
                    return
                if item.comp_type == 'CLK':
                    # Doble-click conmuta manualmente y detiene la oscilación
                    # automática (entra en modo manual como un LOGIC_STATE).
                    item.clk_running = False
                    item.value = 0.0 if item.value else 1.0
                    item.update()
                    self.logic_state_toggled.emit(item)
                    return
                self._edit_component(item)
                return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete or event.key() == Qt.Key.Key_Backspace:
            for item in self.selectedItems():
                if isinstance(item, ComponentItem) and item in self.components:
                    self.components.remove(item)
                elif isinstance(item, WireItem) and item in self.wires:
                    self.wires.remove(item)
                self.removeItem(item)
        elif event.key() == Qt.Key.Key_R:
            for item in self.selectedItems():
                if isinstance(item, ComponentItem):
                    item.rotate_90()
            self.status_message.emit("Componente rotado 90 grados (R para seguir rotando)")
        elif event.key() == Qt.Key.Key_Escape:
            if self._wire_preview:
                self.removeItem(self._wire_preview)
                self._wire_preview = None
            self._wire_start = None
            self.set_mode('select')
        super().keyPressEvent(event)

    def update_wires_for_component(self, comp: 'ComponentItem'):
        """Actualiza todos los cables conectados al componente dado."""
        for wire in self.wires:
            if wire.start_comp is comp or wire.end_comp is comp:
                wire.update_from_pins()

    # ── Extraccion de netlist por Union-Find ─────
    def extract_netlist(self) -> Dict[str, str]:
        """
        Analiza los cables del canvas y asigna nodos automaticamente.
        Union-Find: une pines conectados por cables en el mismo nodo.
        GND se mapea al nodo 0. Retorna {CompNombre__p1: net_X, ...}
        """
        # Cambia esto de GRID_SIZE a un valor pequeño
        SNAP = 5

        # ── 1. Registrar pines de componentes ───────────────────────────
        pins = {}
        for comp in self.components:
            p1, p2 = comp.pin_positions_scene()
            pins[f"{comp.name}__p1"] = p1
            pins[f"{comp.name}__p2"] = p2
            # Registrar pin3 para dispositivos de 3 terminales
            if comp.comp_type in ('BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP'):
                pins[f"{comp.name}__p3"] = comp.pin3_position_scene()
            elif comp.comp_type in ComponentItem.FLIPFLOP_TYPES:
                pins[f"{comp.name}__p3"] = comp.pin3_position_scene()
                pins[f"{comp.name}__p4"] = comp.pin4_position_scene()  # SET
                pins[f"{comp.name}__p5"] = comp.pin5_position_scene()  # RESET
                pins[f"{comp.name}__p6"] = comp.pin6_position_scene()  # Q̄
            elif comp.comp_type == 'MUX2':
                pins[f"{comp.name}__p3"] = comp.pin3_position_scene()
            elif comp.comp_type in ComponentItem.FOUR_PIN_TYPES:
                pins[f"{comp.name}__p3"] = comp.pin3_position_scene()
                pins[f"{comp.name}__p4"] = comp.pin4_position_scene()
            elif comp.comp_type in ('AND', 'OR', 'NAND', 'NOR', 'XOR', 'COMPARATOR'):
                # Registrar TODOS los pines de entrada de la puerta:
                # p1 = salida (ya registrado), p2 = entrada 1 (ya registrado),
                # p3 = entrada 2, p4 = entrada 3, ...
                gw, gh, step, n_in = comp._gate_geometry()
                ys = comp._gate_pin_ys()
                for i, y in enumerate(ys):
                    pin_key = f"{comp.name}__p{i + 2}"  # p2, p3, p4, ...
                    pins[pin_key] = comp.mapToScene(QPointF(-gw - 10, y))

        # ── 2. Union-Find sobre pines + extremos de cables ───────────────
        # Incluimos los extremos de cables como nodos propios del grafo
        # para propagar correctamente cadenas de cables sin pines en el medio
        all_nodes: Dict[str, QPointF] = dict(pins)
        for idx, wire in enumerate(self.wires):
            line = wire.line()
            wp1 = wire.mapToScene(line.p1())
            wp2 = wire.mapToScene(line.p2())
            all_nodes[f"__wire{idx}__p1"] = wp1
            all_nodes[f"__wire{idx}__p2"] = wp2

        parent = {nid: nid for nid in all_nodes}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        def pts_near(pa: QPointF, pb: QPointF) -> bool:
            return abs(pa.x() - pb.x()) < SNAP and abs(pa.y() - pb.y()) < SNAP

        # Unir nodos (pines y extremos de cable) que se tocan espacialmente
        node_ids = list(all_nodes.keys())
        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                if pts_near(all_nodes[node_ids[i]], all_nodes[node_ids[j]]):
                    union(node_ids[i], node_ids[j])

        # Unir los dos extremos de cada cable entre sí (esto propaga
        # la conectividad a través de cables que no tocan ningún pin)
        for idx in range(len(self.wires)):
            union(f"__wire{idx}__p1", f"__wire{idx}__p2")

        # ── 3a. Unir pines de net labels con el mismo sheet_label ───────
        # Esta es la lógica central de los nodos inalámbricos:
        # todos los net labels (IN u OUT) que comparten sheet_label
        # se unen en el mismo grupo del Union-Find, igual que si hubiera
        # un cable físico entre ellos. Funciona en la misma hoja Y entre hojas.
        label_first_pin: Dict[str, str] = {}  # label/alias → pin_key del primer label visto
        for comp in self.components:
            if comp.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT') and comp.sheet_label:
                pin_key = f"{comp.name}__p1"
                if pin_key not in pins:
                    continue
                aliases = []
                for lbl in (comp.sheet_label.strip(), comp.name.strip()):
                    if lbl and lbl not in aliases:
                        aliases.append(lbl)
                for lbl in aliases:
                    if lbl not in label_first_pin:
                        label_first_pin[lbl] = pin_key
                    else:
                        # Unir este pin con el primer pin que tiene el mismo label
                        union(label_first_pin[lbl], pin_key)

        # ── 3b. Detectar grupos GND ──────────────────────────────────────
        # Registrar AMBOS pines del componente GND como tierra
        gnd_roots: set = set()
        for comp in self.components:
            if comp.comp_type == 'GND':
                gnd_roots.add(find(f"{comp.name}__p1"))
                gnd_roots.add(find(f"{comp.name}__p2"))

        # ── 4. Asignar nombres de nodo ───────────────────────────────────
        pin_ids = list(pins.keys())
        groups: Dict[str, list] = {}
        for pid in pin_ids:
            groups.setdefault(find(pid), []).append(pid)

        root_labels: Dict[str, List[str]] = {}
        for comp in self.components:
            if comp.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT') and comp.sheet_label:
                pin_key = f"{comp.name}__p1"
                if pin_key in pins:
                    lbl = comp.sheet_label.strip()
                    if lbl:
                        root = find(pin_key)
                        root_labels.setdefault(root, [])
                        if lbl not in root_labels[root]:
                            root_labels[root].append(lbl)

        root_to_name: Dict[str, str] = {}
        for root in groups:
            if root in gnd_roots:
                root_to_name[root] = '0'
            elif root in root_labels:
                # En grupos con net labels, la etiqueta visible es el nombre
                # electrico real. Asi una salida manual "Y" conecta con una
                # netlabel "Y" y tambien con el alias interno "OUT_Y".
                root_to_name[root] = sorted(root_labels[root], key=lambda s: (len(s), s))[0]
            else:
                # Usar el primer pin del grupo como nombre canónico del net.
                # Esto hace que el nombre sea estable y único por componente,
                # evitando que circuitos independientes compartan nombres de red
                # entre llamadas sucesivas a extract_netlist().
                canonical = min(groups[root])  # orden lexicográfico → determinista
                root_to_name[root] = f'net_{canonical}'

        return {pid: root_to_name[find(pid)] for pid in pin_ids}

    # ── Editar propiedades ───────────────────────
    def _edit_component(self, item: ComponentItem):
        dialog = ComponentDialog(item, COLORS)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            item.name      = data['name']
            item.value     = data['value']
            item.node1     = data['node1']
            item.node2     = data['node2']
            item.node3     = data['node3']
            if item.comp_type == 'VAC':
                item.frequency = data['frequency']
                item.phase_deg = data['phase_deg']
                item.ac_mode   = data['ac_mode']
            if item.comp_type == 'Z':
                item.z_mode   = data.get('z_mode', 'rect')
                item.z_real   = data.get('z_real', 100.0)
                item.z_imag   = data.get('z_imag', 0.0)
                item.z_mag    = data.get('z_mag', 100.0)
                item.z_phase  = data.get('z_phase', 0.0)
            if item.comp_type == 'LED':
                item.led_color = data.get('led_color', 'red')
            # POT
            if item.comp_type == 'POT' and 'pot_wiper' in data:
                item.pot_wiper = max(0.0, min(1.0, float(data['pot_wiper'])))
            # XFMR
            if item.comp_type == 'XFMR':
                if 'xfmr_ratio' in data: item.xfmr_ratio = float(data['xfmr_ratio'])
                if 'xfmr_imax'  in data: item.xfmr_imax  = float(data['xfmr_imax'])
            # 4º nodo
            if item.comp_type in ComponentItem.FOUR_PIN_TYPES and 'node4' in data:
                item.node4 = data['node4']
            # Etiqueta inter-hoja
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT') and 'sheet_label' in data:
                item.sheet_label = data['sheet_label']
            # Campos digitales
            if item.comp_type in ComponentItem.DIGITAL_TYPES:
                if 'dig_inputs'  in data: item.dig_inputs      = data['dig_inputs']
                if item.comp_type == 'NOT': item.dig_inputs = 1
                if 'dig_bits'    in data: item.dig_bits         = data['dig_bits']
                if 'dig_bits_adc'in data: item.dig_bits_adc    = data['dig_bits_adc']
                if 'dig_vref'    in data: item.dig_vref         = data['dig_vref']
                if 'dig_tpd_ns'  in data: item.dig_tpd_ns      = data['dig_tpd_ns']
                if 'dig_clk'     in data: item.dig_clk          = data['dig_clk']
                if 'dig_analog_node' in data: item.dig_analog_node = data['dig_analog_node']
                if 'dig_input_nodes' in data: item.dig_input_nodes  = data['dig_input_nodes']
            item.update()

    def update_wires_for_component(self, comp: 'ComponentItem'):
        """Actualiza todos los cables conectados al componente dado."""
        for wire in self.wires:
            if wire.start_comp is comp:
                pins = comp.all_pin_positions_scene()
                if 0 <= wire.start_pin_idx < len(pins):
                    wire.setLine(QLineF(pins[wire.start_pin_idx], wire.line().p2()))
            if wire.end_comp is comp:
                pins = comp.all_pin_positions_scene()
                if 0 <= wire.end_pin_idx < len(pins):
                    wire.setLine(QLineF(wire.line().p1(), pins[wire.end_pin_idx]))

# ══════════════════════════════════════════════════════════════
# Helper: convertir un ComponentItem analógico a objetos del engine
# ══════════════════════════════════════════════════════════════
def build_engine_components_for_item(item, pin_node):
    """
    Devuelve la lista de componentes del motor MNA que representan a `item`.
    Lista vacía si:
      • el item es de tipo digital (lo gestiona el engine digital aparte),
      • no se puede construir (valor inválido, etc.).

    Casos especiales:
      • POT     → 1 Potentiometer.
      • XFMR    → 1 Transformer  (4 nodos).
      • BRIDGE  → 4 Diodes interconectados como puente.
    """
    from engine import (
        Resistor, VoltageSource, VoltageSourceAC, CurrentSource,
        Capacitor, Inductor, Diode, BJT, MOSFET, OpAmp, Impedance,
        Potentiometer, Transformer,
    )

    if item.comp_type in ComponentItem.DIGITAL_TYPES:
        return []

    n1 = item.node1.strip() or pin_node.get(f"{item.name}__p1", f"iso_{item.name}_p1")
    n2 = item.node2.strip() or pin_node.get(f"{item.name}__p2", "0")
    n3 = ((item.node3.strip() if hasattr(item, "node3") and item.node3.strip()
           else pin_node.get(f"{item.name}__p3", "")))
    n4 = ((item.node4.strip() if hasattr(item, "node4") and item.node4.strip()
           else pin_node.get(f"{item.name}__p4", "")))

    ct = item.comp_type
    try:
        if ct == 'R' and item.value > 0:
            return [Resistor(item.name, n1, n2, item.value)]
        if ct == 'POT':
            return [Potentiometer(item.name, n1, n2,
                                  R_total=max(item.value, 1.0),
                                  wiper=item.pot_wiper)]
        if ct == 'V':
            return [VoltageSource(item.name, n2, n1, item.value)]
        if ct == 'VAC':
            return [VoltageSourceAC(item.name, n2, n1,
                                    amplitude=item.value, frequency=item.frequency,
                                    phase_deg=item.phase_deg, mode=item.ac_mode)]
        if ct == 'I':
            return [CurrentSource(item.name, n1, n2, item.value)]
        if ct == 'C' and item.value > 0:
            return [Capacitor(item.name, n1, n2, item.value)]
        if ct == 'L' and item.value > 0:
            return [Inductor(item.name, n1, n2, item.value)]
        if ct == 'D':
            Is_v = item.value if item.value > 0 else 1e-14
            return [Diode(item.name, n1, n2, Is=Is_v,
                          n=1.0, Vd_init=0.6, Vd_max=2.0)]
        if ct == 'LED':
            # Parámetros LED por color (Vf nominal a ~10 mA).
            # El COLOR es la única fuente de verdad para los parámetros físicos
            # del LED.  El campo `value` del item NO se utiliza aquí —
            # mantenerlo como override sería peligroso porque su default
            # heredado del diodo Si (1e-14) hace que el LED conduzca a 0.6V.
            #          (Is,         n,    Vf_typ,  Vd_init)
            led_params = {
                'red':    (1.0e-18, 2.0,  1.8,     1.7),
                'orange': (1.0e-19, 2.1,  2.0,     1.9),
                'yellow': (1.0e-20, 2.2,  2.1,     2.0),
                'green':  (1.0e-23, 2.5,  2.2,     2.1),
                'blue':   (1.0e-27, 3.0,  3.0,     2.9),
                'white':  (1.0e-27, 3.0,  3.1,     3.0),
            }
            color = getattr(item, 'led_color', 'red')
            Is_v, n_v, _, Vd0 = led_params.get(color, led_params['red'])
            return [Diode(item.name, n1, n2, Is=Is_v, n=n_v,
                          Vd_init=Vd0, Vd_max=5.0)]
        if ct in ('BJT_NPN', 'BJT_PNP'):
            t = 'NPN' if ct == 'BJT_NPN' else 'PNP'
            return [BJT(item.name, n1, n3 or f'b_{item.name}', n2,
                        type_=t, Bf=item.value if item.value > 0 else 100)]
        if ct in ('NMOS', 'PMOS'):
            t = 'NMOS' if ct == 'NMOS' else 'PMOS'
            return [MOSFET(item.name, n1, n3 or f'g_{item.name}', n2,
                           type_=t, Kn=item.value if item.value > 0 else 1e-3)]
        if ct == 'OPAMP':
            return [OpAmp(item.name, n1, n3 or f'vp_{item.name}', n2,
                          A=item.value if item.value > 0 else 1e5)]
        if ct == 'Z':
            import math as _m
            Z_val = (complex(item.z_real, item.z_imag) if item.z_mode == 'rect'
                     else complex(item.z_mag*_m.cos(_m.radians(item.z_phase)),
                                  item.z_mag*_m.sin(_m.radians(item.z_phase))))
            if abs(Z_val) > 1e-12:
                return [Impedance(item.name, n1, n2, Z_val)]
        if ct == 'XFMR':
            n3_x = n3 or f'sec1_{item.name}'
            n4_x = n4 or f'sec2_{item.name}'
            return [Transformer(item.name, n1, n2, n3_x, n4_x,
                                ratio=item.xfmr_ratio,
                                I_max=item.xfmr_imax)]
        if ct == 'BRIDGE':
            n3_b = n3 or f'dcp_{item.name}'   # DC+
            n4_b = n4 or f'dcn_{item.name}'   # DC−
            Is = 1e-14
            return [
                Diode(f'{item.name}_D1', n1,   n3_b, Is=Is),  # AC1 → DC+
                Diode(f'{item.name}_D2', n2,   n3_b, Is=Is),  # AC2 → DC+
                Diode(f'{item.name}_D3', n4_b, n1,   Is=Is),  # DC− → AC1
                Diode(f'{item.name}_D4', n4_b, n2,   Is=Is),  # DC− → AC2
            ]
    except Exception:
        pass
    return []


# ══════════════════════════════════════════════════════════════
# VENTANA PRINCIPAL
# ══════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CircuitSim — Simulador de Circuitos")
        self.resize(1280, 800)
        self.solver = MNASolver()
        self._sim_running = False
        self._sim_all_comps = None
        self._sim_pin_node = None
        # Timer para simulación continua (actualiza LEDs y canvas en vivo)
        from PyQt6.QtCore import QTimer
        self._sim_timer = QTimer(self)
        self._sim_timer.setInterval(200)   # ms entre actualizaciones
        self._sim_timer.timeout.connect(self._tick_simulation)

        # ── Reloj global para componentes CLK ──────────────────────────────
        # Cada CLK con clk_running=True conmuta su valor cada medio período
        # de _clk_freq_hz. La frecuencia es ajustable desde Herramientas.
        self._clk_freq_hz: float = 1.0
        self._clk_timer = QTimer(self)
        self._clk_timer.timeout.connect(self._tick_clk)
        self._update_clk_timer_interval()

        self._build_ui()
        self._apply_style()
        self._load_demo_circuit()

    # ── CLK (oscilador global) ────────────────────────────────────────────
    def _update_clk_timer_interval(self):
        """Configura el intervalo del timer según _clk_freq_hz.
        Half-period en ms: 1/(2f) * 1000.  Mínimo 10 ms para no saturar la GUI.
        """
        f = max(0.001, float(self._clk_freq_hz))
        half_period_ms = max(10, int(1000.0 / (2.0 * f)))
        self._clk_timer.setInterval(half_period_ms)

    def _tick_clk(self):
        """Conmuta los CLK que están en modo automático."""
        any_active = False
        for sheet in self._sheets:
            for it in sheet['scene'].components:
                if it.comp_type == 'CLK' and it.clk_running:
                    it.value = 0.0 if it.value else 1.0
                    it.update()
                    any_active = True
        if not any_active:
            self._clk_timer.stop()
            return
        # Re-disparar simulación en vivo si está activa
        if self._sim_running:
            self._run_simulation_dc(silent=True)

    def _toggle_clk_running(self):
        """Atajo Ctrl+K: invierte el estado oscilando de los CLK seleccionados.
        Si no hay ninguno seleccionado, conmuta todos los CLK del canvas.
        """
        sel = [it for it in self.scene.selectedItems()
               if isinstance(it, ComponentItem) and it.comp_type == 'CLK']
        targets = sel if sel else [
            it for it in self.scene.components if it.comp_type == 'CLK']
        if not targets:
            self.statusBar().showMessage(
                "No hay componentes CLK en la hoja activa.")
            return
        # Si al menos uno está corriendo, los detenemos todos. Si ninguno
        # corre, los iniciamos todos.
        any_running = any(it.clk_running for it in targets)
        for it in targets:
            it.clk_running = not any_running
            it.update()
        # Iniciar/parar el timer global
        any_running_now = any(
            it.clk_running for sheet in self._sheets
            for it in sheet['scene'].components if it.comp_type == 'CLK')
        if any_running_now:
            self._update_clk_timer_interval()
            self._clk_timer.start()
            self.statusBar().showMessage(
                f"CLK activado a {self._clk_freq_hz:g} Hz "
                f"({len(targets)} componente{'s' if len(targets)!=1 else ''})")
        else:
            self._clk_timer.stop()
            self.statusBar().showMessage("CLK detenido.")

    def _set_clk_frequency(self):
        """Diálogo Herramientas → Frecuencia CLK."""
        from PyQt6.QtWidgets import QInputDialog
        f, ok = QInputDialog.getDouble(
            self, "Frecuencia CLK",
            "Frecuencia de oscilación del componente CLK (Hz):",
            self._clk_freq_hz, 0.01, 100000.0, 3)
        if not ok:
            return
        self._clk_freq_hz = f
        self._update_clk_timer_interval()
        self.statusBar().showMessage(f"Frecuencia CLK = {f:g} Hz")

    def _open_circuit_analyzer(self):
        """Abre el analizador de circuitos digitales preservando el estado previo."""
        state = getattr(self, '_analyzer_state', None)
        dlg = CircuitAnalyzerDialog(parent=self, initial_state=state)
        dlg.exec()

    def _build_tools_button(self):
        """Construye el QToolButton 'Herramientas' con menú desplegable.
        Aparece justo después del botón '+ Hoja' en la toolbar principal.
        """
        from PyQt6.QtWidgets import QToolButton, QMenu
        btn = QToolButton(self)
        btn.setText("Herramientas")
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        menu = QMenu(btn)
        act_freq = QAction("Frecuencia CLK…", self)
        act_freq.triggered.connect(self._set_clk_frequency)
        menu.addAction(act_freq)
        act_analyze = QAction("Analizar Circuito…", self)
        act_analyze.triggered.connect(self._open_circuit_analyzer)
        menu.addAction(act_analyze)
        btn.setMenu(menu)
        # Mostrar el menú también al pasar el cursor (hover)
        btn.installEventFilter(self)
        self._tools_button = btn
        return btn

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if (getattr(self, '_tools_button', None) is obj
                and event.type() == QEvent.Type.Enter):
            obj.showMenu()
        return super().eventFilter(obj, event)

    # ── Construcción UI ──────────────────────────
    def _build_ui(self):
        # Sistema de hojas (tabs) — cada hoja tiene su propia escena y vista
        self._sheets: List[Dict] = []  # [{scene, view, name}]
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_sheet)
        self.tab_widget.currentChanged.connect(self._on_sheet_changed)

        # El botón para agregar hojas vive en la toolbar principal (+ Hoja)
        # Doble-click en la pestaña para renombrar
        self.tab_widget.tabBarDoubleClicked.connect(self._rename_sheet)

        # Crear la primera hoja
        self._add_sheet(name="Hoja 1")

        # Panel derecho (propiedades + resultados)
        self._build_right_panel()

        # Layout central: tabs + panel derecho
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tab_widget)
        splitter.addWidget(self.right_panel)
        splitter.setSizes([1000, 280])

        self.setCentralWidget(splitter)

        # ── Toolbar PRINCIPAL (fila 1: archivo, zoom, etc.) ──────────────
        self._build_main_toolbar()

        # ── Toolbar SECUNDARIA (fila 2: categorías, herramientas, simulación)
        self._build_component_toolbar()

        # Status bar
        self.statusBar().showMessage("Listo — Selecciona una categoría para colocar componentes")

    # ── Propiedades: scene y view apuntan a la hoja activa ─────
    @property
    def scene(self) -> 'CircuitScene':
        idx = self.tab_widget.currentIndex()
        if idx < 0 or idx >= len(self._sheets):
            return self._sheets[0]['scene']
        return self._sheets[idx]['scene']

    @property
    def view(self) -> QGraphicsView:
        idx = self.tab_widget.currentIndex()
        if idx < 0 or idx >= len(self._sheets):
            return self._sheets[0]['view']
        return self._sheets[idx]['view']

    def _create_scene_view(self) -> Tuple[CircuitScene, QGraphicsView]:
        scene = CircuitScene()
        scene.component_selected.connect(self._on_component_selected)
        scene.status_message.connect(self.statusBar().showMessage)
        scene.logic_state_toggled.connect(self._on_logic_state_toggled)

        view = QGraphicsView(scene)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        view.wheelEvent = self._wheel_zoom
        return scene, view

    def _add_sheet(self, name: str = ''):
        if not name:
            idx = len(self._sheets) + 1
            name = f"Hoja {idx}"
        scene, view = self._create_scene_view()
        sheet = {'scene': scene, 'view': view, 'name': name}
        self._sheets.append(sheet)
        tab_idx = self.tab_widget.addTab(view, name)
        self.tab_widget.setCurrentIndex(tab_idx)

    def _close_sheet(self, index: int):
        if len(self._sheets) <= 1:
            self.statusBar().showMessage("No se puede cerrar la última hoja")
            return
        reply = QMessageBox.question(
            self, "Cerrar hoja",
            f"¿Cerrar \"{self._sheets[index]['name']}\"? Se perderán los componentes.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._sheets.pop(index)
            self.tab_widget.removeTab(index)

    def _on_sheet_changed(self, index: int):
        if 0 <= index < len(self._sheets):
            self.statusBar().showMessage(f"Hoja activa: {self._sheets[index]['name']}")

    def _rename_sheet(self, index: int):
        if 0 <= index < len(self._sheets):
            old_name = self._sheets[index]['name']
            new_name, ok = QInputDialog.getText(
                self, "Renombrar hoja", "Nuevo nombre:", text=old_name)
            if ok and new_name.strip():
                self._sheets[index]['name'] = new_name.strip()
                self.tab_widget.setTabText(index, new_name.strip())

    def _build_right_panel(self):
        self.right_panel = QWidget()
        self.right_panel.setFixedWidth(260)
        layout = QVBoxLayout(self.right_panel)
        layout.setContentsMargins(8, 8, 8, 8)

        # Propiedades del componente seleccionado
        prop_label = QLabel("PROPIEDADES")
        prop_label.setFont(QFont('Consolas', 9, QFont.Weight.Bold))
        layout.addWidget(prop_label)

        self.prop_table = QTableWidget(0, 2)
        self.prop_table.setHorizontalHeaderLabels(["Campo", "Valor"])
        self.prop_table.horizontalHeader().setStretchLastSection(True)
        self.prop_table.setMaximumHeight(200)
        layout.addWidget(self.prop_table)

        # ── Slider de potenciómetro (visible sólo cuando hay un POT seleccionado) ──
        from PyQt6.QtWidgets import QSlider
        self.pot_panel = QWidget()
        pot_layout = QVBoxLayout(self.pot_panel)
        pot_layout.setContentsMargins(0, 4, 0, 4)
        self.pot_label = QLabel("CURSOR DEL POTENCIÓMETRO")
        self.pot_label.setFont(QFont('Consolas', 8, QFont.Weight.Bold))
        pot_layout.addWidget(self.pot_label)

        self.pot_slider = QSlider(Qt.Orientation.Horizontal)
        self.pot_slider.setRange(0, 1000)        # resolución 0.1%
        self.pot_slider.setValue(500)
        self.pot_slider.setToolTip(
            "Mueve el cursor del potenciómetro en tiempo real.\n"
            "Si la simulación está activa, el efecto se ve al instante.")
        self.pot_slider.valueChanged.connect(self._on_pot_slider)
        pot_layout.addWidget(self.pot_slider)

        self.pot_value_label = QLabel("50.0% — R = ----")
        self.pot_value_label.setFont(QFont('Consolas', 8))
        pot_layout.addWidget(self.pot_value_label)

        self.pot_panel.setVisible(False)
        layout.addWidget(self.pot_panel)
        self._selected_pot = None   # ComponentItem actualmente seleccionado (POT)

        # Resultados de simulación
        res_label = QLabel("RESULTADOS")
        res_label.setFont(QFont('Consolas', 9, QFont.Weight.Bold))
        layout.addWidget(res_label)

        self.results_text = QTextEdit()
        self.results_text.setReadOnly(True)
        self.results_text.setFont(QFont('Consolas', 9))
        layout.addWidget(self.results_text)

        # Botón triángulo de potencia (visible solo tras análisis AC)
        self.btn_power_triangle = QPushButton("📐  Ver Triángulo de Potencia")
        self.btn_power_triangle.setFont(QFont('Consolas', 9))
        self.btn_power_triangle.setVisible(False)
        self.btn_power_triangle.clicked.connect(self._show_power_triangle)
        layout.addWidget(self.btn_power_triangle)
        self._last_ac_result = None   # guardamos el resultado AC para el popup

    def _build_main_toolbar(self):
        """Barra superior (fila 1): archivo, zoom, etc."""
        tb = self.addToolBar("Principal")
        tb.setMovable(False)
        tb.setObjectName("main_toolbar")

        actions = [
            ("Nuevo",           "Ctrl+N",          self._new_circuit),
            ("Abrir",           "Ctrl+O",          self._open_circuit),
            ("Guardar",         "Ctrl+S",          self._save_circuit),
            ("Guardar como…",   "Ctrl+Shift+S",    self._save_circuit_as),
            ("Exportar SPICE",  "Ctrl+E",          self._export_spice),
            ("|", None, None),
            ("+ Hoja",       "Ctrl+T", lambda: self._add_sheet()),
            ("__TOOLS__", None, None),                    # placeholder Herramientas
            ("Limpiar",      "Ctrl+L", self._clear_circuit),
            ("Zoom +",       "Ctrl+=", lambda: self.view.scale(1.2, 1.2)),
            ("Zoom −",       "Ctrl+-", lambda: self.view.scale(1/1.2, 1/1.2)),
            ("Restablecer",  "Ctrl+0", self._reset_zoom),
        ]
        for name, shortcut, fn in actions:
            if name == '|':
                tb.addSeparator()
                continue
            if name == '__TOOLS__':
                tb.addWidget(self._build_tools_button())
                continue
            act = QAction(name, self)
            if shortcut:
                act.setShortcut(shortcut)
            act.triggered.connect(fn)
            tb.addAction(act)

        # ── Atajo global Ctrl+K para conmutar oscilación de CLK ──────────
        clk_act = QAction("Toggle CLK", self)
        clk_act.setShortcut("Ctrl+K")
        clk_act.triggered.connect(self._toggle_clk_running)
        self.addAction(clk_act)

        # ── Botón Configuración (alineado a la derecha) ──────────────────
        tb.addSeparator()
        from PyQt6.QtWidgets import QSizePolicy
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        act_settings = QAction("⚙ Configuración", self)
        act_settings.setShortcut("Ctrl+,")
        act_settings.setToolTip(
            "Abrir ventana de configuración (temas, preferencias…)")
        act_settings.triggered.connect(self._open_settings_dialog)
        tb.addAction(act_settings)

        self._current_file: Optional[str] = None

    # ── Configuración / Tema ──────────────────────────────────────────────
    def _open_settings_dialog(self):
        """Abre el diálogo de configuración."""
        dlg = SettingsDialog(THEME_MANAGER, COLORS,
                             parent=self,
                             current_theme_id=THEME_MANAGER.load_selection(),
                             on_theme_change=self._apply_theme_change)
        dlg.exec()

    def _apply_theme_change(self, theme_id: str):
        """Callback que el SettingsDialog invoca al elegir un tema."""
        applied = apply_theme_to_colors(theme_id)
        THEME_MANAGER.save_selection(applied)
        self._refresh_theme_in_ui()
        meta = THEME_MANAGER.get_theme_meta(applied)
        if meta:
            self.statusBar().showMessage(f"Tema aplicado: {meta['name']}", 3000)

    def _refresh_theme_in_ui(self):
        """Re-aplica stylesheet y fuerza redibujo del canvas tras cambiar tema."""
        self._apply_style()
        for sheet in self._sheets:
            sheet['scene'].setBackgroundBrush(QBrush(QColor(COLORS['bg'])))
            sheet['scene'].update()
            sheet['view'].viewport().update()

    def _build_component_toolbar(self):
        """Barra secundaria (fila 2): categorías de componentes, herramientas y simulación."""
        # ── FORZAR SALTO DE LÍNEA antes de esta toolbar ──────────────────
        self.addToolBarBreak()

        tb = QToolBar("Componentes", self)
        tb.setMovable(False)
        tb.setObjectName("component_toolbar")
        self.addToolBar(tb)

        # ── Categorías de componentes ────────────────────────────────────
        categories = [
            ("Pasivos", [
                ('R',   'Resistor',      '━┤ZZZ├━'),
                ('POT', 'Potenciómetro', '━┤Z↗├━'),
                ('C',   'Capacitor',     '━┤  ├━'),
                ('L',   'Inductor',      '━⌒⌒⌒━'),
                ('Z',   'Impedancia',    '━┤▭├━'),
            ]),
            ("Fuentes", [
                ('V',   'Fuente VDC',  '━(+)━'),
                ('VAC', 'Fuente VAC',  '━(~)━'),
                ('I',   'Fuente I',    '━(→)━'),
            ]),
            ("Semiconductores", [
                ('D',       'Diodo',                 '━|▷|━'),
                ('LED',     'LED',                   '━|▷|★'),
                ('BRIDGE',  'Puente rectificador',   '◇'),
                ('BJT_NPN', 'BJT NPN',               '━(NPN)'),
                ('BJT_PNP', 'BJT PNP',               '━(PNP)'),
                ('NMOS',    'MOSFET N',              '━[N]━'),
                ('PMOS',    'MOSFET P',              '━[P]━'),
                ('OPAMP',   'Op-Amp',                '━[▷]━'),
                ('XFMR',    'Transformador',         '⌇⌇'),
            ]),
            ("Referencia", [
                ('GND',          'Tierra',          '⏚'),
                ('NODE',         'Nodo',            '•'),
                ('NET_LABEL_IN',  'Net Label Entrada', '→▷'),
                ('NET_LABEL_OUT', 'Net Label Salida',  '◁→'),
            ]),
            ("Digital", [
                ('AND',       'Puerta AND',     '&'),
                ('OR',        'Puerta OR',      '≥1'),
                ('NOT',       'Inversor NOT',   '○'),
                ('NAND',      'Puerta NAND',    '&̄'),
                ('NOR',       'Puerta NOR',     '≥1̄'),
                ('XOR',       'Puerta XOR',     '=1'),
                ('DFF',       'Flip-flop D',    '▣D'),
                ('JKFF',      'Flip-flop JK',   '▣JK'),
                ('TFF',       'Flip-flop T',    '▣T'),
                ('SRFF',      'Flip-flop SR',   '▣SR'),
                ('COUNTER',   'Contador binario','#'),
                ('MUX2',      'Multiplexor 2:1','⊞'),
                ('ADC_BRIDGE','Puente ADC',     'A→D'),
                ('DAC_BRIDGE','Puente DAC',     'D→A'),
                ('COMPARATOR','Comparador',     'CMP'),
                ('PWM',       'Salida PWM',     '⊓⊓'),
                ('LOGIC_STATE','Estado Lógico',  '0/1'),
                ('CLK',       'Reloj (CLK)',    '⏲'),
            ]),
        ]

        for cat_name, items in categories:
            btn = QPushButton(cat_name)
            btn.setFont(QFont('Consolas', 9))
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, c=cat_name, it=items: self._show_picker(c, it))
            tb.addWidget(btn)

        tb.addSeparator()

        # ── Herramientas ─────────────────────────────────────────────────
        btn_select = QPushButton("↖ Seleccionar")
        btn_select.setFont(QFont('Consolas', 9))
        btn_select.setFixedHeight(28)
        btn_select.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_select.clicked.connect(self._set_select_mode)
        tb.addWidget(btn_select)

        btn_wire = QPushButton("✏ Cable")
        btn_wire.setFont(QFont('Consolas', 9))
        btn_wire.setFixedHeight(28)
        btn_wire.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_wire.clicked.connect(self._set_wire_mode)
        tb.addWidget(btn_wire)

        tb.addSeparator()

        # ── Simulación ─────────────────────────────────────────────────────
        # Estándar lógico fijo: CMOS 5 V (no expuesto en la UI)
        self.run_btn = QPushButton("▶  SIMULAR AUTO")
        self.run_btn.setFont(QFont('Consolas', 10, QFont.Weight.Bold))
        self.run_btn.setFixedHeight(28)
        self.run_btn.setCheckable(True)
        self.run_btn.setToolTip("Detecta automáticamente: DC · AC · Digital · Mixto")
        self.run_btn.clicked.connect(self._toggle_simulation)
        tb.addWidget(self.run_btn)

    # ── Estilo ───────────────────────────────────
    def _apply_style(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: {COLORS['bg']};
                color: {COLORS['text']};
                font-family: 'Consolas', monospace;
            }}
            QToolBar {{
                background: {COLORS['toolbar']};
                border-bottom: 1px solid {COLORS['panel_brd']};
                padding: 4px;
                spacing: 6px;
            }}
            QToolBar#component_toolbar {{
                background: {COLORS['panel']};
                border-bottom: 2px solid {COLORS['panel_brd']};
            }}
            QToolBar QToolButton {{
                color: {COLORS['text']};
                padding: 4px 10px;
                font-family: 'Consolas';
            }}
            QPushButton {{
                background: {COLORS['comp_body']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['panel_brd']};
                border-radius: 4px;
                padding: 5px 12px;
                text-align: left;
            }}
            QPushButton:hover  {{ background: {COLORS['toolbar']}; }}
            QPushButton:checked {{ background: {COLORS['component']}; color: white; }}
            QPushButton#run    {{ background: {COLORS['component']}; color: white; font-weight: bold; }}
            QTableWidget {{
                background: {COLORS['comp_body']};
                color: {COLORS['text']};
                gridline-color: {COLORS['panel_brd']};
                border: 1px solid {COLORS['panel_brd']};
            }}
            QHeaderView::section {{
                background: {COLORS['toolbar']};
                color: {COLORS['text']};
                border: none; padding: 4px;
            }}
            QTextEdit {{
                background: {COLORS['comp_body']};
                color: {COLORS['current']};
                border: 1px solid {COLORS['panel_brd']};
                border-radius: 4px;
            }}
            QScrollBar:vertical, QScrollBar:horizontal {{
                background: {COLORS['comp_body']}; width: 8px; height: 8px;
            }}
            QScrollBar::handle {{ background: {COLORS['panel_brd']}; border-radius: 4px; }}
            QStatusBar {{ background: {COLORS['toolbar']}; color: {COLORS['text_dim']}; }}
            QSplitter::handle {{ background: {COLORS['panel_brd']}; width: 1px; }}
            QGroupBox {{
                font-family: 'Consolas', monospace;
                margin-top: 6px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 6px;
                padding: 0 4px;
            }}
        """)

    # ── Modos ────────────────────────────────────
    def _show_picker(self, category_name: str, items: List[tuple]):
        """Abre el diálogo de selección y, si se acepta, activa el modo colocación."""
        dialog = ComponentPickerDialog(category_name, items, ComponentItem, COLORS, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            ctype = dialog.get_selected_type()
            if ctype:
                self._set_place_mode(ctype)

    def _set_place_mode(self, comp_type: str):
        self.scene.set_mode(f'place_{comp_type}')
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.statusBar().showMessage(f"Click en el canvas para colocar: {comp_type}")

    def _set_wire_mode(self):
        self.scene.set_mode('wire')
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.statusBar().showMessage("Wire: click para iniciar, click para terminar, ESC para cancelar")

    def _set_select_mode(self):
        self.scene.set_mode('select')
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.statusBar().showMessage("Modo selección")

    def _on_sim_mode_changed(self, mode: str):
        pass  # modo automático — no se usa

    # ── Simulación automática ─────────────────────────────────────────────
    def _merge_all_sheets(self) -> Tuple[List[ComponentItem], Dict[str, str]]:
        """Combina componentes y netlist de todas las hojas para simulación multi-hoja.

        Cada hoja ya resuelve sus propios net labels internamente (via extract_netlist).
        Este método solo necesita unificar los nets ENTRE hojas distintas cuando el
        mismo sheet_label aparece en hojas diferentes.
        """
        all_components: List[ComponentItem] = []
        merged_pin_node: Dict[str, str] = {}

        sheet_netlists = []
        for i, sheet in enumerate(self._sheets):
            sc = sheet['scene']
            pn = sc.extract_netlist()
            prefix = f"_s{i}_"
            prefixed_pn = {}
            for pin_id, net_name in pn.items():
                new_net = (net_name if net_name == '0' or not net_name.startswith('net_')
                           else prefix + net_name)
                prefixed_pn[pin_id] = new_net
            sheet_netlists.append(prefixed_pn)
            all_components.extend(sc.components)
            merged_pin_node.update(prefixed_pn)

        # ── Unificar nets ENTRE hojas por sheet_label ─────────────────────
        # Dentro de cada hoja, extract_netlist ya unió los net labels.
        # Aquí unimos los nets canónicos de cada hoja que comparten label.
        label_canonical: Dict[str, str] = {}  # label → net canónico global

        for i, sheet in enumerate(self._sheets):
            pn = sheet_netlists[i]
            for comp in sheet['scene'].components:
                if comp.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT') and comp.sheet_label:
                    pin_key = f"{comp.name}__p1"
                    if pin_key not in pn:
                        continue
                    net = pn[pin_key]   # net ya prefijado con _sN_
                    aliases = []
                    for lbl in (comp.sheet_label.strip(), comp.name.strip()):
                        if lbl and lbl not in aliases:
                            aliases.append(lbl)
                    for lbl in aliases:
                        if lbl not in label_canonical:
                            label_canonical[lbl] = net
                        elif label_canonical[lbl] != net:
                            # Reemplazar en toda la netlist
                            target = label_canonical[lbl]
                            for k in merged_pin_node:
                                if merged_pin_node[k] == net:
                                    merged_pin_node[k] = target
                            net = target

        return all_components, merged_pin_node

    def _get_sim_context(self):
        """Retorna (all_comps, pin_node) considerando net labels multi-hoja."""
        has_net_labels = any(
            comp.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT')
            for sheet in self._sheets
            for comp in sheet['scene'].components
        )
        if has_net_labels or len(self._sheets) > 1:
            return self._merge_all_sheets()
        return list(self.scene.components), self.scene.extract_netlist()

    def _toggle_simulation(self, checked: bool):
        """Analiza el circuito y despacha automáticamente al solver correcto."""
        if not checked:
            self._stop_simulation()
            return

        all_comps, pin_node = self._get_sim_context()
        self._sim_all_comps = all_comps
        self._sim_pin_node = pin_node

        std_name = DEFAULT_LOGIC_STANDARD
        analyzer = CircuitAnalyzer(logic_standard=std_name)
        flags = analyzer.analyze(all_comps, pin_node)

        self.results_text.setPlainText(flags.summary() + "\n\nAnalizando...")
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        # Circuitos con puentes ADC/DAC explícitos o fronteras implícitas reales
        # → co-simulación transitoria (one-shot)
        _needs_transient = (
            flags.has_bridges
            or bool(flags.implicit_boundary_nodes)
            or (flags.has_dc and flags.has_ac)
        )
        if _needs_transient:
            self.run_btn.setChecked(False)
            self.run_btn.setText("▶  SIMULAR AUTO")
            self._run_simulation_auto(flags, pin_node)
        elif flags.has_ac and not flags.has_dc:
            self.run_btn.setChecked(False)
            self.run_btn.setText("▶  SIMULAR AUTO")
            self._run_simulation_ac()
        elif flags.has_dc or flags.has_digital:
            # DC puro, digital puro, o digital+DC sin fronteras → simulación continua
            self._sim_running = True
            self.run_btn.setText("■  DETENER")
            self._sim_timer.start()
            self._run_simulation_dc()
        else:
            self.run_btn.setChecked(False)
            self.run_btn.setText("▶  SIMULAR AUTO")
            self.results_text.setPlainText(
                "⚠  No se encontraron componentes para simular.\n"
                "Añade componentes al canvas y conéctalos a tierra.")

    def _stop_simulation(self):
        """Detiene la simulación y apaga todos los LEDs en todas las hojas."""
        self._sim_running = False
        self._sim_timer.stop()
        self._sim_all_comps = None
        self._sim_pin_node = None
        self.run_btn.setChecked(False)
        self.run_btn.setText("▶  SIMULAR AUTO")
        for sheet in self._sheets:
            for item in sheet['scene'].components:
                if item.comp_type == 'LED':
                    item.led_on = False
                    item.update()

    def _tick_simulation(self):
        """Llamado por QTimer: re-corre DC silenciosamente para actualizar LEDs."""
        if self._sim_running:
            all_comps, pin_node = self._get_sim_context()
            self._sim_all_comps = all_comps
            self._sim_pin_node = pin_node
            self._run_simulation_dc(silent=True)

    def _run_simulation(self):
        """Compatibilidad: despacha al toggle."""
        self._toggle_simulation(True)

    def _run_simulation_auto(self, flags=None, pin_node=None):
        """Corre DC + AC + mixto según flags y muestra todo en un panel."""
        from PyQt6.QtWidgets import QApplication
        from engine.digital_engine import (
            DigitalSimulator, Gate, DFF, JKFF, TFF, SRFF, BinaryCounter, MUX,
        )
        from engine.bridges import ADC, DAC, ComparatorBridge, PWMBridge
        from engine.mixed_signal import MixedSignalInterface
        import cmath as _cmath

        if pin_node is None:
            pin_node = getattr(self, '_sim_pin_node', None) or self.scene.extract_netlist()
        if flags is None:
            sim_comps_for_flags = getattr(self, '_sim_all_comps', None) or list(self.scene.components)
            std_name = DEFAULT_LOGIC_STANDARD
            analyzer = CircuitAnalyzer(logic_standard=std_name)
            flags = analyzer.analyze(sim_comps_for_flags, pin_node)

        sim_components = getattr(self, '_sim_all_comps', None) or list(self.scene.components)

        std_name = DEFAULT_LOGIC_STANDARD
        out = ["═══ SIMULACIÓN AUTOMÁTICA ═══", f"  {flags.summary()}", ""]
        if flags.warnings:
            out.extend([f"  ⚠ {w}" for w in flags.warnings]); out.append("")

        # ── Construir componentes analógicos ─────────────────────────────
        analog_comps, build_errors = [], []
        for item in sim_components:
            if item.comp_type in ComponentItem.DIGITAL_TYPES:
                continue
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                continue
            try:
                analog_comps.extend(build_engine_components_for_item(item, pin_node))
            except Exception as e:
                build_errors.append(f"{item.name}: {e}")

        # ── DC ────────────────────────────────────────────────────────────
        if flags.has_dc and analog_comps:
            dc_comps = [VoltageSource(c.name, c.n_pos, c.n_neg, 0.0)
                        if isinstance(c, VoltageSourceAC) else c for c in analog_comps]
            dc = self.solver.solve_dc(dc_comps)
            out.append("── Voltajes DC ──")
            if dc["success"]:
                for node, v in sorted(dc["voltages"].items()):
                    out.append(f"  V({node}) = {v:+.4f} V")
                if dc.get("branch_currents"):
                    out.append(""); out.append("── Corrientes DC ──")
                    for name, i in dc["branch_currents"].items():
                        out.append(f"  I({name}) = {i*1000:+.4f} mA")
                for item in sim_components:
                    n1 = item.node1.strip() or pin_node.get(f"{item.name}__p1", "")
                    n2 = item.node2.strip() or pin_node.get(f"{item.name}__p2", "0")
                    item.result_voltage = dc["voltages"].get(n1)
                    if item.comp_type == "LED":
                        item.led_on = False
                        op = dc.get("operating_points", {}).get(item.name, {})
                        Id_op = op.get("Id", op.get("id")) if op else None
                        if Id_op is not None:
                            item.led_on = float(Id_op) > 1e-4
                        else:
                            v_a = dc["voltages"].get(n1, 0)
                            v_k = dc["voltages"].get(n2, 0)
                            vf_min = {'red':1.5,'orange':1.7,'yellow':1.8,
                                      'green':1.9,'blue':2.6,'white':2.6}
                            thr = vf_min.get(getattr(item,'led_color','red'), 1.5)
                            item.led_on = (v_a - v_k) > thr
                    item.update()
            else:
                out.append(f"  ✗ {dc['error']}")
            out.append("")

        # ── AC ────────────────────────────────────────────────────────────
        if flags.has_ac and analog_comps:
            freq = next((it.frequency for it in sim_components
                         if it.comp_type == "VAC"), 60.0)
            ac = self.solver.solve_ac_single(analog_comps, freq)
            out.append(f"── Fasores AC ({freq} Hz) ──")
            if ac["success"]:
                for node, V in sorted(ac["voltages"].items()):
                    out.append(f"  V({node}) = {abs(V):.4f} V  ∠{_cmath.phase(V)*180/_cmath.pi:.2f}°")
                t = ac.get("total", {})
                if t:
                    out += ["", "── Potencia total ──",
                            f"  P={t.get('P',0):+.4f} W  Q={t.get('Q',0):+.4f} VAR",
                            f"  S={t.get('S',0):.4f} VA  fp={t.get('fp',0):.4f} ({t.get('fp_type','')})"]
                self._last_ac_result = ac
                self.btn_power_triangle.setVisible(True)
            else:
                out.append(f"  ✗ {ac['error']}")
            out.append("")

        # ── Mixto ─────────────────────────────────────────────────────────
        if flags.needs_mixed:
            # Si no hay puentes reales ni nodos frontera, no hace falta
            # co-simulación transitoria: correr DC analógico + digital por separado
            _only_isolated = (
                not flags.has_bridges
                and not flags.implicit_boundary_nodes
            )
            if _only_isolated:
                # Evaluar puertas digitales usando los voltajes DC ya calculados
                # para determinar los niveles lógicos en las entradas
                _dc_voltages = {}
                if flags.has_dc and analog_comps:
                    _dc_res = self.solver.solve_dc(
                        [VoltageSource(c.name, c.n_pos, c.n_neg, 0.0)
                         if isinstance(c, VoltageSourceAC) else c
                         for c in analog_comps])
                    if _dc_res.get("success"):
                        _dc_voltages = _dc_res["voltages"]
                self._evaluate_digital_gates(pin_node, _dc_voltages, out=out)
                out.append("")
                self.results_text.setPlainText("\n".join(out))
                self.scene.update()
                return
            t_stop = 1e-3   # 1 ms por defecto
            dt_chunk = max(t_stop / 100, 1e-6)
            dsim = DigitalSimulator()
            adc_list, dac_list = [], []
            _gmap = {"AND":"AND","OR":"OR","NOT":"NOT","NAND":"NAND","NOR":"NOR","XOR":"XOR"}
            for item in sim_components:
                ct = item.comp_type; tpd = item.dig_tpd_ns * 1e-9
                try:
                    if ct in _gmap:
                        n_in = max(1, item.dig_inputs)
                        dsim.add(Gate(item.name, _gmap[ct],
                                      [f"{item.name}_I{i}" for i in range(n_in)],
                                      f"{item.name}_Y", t_pd=tpd))
                    elif ct == "DFF":
                        dsim.add(DFF(item.name, d=f"{item.name}_D", clk=item.dig_clk,
                                     q=f"{item.name}_Q", qn=f"{item.name}_Qn", t_pd=tpd))
                    elif ct == "ADC_BRIDGE":
                        nd = item.dig_analog_node or pin_node.get(f"{item.name}__p1","")
                        adc_list.append(ADC(item.name, node=nd, bits=item.dig_bits_adc, vref=item.dig_vref))
                    elif ct == "DAC_BRIDGE":
                        nd = item.dig_analog_node or pin_node.get(f"{item.name}__p1","")
                        dac_list.append(DAC(item.name, bits=item.dig_bits_adc, vref=item.dig_vref, out_node=nd))
                    elif ct == "COMPARATOR":
                        nd = item.dig_analog_node or pin_node.get(f"{item.name}__p1","")
                        adc_list.append(ComparatorBridge(item.name, node_pos=nd))
                except Exception as e:
                    build_errors.append(f"{item.name}: {e}")
            if flags.implicit_boundary_nodes:
                std = LOGIC_STANDARDS.get(std_name, DEFAULT_STANDARD)
                out.append(f"── Fronteras implícitas ({std_name}) ──")
                for node in flags.implicit_boundary_nodes:
                    out.append(f"  Nodo '{node}'")
                    adc_list.append(ADC(f"__impl_{node}", node=node, bits=1, vref=std.Vdd))
                out.append("")
            if analog_comps:
                iface = MixedSignalInterface(self.solver, dsim, analog_comps)
                for a in adc_list: iface.add_adc(a)
                for d in dac_list:
                    if hasattr(d,"pwm_net"): iface.add_pwm(d)
                    elif hasattr(d,"input_nets"): iface.add_dac(d)
                    else: iface.add_comparator(d)
                mr = iface.run_iterative(t_stop=t_stop, dt_chunk=dt_chunk,
                                          dt_analog=min(dt_chunk/10, 1e-6))
                out.append("── Co-simulación mixta ──")
                if mr.success:
                    for nd, arr in sorted(mr.analog_voltages.items()):
                        if len(arr) > 0: out.append(f"  V({nd}) = {arr[-1]:+.4f} V")
                    for net, hist in sorted(mr.digital_waveforms.items()):
                        if hist and not net.startswith("__impl"):
                            out.append(f"  {net} = {hist[-1][1]}")
                else:
                    out.append(f"  ✗ {mr.error}")
                out.append("")

        if build_errors:
            out.append("── Advertencias ──")
            out.extend([f"  ⚠ {e}" for e in build_errors])
        self.results_text.setPlainText("\n".join(out))
        self.scene.update()

    def _run_simulation_dc(self, silent: bool = False):
        components = []
        errors = []

        # Usar contexto multi-hoja si disponible (net labels / múltiples hojas)
        sim_comps = getattr(self, '_sim_all_comps', None) or list(self.scene.components)
        pin_node = getattr(self, '_sim_pin_node', None) or self.scene.extract_netlist()

        for item in sim_comps:
            # Net labels y nodos auxiliares no generan componentes de engine
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                continue

            # Prioridad: nodo manual del usuario > nodo extraido automaticamente
            auto_n1 = pin_node.get(f"{item.name}__p1", f'iso_{item.name}_p')
            auto_n2 = pin_node.get(f"{item.name}__p2", '0')
            auto_n3 = pin_node.get(f"{item.name}__p3", '')

            n1 = item.node1.strip() if item.node1.strip() else auto_n1
            n2 = item.node2.strip() if item.node2.strip() else auto_n2
            n3 = item.node3.strip() if item.node3.strip() else auto_n3

            try:
                # ── Componentes digitales: se ignoran en DC/AC ──────────
                if item.comp_type in ComponentItem.DIGITAL_TYPES:
                    # LOGIC_STATE / CLK: modelar como fuente de voltaje ideal
                    if item.comp_type in ('LOGIC_STATE', 'CLK'):
                        std_name = DEFAULT_LOGIC_STANDARD
                        std = LOGIC_STANDARDS.get(std_name, DEFAULT_STANDARD)
                        v_out = std.Voh if item.value else std.Vol
                        out_node = item.node1.strip() or pin_node.get(f"{item.name}__p1", f"ls_{item.name}")
                        if out_node and out_node not in ('0', 'gnd', 'GND'):
                            components.append(VoltageSource(item.name, out_node, '0', v_out))
                    continue

                # En DC la fuente VAC vale 0 V (valor medio de senoidal)
                if item.comp_type == 'VAC':
                    components.append(VoltageSource(item.name, n1, n2, 0.0))
                    continue

                # Validación específica de R
                if item.comp_type == 'R' and item.value <= 0:
                    errors.append(f"{item.name}: resistencia debe ser > 0")
                    continue

                # Resto de componentes analógicos (incluye POT, XFMR, BRIDGE)
                components.extend(build_engine_components_for_item(item, pin_node))
            except Exception as e:
                errors.append(f"{item.name}: {e}")

        # ── Excluir LEDs/Diodos cuyo ánodo es salida exclusiva de puerta digital ──
        # Esos componentes no tienen driver analógico → matriz singular.
        # Se evalúan luego con _evaluate_digital_gates.
        _gate_types_dc = {'AND','OR','NOT','NAND','NOR','XOR','NAND','NOR',
                          'DFF','JKFF','TFF','SRFF','MUX2','COUNTER'}
        _dig_out_nodes = set()
        for _item in sim_comps:
            if _item.comp_type in _gate_types_dc:
                _on = _item.node1.strip() or pin_node.get(f"{_item.name}__p1", "")
                if _on and _on not in ('0','gnd','GND'):
                    _dig_out_nodes.add(_on)
        # Reunir todos los nodos que tienen driver analógico
        _analog_driver_nodes = set()
        for _c in components:
            for _attr in ('n_pos','n_neg','n_p','n_n','n_out','n_in',
                          'n_base','n_collector','n_emitter',
                          'n_gate','n_drain','n_source'):
                _nd = getattr(_c, _attr, None)
                if _nd and _nd not in ('0','gnd','GND'):
                    _analog_driver_nodes.add(_nd)
        # Quitar del netlist analógico los LED/Diodo cuyo ánodo
        # solo está en nodos de salida digital sin otro driver analógico
        components = [
            _c for _c in components
            if not (
                getattr(_c, '__class__', None) is not None
                and _c.__class__.__name__ == 'Diode'
                and getattr(_c, 'n_p', getattr(_c, 'n_pos', '')) in _dig_out_nodes
                and getattr(_c, 'n_p', getattr(_c, 'n_pos', '')) not in _analog_driver_nodes
            )
        ]

        if not components:
            # Solo puertas digitales y LEDs en sus salidas: evaluar directo.
            # FIX: construir dc_voltages desde los LOGIC_STATE antes de
            # llamar a _evaluate_digital_gates. Sin esto, el diccionario
            # llega vacío y todas las entradas se leen como 0 V (LOW).
            std_name = DEFAULT_LOGIC_STANDARD
            std = LOGIC_STANDARDS.get(std_name, DEFAULT_STANDARD)
            _dig_voltages = {}
            for _it in sim_comps:
                if _it.comp_type in ('LOGIC_STATE', 'CLK'):
                    _v = std.Voh if _it.value else std.Vol
                    _net = _it.node1.strip() or pin_node.get(f"{_it.name}__p1", "")
                    if _net:
                        _dig_voltages[_net] = _v
            out = ["═══ SIMULACIÓN DIGITAL ═══", ""]
            self._evaluate_digital_gates(pin_node, _dig_voltages, silent=silent, out=out, sim_comps=sim_comps)
            if not silent:
                self.results_text.setPlainText('\n'.join(out))
            for sheet in self._sheets:
                sheet['scene'].update()
            return

        # Mostrar netlist extraida antes de simular
        if not silent:
            out_pre = ["═══ NETLIST EXTRAIDA ═══"]
            for item in sim_comps:
                if item.comp_type in ('GND', 'NODE'):
                    continue
                auto_n1 = pin_node.get(f"{item.name}__p1", '?')
                auto_n2 = pin_node.get(f"{item.name}__p2", '?')
                n1_show = item.node1.strip() if item.node1.strip() else auto_n1
                n2_show = item.node2.strip() if item.node2.strip() else auto_n2
                out_pre.append(f"  {item.name}: {n1_show} → {n2_show}  ({item._format_value()})")
            out_pre.append("")
            self.results_text.setPlainText('\n'.join(out_pre) + "Simulando...")
            QApplication.processEvents()

        result = self.solver.solve_dc(components)

        # Mostrar resultados
        out = []
        if result['success']:
            out.append("═══ ANÁLISIS DC ═══\n")
            out.append("── Tensiones nodales ──")
            for node, v in sorted(result['voltages'].items()):
                out.append(f"  V({node}) = {v:+.4f} V")
            if result.get('branch_currents'):
                out.append("\n── Corrientes de rama ──")
                for name, i in result['branch_currents'].items():
                    out.append(f"  I({name}) = {i*1000:+.4f} mA")

            # Iteraciones Newton-Raphson (si aplica)
            if 'iterations' in result:
                out.append(f"\n  [NR convergió en {result['iterations']} iteraciones]")
            if 'warning' in result:
                out.append(f"\n  ⚠ {result['warning']}")

            # Puntos de operación de componentes no-lineales
            if result.get('operating_points'):
                out.append("\n── Puntos de operación ──")
                for comp_name, op in result['operating_points'].items():
                    out.append(f"  {comp_name}:")
                    for k, v in op.items():
                        if isinstance(v, float):
                            out.append(f"    {k} = {v:.4g}")
                        else:
                            out.append(f"    {k} = {v}")

            # Corrientes y potencias
            out.append("\n── Corrientes y potencias ──")
            for comp in components:
                if isinstance(comp, VoltageSource):
                    i_branch = result['branch_currents'].get(comp.name, 0)
                    p = comp.V * i_branch
                    out.append(f"  I({comp.name}) = {i_branch*1000:+.4f} mA  |  P = {abs(p):.4f} W")
                elif isinstance(comp, Resistor):
                    v1 = result['voltages'].get(comp.n1, 0)
                    v2 = result['voltages'].get(comp.n2, 0)
                    i_r = (v1 - v2) / comp.R
                    p   = (v1 - v2)**2 / comp.R
                    out.append(f"  I({comp.name}) = {i_r*1000:+.4f} mA  |  P = {p*1000:.4f} mW")
                elif isinstance(comp, CurrentSource):
                    v1 = result['voltages'].get(comp.n_pos, 0)
                    v2 = result['voltages'].get(comp.n_neg, 0)
                    p = comp.I_val * (v1 - v2)
                    out.append(f"  I({comp.name}) = {comp.I_val*1000:+.4f} mA  |  P = {abs(p):.4f} W")

            # Actualizar canvas con voltajes y estado LED
            for item in sim_comps:
                auto_n1 = pin_node.get(f"{item.name}__p1", '')
                auto_n2 = pin_node.get(f"{item.name}__p2", '0')
                n1 = item.node1.strip() if item.node1.strip() else auto_n1
                n2 = item.node2.strip() if item.node2.strip() else auto_n2
                if n1 in result['voltages']:
                    item.result_voltage = result['voltages'][n1]
                else:
                    item.result_voltage = None
                if item.comp_type == 'LED':
                    led_on = False
                    op = result.get('operating_points', {}).get(item.name, {})
                    id_ = None
                    if op:
                        id_ = op.get('Id', op.get('id', op.get('I', None)))
                    if id_ is not None:
                        led_on = float(id_) > 1e-4
                    else:
                        v_a = result['voltages'].get(n1, None)
                        v_k = result['voltages'].get(n2, None)
                        if v_a is not None and v_k is not None:
                            vf_min = {'red':1.5,'orange':1.7,'yellow':1.8,
                                      'green':1.9,'blue':2.6,'white':2.6}
                            thr = vf_min.get(getattr(item,'led_color','red'), 1.5)
                            led_on = (v_a - v_k) > thr
                    item.led_on = led_on
                item.update()
                if hasattr(item, 'scene') and item.scene():
                    item.scene().update(item.mapToScene(item.boundingRect()).boundingRect())

            # Debug LED — mostrar info de nodos y voltajes del LED
            led_items = [it for it in sim_comps if it.comp_type == 'LED']
            if led_items:
                out.append("\n── Debug LED ──")
                for it in led_items:
                    auto_n1 = pin_node.get(f"{it.name}__p1", '?')
                    auto_n2 = pin_node.get(f"{it.name}__p2", '?')
                    n1d = it.node1.strip() if it.node1.strip() else auto_n1
                    n2d = it.node2.strip() if it.node2.strip() else auto_n2
                    va  = result['voltages'].get(n1d, 'N/A')
                    vk  = result['voltages'].get(n2d, 'N/A')
                    op  = result.get('operating_points', {}).get(it.name, {})
                    out.append(f"  {it.name}: ánodo={n1d}({va}) cátodo={n2d}({vk})")
                    out.append(f"    op={op}  led_on={it.led_on}")

        else:
            if not silent:
                out.append(f"\u2717 Error de simulaci\u00f3n:\n{result['error']}")
                out.append("\nVerifica que el circuito tenga:")
                out.append("  \u2022 Al menos una fuente de voltaje")
                out.append("  \u2022 Nodo de tierra (nodo '0')")
                out.append("  \u2022 Nodos asignados a cada componente")

        if errors and not silent:
            out.append("\n── Advertencias ──")
            out.extend([f"  ⚠ {e}" for e in errors])

        # Evaluar puertas digitales y actualizar LEDs en su salida
        if result.get('success'):
            self._evaluate_digital_gates(pin_node, result['voltages'], silent=silent, out=out, sim_comps=sim_comps)

        if not silent:
            self.results_text.setPlainText('\n'.join(out))
        for sheet in self._sheets:
            sheet['scene'].update()


    def _evaluate_digital_gates(self, pin_node, dc_voltages, silent=False, out=None, sim_comps=None):
        std_name = DEFAULT_LOGIC_STANDARD
        std = LOGIC_STANDARDS.get(std_name, DEFAULT_STANDARD)
        _gmap = {'AND':'AND','OR':'OR','NOT':'NOT','NAND':'NAND','NOR':'NOR','XOR':'XOR'}
        _funcs = {
            'AND':  lambda vals: all(vals),
            'OR':   lambda vals: any(vals),
            'NAND': lambda vals: not all(vals),
            'NOR':  lambda vals: not any(vals),
            'XOR':  lambda vals: bool(sum(vals) % 2),
            'NOT':  lambda vals: not bool(vals[0]),
        }
        _all = sim_comps if sim_comps is not None else list(self.scene.components)
        gate_items = [it for it in _all if it.comp_type in _gmap]
        if gate_items and out is not None and not silent:
            out.append('\n── Señales digitales ──')
        for item in gate_items:
            n_in = max(1, item.dig_inputs)
            input_logics = []
            for i in range(n_in):
                if i == 0:
                    node = (item.node2.strip()
                            or pin_node.get(f'{item.name}__p2', ''))
                elif i == 1:
                    _n3 = item.node3.strip() if hasattr(item, 'node3') else ''
                    node = _n3 or pin_node.get(f'{item.name}__p3', '')
                else:
                    _extra = getattr(item, 'dig_input_nodes', [])
                    _manual_extra = _extra[i-2].strip() if len(_extra) > i-2 else ''
                    node = _manual_extra or pin_node.get(f'{item.name}__p{i+2}', '')
                if node in ('0', 'gnd', 'GND'):
                    v = 0.0
                elif not node:
                    v = 0.0
                else:
                    v = dc_voltages.get(node, 0.0)
                input_logics.append(1 if v >= std.Vih else 0)
            y = int(_funcs[item.comp_type](input_logics))
            out_node = item.node1.strip() or pin_node.get(f'{item.name}__p1', '')
            v_out = std.Voh if y else std.Vol
            if out_node and out_node not in ('0', 'gnd', 'GND'):
                dc_voltages[out_node] = v_out
            for led in _all:
                if led.comp_type == 'LED':
                    led_anode = led.node1.strip() or pin_node.get(f'{led.name}__p1', '')
                    if led_anode == out_node:
                        led.led_on = (v_out > 0.3)
                        led.update()
            if out is not None and not silent:
                out.append(f"  {item.name}_Y = {y}  ({'HIGH' if y else 'LOW'})")

        # ── Evaluación de flip-flops (DFF/JKFF/TFF/SRFF) ─────────────────
        # Lectura de niveles desde dc_voltages, prioridad SET/RESET asíncronos,
        # y actualización del círculo de memoria (dig_q_state).
        ff_items = [it for it in _all
                    if it.comp_type in ComponentItem.FLIPFLOP_TYPES]
        if ff_items and out is not None and not silent:
            out.append('\n── Flip-flops ──')

        def _logic_at(node: str) -> int:
            if not node or node in ('0', 'gnd', 'GND'):
                return 0
            return 1 if dc_voltages.get(node, 0.0) >= std.Vih else 0

        for item in ff_items:
            # Resolver nodos de cada pin: manual > automático.
            n_q   = item.node1.strip() or pin_node.get(f'{item.name}__p1', '')
            n_in1 = item.node2.strip() or pin_node.get(f'{item.name}__p2', '')
            n_in2 = (item.node3.strip() if hasattr(item, 'node3') else '') \
                    or pin_node.get(f'{item.name}__p3', '')
            n_set = pin_node.get(f'{item.name}__p4', '')
            n_rst = pin_node.get(f'{item.name}__p5', '')

            # Persistir último valor de CLK por flip-flop para detectar flancos.
            last_clk = getattr(item, '_last_clk_seen', 0)
            q_prev   = int(getattr(item, 'dig_q_state', 0))
            q_new    = q_prev

            set_active = bool(_logic_at(n_set))
            rst_active = bool(_logic_at(n_rst))

            if rst_active:
                q_new = 0
            elif set_active:
                q_new = 1
            elif item.comp_type == 'SRFF':
                # Asíncrono: S=p2, R=p3
                S = _logic_at(n_in1)
                R = _logic_at(n_in2)
                if S and R:    q_new = 0    # estado prohibido → 0
                elif S:        q_new = 1
                elif R:        q_new = 0
            else:
                # Síncrono por flanco de subida.
                # DFF/TFF: CLK está en p3 (entrada secundaria)
                # JKFF: J=p2, K=p3, CLK = item.dig_clk (net global)
                if item.comp_type == 'JKFF':
                    clk_now = _logic_at(item.dig_clk)
                else:
                    clk_now = _logic_at(n_in2)
                if clk_now == 1 and last_clk == 0:
                    if item.comp_type == 'DFF':
                        q_new = _logic_at(n_in1)
                    elif item.comp_type == 'TFF':
                        if _logic_at(n_in1):
                            q_new = 1 - q_prev
                    elif item.comp_type == 'JKFF':
                        J = _logic_at(n_in1)
                        K = _logic_at(n_in2)
                        if   J == 0 and K == 0: pass
                        elif J == 0 and K == 1: q_new = 0
                        elif J == 1 and K == 0: q_new = 1
                        else:                   q_new = 1 - q_prev
                item._last_clk_seen = clk_now

            item.dig_q_state = q_new

            # Propagar Q y Q̄ a sus nodos para que la cadena digital los vea
            if n_q and n_q not in ('0', 'gnd', 'GND'):
                dc_voltages[n_q] = std.Voh if q_new else std.Vol
            n_qn = pin_node.get(f'{item.name}__p6', '')
            if n_qn and n_qn not in ('0', 'gnd', 'GND'):
                dc_voltages[n_qn] = std.Voh if (1 - q_new) else std.Vol

            # Repintar el componente para reflejar el círculo de memoria
            item.update()

            if out is not None and not silent:
                out.append(f"  {item.name}.Q = {q_new}")

    # ── Panel de propiedades ─────────────────────
    def _run_simulation_ac(self):
        """Análisis AC de frecuencia única con triángulo de potencia."""
        from PyQt6.QtWidgets import QInputDialog

        # Usar contexto multi-hoja si disponible
        sim_comps = getattr(self, '_sim_all_comps', None) or list(self.scene.components)
        pin_node = getattr(self, '_sim_pin_node', None) or self.scene.extract_netlist()

        # Buscar fuente VAC en el canvas para leer la frecuencia
        vac_items = [it for it in sim_comps if it.comp_type == 'VAC']
        if not vac_items:
            self.results_text.setPlainText(
                "⚠  No hay fuentes VAC en el circuito.\n"
                "Agrega una fuente VAC (barra de componentes → Fuentes) para el análisis AC.")
            return

        # Usar la frecuencia de la primera VAC como referencia
        freq_default = vac_items[0].frequency
        freq, ok = QInputDialog.getDouble(
            self, 'Frecuencia de análisis',
            'Frecuencia (Hz):', freq_default, 0.001, 1e9, 3)
        if not ok:
            return

        components = []
        errors     = []

        for item in sim_comps:
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                continue
            auto_n1 = pin_node.get(f"{item.name}__p1", f'iso_{item.name}_p')
            auto_n2 = pin_node.get(f"{item.name}__p2", '0')
            n1 = item.node1.strip() if item.node1.strip() else auto_n1
            n2 = item.node2.strip() if item.node2.strip() else auto_n2

            try:
                if item.comp_type in ComponentItem.DIGITAL_TYPES:
                    continue
                # En AC pura, las fuentes DC contribuyen 0 V
                if item.comp_type == 'V':
                    components.append(VoltageSource(item.name, n1, n2, 0.0))
                    continue
                # En AC pura, los diodos del puente rectificador no tienen
                # sentido (son no-lineales) → omitir BRIDGE.
                if item.comp_type == 'BRIDGE':
                    errors.append(f"{item.name}: BRIDGE requiere análisis transitorio (no AC)")
                    continue
                # Validación específica
                if item.comp_type == 'R' and item.value <= 0:
                    errors.append(f"{item.name}: R debe ser > 0")
                    continue
                # Resto: helper centralizado (POT, XFMR, VAC, C, L, Z, I…)
                components.extend(build_engine_components_for_item(item, pin_node))
            except Exception as e:
                errors.append(f"{item.name}: {e}")

        if not components:
            self.results_text.setPlainText("⚠  No hay componentes simulables.")
            return

        solver = MNASolver()
        result = solver.solve_ac_single(components, freq)

        out = ["═══ ANÁLISIS AC ═══", f"  Frecuencia: {freq} Hz", ""]

        if errors:
            out.append("⚠ Advertencias:")
            out += [f"  {e}" for e in errors]
            out.append("")

        if not result['success']:
            out.append(f"✗ Error: {result['error']}")
            self.results_text.setPlainText('\n'.join(out))
            self.btn_power_triangle.setVisible(False)
            return

        # ── Voltajes nodales ──────────────────────────────────────────────
        out.append("── Voltajes nodales (Vrms / ∠°) ──")
        for node, V in sorted(result['voltages'].items()):
            import cmath
            mag   = abs(V)
            phase = cmath.phase(V) * 180 / cmath.pi
            out.append(f"  V({node}) = {mag:.4f} V  ∠{phase:.2f}°")

        # ── Potencias por componente ──────────────────────────────────────
        out.append("\n── Potencias por componente ──")
        for name, pw in result['powers'].items():
            out.append(f"  {name}:")
            out.append(f"    P = {pw['P']:+.4f} W")
            out.append(f"    Q = {pw['Q']:+.4f} VAR")
            out.append(f"    S = {pw['S']:.4f} VA")
            out.append(f"    fp= {pw['fp']:.4f}")

        # ── Triángulo de potencia total ───────────────────────────────────
        t = result['total']
        out.append("\n── Potencia total del circuito ──")
        out.append(f"  P  = {t['P']:+.4f} W      (potencia real/activa)")
        out.append(f"  Q  = {t['Q']:+.4f} VAR    (potencia reactiva)")
        out.append(f"  S  = {t['S']:.4f} VA     (potencia aparente)")
        out.append(f"  fp = {t['fp']:.4f}  ({t['fp_type']})")
        out.append("")
        out.append("  [Haz click en '📐 Ver Triángulo de Potencia']")

        if result.get('warning'):
            out.append(f"\n⚠ {result['warning']}")

        self.results_text.setPlainText('\n'.join(out))
        self._last_ac_result = result
        self.btn_power_triangle.setVisible(True)


    def _run_simulation_mixed(self):
        """
        Simulación mixta analógica-digital.
        Construye:
          - Lista de componentes MNA (analógicos)
          - DigitalSimulator con puertas y flip-flops del canvas
          - Puentes ADC/DAC desde los bloques ADC_BRIDGE / DAC_BRIDGE
        Luego lanza MixedSignalInterface.run_iterative() y muestra resultados.
        """
        from engine.digital_engine import (
            DigitalSimulator, Gate, DFF, JKFF, TFF, SRFF,
            BinaryCounter, MUX,
        )
        from engine.bridges import ADC, DAC, ComparatorBridge, PWMBridge
        from engine.mixed_signal import MixedSignalInterface

        # ── Parámetros de simulación ─────────────────────────────────────
        t_stop = 1e-3      # 1 ms por defecto
        dt_chunk = 100e-6  # 100 µs por chunk

        self.results_text.setPlainText("Preparando simulación mixta...")
        QApplication.processEvents()

        sim_all = getattr(self, '_sim_all_comps', None) or list(self.scene.components)
        pin_node = getattr(self, '_sim_pin_node', None) or self.scene.extract_netlist()
        analog_comps = []
        errors = []

        # ── Construir circuito analógico ─────────────────────────────────
        for item in sim_all:
            if item.comp_type in ComponentItem.DIGITAL_TYPES:
                continue
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                continue
            auto_n1 = pin_node.get(f"{item.name}__p1", f'iso_{item.name}_p')
            auto_n2 = pin_node.get(f"{item.name}__p2", '0')
            n1 = item.node1.strip() if item.node1.strip() else auto_n1
            n2 = item.node2.strip() if item.node2.strip() else auto_n2
            try:
                if item.comp_type in ComponentItem.DIGITAL_TYPES:
                    continue
                if item.comp_type == 'VAC':
                    # En esta ruta DC se ignora la VAC
                    analog_comps.append(VoltageSource(item.name, n1, n2, 0.0))
                    continue
                analog_comps.extend(build_engine_components_for_item(item, pin_node))
            except Exception as e:
                errors.append(f"{item.name}: {e}")

        if not analog_comps:
            self.results_text.setPlainText(
                "⚠  No hay componentes analógicos en el canvas.\n"
                "Añade al menos una fuente y una resistencia.")
            return

        # ── Construir circuito digital ────────────────────────────────────
        dsim = DigitalSimulator()
        adc_bridges = []
        dac_bridges = []

        _gate_map = {'AND':'AND','OR':'OR','NOT':'NOT',
                     'NAND':'NAND','NOR':'NOR','XOR':'XOR'}

        for item in sim_all:
            ct = item.comp_type
            tpd = item.dig_tpd_ns * 1e-9
            try:
                if ct in _gate_map:
                    n_in = max(1, item.dig_inputs)
                    ins  = [f'{item.name}_I{i}' for i in range(n_in)]
                    dsim.add(Gate(item.name, _gate_map[ct], ins,
                                  f'{item.name}_Y', t_pd=tpd))
                elif ct == 'DFF':
                    dsim.add(DFF(item.name,
                                 d=f'{item.name}_D', clk=item.dig_clk,
                                 q=f'{item.name}_Q', qn=f'{item.name}_Qn',
                                 t_pd=tpd))
                elif ct == 'JKFF':
                    dsim.add(JKFF(item.name,
                                  j=f'{item.name}_J', k=f'{item.name}_K',
                                  clk=item.dig_clk,
                                  q=f'{item.name}_Q', qn=f'{item.name}_Qn',
                                  t_pd=tpd))
                elif ct == 'TFF':
                    dsim.add(TFF(item.name,
                                 t_in=f'{item.name}_T', clk=item.dig_clk,
                                 q=f'{item.name}_Q', qn=f'{item.name}_Qn',
                                 t_pd=tpd))
                elif ct == 'SRFF':
                    dsim.add(SRFF(item.name,
                                  s=f'{item.name}_S', r=f'{item.name}_R',
                                  q=f'{item.name}_Q', qn=f'{item.name}_Qn',
                                  t_pd=tpd))
                elif ct == 'COUNTER':
                    dsim.add(BinaryCounter(item.name, n=item.dig_bits,
                                           clk=item.dig_clk,
                                           q_prefix=f'{item.name}_Q',
                                           t_pd=tpd))
                elif ct == 'MUX2':
                    dsim.add(MUX(item.name,
                                 inputs=[f'{item.name}_I0', f'{item.name}_I1'],
                                 sel=[f'{item.name}_SEL'],
                                 output=f'{item.name}_Y', t_pd=tpd))
                elif ct == 'ADC_BRIDGE':
                    node = item.dig_analog_node or pin_node.get(f"{item.name}__p1", '')
                    adc  = ADC(item.name, node=node,
                               bits=item.dig_bits_adc,
                               vref=item.dig_vref,
                               clk=item.dig_clk if item.dig_clk else None)
                    adc_bridges.append(adc)
                elif ct == 'DAC_BRIDGE':
                    node = item.dig_analog_node or pin_node.get(f"{item.name}__p1", '')
                    dac  = DAC(item.name, bits=item.dig_bits_adc,
                               vref=item.dig_vref, out_node=node,
                               clk=item.dig_clk if item.dig_clk else None)
                    dac_bridges.append(dac)
                elif ct == 'COMPARATOR':
                    node = item.dig_analog_node or pin_node.get(f"{item.name}__p1", '')
                    cmp  = ComparatorBridge(item.name, node_pos=node)
                    adc_bridges.append(cmp)
                elif ct == 'PWM':
                    pwm  = PWMBridge(item.name,
                                     pwm_net=f'{item.name}_IN',
                                     vmax=item.dig_vref)
                    dac_bridges.append(pwm)
            except Exception as e:
                errors.append(f"{item.name} (digital): {e}")

        # ── Lanzar co-simulación ─────────────────────────────────────────
        iface = MixedSignalInterface(self.solver, dsim, analog_comps)
        for adc in adc_bridges:
            iface.add_adc(adc)
        for dac in dac_bridges:
            if hasattr(dac, 'pwm_net'):   # PWMBridge
                iface.add_pwm(dac)
            elif hasattr(dac, 'input_nets'):  # DAC
                iface.add_dac(dac)
            else:
                iface.add_comparator(dac)

        result = iface.run_iterative(
            t_stop=t_stop,
            dt_chunk=dt_chunk,
            dt_analog=min(dt_chunk / 10, 1e-6),
        )

        # ── Mostrar resultados ───────────────────────────────────────────
        out = ["═══ SIMULACIÓN MIXTA ═══", ""]
        if not result.success:
            out.append(f"✗ Error: {result.error}")
        else:
            out.append(result.summary())
            out.append("")
            out.append("── Voltajes analógicos (valor final) ──")
            if len(result.t):
                for node, arr in sorted(result.analog_voltages.items()):
                    if len(arr):
                        out.append(f"  V({node}) = {arr[-1]:+.4f} V")
            if result.digital_waveforms:
                out.append("")
                out.append("── Señales digitales (valor final) ──")
                for net, hist in sorted(result.digital_waveforms.items()):
                    if hist:
                        out.append(f"  {net} = {hist[-1][1]}")
            if result.adc_samples:
                out.append("")
                out.append("── Muestras ADC (último código) ──")
                for name, samples in result.adc_samples.items():
                    if samples:
                        out.append(f"  {name}: code={samples[-1][1]}")
            if result.warnings:
                out.append("")
                for w in result.warnings:
                    out.append(f"  ⚠ {w}")

        if errors:
            out.append("\n── Errores de construcción ──")
            out.extend([f"  ✗ {e}" for e in errors])

        self.results_text.setPlainText('\n'.join(out))

    def _show_power_triangle(self):
        if not self._last_ac_result:
            return
        dlg = PowerTriangleDialog(self._last_ac_result, COLORS, parent=self)
        dlg.exec()

    def _on_logic_state_toggled(self, item):
        """Re-ejecuta la simulación cuando un LOGIC_STATE cambia de estado."""
        if self._sim_running:
            self._run_simulation_dc(silent=True)
        else:
            # Aunque no esté en modo continuo, actualizar igual (one-shot silencioso)
            pin_node = self.scene.extract_netlist()
            std_name = DEFAULT_LOGIC_STANDARD
            std = LOGIC_STANDARDS.get(std_name, DEFAULT_STANDARD)
            # Calcular voltaje del estado y actualizar display del prop_table
            v = std.Voh if item.value else std.Vol
            self._on_component_selected(item)

    # ──────────────────────────────────────────────────────────────────
    # Potenciómetro: control en tiempo real desde el panel derecho
    # ──────────────────────────────────────────────────────────────────
    def _on_pot_slider(self, value: int):
        """El usuario movió el slider → actualiza el wiper en vivo."""
        if self._selected_pot is None:
            return
        self._selected_pot.pot_wiper = value / 1000.0
        self._selected_pot.update()           # repinta el componente con la flecha movida
        self._update_pot_label()
        # Si la simulación continua está corriendo, el siguiente tick
        # recalcula con el nuevo valor automáticamente.

    def _update_pot_label(self):
        if self._selected_pot is None:
            return
        w   = self._selected_pot.pot_wiper
        Rt  = max(self._selected_pot.value, 1.0)
        Ref = Rt * w
        # Formato bonito de R efectiva
        if Ref >= 1e6:   r_str = f"{Ref/1e6:.2f} MΩ"
        elif Ref >= 1e3: r_str = f"{Ref/1e3:.2f} kΩ"
        else:            r_str = f"{Ref:.2f} Ω"
        self.pot_value_label.setText(f"{w*100:.1f}% — R = {r_str}")

    def _on_component_selected(self, item):
        self.prop_table.setRowCount(0)
        # ── Slider del potenciómetro ──────────────────────────────────────
        if item is not None and item.comp_type == 'POT':
            self._selected_pot = item
            self.pot_slider.blockSignals(True)
            self.pot_slider.setValue(int(item.pot_wiper * 1000))
            self.pot_slider.blockSignals(False)
            self.pot_panel.setVisible(True)
            self._update_pot_label()
        else:
            self._selected_pot = None
            self.pot_panel.setVisible(False)

        if item is None:
            return

        # Nodos automáticos desde cables
        pin_node = self.scene.extract_netlist()

        def _node_display(manual, auto_key):
            v = manual.strip() if manual.strip() else pin_node.get(auto_key, '—')
            return v if manual.strip() else f"{v} (auto)"

        rows = [
            ("Tipo",     item.comp_type),
            ("Nombre",   item.name),
            ("Valor",    f"{item.value} {item.unit}"),
            ("Rotación", f"{item._angle}°"),
        ]

        if item.comp_type in DIGITAL_GATE_TYPES:
            n_in = item.dig_inputs if item.comp_type != 'NOT' else 1
            rows.append(("Salida (Y)", _node_display(item.node1, f"{item.name}__p1")))
            rows.append(("Entrada 1 (A)", _node_display(item.node2, f"{item.name}__p2")))
            if n_in >= 2:
                rows.append(("Entrada 2 (B)", _node_display(
                    item.node3 if hasattr(item,'node3') else '', f"{item.name}__p3")))
            for i in range(2, n_in):
                _extra = getattr(item, 'dig_input_nodes', [])
                _manual = _extra[i-2] if len(_extra) > i-2 else ''
                rows.append((f"Entrada {i+1}", _node_display(_manual, f"{item.name}__p{i+2}")))
            rows.append(("Nº entradas", str(n_in)))
            rows.append(("Retardo tpd", f"{item.dig_tpd_ns} ns"))
        elif item.comp_type in DIGITAL_FLIPFLOP_TYPES:
            rows.append(("Salida Q",    _node_display(item.node1, f"{item.name}__p1")))
            rows.append(("Dato D / J",  _node_display(item.node2, f"{item.name}__p2")))
            rows.append(("CLK",         _node_display(
                item.node3 if hasattr(item,'node3') else '', f"{item.name}__p3")))
        elif item.comp_type == 'LOGIC_STATE':
            rows.append(("Salida",  _node_display(item.node1, f"{item.name}__p1")))
            rows.append(("Estado",  "1 (HIGH)" if item.value else "0 (LOW)"))
        elif item.comp_type in FOUR_PIN_NODE_LABELS:
            lbls = FOUR_PIN_NODE_LABELS[item.comp_type]
            rows.append((lbls[0], _node_display(item.node1, f"{item.name}__p1")))
            rows.append((lbls[1], _node_display(item.node2, f"{item.name}__p2")))
            rows.append((lbls[2], _node_display(
                item.node3 if hasattr(item, 'node3') else '', f"{item.name}__p3")))
            rows.append((lbls[3], _node_display(
                item.node4 if hasattr(item, 'node4') else '', f"{item.name}__p4")))
        else:
            lbl1, lbl2, lbl3 = COMPONENT_NODE_LABELS.get(
                item.comp_type, DEFAULT_NODE_LABELS)
    
            # Para fuentes, invertir el orden de visualización
            # porque node1=pin izquierdo=negativo, node2=pin derecho=positivo
            if item.comp_type in ('V', 'VAC', 'I'):
                rows.append(("Nodo +", _node_display(item.node2, f"{item.name}__p2")))  # Nodo +
                rows.append(("Nodo -", _node_display(item.node1, f"{item.name}__p1")))  # Nodo −
            else:
                rows.append((lbl1, _node_display(item.node1, f"{item.name}__p1")))
                rows.append((lbl2, _node_display(item.node2, f"{item.name}__p2")))
            if lbl3 is not None:
                rows.append((lbl3, _node_display(
                    item.node3 if hasattr(item,'node3') else '', f"{item.name}__p3")))
            if item.comp_type == 'Z':
                rows.append(("Modo Z", item.z_mode))
                if item.z_mode == 'rect':
                    rows.append(("Z", f"{item.z_real:.4g} {item.z_imag:+.4g}j Ω"))
                else:
                    rows.append(("Z", f"{item.z_mag:.4g} ∠{item.z_phase:.2f}° Ω"))

        for label, val in rows:
            r = self.prop_table.rowCount()
            self.prop_table.insertRow(r)
            self.prop_table.setItem(r, 0, QTableWidgetItem(label))
            self.prop_table.setItem(r, 1, QTableWidgetItem(str(val)))

    # ── Circuito de demo ─────────────────────────
    def _load_demo_circuit(self):
        """Carga un divisor de voltaje de ejemplo."""
        s = self.scene
        s.place_component('V',   QPointF(-120,   0), 'V1', 10.0, 'V',  '0', 'A')
        s.place_component('R',   QPointF(   0, -80), 'R1', 1000.0, 'Ω', 'A', 'B')
        s.place_component('R',   QPointF(   0,  80), 'R2', 1000.0, 'Ω', 'B', '0')
        s.place_component('GND', QPointF(-120,  80), 'GND1')
        self.results_text.setPlainText(
            "Circuito demo: divisor de voltaje\n"
            "V1=10V, R1=R2=1kΩ\n\n"
            "Esperado: V(B) = 5.0 V\n\n"
            "Presiona ▶ SIMULAR AUTO para verificar.\n\n"
            "Tip: doble-click sobre un componente\npara editar sus nodos y valores."
        )

    # ── Acciones ─────────────────────────────────
    def _new_circuit(self):
        reply = QMessageBox.question(
            self, "Nuevo circuito",
            "¿Descartar el circuito actual (todas las hojas)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._clear_all_sheets()
            self._current_file = None
            self.setWindowTitle("CircuitSim — Simulador de Circuitos")
            self._load_demo_circuit()

    def _clear_circuit(self):
        """Limpia solo la hoja activa."""
        for item in self.scene.components + self.scene.wires:
            self.scene.removeItem(item)
        self.scene.components.clear()
        self.scene.wires.clear()
        self.scene._comp_counter.clear()
        self.results_text.clear()

    def _clear_all_sheets(self):
        """Elimina todas las hojas y crea una nueva vacía."""
        for sheet in self._sheets:
            sc = sheet['scene']
            for item in sc.components + sc.wires:
                sc.removeItem(item)
            sc.components.clear()
            sc.wires.clear()
            sc._comp_counter.clear()
        self._sheets.clear()
        self.tab_widget.clear()
        self._add_sheet(name="Hoja 1")
        self.results_text.clear()
        self._current_file = None
        self.setWindowTitle("CircuitSim — Simulador de Circuitos")

    # ── Serialización de una hoja ─────────────────
    def _serialize_sheet(self, scene: CircuitScene) -> dict:
        sheet_data = {'components': [], 'wires': []}
        for item in scene.components:
            entry = {
                'type':  item.comp_type,
                'name':  item.name,
                'value': item.value,
                'unit':  item.unit,
                'node1': item.node1,
                'node2': item.node2,
                'node3': item.node3,
                'x':     item.pos().x(),
                'y':     item.pos().y(),
                'angle': item._angle,
            }
            if item.comp_type == 'VAC':
                entry['frequency'] = item.frequency
                entry['phase_deg'] = item.phase_deg
                entry['ac_mode']   = item.ac_mode
            if item.comp_type == 'LED':
                entry['led_color'] = item.led_color
            if item.comp_type == 'Z':
                entry['z_real']  = item.z_real
                entry['z_imag']  = item.z_imag
                entry['z_mag']   = item.z_mag
                entry['z_phase'] = item.z_phase
                entry['z_mode']  = item.z_mode
            if item.comp_type == 'POT':
                entry['pot_wiper'] = item.pot_wiper
            if item.comp_type == 'XFMR':
                entry['xfmr_ratio'] = item.xfmr_ratio
                entry['xfmr_imax']  = item.xfmr_imax
            if item.comp_type == 'BRIDGE':
                entry['bridge_vf'] = item.bridge_vf
            if item.comp_type in ComponentItem.FOUR_PIN_TYPES:
                entry['node4'] = item.node4
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
                entry['sheet_label'] = item.sheet_label
            if item.comp_type == 'CLK':
                entry['clk_running'] = item.clk_running
            sheet_data['components'].append(entry)

        for wire in scene.wires:
            line = wire.line()
            sheet_data['wires'].append({
                'x1': line.x1(), 'y1': line.y1(),
                'x2': line.x2(), 'y2': line.y2(),
            })
        return sheet_data

    def _load_sheet_data(self, scene: CircuitScene, sheet_data: dict):
        for c in sheet_data.get('components', []):
            item = scene.place_component(
                c['type'], QPointF(c['x'], c['y']),
                name=c['name'], value=c['value'],
                unit=c.get('unit', ''),
                node1=c.get('node1', ''),
                node2=c.get('node2', ''),
                node3=c.get('node3', '')
            )
            angle = c.get('angle', 0)
            if angle:
                item._angle = angle
                item.setRotation(angle)
            if c['type'] == 'VAC':
                item.frequency = c.get('frequency', 60.0)
                item.phase_deg = c.get('phase_deg', 0.0)
                item.ac_mode   = c.get('ac_mode', 'rms')
            if c['type'] == 'LED':
                item.led_color = c.get('led_color', 'red')
            if c['type'] == 'Z':
                item.z_real  = c.get('z_real',  100.0)
                item.z_imag  = c.get('z_imag',  0.0)
                item.z_mag   = c.get('z_mag',   100.0)
                item.z_phase = c.get('z_phase', 0.0)
                item.z_mode  = c.get('z_mode',  'rect')
            if c['type'] == 'POT' and 'pot_wiper' in c:
                item.pot_wiper = max(0.0, min(1.0, float(c['pot_wiper'])))
            if c['type'] == 'XFMR':
                item.xfmr_ratio = c.get('xfmr_ratio', 2.0)
                item.xfmr_imax  = c.get('xfmr_imax',  1.0)
            if c['type'] == 'BRIDGE':
                item.bridge_vf = c.get('bridge_vf', 0.7)
            if c['type'] in ComponentItem.FOUR_PIN_TYPES and 'node4' in c:
                item.node4 = c['node4']
            if c['type'] in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
                item.sheet_label = c.get('sheet_label', item.name)
            if c['type'] == 'CLK':
                item.clk_running = bool(c.get('clk_running', False))

        for w in sheet_data.get('wires', []):
            wire = WireItem(QPointF(w['x1'], w['y1']), QPointF(w['x2'], w['y2']))
            scene.addItem(wire)
            scene.wires.append(wire)

    # ── Guardar (.csin) ──────────────────────────
    def _save_circuit(self):
        path = self._current_file
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self, "Guardar circuito", "",
                "CircuitSim (*.csin);;Todos los archivos (*)"
            )
        if not path:
            return
        if not path.endswith('.csin'):
            path += '.csin'

        sheets = []
        for sheet in self._sheets:
            sd = self._serialize_sheet(sheet['scene'])
            sd['name'] = sheet['name']
            sheets.append(sd)

        data = {'version': '2.0', 'sheets': sheets}

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self._current_file = path
        self.setWindowTitle(f"CircuitSim — {os.path.basename(path)}")
        self.statusBar().showMessage(f"Guardado: {path}")

    # ── Guardar como (.csin) ─────────────────────
    def _save_circuit_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Guardar circuito como", "",
            "CircuitSim (*.csin);;Todos los archivos (*)"
        )
        if not path:
            return
        if not path.endswith('.csin'):
            path += '.csin'
        self._current_file = path
        self._save_circuit()

    # ── Abrir (.csin) ────────────────────────────
    def _open_circuit(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Abrir circuito", "",
            "CircuitSim (*.csin);;Todos los archivos (*)"
        )
        if not path:
            return

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir el archivo:\n{e}")
            return

        # Compatibilidad con formato v1 (una sola hoja)
        if 'sheets' not in data:
            data = {'version': '2.0', 'sheets': [{
                'name': 'Hoja 1',
                'components': data.get('components', []),
                'wires': data.get('wires', []),
            }]}

        self._clear_all_sheets()
        self._sheets.clear()
        self.tab_widget.clear()

        for sd in data['sheets']:
            name = sd.get('name', f'Hoja {len(self._sheets)+1}')
            self._add_sheet(name=name)
            scene = self._sheets[-1]['scene']
            self._load_sheet_data(scene, sd)

        self._current_file = path
        self.setWindowTitle(f"CircuitSim — {os.path.basename(path)}")
        self.statusBar().showMessage(f"Abierto: {path}")

    # ── Exportar netlist SPICE (.net) ────────────
    def _export_spice(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar netlist SPICE", "",
            "SPICE Netlist (*.net);;Todos los archivos (*)"
        )
        if not path:
            return
        if not path.endswith('.net'):
            path += '.net'

        lines = []
        lines.append(f"* CircuitSim — Netlist exportado")
        lines.append(f"* Archivo: {os.path.basename(path)}")
        lines.append("")

        type_map = {'R': 'R', 'C': 'C', 'L': 'L', 'V': 'V', 'I': 'I', 'Z': 'Z'}

        for item in self.scene.components:
            if item.comp_type not in type_map:
                continue
            n1 = item.node1.strip() or '?'
            n2 = item.node2.strip() or '0'
            val = item.value

            # Formatear valor en notación SPICE
            if abs(val) >= 1e6:
                val_str = f"{val/1e6:.6g}Meg"
            elif abs(val) >= 1e3:
                val_str = f"{val/1e3:.6g}k"
            elif abs(val) >= 1:
                val_str = f"{val:.6g}"
            elif abs(val) >= 1e-3:
                val_str = f"{val*1e3:.6g}m"
            elif abs(val) >= 1e-6:
                val_str = f"{val*1e6:.6g}u"
            elif abs(val) >= 1e-9:
                val_str = f"{val*1e9:.6g}n"
            else:
                val_str = f"{val:.6g}"

            # Para impedancias, exportar como R + jX
            if item.comp_type == 'Z':
                if item.z_mode == 'rect':
                    val_str = f"{item.z_real:.6g}"
                    if abs(item.z_imag) > 1e-12:
                        sign = '+' if item.z_imag >= 0 else ''
                        val_str += f"{sign}{item.z_imag:.6g}j"
                else:
                    ph_rad = math.radians(item.z_phase)
                    zr = item.z_mag * math.cos(ph_rad)
                    zx = item.z_mag * math.sin(ph_rad)
                    val_str = f"{zr:.6g}"
                    if abs(zx) > 1e-12:
                        sign = '+' if zx >= 0 else ''
                        val_str += f"{sign}{zx:.6g}j"

            lines.append(f"{item.name} {n1} {n2} {val_str}")

        lines.append("")
        lines.append(".op")
        lines.append(".end")

        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        self.statusBar().showMessage(f"Netlist exportado: {path}")
        QMessageBox.information(
            self, "Exportado",
            f"Netlist SPICE guardado en:\n{path}\n\nCompatible con LTspice y ngspice."
        )

    def _reset_zoom(self):
        self.view.resetTransform()
        self.view.centerOn(0, 0)

    def _wheel_zoom(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.view.scale(factor, factor)


# ══════════════════════════════════════════════════════════════
# ANALIZADOR DE CIRCUITOS DIGITALES
# ══════════════════════════════════════════════════════════════
# Quine-McCluskey con soporte de don't cares: implementación independiente
# y ligera (sin dependencias externas).  Se utiliza desde el diálogo
# CircuitAnalyzerDialog para reducir tablas de verdad a SOP / POS mínimas.

def _qm_combine(a: str, b: str):
    """Combina dos cubos si difieren en exactamente 1 bit; retorna el cubo
    fusionado (con '-' en la posición que difería) o None."""
    if len(a) != len(b):
        return None
    diff = 0
    out = []
    for x, y in zip(a, b):
        if x != y:
            diff += 1
            if diff > 1:
                return None
            out.append('-')
        else:
            out.append(x)
    return ''.join(out) if diff == 1 else None


def _qm_prime_implicants(terms, num_vars: int):
    """Quine-McCluskey: encuentra todos los implicantes primos del conjunto
    de términos (mintérminos + don't cares).  Cada cubo es un string binario
    de longitud num_vars con caracteres en {'0','1','-'}."""
    if not terms:
        return []
    cubes = {format(t, f'0{num_vars}b') for t in terms}
    primes = set()
    while cubes:
        groups = {}
        for c in cubes:
            ones = c.replace('-', '').count('1')
            groups.setdefault(ones, []).append(c)
        used = set()
        next_cubes = set()
        keys = sorted(groups)
        for i in range(len(keys) - 1):
            for a in groups[keys[i]]:
                for b in groups[keys[i + 1]]:
                    combined = _qm_combine(a, b)
                    if combined is not None:
                        used.add(a); used.add(b)
                        next_cubes.add(combined)
        for c in cubes:
            if c not in used:
                primes.add(c)
        cubes = next_cubes
    return list(primes)


def _qm_covers(prime: str, term: int, num_vars: int) -> bool:
    bin_t = format(term, f'0{num_vars}b')
    return all(p == '-' or p == m for p, m in zip(prime, bin_t))


def _qm_minimum_cover(primes, must_cover, num_vars: int):
    """Cobertura mínima de must_cover usando los primes disponibles.
    Algoritmo: implicantes esenciales primero, luego greedy para el resto."""
    if not must_cover:
        return []
    coverage = {p: {m for m in must_cover if _qm_covers(p, m, num_vars)}
                for p in primes}
    selected, remaining = set(), set(must_cover)
    while remaining:
        # Esenciales: mintérminos cubiertos por solo un implicante primo
        essential_added = False
        for m in list(remaining):
            covers_m = [p for p in primes if m in coverage[p]]
            if len(covers_m) == 1:
                p = covers_m[0]
                if p not in selected:
                    selected.add(p)
                    essential_added = True
                remaining -= coverage[p]
        if remaining and not essential_added:
            # Greedy: el prime que cubra más mintérminos restantes
            best, best_n = None, -1
            for p in primes:
                n = len(coverage[p] & remaining)
                if n > best_n:
                    best, best_n = p, n
            if best is None or best_n <= 0:
                break
            selected.add(best)
            remaining -= coverage[best]
    return list(selected)


def _qm_literal_count(prime: str) -> int:
    return sum(1 for bit in prime if bit != '-')


def _qm_minimum_cover_exact(primes, must_cover, num_vars: int):
    """Exact cover used by the digital analyzer for stable SOP/POS output."""
    if not must_cover:
        return []

    primes = sorted(set(primes), key=lambda p: (_qm_literal_count(p), p))
    coverage = {
        p: {m for m in must_cover if _qm_covers(p, m, num_vars)}
        for p in primes
    }
    selected, remaining = set(), set(must_cover)

    while remaining:
        essential_added = False
        for m in list(remaining):
            covers_m = [p for p in primes if m in coverage[p]]
            if len(covers_m) == 1:
                p = covers_m[0]
                selected.add(p)
                essential_added = True
                remaining -= coverage[p]
        if not essential_added:
            break

    if remaining:
        candidates = [p for p in primes if coverage[p] & remaining and p not in selected]
        best_combo = None
        best_score = None
        for r in range(1, len(candidates) + 1):
            found_at_size = False
            for combo in combinations(candidates, r):
                covered = set()
                for p in combo:
                    covered |= coverage[p]
                if not remaining <= covered:
                    continue
                found_at_size = True
                score = (r, sum(_qm_literal_count(p) for p in combo), tuple(combo))
                if best_score is None or score < best_score:
                    best_combo = combo
                    best_score = score
            if found_at_size:
                break
        if best_combo:
            selected.update(best_combo)

    return sorted(selected, key=lambda p: (_qm_literal_count(p), p))


def _prime_to_sop_term(prime: str, var_names) -> str:
    parts = []
    for bit, name in zip(prime, var_names):
        if bit == '0':
            parts.append(f"{name}'")
        elif bit == '1':
            parts.append(name)
    return ' · '.join(parts) if parts else '1'


def _prime_to_pos_term(prime: str, var_names) -> str:
    """Para POS, los implicantes representan los maxtérminos del original;
    la conversión invierte cada literal porque corresponde al complemento."""
    parts = []
    for bit, name in zip(prime, var_names):
        if bit == '0':
            parts.append(name)
        elif bit == '1':
            parts.append(f"{name}'")
    if not parts:
        return '0'
    return '(' + ' + '.join(parts) + ')'


def _sop_cover(minterms, dont_cares, num_vars: int):
    if not minterms:
        return []
    if len(set(minterms) | set(dont_cares)) >= 2 ** num_vars and len(minterms) == 2 ** num_vars:
        return ['-' * num_vars]
    primes = _qm_prime_implicants(list(set(minterms) | set(dont_cares)), num_vars)
    return _qm_minimum_cover_exact(primes, list(minterms), num_vars)


def _pos_cover(minterms, dont_cares, num_vars: int):
    all_idx = set(range(2 ** num_vars))
    maxterms = sorted(all_idx - set(minterms) - set(dont_cares))
    if not maxterms:
        return []
    if len(maxterms) == 2 ** num_vars:
        return ['-' * num_vars]
    primes = _qm_prime_implicants(list(set(maxterms) | set(dont_cares)), num_vars)
    return _qm_minimum_cover_exact(primes, list(maxterms), num_vars)


def simplify_sop(minterms, dont_cares, var_names):
    n = len(var_names)
    cover = _sop_cover(minterms, dont_cares, n)
    if not cover:
        return '0'
    if cover == ['-' * n]:
        return '1'
    return ' + '.join(_prime_to_sop_term(p, var_names) for p in cover)


def simplify_pos(minterms, dont_cares, var_names):
    n = len(var_names)
    cover = _pos_cover(minterms, dont_cares, n)
    if not cover:
        return '1'
    if cover == ['-' * n]:
        return '0'
    return ' * '.join(_prime_to_pos_term(p, var_names) for p in cover)


NOTATION_LABELS = {
    'math_bar': "Matematica (barrita)",
    'math_prime': "Matematica alternativa (')",
    'logic_words': "Logica",
    'logic_symbols': "Logica alternativa",
    'program_bool': "Programando con booleanos",
    'program_bits': "Programando con bits",
}


def _overline(text: str, rich: bool = False) -> str:
    if rich:
        return f'<span style="text-decoration: overline;">{html.escape(text)}</span>'
    return f"bar({text})"


def _notation_ops(notation: str, form: str, rich: bool = False):
    if notation == 'logic_words':
        return {
            'not': lambda name: f"¬{name}",
            'and': ' ∧ ',
            'or':  ' ∨ ',
            'sop_join': ' ∨ ',
            'pos_join': ' ∧ ',
        }
    if notation == 'logic_symbols':
        return {
            'not': lambda name: f"~{name}",
            'and': ' ∧ ',
            'or':  ' ∨ ',
            'sop_join': ' ∨ ',
            'pos_join': ' ∧ ',
        }
    if notation == 'program_bool':
        return {
            'not': lambda name: f"!{name}",
            'and': ' && ',
            'or':  ' | ',
            'sop_join': ' | ',
            'pos_join': ' && ',
        }
    if notation == 'program_bits':
        return {
            'not': lambda name: f"~{name}",
            'and': ' & ',
            'or':  ' | ',
            'sop_join': ' | ',
            'pos_join': ' & ',
        }
    if notation == 'math_bar':
        return {
            'not': lambda name: _overline(name, rich=rich),
            'and': ' · ',
            'or':  ' + ',
            'sop_join': ' + ',
            'pos_join': ' · ',
        }
    return {
        'not': lambda name: f"{name}'",
        'and': ' · ',
        'or':  ' + ',
        'sop_join': ' + ',
        'pos_join': ' · ',
    }


def _format_sop_cover(cover, var_names, notation: str, rich: bool = False) -> str:
    n = len(var_names)
    if not cover:
        return '0'
    if cover == ['-' * n]:
        return '1'
    ops = _notation_ops(notation, 'sop', rich=rich)
    terms = []
    for prime in cover:
        lits = []
        for bit, name in zip(prime, var_names):
            if bit == '0':
                lits.append(ops['not'](name))
            elif bit == '1':
                lits.append(html.escape(name) if rich else name)
        terms.append(ops['and'].join(lits) if lits else '1')
    return ops['sop_join'].join(terms)


def _format_pos_cover(cover, var_names, notation: str, rich: bool = False) -> str:
    n = len(var_names)
    if not cover:
        return '1'
    if cover == ['-' * n]:
        return '0'
    ops = _notation_ops(notation, 'pos', rich=rich)
    clauses = []
    for prime in cover:
        lits = []
        for bit, name in zip(prime, var_names):
            if bit == '0':
                lits.append(html.escape(name) if rich else name)
            elif bit == '1':
                lits.append(ops['not'](name))
        clauses.append('(' + ops['or'].join(lits) + ')' if lits else '0')
    return ops['pos_join'].join(clauses)


def _gray_codes(bits: int) -> list:
    if bits <= 0:
        return ['']
    codes = ['0', '1']
    for _ in range(1, bits):
        codes = ['0' + c for c in codes] + ['1' + c for c in reversed(codes)]
    return codes


KMAP_GROUPS_ROLE = int(Qt.ItemDataRole.UserRole) + 21


class TruthTableSeparatorDelegate(QStyledItemDelegate):
    def __init__(self, separator_column: int = -1, color: str = '#f5a623',
                 parent=None):
        super().__init__(parent)
        self.separator_column = separator_column
        self.color = color

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if index.column() != self.separator_column:
            return
        painter.save()
        pen = QPen(QColor(self.color), 3)
        painter.setPen(pen)
        x = option.rect.right()
        painter.drawLine(x, option.rect.top(), x, option.rect.bottom())
        painter.restore()


class KMapGroupDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        groups = index.data(KMAP_GROUPS_ROLE) or []
        if groups:
            painter.save()
            for i, group in enumerate(groups[:4]):
                color = QColor(group['color'])
                color.setAlpha(42)
                painter.fillRect(option.rect.adjusted(1, 1, -1, -1), color)
            painter.restore()

        super().paint(painter, option, index)

        if not groups:
            return
        painter.save()
        for i, group in enumerate(groups[:4]):
            color = QColor(group['color'])
            color.setAlpha(230)
            pen = QPen(color, 2)
            painter.setPen(pen)
            inset = 2 + i * 3
            painter.drawRoundedRect(option.rect.adjusted(inset, inset, -inset, -inset),
                                    4, 4)
        painter.restore()


class AutoBuildCircuitDialog(QDialog):
    def __init__(self, default_sheet_name: str = "Circuito simplificado",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Armar circuito automaticamente")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.sheet_name_edit = QLineEdit(default_sheet_name)
        form.addRow("Nombre de la hoja:", self.sheet_name_edit)
        layout.addLayout(form)

        self.two_input_only_check = QCheckBox("Usar compuertas unicamente de 2 entradas")
        self.nand_only_check = QCheckBox("Armar solo usando compuertas NAND")
        layout.addWidget(self.two_input_only_check)
        layout.addWidget(self.nand_only_check)

        hint = QLabel(
            "<small>Estas opciones definen la topologia que usara el generador "
            "al convertir las ecuaciones minimizadas en una hoja nueva.</small>")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_options(self) -> dict:
        return {
            'sheet_name': self.sheet_name_edit.text().strip(),
            'two_input_only': self.two_input_only_check.isChecked(),
            'nand_only': self.nand_only_check.isChecked(),
        }


class CircuitAnalyzerDialog(QDialog):
    """Analizador de circuitos digitales.

    Pestañas:
      1. Entradas / Salidas — definir variables del circuito.
      2. Tabla de verdad    — llenar con 0/1/X; reduce filas todo-X.
      3. Ecuaciones         — SOP / POS minimizados; botón futuro
                              "Armar circuito automáticamente".
      4. Mapa de Karnaugh   — placeholder para una futura implementación
                              que también permita ingresar K-maps y
                              obtener SOP/POS.
    """

    def __init__(self, parent=None, initial_state: dict = None):
        super().__init__(parent)
        self.setWindowTitle("Analizar Circuito Digital")
        self.resize(820, 580)

        # Estado del modelo
        self.var_inputs:  list = ['A', 'B']
        self.var_outputs: list = ['Y']
        self.truth_data: dict = {}
        self._last_simplification: dict = {}
        self._last_highlight: str = 'sop'
        self._all_outputs_label = "Todas las salidas"
        self._notation_id = 'math_prime'
        self._kmap_groups = []
        
        self._build_ui()
        
        # Restaurar estado si existe
        if initial_state:
            self._restore_state(initial_state)

    def _restore_state(self, state: dict):
        """Restaura variables, tabla, ecuaciones y configuración guardadas."""
        if not state:
            return

        # Bloquear señales para evitar reconstrucciones en cascada
        self.inputs_list.blockSignals(True)
        self.outputs_list.blockSignals(True)
        self.notation_selector.blockSignals(True)
        self.truth_table.blockSignals(True)

        # Recuperar datos
        self.var_inputs = list(state.get('var_inputs', ['A', 'B']))
        self.var_outputs = list(state.get('var_outputs', ['Y']))
        self.truth_data = dict(state.get('truth_data', {}))
        self._last_simplification = dict(state.get('_last_simplification', {}))
        self._last_highlight = state.get('_last_highlight', 'sop')
        self._notation_id = state.get('_notation_id', 'math_prime')

        # Actualizar listas de I/O
        self.inputs_list.clear()
        self.inputs_list.addItems(self.var_inputs)
        self.outputs_list.clear()
        self.outputs_list.addItems(self.var_outputs)

        # Restaurar notación
        idx = self.notation_selector.findData(self._notation_id)
        self.notation_selector.setCurrentIndex(idx if idx >= 0 else 1)

        # Desbloquear
        self.inputs_list.blockSignals(False)
        self.outputs_list.blockSignals(False)
        self.notation_selector.blockSignals(False)
        self.truth_table.blockSignals(False)

        # Reconstruir UI según datos recuperados
        if self.var_inputs and self.var_outputs:
            self._rebuild_truth_table()

        if self._last_simplification:
            self._populate_eqs_tab(highlight=self._last_highlight)
            self.tabs.setCurrentIndex(2)  # Saltar a Ecuaciones si hay análisis previo

        if self.tabs.currentIndex() == 3:
            self._refresh_kmap()

    def closeEvent(self, event):
        """Guarda automáticamente el estado en MainWindow antes de cerrar."""
        if self.parent():
            self.parent()._analyzer_state = {
                'var_inputs': self.var_inputs,
                'var_outputs': self.var_outputs,
                'truth_data': self.truth_data,
                '_last_simplification': self._last_simplification,
                '_last_highlight': self._last_highlight,
                '_notation_id': self._notation_id,
            }
        event.accept()
        
    # ── UI principal ──────────────────────────────────────────────────────
    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_io_tab(),    "Entradas y Salidas")
        self.tabs.addTab(self._build_truth_tab(), "Tabla de Verdad")
        self.tabs.addTab(self._build_eqs_tab(),   "Ecuaciones simplificadas")
        self.tabs.addTab(self._build_kmap_tab(),  "Mapa de Karnaugh")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)

        # Botón cerrar
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ── Pestaña 1: I/O ────────────────────────────────────────────────────
    def _build_io_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        h = QHBoxLayout()

        def col(title, list_widget, edit_widget, btn_add, btn_rm):
            box = QGroupBox(title)
            v = QVBoxLayout(box)
            v.addWidget(list_widget)
            v.addWidget(edit_widget)
            row = QHBoxLayout()
            row.addWidget(btn_add); row.addWidget(btn_rm)
            v.addLayout(row)
            return box

        # Entradas
        self.inputs_list = QListWidget()
        self.inputs_list.addItems(self.var_inputs)
        self.in_edit  = QLineEdit(); self.in_edit.setPlaceholderText("Ej: A, B, EN")
        btn_add_in = QPushButton("Añadir entrada")
        btn_rm_in  = QPushButton("Quitar seleccionada")
        btn_add_in.clicked.connect(self._add_input)
        btn_rm_in.clicked.connect(self._remove_input)
        self.in_edit.returnPressed.connect(self._add_input)

        # Salidas
        self.outputs_list = QListWidget()
        self.outputs_list.addItems(self.var_outputs)
        self.out_edit = QLineEdit(); self.out_edit.setPlaceholderText("Ej: Y, S, COUT")
        btn_add_out = QPushButton("Añadir salida")
        btn_rm_out  = QPushButton("Quitar seleccionada")
        btn_add_out.clicked.connect(self._add_output)
        btn_rm_out.clicked.connect(self._remove_output)
        self.out_edit.returnPressed.connect(self._add_output)

        h.addWidget(col("Entradas",
                        self.inputs_list, self.in_edit, btn_add_in, btn_rm_in))
        h.addWidget(col("Salidas",
                        self.outputs_list, self.out_edit, btn_add_out, btn_rm_out))

        info = QLabel(
            "<small>Define los nombres de variables.  "
            "Al pasar a la pestaña <b>Tabla de Verdad</b> se generarán "
            "automáticamente las 2<sup>N</sup> filas correspondientes."
            "</small>")
        info.setWordWrap(True)
        outer.addLayout(h)
        outer.addWidget(info)
        return w

    def _parse_variable_names(self, text: str) -> list:
        names = [n for n in re.split(r'[\s,;]+', text.strip()) if n]
        valid = []
        invalid = []
        for name in names:
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
                valid.append(name)
            else:
                invalid.append(name)
        if invalid:
            QMessageBox.warning(
                self, "Nombre invalido",
                "Usa nombres como A, B, EN, S0 o COUT.")
        return valid

    def _add_variables(self, edit: QLineEdit, target: list, widget: QListWidget):
        added = False
        for name in self._parse_variable_names(edit.text()):
            if name in self.var_inputs or name in self.var_outputs:
                QMessageBox.warning(self, "Nombre duplicado",
                                    f"La variable '{name}' ya existe.")
                continue
            target.append(name)
            widget.addItem(name)
            added = True
        if added:
            edit.clear()
            self._last_simplification.clear()

    def _add_input(self):
        self._add_variables(self.in_edit, self.var_inputs, self.inputs_list)

    def _remove_input(self):
        row = self.inputs_list.currentRow()
        if row < 0:
            return
        name = self.var_inputs.pop(row)
        self.inputs_list.takeItem(row)
        # Limpiar truth_data inválido (cambia el número de filas)
        self.truth_data.clear()
        self._last_simplification.clear()

    def _add_output(self):
        self._add_variables(self.out_edit, self.var_outputs, self.outputs_list)

    def _remove_output(self):
        row = self.outputs_list.currentRow()
        if row < 0:
            return
        name = self.var_outputs.pop(row)
        self.outputs_list.takeItem(row)
        # Eliminar las celdas asociadas a esa salida
        for k in list(self.truth_data.keys()):
            if k[0] == name:
                del self.truth_data[k]
        self._last_simplification.clear()

    # ── Pestaña 2: Tabla de verdad ────────────────────────────────────────
    def _build_truth_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        info = QLabel(
            "<small>Llena cada celda de salida con <b>0</b>, <b>1</b> o "
            "<b>X</b> (don't care).  Las filas donde todas las salidas son "
            "<b>X</b> se ocultan automáticamente — no afectan a la "
            "minimización.</small>")
        info.setWordWrap(True)
        v.addWidget(info)

        self.truth_table = QTableWidget()
        self.truth_table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked |
            QTableWidget.EditTrigger.SelectedClicked)
        self._truth_delegate = TruthTableSeparatorDelegate(
            color=COLORS.get('comp_sel', '#f5a623'),
            parent=self.truth_table)
        self.truth_table.setItemDelegate(self._truth_delegate)
        self.truth_table.itemChanged.connect(self._on_truth_cell_changed)
        v.addWidget(self.truth_table)

        # Selector de salida a simplificar
        row = QHBoxLayout()
        self.truth_count_label = QLabel("")
        row.addWidget(self.truth_count_label)
        row.addStretch(1)
        row.addWidget(QLabel("Salida a simplificar:"))
        self.output_selector = QComboBox()
        row.addWidget(self.output_selector)
        v.addLayout(row)

        # Botones de minimización
        btn_row = QHBoxLayout()
        self.btn_sop = QPushButton("→ Mintérminos (SOP)")
        self.btn_pos = QPushButton("→ Maxtérminos (POS)")
        self.btn_sop.clicked.connect(lambda: self._simplify_and_show('sop'))
        self.btn_pos.clicked.connect(lambda: self._simplify_and_show('pos'))
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_sop)
        btn_row.addWidget(self.btn_pos)
        v.addLayout(btn_row)

        return w

    def _rebuild_truth_table(self):
        """Reconstruye la tabla de verdad según las variables definidas."""
        n_in  = len(self.var_inputs)
        n_out = len(self.var_outputs)
        if n_in == 0 or n_out == 0:
            self.truth_table.clear()
            self.truth_table.setRowCount(0)
            self.truth_table.setColumnCount(0)
            self.truth_count_label.setText("Filas activas: 0/0")
            self.output_selector.clear()
            return

        n_rows = 2 ** n_in
        # Bloquear señales mientras (re)llenamos
        self.truth_table.blockSignals(True)
        self.truth_table.clear()
        self.truth_table.setRowCount(n_rows)
        self.truth_table.setColumnCount(n_in + n_out + 1)   # +1 → columna #
        headers = ['#'] + list(self.var_inputs) + list(self.var_outputs)
        self.truth_table.setHorizontalHeaderLabels(headers)
        self._truth_delegate.separator_column = n_in
        input_header = QBrush(QColor(COLORS.get('grid', '#16213e')))
        output_header = QBrush(QColor(COLORS.get('toolbar', '#0f3460')))
        separator_brush = QBrush(QColor(COLORS.get('comp_sel', '#f5a623')))
        for c in range(self.truth_table.columnCount()):
            header_item = self.truth_table.horizontalHeaderItem(c)
            if header_item is None:
                continue
            if c == 0:
                header_item.setBackground(QBrush(QColor(COLORS.get('panel', '#16213e'))))
            elif c <= n_in:
                header_item.setBackground(input_header)
            else:
                header_item.setBackground(output_header)
            header_item.setForeground(QBrush(QColor(COLORS.get('text', '#e0e0e0'))))
        if n_in > 0:
            sep_header = self.truth_table.horizontalHeaderItem(n_in)
            if sep_header is not None:
                sep_header.setBackground(separator_brush)

        for r in range(n_rows):
            # Columna #: índice del mintérmino
            num_item = QTableWidgetItem(str(r))
            num_item.setFlags(num_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.truth_table.setItem(r, 0, num_item)
            # Columnas de entrada (binario, no editables)
            bin_str = format(r, f'0{n_in}b')
            for c, b in enumerate(bin_str):
                it = QTableWidgetItem(b)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.truth_table.setItem(r, c + 1, it)
            # Columnas de salida (editables, recuperar valor previo si existe)
            for j, oname in enumerate(self.var_outputs):
                key = (oname, r)
                val = self.truth_data.get(key, '0')
                it = QTableWidgetItem(val)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.truth_table.setItem(r, n_in + 1 + j, it)

        self.truth_table.resizeColumnsToContents()
        self.truth_table.blockSignals(False)
        self._refresh_hidden_rows()

        # Refrescar combo de salida a simplificar
        self.output_selector.blockSignals(True)
        self.output_selector.clear()
        self.output_selector.addItem(self._all_outputs_label)
        self.output_selector.addItems(self.var_outputs)
        self.output_selector.blockSignals(False)

    def _on_truth_cell_changed(self, item):
        """Normaliza el contenido a 0/1/X y oculta filas todo-X."""
        n_in = len(self.var_inputs)
        col = item.column()
        if col < n_in + 1:    # columna # o entrada
            return
        out_idx = col - (n_in + 1)
        if out_idx < 0 or out_idx >= len(self.var_outputs):
            return
        oname = self.var_outputs[out_idx]
        row = item.row()
        text = item.text().strip().lower()
        if text in ('1', 'true', 'high', 'h'):
            normalized = '1'
        elif text in ('x', 'd', 'dc', '-', '?'):
            normalized = 'X'
        else:
            normalized = '0'
        self.truth_data[(oname, row)] = normalized
        # Re-mostrar el valor normalizado (sin disparar el slot recursivamente)
        self.truth_table.blockSignals(True)
        item.setText(normalized)
        self.truth_table.blockSignals(False)
        self._refresh_hidden_rows()
        self._last_simplification.clear()

    def _refresh_hidden_rows(self):
        """Oculta filas donde TODAS las salidas son don't care (X)."""
        n_in = len(self.var_inputs)
        if n_in == 0 or not self.var_outputs:
            return
        n_rows = 2 ** n_in
        active = 0
        for r in range(n_rows):
            all_x = all(self.truth_data.get((o, r), '0') == 'X'
                        for o in self.var_outputs)
            self.truth_table.setRowHidden(r, all_x)
            if not all_x:
                active += 1
        self.truth_count_label.setText(f"Filas activas: {active}/{n_rows}")

    def _gather_terms(self, output_name: str):
        """Devuelve (minterms, dont_cares, maxterms) para la salida dada."""
        n_in = len(self.var_inputs)
        n_rows = 2 ** n_in
        minterms, dont_cares, maxterms = [], [], []
        for r in range(n_rows):
            v = self.truth_data.get((output_name, r), '0')
            if v == '1':
                minterms.append(r)
            elif v == 'X':
                dont_cares.append(r)
            else:
                maxterms.append(r)
        return minterms, dont_cares, maxterms

    def _simplify_and_show(self, mode: str):
        """mode: 'sop' o 'pos'.  Calcula la expresión y salta a Ecuaciones."""
        if not self.var_inputs or not self.var_outputs:
            QMessageBox.warning(self, "Faltan variables",
                                "Define al menos una entrada y una salida.")
            return
        selected = self.output_selector.currentText()
        outputs = (self.var_outputs if selected in ('', self._all_outputs_label)
                   else [selected])
        results = {}
        for o in outputs:
            mins, dcs, maxs = self._gather_terms(o)
            sop_cover = _sop_cover(mins, dcs, len(self.var_inputs))
            pos_cover = _pos_cover(mins, dcs, len(self.var_inputs))
            results[o] = {
                'sop_cover': sop_cover,
                'pos_cover': pos_cover,
                'minterms': mins,
                'maxterms': maxs,
                'dont_cares': dcs,
            }
        self._last_simplification = results
        self._last_highlight = mode
        self._populate_eqs_tab(highlight=mode)
        self.tabs.setCurrentIndex(2)   # Saltar a la pestaña Ecuaciones

    # ── Pestaña 3: Ecuaciones simplificadas ───────────────────────────────
    def _build_eqs_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        info = QLabel(
            "<small>Resultados de la minimización por Quine-McCluskey "
            "con soporte para don't cares.  Notación: <b>'</b> = NEGADO, "
            "<b>·</b> = AND, <b>+</b> = OR.</small>")
        info.setWordWrap(True)
        v.addWidget(info)

        notation_row = QHBoxLayout()
        notation_row.addWidget(QLabel("Notacion:"))
        self.notation_selector = QComboBox()
        for notation_id, label in NOTATION_LABELS.items():
            self.notation_selector.addItem(label, notation_id)
        self.notation_selector.setCurrentIndex(1)
        self.notation_selector.currentIndexChanged.connect(self._on_notation_changed)
        notation_row.addWidget(self.notation_selector)
        notation_row.addStretch(1)
        v.addLayout(notation_row)

        self.eqs_text = QTextEdit()
        self.eqs_text.setReadOnly(True)
        self.eqs_text.setFont(QFont('Consolas', 11))
        v.addWidget(self.eqs_text)

        # Botón futuro: armar circuito automáticamente
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_build_circuit = QPushButton("⚙  Armar circuito automáticamente")
        self.btn_build_circuit.setToolTip(
            "Próximamente: genera y coloca las puertas lógicas necesarias "
            "en una hoja nueva, conectándolas según las ecuaciones SOP.")
        self.btn_build_circuit.clicked.connect(self._build_circuit_stub)
        btn_row.addWidget(self.btn_build_circuit)
        v.addLayout(btn_row)
        return w

    def _on_notation_changed(self, _index: int):
        self._notation_id = self.notation_selector.currentData() or 'math_prime'
        if self._last_simplification:
            self._populate_eqs_tab(highlight=self._last_highlight)

    def _populate_eqs_tab(self, highlight: str = 'sop'):
        if not self._last_simplification:
            self.eqs_text.setPlainText(
                "Aún no se ha minimizado ninguna salida.  "
                "Ve a la pestaña 'Tabla de Verdad', llena los valores "
                "y pulsa Mintérminos (SOP) o Maxtérminos (POS).")
            return
        lines = []
        notation = self._notation_id
        rich = notation == 'math_bar'
        for oname, info in self._last_simplification.items():
            mins = info['minterms']
            maxs = info.get('maxterms', [])
            dcs = info['dont_cares']
            sop = _format_sop_cover(info['sop_cover'], self.var_inputs, notation, rich=rich)
            pos = _format_pos_cover(info['pos_cover'], self.var_inputs, notation, rich=rich)
            lines.append(f"── {oname} ──")
            mins_str = ','.join(str(m) for m in mins) or '∅'
            maxs_str = ','.join(str(m) for m in maxs) or '∅'
            dcs_str  = ','.join(str(d) for d in dcs) if dcs else '∅'
            lines.append(f"  Σm = ({mins_str})    ΠM = ({maxs_str})    d = ({dcs_str})")
            mark_sop = '◀' if highlight == 'sop' else ' '
            mark_pos = '◀' if highlight == 'pos' else ' '
            lines.append(f"  SOP {mark_sop}   {oname} = {sop}")
            lines.append(f"  POS {mark_pos}   {oname} = {pos}")
            lines.append("")
        if rich:
            escaped = []
            for line in lines:
                escaped.append(line if '<span' in line else html.escape(line))
            body = '<br>'.join(escaped)
            self.eqs_text.setHtml(
                '<div style="font-family: Consolas, monospace; font-size: 11pt; white-space: pre;">'
                f'{body}</div>')
        else:
            self.eqs_text.setPlainText('\n'.join(lines))

    def _safe_generated_name(self, text: str) -> str:
        safe = re.sub(r'[^A-Za-z0-9_]+', '_', text.strip())
        safe = safe.strip('_')
        return safe or 'X'

    def _ensure_auto_build_results(self) -> dict:
        if self._last_simplification:
            return self._last_simplification
        results = {}
        for output in self.var_outputs:
            mins, dcs, maxs = self._gather_terms(output)
            results[output] = {
                'sop_cover': _sop_cover(mins, dcs, len(self.var_inputs)),
                'pos_cover': _pos_cover(mins, dcs, len(self.var_inputs)),
                'minterms': mins,
                'maxterms': maxs,
                'dont_cares': dcs,
            }
        self._last_simplification = results
        return results

    def _place_generated_gate(self, scene, gate_type: str, name: str,
                              x: float, y: float, input_nets: list,
                              output_net: str):
        node2 = input_nets[0] if input_nets else '0'
        node3 = input_nets[1] if len(input_nets) > 1 else ''
        item = scene.place_component(
            gate_type, QPointF(x, y), name=name, value=0.0,
            node1=output_net, node2=node2, node3=node3)
        item.dig_inputs = 1 if gate_type == 'NOT' else max(1, len(input_nets))
        item.dig_input_nodes = list(input_nets[2:])
        item.update()
        return item

    def _combine_generated_inputs(self, scene, gate_type: str, prefix: str,
                                  input_nets: list, output_net: str,
                                  x: float, y: float, two_input_only: bool) -> str:
        if not input_nets:
            return ''
        safe_prefix = self._safe_generated_name(prefix)
        if len(input_nets) == 1:
            self._place_generated_gate(
                scene, gate_type, f"{safe_prefix}_{gate_type}",
                x, y, input_nets, output_net)
            return output_net
        if (not two_input_only) or len(input_nets) <= 2:
            self._place_generated_gate(
                scene, gate_type, f"{safe_prefix}_{gate_type}",
                x, y, input_nets, output_net)
            return output_net

        current = input_nets[0]
        for i, next_net in enumerate(input_nets[1:], start=1):
            is_last = i == len(input_nets) - 1
            stage_out = output_net if is_last else f"{safe_prefix}_stage_{i}"
            self._place_generated_gate(
                scene, gate_type, f"{safe_prefix}_{gate_type}{i}",
                x + (i - 1) * 110, y, [current, next_net], stage_out)
            current = stage_out
        return current

    def _auto_build_sop_circuit(self, opts: dict) -> bool:
        if opts.get('nand_only'):
            QMessageBox.warning(
                self, "Solo NAND",
                "La generacion solo con NAND todavia no esta conectada. "
                "Desmarca esa opcion para armar el circuito SOP con AND/OR/NOT.")
            return False

        owner = self.parent()
        if owner is None or not hasattr(owner, '_add_sheet'):
            QMessageBox.warning(self, "Armado automatico",
                                "No se encontro la ventana principal para crear la hoja.")
            return False

        results = self._ensure_auto_build_results()
        if not self.var_inputs or not self.var_outputs:
            QMessageBox.warning(self, "Faltan variables",
                                "Define al menos una entrada y una salida.")
            return False

        owner._add_sheet(opts['sheet_name'])
        scene = owner.scene
        two_input_only = bool(opts.get('two_input_only'))

        input_x = -520
        not_x = -330
        term_x = -90
        out_x = 390 if two_input_only else 270
        label_x = 700 if two_input_only else 520
        y_step = 80

        used_negated = {
            var for info in results.values()
            for prime in info.get('sop_cover', [])
            for bit, var in zip(prime, self.var_inputs)
            if bit == '0'
        }
        neg_nets = {}

        for i, var in enumerate(self.var_inputs):
            safe_var = self._safe_generated_name(var)
            y = i * y_step
            src = scene.place_component(
                'LOGIC_STATE', QPointF(input_x, y),
                name=f"IN_{safe_var}", value=0.0, node1=var)
            src.update()
            if var in used_negated:
                neg_net = f"NOT_{safe_var}"
                neg_nets[var] = neg_net
                self._place_generated_gate(
                    scene, 'NOT', f"NOT_{safe_var}", not_x, y,
                    [var], neg_net)

        first_output_y = max(len(self.var_inputs) * y_step + 80, 120)
        for out_index, (output, info) in enumerate(results.items()):
            safe_out = self._safe_generated_name(output)
            cover = info.get('sop_cover', [])
            block_y = first_output_y + out_index * max(180, (len(cover) + 1) * y_step)
            term_nets = []

            if not cover:
                const = scene.place_component(
                    'LOGIC_STATE', QPointF(out_x, block_y),
                    name=f"CONST0_{safe_out}", value=0.0, node1=output)
                const.update()
            elif cover == ['-' * len(self.var_inputs)]:
                const = scene.place_component(
                    'LOGIC_STATE', QPointF(out_x, block_y),
                    name=f"CONST1_{safe_out}", value=1.0, node1=output)
                const.update()
            else:
                for term_index, prime in enumerate(cover):
                    lits = []
                    for bit, var in zip(prime, self.var_inputs):
                        if bit == '1':
                            lits.append(var)
                        elif bit == '0':
                            lits.append(neg_nets.get(var, f"NOT_{self._safe_generated_name(var)}"))
                    term_y = block_y + term_index * y_step
                    if len(lits) == 1:
                        term_nets.append(lits[0])
                    else:
                        term_net = f"{safe_out}_T{term_index + 1}"
                        self._combine_generated_inputs(
                            scene, 'AND', f"{safe_out}_T{term_index + 1}",
                            lits, term_net, term_x, term_y, two_input_only)
                        term_nets.append(term_net)

                final_y = block_y + max(0, (len(term_nets) - 1) * y_step / 2)
                self._combine_generated_inputs(
                    scene, 'OR', f"{safe_out}_OUT",
                    term_nets, output, out_x, final_y, two_input_only)

            out_label = scene.place_component(
                'NET_LABEL_OUT', QPointF(label_x, block_y),
                name=f"OUT_{safe_out}", value=0.0, node1=output)
            out_label.sheet_label = output
            out_label.update()

        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-120, -120, 160, 120))
        if hasattr(owner, 'statusBar'):
            owner.statusBar().showMessage(
                f"Circuito generado en la hoja '{opts['sheet_name']}'")
        return True

    def _build_circuit_stub(self):
        dlg = AutoBuildCircuitDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        opts = dlg.get_options()
        if not opts['sheet_name']:
            QMessageBox.warning(self, "Nombre requerido",
                                "Indica un nombre para la hoja del circuito.")
            return
        self._last_auto_build_options = opts
        if self._auto_build_sop_circuit(opts):
            QMessageBox.information(
                self, "Armado automatico",
                f"Circuito generado en la hoja '{opts['sheet_name']}'.")

    # ── Pestaña 4: Mapa de Karnaugh (placeholder) ─────────────────────────
    def _build_kmap_tab(self) -> QWidget:
        w = QWidget()
        self._kmap_tab = w
        v = QVBoxLayout(w)
        title = QLabel("<h3>Mapa de Karnaugh</h3>")
        v.addWidget(title)

        top = QHBoxLayout()
        top.addWidget(QLabel("Salida:"))
        self.kmap_output_selector = QComboBox()
        self.kmap_output_selector.currentIndexChanged.connect(self._refresh_kmap)
        top.addWidget(self.kmap_output_selector)
        top.addWidget(QLabel("Grupos visibles:"))
        self.kmap_group_selector = QComboBox()
        self.kmap_group_selector.addItem("SOP", 'sop')
        self.kmap_group_selector.addItem("POS", 'pos')
        self.kmap_group_selector.currentIndexChanged.connect(self._on_kmap_group_mode_changed)
        top.addWidget(self.kmap_group_selector)
        btn_refresh = QPushButton("Actualizar")
        btn_refresh.clicked.connect(self._refresh_kmap)
        top.addWidget(btn_refresh)
        top.addStretch(1)
        v.addLayout(top)

        self.kmap_summary = QLabel("")
        self.kmap_summary.setWordWrap(True)
        v.addWidget(self.kmap_summary)

        self.kmap_table = QTableWidget()
        self.kmap_table.setItemDelegate(KMapGroupDelegate(self.kmap_table))
        self.kmap_table.setMouseTracking(True)
        self.kmap_table.viewport().setMouseTracking(True)
        self.kmap_table.cellEntered.connect(self._on_kmap_cell_entered)
        self.kmap_table.itemChanged.connect(self._on_kmap_cell_changed)
        v.addWidget(self.kmap_table)

        self.kmap_group_hint = QLabel("Pasa el cursor sobre una celda agrupada.")
        self.kmap_group_hint.setTextFormat(Qt.TextFormat.RichText)
        self.kmap_group_hint.setWordWrap(True)
        self.kmap_group_hint.setStyleSheet(
            f"color: {COLORS.get('text_dim', '#7f8c8d')};")
        v.addWidget(self.kmap_group_hint)

        btn_row = QHBoxLayout()
        self.btn_kmap_sop = QPushButton("K-map -> SOP")
        self.btn_kmap_pos = QPushButton("K-map -> POS")
        self.btn_kmap_export = QPushButton("Exportar PNG")
        self.btn_kmap_sop.clicked.connect(lambda: self._simplify_from_kmap('sop'))
        self.btn_kmap_pos.clicked.connect(lambda: self._simplify_from_kmap('pos'))
        self.btn_kmap_export.clicked.connect(self._export_kmap_png)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_kmap_sop)
        btn_row.addWidget(self.btn_kmap_pos)
        btn_row.addWidget(self.btn_kmap_export)
        v.addLayout(btn_row)

        msg = QLabel(
            "<p>Próximamente:</p>"
            "<ul>"
            "<li>Visualización del K-map para cada salida con sus "
            "agrupaciones óptimas.</li>"
            "<li>Edición directa del mapa para introducir 0/1/X.</li>"
            "<li>Generar SOP y POS desde el mapa "
            "y viceversa (ecuación → mapa).</li>"
            "<li>Exportación a imagen.</li>"
            "</ul>")
        msg.setWordWrap(True)
        msg.setVisible(False)
        v.addWidget(msg)
        future = QGroupBox("Opciones futuras")
        future.setVisible(False)
        future_row = QHBoxLayout(future)
        for text in (
                "Exportar K-map",
                "K-map -> SOP/POS",
                "Ecuaciones -> K-map"):
            btn = QPushButton(text)
            btn.setEnabled(False)
            future_row.addWidget(btn)
        v.addWidget(future)
        v.addStretch(1)
        return w

    # ── Cambio de pestaña ─────────────────────────────────────────────────
    def _sync_kmap_outputs(self):
        if not hasattr(self, 'kmap_output_selector'):
            return
        current = self.kmap_output_selector.currentText()
        self.kmap_output_selector.blockSignals(True)
        self.kmap_output_selector.clear()
        self.kmap_output_selector.addItems(self.var_outputs)
        if current in self.var_outputs:
            self.kmap_output_selector.setCurrentText(current)
        self.kmap_output_selector.blockSignals(False)

    def _kmap_axis_labels(self):
        n = len(self.var_inputs)
        row_bits = n // 2
        col_bits = n - row_bits
        row_vars = self.var_inputs[:row_bits]
        col_vars = self.var_inputs[row_bits:]
        row_codes = _gray_codes(row_bits)
        col_codes = _gray_codes(col_bits)

        def label(vars_, code):
            if not vars_:
                return '1'
            return ''.join(vars_) + '=' + (code or '0')

        return row_vars, col_vars, row_codes, col_codes, label

    def _kmap_group_mode(self) -> str:
        if not hasattr(self, 'kmap_group_selector'):
            return 'sop'
        return self.kmap_group_selector.currentData() or 'sop'

    def _on_kmap_group_mode_changed(self, _index: int):
        self._refresh_kmap()

    def _kmap_group_term(self, prime: str, mode: str) -> str:
        notation = getattr(self, '_notation_id', 'math_prime')
        rich = notation == 'math_bar'
        if mode == 'pos':
            return _format_pos_cover([prime], self.var_inputs, notation, rich=rich)
        return _format_sop_cover([prime], self.var_inputs, notation, rich=rich)

    def _kmap_group_hint_html(self, groups: list) -> str:
        parts = []
        for i, group in enumerate(groups):
            label = html.escape(f"{group['mode']} grupo {i + 1}: ")
            term = group['term']
            if '<span' not in term:
                term = html.escape(term)
            parts.append(f"{label}{term}")
        return '<br>'.join(parts)

    def _build_kmap_groups(self, output: str, mode: str) -> list:
        if not output or output not in self.var_outputs:
            return []
        n = len(self.var_inputs)
        if n == 0:
            return []

        mins, dcs, _maxs = self._gather_terms(output)
        cover = (_pos_cover(mins, dcs, n) if mode == 'pos'
                 else _sop_cover(mins, dcs, n))
        colors = [
            '#f5a623', '#4ecca3', '#e94560', '#6c8cff',
            '#ff6f91', '#2dd4bf', '#ffd166', '#a78bfa',
        ]
        groups = []
        for i, prime in enumerate(cover):
            cells = [idx for idx in range(2 ** n)
                     if _qm_covers(prime, idx, n)]
            groups.append({
                'prime': prime,
                'term': self._kmap_group_term(prime, mode),
                'cells': cells,
                'color': colors[i % len(colors)],
                'mode': mode.upper(),
            })
        return groups

    def _on_kmap_cell_entered(self, row: int, col: int):
        item = self.kmap_table.item(row, col)
        groups = item.data(KMAP_GROUPS_ROLE) if item else None
        if not groups:
            self.kmap_group_hint.setText("Esta celda no pertenece a ningun grupo visible.")
            return
        self.kmap_group_hint.setText(self._kmap_group_hint_html(groups))

    def _export_kmap_png(self):
        if not hasattr(self, 'kmap_table') or self.kmap_table.rowCount() == 0:
            QMessageBox.warning(self, "Exportar K-map",
                                "No hay un K-map listo para exportar.")
            return
        output = self.kmap_output_selector.currentText() or 'kmap'
        safe_output = re.sub(r'[^A-Za-z0-9_-]+', '_', output).strip('_') or 'kmap'
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar K-map a PNG", f"{safe_output}_kmap.png",
            "Imagen PNG (*.png)")
        if not path:
            return
        if not path.lower().endswith('.png'):
            path += '.png'
        widget = self.kmap_table
        ok = widget.grab().save(path, 'PNG')
        if ok:
            QMessageBox.information(self, "K-map exportado",
                                    f"Imagen guardada en:\n{path}")
        else:
            QMessageBox.warning(self, "Error",
                                f"No se pudo guardar la imagen:\n{path}")

    def _refresh_kmap(self):
        if not hasattr(self, 'kmap_table'):
            return
        self._sync_kmap_outputs()
        n = len(self.var_inputs)
        output = self.kmap_output_selector.currentText() if self.var_outputs else ''
        mode = self._kmap_group_mode()

        self.kmap_table.blockSignals(True)
        self.kmap_table.clear()
        self._kmap_groups = []
        if n == 0 or not output:
            self.kmap_table.setRowCount(0)
            self.kmap_table.setColumnCount(0)
            self.kmap_summary.setText("Define al menos una entrada y una salida.")
            self.kmap_group_hint.setText("Pasa el cursor sobre una celda agrupada.")
            self.kmap_table.blockSignals(False)
            return
        if n > 4:
            self.kmap_table.setRowCount(0)
            self.kmap_table.setColumnCount(0)
            self.kmap_summary.setText("La vista inicial de K-map soporta de 1 a 4 variables.")
            self.kmap_group_hint.setText("Las agrupaciones visibles estan disponibles hasta 4 variables.")
            self.kmap_table.blockSignals(False)
            return

        row_vars, col_vars, row_codes, col_codes, label = self._kmap_axis_labels()
        self._kmap_groups = self._build_kmap_groups(output, mode)
        self.kmap_table.setRowCount(len(row_codes))
        self.kmap_table.setColumnCount(len(col_codes))
        self.kmap_table.setVerticalHeaderLabels([label(row_vars, c) for c in row_codes])
        self.kmap_table.setHorizontalHeaderLabels([label(col_vars, c) for c in col_codes])

        for r, row_code in enumerate(row_codes):
            for c, col_code in enumerate(col_codes):
                bits = row_code + col_code
                idx = int(bits, 2) if bits else 0
                val = self.truth_data.get((output, idx), '0')
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setData(Qt.ItemDataRole.UserRole, idx)
                cell_groups = [g for g in self._kmap_groups if idx in g['cells']]
                item.setData(KMAP_GROUPS_ROLE, cell_groups)
                if cell_groups:
                    item.setToolTip(self._kmap_group_hint_html(cell_groups))
                self.kmap_table.setItem(r, c, item)

        self.kmap_table.resizeColumnsToContents()
        self.kmap_summary.setText(
            f"K-map de {output}: filas {', '.join(row_vars) or '1'}; "
            f"columnas {', '.join(col_vars) or '1'}. "
            f"Mostrando agrupaciones {mode.upper()} ({len(self._kmap_groups)} grupos).")
        self.kmap_group_hint.setText("Pasa el cursor sobre una celda agrupada.")
        self.kmap_table.blockSignals(False)

    def _on_kmap_cell_changed(self, item):
        output = self.kmap_output_selector.currentText()
        idx = item.data(Qt.ItemDataRole.UserRole)
        if output not in self.var_outputs or idx is None:
            return
        text = item.text().strip().lower()
        if text in ('1', 'true', 'high', 'h'):
            normalized = '1'
        elif text in ('x', 'd', 'dc', '-', '?'):
            normalized = 'X'
        else:
            normalized = '0'
        self.truth_data[(output, int(idx))] = normalized
        self.kmap_table.blockSignals(True)
        item.setText(normalized)
        self.kmap_table.blockSignals(False)
        self._last_simplification.clear()
        self._refresh_kmap()

    def _simplify_from_kmap(self, mode: str):
        output = self.kmap_output_selector.currentText()
        if hasattr(self, 'kmap_group_selector'):
            idx = self.kmap_group_selector.findData(mode)
            if idx >= 0:
                self.kmap_group_selector.setCurrentIndex(idx)
        self._rebuild_truth_table()
        if output in self.var_outputs and hasattr(self, 'output_selector'):
            self.output_selector.setCurrentText(output)
        self._simplify_and_show(mode)

    def _on_tab_changed(self, idx: int):
        if idx == 1:    # Tabla de verdad
            self._rebuild_truth_table()
        elif idx == 2:  # Ecuaciones (mostrar texto previo si existe)
            if self._last_simplification:
                self._populate_eqs_tab(highlight=self._last_highlight)
            else:
                self.eqs_text.setPlainText(
                    "Aún no se ha minimizado ninguna salida.  Ve a la "
                    "pestaña 'Tabla de Verdad', llena los valores y "
                    "pulsa Mintérminos (SOP) o Maxtérminos (POS).")
        elif idx == 3:  # Mapa de Karnaugh
            self._refresh_kmap()


# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("CircuitSim")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
