"""
CircuitSim — Simulador de circuitos open source
GUI principal con canvas drag-and-drop, PyQt6
"""

import sys
import math
import json
import os
from typing import Optional, List, Dict, Tuple

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGraphicsScene, QGraphicsView, QGraphicsItem, QGraphicsLineItem,
    QGraphicsEllipseItem, QGraphicsTextItem, QGraphicsRectItem,
    QToolBar, QLabel, QDockWidget, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QSplitter, QDialog, QFormLayout,
    QLineEdit, QDialogButtonBox, QMessageBox, QStatusBar, QFrame,
    QGraphicsPathItem, QPushButton, QComboBox, QDoubleSpinBox,
    QScrollArea, QGroupBox, QTextEdit, QFileDialog,
    QListWidget, QListWidgetItem
)
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath,
    QIcon, QPixmap, QTransform, QAction, QKeySequence
)
from PyQt6.QtCore import (
    Qt, QPointF, QRectF, QLineF, pyqtSignal, QObject, QSize
)

# Motor MNA
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from engine import Resistor, VoltageSource, VoltageSourceAC, CurrentSource, Capacitor, Inductor
from engine import Diode, BJT, MOSFET, OpAmp, Impedance, MNASolver
from circuit_analyzer import (
    CircuitAnalyzer, ImplicitBridgeDetector,
    LOGIC_STANDARDS, DEFAULT_STANDARD, AnalysisFlags,
)
from themes import ThemeManager, BUILTIN_THEMES, DEFAULT_THEME_ID


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

    COMP_TYPES = ['R', 'V', 'VAC', 'I', 'C', 'L', 'Z', 'GND', 'NODE',
                  'D', 'LED', 'BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP',
                  # ── Digital ──
                  'AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR',
                  'DFF', 'JKFF', 'TFF', 'SRFF',
                  'MUX2', 'COUNTER',
                  'ADC_BRIDGE', 'DAC_BRIDGE', 'COMPARATOR', 'PWM']

    # Tipos que pertenecen al dominio digital (no se pasan al MNA)
    DIGITAL_TYPES = {
        'AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR',
        'DFF', 'JKFF', 'TFF', 'SRFF',
        'MUX2', 'COUNTER',
        'ADC_BRIDGE', 'DAC_BRIDGE', 'COMPARATOR', 'PWM',
        'LOGIC_STATE',
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
        # Puertas: bounding rect dinámico según altura real
        if self.comp_type in ('AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR',
                               'COMPARATOR', 'PWM', 'MUX2'):
            gw, gh, step, n = self._gate_geometry()
            margin = 20
            return QRectF(-gw - 10 - margin, -gh - margin,
                          (gw + 10) * 2 + margin * 2, gh * 2 + margin * 2)
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
        return QPointF(0, 0)

    def pin3_position_scene(self) -> QPointF:
        return self.mapToScene(self.pin3_position())

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
        elif self.comp_type in ('DFF', 'JKFF', 'TFF', 'SRFF', 'MUX2'):
            pins.append(self.pin3_position_scene())
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
        # ── Digital ──────────────────────────────────────────────────────
        elif self.comp_type in ('AND', 'NAND', 'OR', 'NOR', 'XOR', 'NOT'):
            self._draw_ansi_gate(painter, pen_body, pen_wire, body_color)
        elif self.comp_type in ('DFF', 'JKFF', 'TFF', 'SRFF'):
            lbl = {'DFF':'D-FF','JKFF':'JK-FF','TFF':'T-FF','SRFF':'SR-FF'}[self.comp_type]
            self._draw_digital_ff(painter, pen_body, pen_wire, body_color, lbl)
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

        # Nombre y valor
        self._draw_labels(painter, text_color)

        # Pines — los dispositivos de 3 terminales dibujan sus propios pines
        # internamente con etiquetas; solo dibujar pines genéricos para el resto
        three_terminal = ('BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP')
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
            painter.drawText(QRectF(-r+4, -r+4, r-4, r*2-8), Qt.AlignmentFlag.AlignCenter, '+')
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

    def _draw_digital_ff(self, painter, pen_body, pen_wire, body_color, label: str):
        """Flip-flop: caja con D/CLK a la izquierda, Q/Qn a la derecha."""
        hw, hh = COMP_W // 2, COMP_H // 2 + 8
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, hw * 2, hh * 2))
        # Etiqueta
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        font = QFont('Consolas', 7, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(-hw, -hh, hw * 2, 14),
                         Qt.AlignmentFlag.AlignCenter, label)
        # Pines izquierda: D (arriba), CLK (abajo)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, -hh // 2), QPointF(-hw, -hh // 2))
        painter.drawLine(QPointF(-hw - 10,  hh // 2), QPointF(-hw,  hh // 2))
        # Pines derecha: Q (arriba), Qn (abajo)
        painter.drawLine(QPointF(hw, -hh // 2), QPointF(hw + 10, -hh // 2))
        painter.drawLine(QPointF(hw,  hh // 2), QPointF(hw + 10,  hh // 2))
        # Símbolo de reloj (triángulo)
        painter.setPen(QPen(QColor(COLORS['component']), 1))
        cy = hh // 2
        painter.drawLine(QPointF(-hw, cy - 5), QPointF(-hw + 6, cy))
        painter.drawLine(QPointF(-hw + 6, cy), QPointF(-hw, cy + 5))
        # Etiquetas de pines
        font2 = QFont('Consolas', 6)
        painter.setFont(font2)
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.drawText(QRectF(-hw + 2, -hh // 2 - 8, 14, 10), Qt.AlignmentFlag.AlignLeft, 'D')
        painter.drawText(QRectF(-hw + 8, hh // 2 - 8, 20, 10), Qt.AlignmentFlag.AlignLeft, 'CLK')
        painter.drawText(QRectF(hw - 14, -hh // 2 - 8, 14, 10), Qt.AlignmentFlag.AlignRight, 'Q')
        painter.drawText(QRectF(hw - 14, hh // 2 - 8, 18, 10), Qt.AlignmentFlag.AlignRight, 'Q̄')
        # Puntos de pin
        pin_color = QColor(COLORS['pin'])
        for px, py in [(-hw - 10, -hh // 2), (-hw - 10, hh // 2),
                       (hw + 10, -hh // 2), (hw + 10, hh // 2)]:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(QPointF(px, py), PIN_RADIUS, PIN_RADIUS)

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

    def _draw_labels(self, painter, text_color):
        if self.comp_type in ('GND', 'NODE'):
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
        return super().itemChange(change, value)


# ══════════════════════════════════════════════════════════════
# ÍTEM DE CABLE (WIRE)
# ══════════════════════════════════════════════════════════════
class WireItem(QGraphicsLineItem):
    def __init__(self, p1: QPointF, p2: QPointF):
        super().__init__(QLineF(p1, p2))
        self.setPen(QPen(QColor(COLORS['wire']), 2, Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.RoundCap))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)

    def paint(self, painter, option, widget):
        if self.isSelected():
            self.setPen(QPen(QColor(COLORS['wire_sel']), 2.5))
        else:
            self.setPen(QPen(QColor(COLORS['wire']), 2, Qt.PenStyle.SolidLine,
                             Qt.PenCapStyle.RoundCap))
        super().paint(painter, option, widget)


# ══════════════════════════════════════════════════════════════
# DIÁLOGO DE SELECCIÓN DE COMPONENTES (con preview)
# ══════════════════════════════════════════════════════════════
class ComponentPickerDialog(QDialog):
    """
    Ventana emergente que muestra una lista de componentes de una categoría
    y una preview gráfica del componente seleccionado.
    """
    def __init__(self, category_name: str, components: List[tuple], parent=None):
        """
        components: lista de tuplas (comp_type, label, symbol_ascii)
        """
        super().__init__(parent)
        self.setWindowTitle(f"Seleccionar componente — {category_name}")
        self._components = components
        self._selected_type = None
        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setSpacing(12)

        # ── Lista de componentes ─────────────────────────────────────────
        left = QVBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.setFont(QFont('Consolas', 10))
        for ctype, label, sym in self._components:
            item = QListWidgetItem(f"{sym}   {label}")
            item.setData(Qt.ItemDataRole.UserRole, ctype)
            self.list_widget.addItem(item)
        self.list_widget.currentRowChanged.connect(self._update_preview)
        self.list_widget.itemDoubleClicked.connect(self.accept)
        left.addWidget(self.list_widget)

        btn_row = QHBoxLayout()
        self.place_btn = QPushButton("Colocar")
        self.place_btn.setDefault(True)
        self.place_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self.place_btn)
        btn_row.addWidget(cancel_btn)
        left.addLayout(btn_row)
        layout.addLayout(left, 1)

        # ── Preview gráfica ──────────────────────────────────────────────
        right = QVBoxLayout()
        preview_title = QLabel("Vista previa")
        preview_title.setFont(QFont('Consolas', 9, QFont.Weight.Bold))
        preview_title.setStyleSheet(f"color: {COLORS['component']};")
        right.addWidget(preview_title)

        self.preview_scene = QGraphicsScene()
        self.preview_view = QGraphicsView(self.preview_scene)
        self.preview_view.setFixedSize(280, 220)
        self.preview_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.preview_view.setBackgroundBrush(QBrush(QColor(COLORS['bg'])))
        self.preview_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_view.setFrameShape(QFrame.Shape.StyledPanel)
        right.addWidget(self.preview_view)
        right.addStretch()
        layout.addLayout(right, 2)

        self.list_widget.setCurrentRow(0)

    def _update_preview(self, row: int):
        self.preview_scene.clear()
        if row < 0 or row >= self.list_widget.count():
            return
        ctype = self.list_widget.item(row).data(Qt.ItemDataRole.UserRole)
        self._selected_type = ctype

        # Valores por defecto para que la preview se vea bien
        defaults = {
            'R': 1000.0, 'V': 5.0, 'VAC': 120.0, 'I': 0.001,
            'C': 1e-6, 'L': 1e-3, 'Z': 0.0,
            'D': 1e-14, 'BJT_NPN': 100.0, 'BJT_PNP': 100.0,
            'NMOS': 1e-3, 'PMOS': 1e-3, 'OPAMP': 1e5,
            'GND': 0.0, 'NODE': 0.0
        }
        val = defaults.get(ctype, 1.0)
        item = ComponentItem(ctype, f"{ctype}1", val, '', '', '')
        if ctype == 'Z':
            item.z_real = 100.0
            item.z_imag = 50.0

        # Deshabilitar interacción en la preview
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, False)

        self.preview_scene.addItem(item)
        br = item.boundingRect()
        self.preview_scene.setSceneRect(br.adjusted(-30, -30, 30, 30))
        self.preview_view.centerOn(item)

    def _apply_style(self):
        self.setStyleSheet(f"""
            QDialog {{
                background: {COLORS['panel']};
                color: {COLORS['text']};
            }}
            QLabel {{
                color: {COLORS['text']};
            }}
            QListWidget {{
                background: {COLORS['comp_body']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['panel_brd']};
                border-radius: 4px;
                padding: 4px;
            }}
            QListWidget::item {{
                padding: 6px;
                border-bottom: 1px solid {COLORS['panel_brd']};
            }}
            QListWidget::item:selected {{
                background: {COLORS['component']};
                color: white;
            }}
            QPushButton {{
                background: {COLORS['component']};
                color: white;
                border-radius: 4px;
                padding: 6px 16px;
            }}
            QPushButton:hover {{
                background: {COLORS['comp_sel']};
            }}
        """)

    def get_selected_type(self) -> Optional[str]:
        return self._selected_type


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
        self._draw_grid()

    # ── Grid ────────────────────────────────────
    def _draw_grid(self):
        pen_minor = QPen(QColor(COLORS['grid_line']), 0.5, Qt.PenStyle.DotLine)
        pen_major = QPen(QColor(COLORS['grid_line']), 1.0, Qt.PenStyle.SolidLine)
        r = int(self.sceneRect().width() // GRID_SIZE)
        for i in range(-r, r):
            pen = pen_major if i % 5 == 0 else pen_minor
            self.addLine(i*GRID_SIZE, -1000, i*GRID_SIZE, 1000, pen)
            self.addLine(-1000, i*GRID_SIZE, 1000, i*GRID_SIZE, pen)

    # ── Modo ────────────────────────────────────
    def set_mode(self, mode: str):
        self._mode = mode
        if not mode.startswith('place') and mode != 'wire':
            if self._wire_preview:
                self.removeItem(self._wire_preview)
                self._wire_preview = None
            self._wire_start = None

    # ── Colocar componente ───────────────────────
    def place_component(self, comp_type: str, pos: QPointF,
                        name: str = '', value: float = 0.0, unit: str = '',
                        node1: str = '', node2: str = '', node3: str = '') -> ComponentItem:
        if not name:
            count = self._comp_counter.get(comp_type, 0) + 1
            self._comp_counter[comp_type] = count
            prefixes = {'R': 'R', 'V': 'V', 'I': 'I', 'C': 'C', 'L': 'L',
                        'GND': 'GND', 'NODE': 'N', 'LOGIC_STATE': 'LS',
                        'AND': 'AND', 'OR': 'OR', 'NOT': 'NOT', 'NAND': 'NAND',
                        'NOR': 'NOR', 'XOR': 'XOR', 'DFF': 'FF', 'JKFF': 'FF',
                        'TFF': 'FF', 'SRFF': 'FF', 'COUNTER': 'CNT', 'MUX2': 'MUX'}
            name = f"{prefixes.get(comp_type, comp_type)}{count}"

        units = {'R': 'Ω', 'V': 'V', 'VAC': 'V', 'I': 'A', 'C': 'F', 'L': 'H',
                 'D': 'A', 'LED': 'A', 'BJT_NPN': 'hFE', 'BJT_PNP': 'hFE',
                 'NMOS': 'A/V²', 'PMOS': 'A/V²', 'OPAMP': 'V/V'}
        if not unit:
            unit = units.get(comp_type, '')

        defaults = {'R': 1000.0, 'V': 5.0, 'VAC': 120.0, 'I': 0.001, 'C': 1e-6, 'L': 1e-3,
                    'D': 1e-14, 'LED': 1e-14, 'BJT_NPN': 100.0, 'BJT_PNP': 100.0,
                    'NMOS': 1e-3, 'PMOS': 1e-3, 'OPAMP': 1e5,
                    'LOGIC_STATE': 0.0}  # 0=LOW, 1=HIGH
        if value == 0.0 and comp_type != 'LOGIC_STATE':
            value = defaults.get(comp_type, 1.0)
        elif comp_type == 'LOGIC_STATE':
            value = defaults.get(comp_type, 0.0)

        item = ComponentItem(comp_type, name, value, unit, node1, node2, node3)
        if comp_type == 'NOT':
            item.dig_inputs = 1
        snap_x = round(pos.x() / GRID_SIZE) * GRID_SIZE
        snap_y = round(pos.y() / GRID_SIZE) * GRID_SIZE
        item.setPos(snap_x, snap_y)
        self.addItem(item)
        self.components.append(item)
        return item

    # ── Eventos de mouse ────────────────────────

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
                dy = pos.y() - pt.y()
                d  = (dx*dx + dy*dy) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best_pt   = pt
        if best_pt is not None:
            return best_pt
        return QPointF(round(pos.x()/GRID_SIZE)*GRID_SIZE,
                       round(pos.y()/GRID_SIZE)*GRID_SIZE)

    def mousePressEvent(self, event):
        pos = event.scenePos()

        if self._mode.startswith('place_'):
            comp_type = self._mode.split('_', 1)[1]
            self.place_component(comp_type, pos)
            self.status_message.emit(f"Componente {comp_type} colocado en ({pos.x():.0f}, {pos.y():.0f})")
            return

        if self._mode == 'wire':
            snap = self._snap_to_pin_or_grid(pos)
            if self._wire_start is None:
                self._wire_start = snap
                self._wire_preview = WireItem(snap, snap)
                self.addItem(self._wire_preview)
            else:
                # Finalizar cable
                wire = WireItem(self._wire_start, snap)
                self.addItem(wire)
                self.wires.append(wire)
                if self._wire_preview:
                    self.removeItem(self._wire_preview)
                self._wire_preview = WireItem(snap, snap)
                self.addItem(self._wire_preview)
                self._wire_start = snap
                self.status_message.emit("Cable colocado")
            return

        super().mousePressEvent(event)

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
            elif comp.comp_type in ('DFF', 'JKFF', 'TFF', 'SRFF', 'MUX2'):
                pins[f"{comp.name}__p3"] = comp.pin3_position_scene()
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

        # ── 3. Detectar grupos GND ───────────────────────────────────────
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

        net_counter = 0
        root_to_name: Dict[str, str] = {}
        for root in groups:
            if root in gnd_roots:
                root_to_name[root] = '0'
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
        dialog = ComponentDialog(item)
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


# ══════════════════════════════════════════════════════════════
# DIÁLOGO DE PROPIEDADES
# ══════════════════════════════════════════════════════════════
class ComponentDialog(QDialog):
    def __init__(self, item: ComponentItem, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Propiedades — {item.comp_type}")
        self.setStyleSheet(f"""
            QDialog {{ background: {COLORS['panel']}; color: {COLORS['text']}; }}
            QLabel  {{ color: {COLORS['text']}; }}
            QLineEdit, QDoubleSpinBox {{
                background: {COLORS['bg']}; color: {COLORS['text']};
                border: 1px solid {COLORS['panel_brd']}; border-radius: 4px;
                padding: 4px;
            }}
            QPushButton {{
                background: {COLORS['component']}; color: white;
                border-radius: 4px; padding: 6px 16px;
            }}
            QPushButton:hover {{ background: {COLORS['comp_sel']}; }}
        """)
        self.item = item
        self._build_ui()

    def _build_ui(self):
        layout = QFormLayout(self)
        layout.setSpacing(10)

        self.name_edit = QLineEdit(self.item.name)
        layout.addRow("Nombre:", self.name_edit)

        # Etiqueta y rango del valor según tipo
        value_labels = {
            'R': 'Resistencia (Ω)', 'V': 'Voltaje (V)', 'I': 'Corriente (A)',
            'C': 'Capacitancia (F)', 'L': 'Inductancia (H)',
            'D': 'Is — Corriente saturación (A)',
            'LED': 'Is — Corriente saturación (A)',
            'BJT_NPN': 'hFE — Ganancia β',
            'BJT_PNP': 'hFE — Ganancia β',
            'NMOS': 'Kn — Transconductancia (A/V²)',
            'PMOS': 'Kp — Transconductancia (A/V²)',
            'OPAMP': 'A — Ganancia lazo abierto (V/V)',
        }
        self.value_spin = QDoubleSpinBox()
        self.value_spin.setRange(-1e12, 1e12)
        self.value_spin.setDecimals(6)
        self.value_spin.setValue(self.item.value)
        layout.addRow(value_labels.get(self.item.comp_type, 'Valor:'), self.value_spin)

        # Etiquetas de nodos según terminales reales del componente
        node_labels = {
            'R':       ('Nodo 1',    'Nodo 2',    None),
            'C':       ('Nodo 1',    'Nodo 2',    None),
            'L':       ('Nodo 1',    'Nodo 2',    None),
            'V':       ('Nodo + (ánodo)',  'Nodo − (cátodo)', None),
            'I':       ('Nodo + (salida)', 'Nodo − (entrada)', None),
            'D':       ('Ánodo (A)', 'Cátodo (K)', None),
            'LED':     ('Ánodo (A)', 'Cátodo (K)', None),
            'BJT_NPN': ('Colector (C)', 'Emisor (E)', 'Base (B)'),
            'BJT_PNP': ('Colector (C)', 'Emisor (E)', 'Base (B)'),
            'NMOS':    ('Drain (D)',    'Source (S)', 'Gate (G)'),
            'PMOS':    ('Drain (D)',    'Source (S)', 'Gate (G)'),
            'OPAMP':   ('Salida (OUT)', 'Entrada − (V−)', 'Entrada + (V+)'),
        }
        _dig_gate_types_prop = {'AND','OR','NOT','NAND','NOR','XOR'}
        _dig_ff_types_prop   = {'DFF','JKFF','TFF','SRFF'}

        if self.item.comp_type in _dig_gate_types_prop:
            # Puertas: Salida (p1) + N entradas (p2..pN+1)
            n_in = self.item.dig_inputs if self.item.comp_type != 'NOT' else 1
            # Nodo salida
            self.node1_edit = QLineEdit(self.item.node1)
            layout.addRow('Salida (Y):', self.node1_edit)
            self.node2_edit = QLineEdit(self.item.node2)
            layout.addRow('Entrada 1 (A):', self.node2_edit)
            self.node3_edit = None
            # Entradas extra — guardadas en item.dig_input_nodes[]
            self._extra_node_edits = []   # list of QLineEdit, index 0 = Entrada 2
            if n_in >= 2:
                # Entrada 2 usa node3
                _n3_val = self.item.node3 if hasattr(self.item, 'node3') else ''
                self.node3_edit = QLineEdit(_n3_val)
                layout.addRow('Entrada 2 (B):', self.node3_edit)
            for i in range(2, n_in):   # Entradas 3..N usan dig_input_nodes
                _extra_nodes = getattr(self.item, 'dig_input_nodes', [])
                _val = _extra_nodes[i-2] if len(_extra_nodes) > i-2 else ''
                _edit = QLineEdit(_val)
                layout.addRow(f'Entrada {i+1}:', _edit)
                self._extra_node_edits.append(_edit)
        elif self.item.comp_type in _dig_ff_types_prop:
            lbl1, lbl2, lbl3 = ('Salida Q', 'Dato D / J', 'CLK')
            self.node1_edit = QLineEdit(self.item.node1)
            self.node2_edit = QLineEdit(self.item.node2)
            layout.addRow(lbl1 + ':', self.node1_edit)
            layout.addRow(lbl2 + ':', self.node2_edit)
            self.node3_edit = QLineEdit(self.item.node3 if hasattr(self.item,'node3') else '')
            layout.addRow(lbl3 + ':', self.node3_edit)
            self._extra_node_edits = []
        else:
            lbl1, lbl2, lbl3 = node_labels.get(self.item.comp_type, ('Nodo +', 'Nodo −', None))
            self.node1_edit = QLineEdit(self.item.node1)
            self.node2_edit = QLineEdit(self.item.node2)
            layout.addRow(lbl1 + ':', self.node1_edit)
            layout.addRow(lbl2 + ':', self.node2_edit)
            self.node3_edit = None
            if lbl3 is not None:
                self.node3_edit = QLineEdit(self.item.node3)
                layout.addRow(lbl3 + ':', self.node3_edit)
            self._extra_node_edits = []

        # Selector de color para LED
        self._led_color_combo = None
        if self.item.comp_type == 'LED':
            from PyQt6.QtWidgets import QComboBox
            self._led_color_combo = QComboBox()
            self._led_color_combo.addItems(['red', 'green', 'blue', 'yellow', 'white', 'orange'])
            self._led_color_combo.setCurrentText(getattr(self.item, 'led_color', 'red'))
            layout.addRow('Color del LED:', self._led_color_combo)

        # Campos extra para fuente AC
        self._freq_spin  = None
        self._phase_spin = None
        self._mode_combo = None
        if self.item.comp_type == 'VAC':
            from PyQt6.QtWidgets import QComboBox
            self._mode_combo = QComboBox()
            self._mode_combo.addItems(['rms', 'peak'])
            self._mode_combo.setCurrentText(self.item.ac_mode)
            layout.addRow('Modo amplitud:', self._mode_combo)

            self._freq_spin = QDoubleSpinBox()
            self._freq_spin.setRange(0.001, 1e9)
            self._freq_spin.setDecimals(3)
            self._freq_spin.setSuffix(' Hz')
            self._freq_spin.setValue(self.item.frequency)
            layout.addRow('Frecuencia:', self._freq_spin)

            self._phase_spin = QDoubleSpinBox()
            self._phase_spin.setRange(-360.0, 360.0)
            self._phase_spin.setDecimals(2)
            self._phase_spin.setSuffix(' °')
            self._phase_spin.setValue(self.item.phase_deg)
            layout.addRow('Fase:', self._phase_spin)

        # ── Campos extra para Impedancia ────────────────────────────────
        self._z_mode_combo = None
        self._z_real = self._z_imag = self._z_mag = self._z_phase = None
        if self.item.comp_type == 'Z':
            from PyQt6.QtWidgets import QComboBox, QStackedWidget, QWidget, QHBoxLayout
            self._z_mode_combo = QComboBox()
            self._z_mode_combo.addItems(['Rectangular (R + jX)', 'Fasorial |Z|∠θ'])
            self._z_mode_combo.setCurrentIndex(0 if self.item.z_mode == 'rect' else 1)
            layout.addRow("Modo entrada:", self._z_mode_combo)

            # Página rectangular
            w_rect = QWidget()
            l_rect = QHBoxLayout(w_rect)
            self._z_real = QDoubleSpinBox()
            self._z_real.setRange(-1e12, 1e12)
            self._z_real.setDecimals(6)
            self._z_real.setSuffix(" Ω")
            self._z_real.setValue(self.item.z_real)
            self._z_imag = QDoubleSpinBox()
            self._z_imag.setRange(-1e12, 1e12)
            self._z_imag.setDecimals(6)
            self._z_imag.setSuffix(" jΩ")
            self._z_imag.setValue(self.item.z_imag)
            l_rect.addWidget(QLabel("Real:"))
            l_rect.addWidget(self._z_real)
            l_rect.addWidget(QLabel("Imag:"))
            l_rect.addWidget(self._z_imag)

            # Página fasorial
            w_phas = QWidget()
            l_phas = QHBoxLayout(w_phas)
            self._z_mag = QDoubleSpinBox()
            self._z_mag.setRange(0, 1e12)
            self._z_mag.setDecimals(6)
            self._z_mag.setSuffix(" Ω")
            self._z_mag.setValue(self.item.z_mag)
            self._z_phase = QDoubleSpinBox()
            self._z_phase.setRange(-360, 360)
            self._z_phase.setDecimals(2)
            self._z_phase.setSuffix(" °")
            self._z_phase.setValue(self.item.z_phase)
            l_phas.addWidget(QLabel("|Z|:"))
            l_phas.addWidget(self._z_mag)
            l_phas.addWidget(QLabel("∠:"))
            l_phas.addWidget(self._z_phase)

            self._z_stack = QStackedWidget()
            self._z_stack.addWidget(w_rect)
            self._z_stack.addWidget(w_phas)
            layout.addRow(self._z_stack)
            self._z_mode_combo.currentIndexChanged.connect(self._z_stack.setCurrentIndex)

        # ── Campos para componentes digitales ────────────────────────────
        self._dig_inputs_spin = None
        self._dig_bits_spin   = None
        self._dig_vref_spin   = None
        self._dig_tpd_spin    = None
        self._dig_clk_edit    = None
        self._dig_anode_edit  = None

        dig_gate_types  = {'AND','OR','NOT','NAND','NOR','XOR'}
        dig_ff_types    = {'DFF','JKFF','TFF','SRFF'}
        dig_bridge_types= {'ADC_BRIDGE','DAC_BRIDGE','COMPARATOR'}
        dig_count_types = {'COUNTER','MUX2'}

        if self.item.comp_type in dig_gate_types:
            if self.item.comp_type != 'NOT':
                self._dig_inputs_spin = QDoubleSpinBox()
                self._dig_inputs_spin.setRange(2, 8)
                self._dig_inputs_spin.setDecimals(0)
                self._dig_inputs_spin.setValue(self.item.dig_inputs)
                layout.addRow('Nº entradas:', self._dig_inputs_spin)

            self._dig_tpd_spin = QDoubleSpinBox()
            self._dig_tpd_spin.setRange(0.001, 1000)
            self._dig_tpd_spin.setDecimals(3)
            self._dig_tpd_spin.setSuffix(' ns')
            self._dig_tpd_spin.setValue(self.item.dig_tpd_ns)
            layout.addRow('Retardo tpd:', self._dig_tpd_spin)

        elif self.item.comp_type in dig_ff_types:
            self._dig_clk_edit = QLineEdit(self.item.dig_clk)
            layout.addRow('Net CLK:', self._dig_clk_edit)

            self._dig_tpd_spin = QDoubleSpinBox()
            self._dig_tpd_spin.setRange(0.001, 1000)
            self._dig_tpd_spin.setDecimals(3)
            self._dig_tpd_spin.setSuffix(' ns')
            self._dig_tpd_spin.setValue(self.item.dig_tpd_ns)
            layout.addRow('Retardo tpd:', self._dig_tpd_spin)

        elif self.item.comp_type in dig_bridge_types:
            self._dig_bits_spin = QDoubleSpinBox()
            self._dig_bits_spin.setRange(1, 24)
            self._dig_bits_spin.setDecimals(0)
            self._dig_bits_spin.setValue(self.item.dig_bits_adc)
            layout.addRow('Resolución (bits):', self._dig_bits_spin)

            self._dig_vref_spin = QDoubleSpinBox()
            self._dig_vref_spin.setRange(0.1, 100.0)
            self._dig_vref_spin.setDecimals(3)
            self._dig_vref_spin.setSuffix(' V')
            self._dig_vref_spin.setValue(self.item.dig_vref)
            layout.addRow('Vref:', self._dig_vref_spin)

            self._dig_anode_edit = QLineEdit(self.item.dig_analog_node)
            layout.addRow('Nodo analógico MNA:', self._dig_anode_edit)

            self._dig_clk_edit = QLineEdit(self.item.dig_clk)
            layout.addRow('Net CLK (opcional):', self._dig_clk_edit)

        elif self.item.comp_type in dig_count_types:
            self._dig_bits_spin = QDoubleSpinBox()
            self._dig_bits_spin.setRange(1, 32)
            self._dig_bits_spin.setDecimals(0)
            self._dig_bits_spin.setValue(self.item.dig_bits)
            layout.addRow('Bits:', self._dig_bits_spin)

            self._dig_clk_edit = QLineEdit(self.item.dig_clk)
            layout.addRow('Net CLK:', self._dig_clk_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self):
        data = {
            'name':      self.name_edit.text(),
            'value':     self.value_spin.value(),
            'node1':     self.node1_edit.text(),
            'node2':     self.node2_edit.text(),
            'node3':     self.node3_edit.text() if self.node3_edit else '',
            'frequency': self._freq_spin.value()  if self._freq_spin  else 60.0,
            'phase_deg': self._phase_spin.value() if self._phase_spin else 0.0,
            'ac_mode':   self._mode_combo.currentText() if self._mode_combo else 'rms',
            'led_color': self._led_color_combo.currentText() if self._led_color_combo else 'red',
        }
        if self.item.comp_type == 'Z' and self._z_mode_combo is not None:
            data['z_mode']  = 'rect' if self._z_mode_combo.currentIndex() == 0 else 'phasor'
            data['z_real']  = self._z_real.value()
            data['z_imag']  = self._z_imag.value()
            data['z_mag']   = self._z_mag.value()
            data['z_phase'] = self._z_phase.value()
        # Campos digitales
        if self._dig_inputs_spin is not None:
            data['dig_inputs']  = int(self._dig_inputs_spin.value())
        if self._dig_bits_spin is not None:
            data['dig_bits']    = int(self._dig_bits_spin.value())
            data['dig_bits_adc']= int(self._dig_bits_spin.value())
        if self._dig_vref_spin is not None:
            data['dig_vref']    = self._dig_vref_spin.value()
        if self._dig_tpd_spin is not None:
            data['dig_tpd_ns']  = self._dig_tpd_spin.value()
        if self._dig_clk_edit is not None:
            data['dig_clk']     = self._dig_clk_edit.text()
        if self._dig_anode_edit is not None:
            data['dig_analog_node'] = self._dig_anode_edit.text()
        # Nodos de entradas extra para puertas con más de 2 entradas
        if hasattr(self, '_extra_node_edits') and self._extra_node_edits:
            data['dig_input_nodes'] = [e.text() for e in self._extra_node_edits]
        return data


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
        # Timer para simulación continua (actualiza LEDs y canvas en vivo)
        from PyQt6.QtCore import QTimer
        self._sim_timer = QTimer(self)
        self._sim_timer.setInterval(200)   # ms entre actualizaciones
        self._sim_timer.timeout.connect(self._tick_simulation)
        self._build_ui()
        self._apply_style()
        self._load_demo_circuit()

    # ── Construcción UI ──────────────────────────
    def _build_ui(self):
        # Escena + Vista
        self.scene = CircuitScene()
        self.scene.component_selected.connect(self._on_component_selected)
        self.scene.status_message.connect(self.statusBar().showMessage)
        self.scene.logic_state_toggled.connect(self._on_logic_state_toggled)

        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        # Zoom con rueda
        self.view.wheelEvent = self._wheel_zoom

        # Panel derecho (propiedades + resultados)
        self._build_right_panel()

        # Layout central: canvas + panel derecho
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.view)
        splitter.addWidget(self.right_panel)
        splitter.setSizes([1000, 280])

        self.setCentralWidget(splitter)

        # ── Toolbar PRINCIPAL (fila 1: archivo, zoom, etc.) ──────────────
        self._build_main_toolbar()

        # ── Toolbar SECUNDARIA (fila 2: categorías, herramientas, simulación)
        self._build_component_toolbar()

        # Status bar
        self.statusBar().showMessage("Listo — Selecciona una categoría para colocar componentes")

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
            ("Limpiar",      "Ctrl+L", self._clear_circuit),
            ("Zoom +",       "Ctrl+=", lambda: self.view.scale(1.2, 1.2)),
            ("Zoom −",       "Ctrl+-", lambda: self.view.scale(1/1.2, 1/1.2)),
            ("Restablecer",  "Ctrl+0", self._reset_zoom),
        ]
        for name, shortcut, fn in actions:
            if name == '|':
                tb.addSeparator()
                continue
            act = QAction(name, self)
            if shortcut:
                act.setShortcut(shortcut)
            act.triggered.connect(fn)
            tb.addAction(act)

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
        dlg = SettingsDialog(parent=self,
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
        # 1) Re-aplicar stylesheet global (lee COLORS al vuelo)
        self._apply_style()
        # 2) Fondo de la escena
        self.scene.setBackgroundBrush(QBrush(QColor(COLORS['bg'])))
        # 3) Forzar repintado de items y viewport
        self.scene.update()
        self.view.viewport().update()

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
                ('R', 'Resistor',    '━┤ZZZ├━'),
                ('C', 'Capacitor',   '━┤  ├━'),
                ('L', 'Inductor',    '━⌒⌒⌒━'),
                ('Z', 'Impedancia',  '━┤▭├━'),
            ]),
            ("Fuentes", [
                ('V',   'Fuente VDC',  '━(+)━'),
                ('VAC', 'Fuente VAC',  '━(~)━'),
                ('I',   'Fuente I',    '━(→)━'),
            ]),
            ("Semiconductores", [
                ('D',       'Diodo',       '━|▷|━'),
                ('LED',     'LED',         '━|▷|★'),
                ('BJT_NPN', 'BJT NPN',     '━(NPN)'),
                ('BJT_PNP', 'BJT PNP',     '━(PNP)'),
                ('NMOS',    'MOSFET N',    '━[N]━'),
                ('PMOS',    'MOSFET P',    '━[P]━'),
                ('OPAMP',   'Op-Amp',      '━[▷]━'),
            ]),
            ("Referencia", [
                ('GND',  'Tierra',   '⏚'),
                ('NODE', 'Nodo',     '•'),
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
        dialog = ComponentPickerDialog(category_name, items, self)
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
    def _toggle_simulation(self, checked: bool):
        """Analiza el circuito y despacha automáticamente al solver correcto."""
        if not checked:
            self._stop_simulation()
            return

        pin_node = self.scene.extract_netlist()
        std_name = DEFAULT_LOGIC_STANDARD
        analyzer = CircuitAnalyzer(logic_standard=std_name)
        flags = analyzer.analyze(self.scene.components, pin_node)

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
        """Detiene la simulación y apaga todos los LEDs."""
        self._sim_running = False
        self._sim_timer.stop()
        self.run_btn.setChecked(False)
        self.run_btn.setText("▶  SIMULAR AUTO")
        for item in self.scene.components:
            if item.comp_type == 'LED':
                item.led_on = False
                item.update()

    def _tick_simulation(self):
        """Llamado por QTimer: re-corre DC silenciosamente para actualizar LEDs."""
        if self._sim_running:
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
            pin_node = self.scene.extract_netlist()
        if flags is None:
            std_name = DEFAULT_LOGIC_STANDARD
            analyzer = CircuitAnalyzer(logic_standard=std_name)
            flags = analyzer.analyze(self.scene.components, pin_node)

        std_name = DEFAULT_LOGIC_STANDARD
        out = ["═══ SIMULACIÓN AUTOMÁTICA ═══", f"  {flags.summary()}", ""]
        if flags.warnings:
            out.extend([f"  ⚠ {w}" for w in flags.warnings]); out.append("")

        # ── Construir componentes analógicos ─────────────────────────────
        analog_comps, build_errors = [], []
        for item in self.scene.components:
            if item.comp_type in ComponentItem.DIGITAL_TYPES:
                continue
            n1 = item.node1.strip() or pin_node.get(f"{item.name}__p1", f"iso_{item.name}")
            n2 = item.node2.strip() or pin_node.get(f"{item.name}__p2", "0")
            n3 = (item.node3.strip() if hasattr(item, "node3") and item.node3.strip()
                  else pin_node.get(f"{item.name}__p3", ""))
            try:
                ct = item.comp_type
                if ct == "R" and item.value > 0:
                    analog_comps.append(Resistor(item.name, n1, n2, item.value))
                elif ct == "V":
                    analog_comps.append(VoltageSource(item.name, n1, n2, item.value))
                elif ct == "VAC":
                    analog_comps.append(VoltageSourceAC(
                        item.name, n1, n2, amplitude=item.value,
                        frequency=item.frequency, phase_deg=item.phase_deg, mode=item.ac_mode))
                elif ct == "I":
                    analog_comps.append(CurrentSource(item.name, n1, n2, item.value))
                elif ct == "C" and item.value > 0:
                    analog_comps.append(Capacitor(item.name, n1, n2, item.value))
                elif ct == "L" and item.value > 0:
                    analog_comps.append(Inductor(item.name, n1, n2, item.value))
                elif ct in ("D", "LED"):
                    analog_comps.append(Diode(item.name, n1, n2,
                                              Is=item.value if item.value > 0 else 1e-14))
                elif ct in ("BJT_NPN", "BJT_PNP"):
                    t = "NPN" if ct == "BJT_NPN" else "PNP"
                    analog_comps.append(BJT(item.name, n1, n3 or f"b_{item.name}", n2,
                                            type_=t, Bf=item.value if item.value > 0 else 100))
                elif ct in ("NMOS", "PMOS"):
                    t = "NMOS" if ct == "NMOS" else "PMOS"
                    analog_comps.append(MOSFET(item.name, n1, n3 or f"g_{item.name}", n2,
                                               type_=t, Kn=item.value if item.value > 0 else 1e-3))
                elif ct == "OPAMP":
                    analog_comps.append(OpAmp(item.name, n1, n3 or f"vp_{item.name}", n2,
                                              A=item.value if item.value > 0 else 1e5))
                elif ct == "Z":
                    import math as _math
                    Z_val = (complex(item.z_real, item.z_imag) if item.z_mode == "rect"
                             else complex(item.z_mag*_math.cos(_math.radians(item.z_phase)),
                                          item.z_mag*_math.sin(_math.radians(item.z_phase))))
                    if abs(Z_val) > 1e-12:
                        analog_comps.append(Impedance(item.name, n1, n2, Z_val))
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
                for item in self.scene.components:
                    n1 = item.node1.strip() or pin_node.get(f"{item.name}__p1", "")
                    n2 = item.node2.strip() or pin_node.get(f"{item.name}__p2", "0")
                    item.result_voltage = dc["voltages"].get(n1)
                    if item.comp_type == "LED":
                        item.led_on = False
                        op = dc.get("operating_points", {}).get(item.name, {})
                        vd = op.get("Vd", op.get("vd")) if op else None
                        item.led_on = float(vd) > 0.3 if vd is not None else (
                            (dc["voltages"].get(n1, 0) - dc["voltages"].get(n2, 0)) > 0.3)
                    item.update()
            else:
                out.append(f"  ✗ {dc['error']}")
            out.append("")

        # ── AC ────────────────────────────────────────────────────────────
        if flags.has_ac and analog_comps:
            freq = next((it.frequency for it in self.scene.components
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
            for item in self.scene.components:
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

        # Extraer nodos automaticos desde los cables del canvas
        pin_node = self.scene.extract_netlist()

        for item in self.scene.components:
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
                    # LOGIC_STATE: modelar como fuente de voltaje ideal
                    if item.comp_type == 'LOGIC_STATE':
                        std_name = DEFAULT_LOGIC_STANDARD
                        std = LOGIC_STANDARDS.get(std_name, DEFAULT_STANDARD)
                        v_out = std.Voh if item.value else std.Vol
                        out_node = item.node1.strip() or pin_node.get(f"{item.name}__p1", f"ls_{item.name}")
                        if out_node and out_node not in ('0', 'gnd', 'GND'):
                            components.append(VoltageSource(item.name, out_node, '0', v_out))
                    continue
                if item.comp_type == 'R':
                    if item.value <= 0:
                        errors.append(f"{item.name}: resistencia debe ser > 0")
                        continue
                    components.append(Resistor(item.name, n1, n2, item.value))
                elif item.comp_type == 'V':
                    components.append(VoltageSource(item.name, n1, n2, item.value))
                elif item.comp_type == 'VAC':
                    # En DC la fuente AC vale 0 V (valor medio de senoidal)
                    components.append(VoltageSource(item.name, n1, n2, 0.0))
                elif item.comp_type == 'I':
                    components.append(CurrentSource(item.name, n1, n2, item.value))
                elif item.comp_type == 'C':
                    components.append(Capacitor(item.name, n1, n2, item.value))
                elif item.comp_type == 'L':
                    components.append(Inductor(item.name, n1, n2, item.value))
                elif item.comp_type in ('D', 'LED'):
                    Is = item.value if item.value > 0 else 1e-14
                    components.append(Diode(item.name, n1, n2, Is=Is))
                elif item.comp_type in ('BJT_NPN', 'BJT_PNP'):
                    # n1=Colector, n2=Emisor, n3=Base
                    type_ = 'NPN' if item.comp_type == 'BJT_NPN' else 'PNP'
                    Bf = item.value if item.value > 0 else 100.0
                    components.append(BJT(item.name, n1, n3 or f'b_{item.name}', n2,
                                          type_=type_, Bf=Bf))
                elif item.comp_type in ('NMOS', 'PMOS'):
                    # n1=Drain, n2=Source, n3=Gate
                    type_ = 'NMOS' if item.comp_type == 'NMOS' else 'PMOS'
                    Kn = item.value if item.value > 0 else 1e-3
                    components.append(MOSFET(item.name, n1, n3 or f'g_{item.name}', n2,
                                             type_=type_, Kn=Kn))
                elif item.comp_type == 'OPAMP':
                    # n1=Salida, n2=Entrada−, n3=Entrada+
                    A = item.value if item.value > 0 else 1e5
                    components.append(OpAmp(item.name, n1, n3 or f'vp_{item.name}', n2, A=A))
                elif item.comp_type == 'Z':
                    if item.z_mode == 'rect':
                        Z_val = complex(item.z_real, item.z_imag)
                    else:
                        mag = item.z_mag
                        ph_rad = math.radians(item.z_phase)
                        Z_val = complex(mag * math.cos(ph_rad), mag * math.sin(ph_rad))
                    if abs(Z_val.real) > 1e-12 or abs(Z_val.imag) > 1e-12:
                        components.append(Impedance(item.name, n1, n2, Z_val))
            except Exception as e:
                errors.append(f"{item.name}: {e}")

        # ── Excluir LEDs/Diodos cuyo ánodo es salida exclusiva de puerta digital ──
        # Esos componentes no tienen driver analógico → matriz singular.
        # Se evalúan luego con _evaluate_digital_gates.
        _gate_types_dc = {'AND','OR','NOT','NAND','NOR','XOR','NAND','NOR',
                          'DFF','JKFF','TFF','SRFF','MUX2','COUNTER'}
        _dig_out_nodes = set()
        for _item in self.scene.components:
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
            for _it in self.scene.components:
                if _it.comp_type == 'LOGIC_STATE':
                    _v = std.Voh if _it.value else std.Vol
                    _net = _it.node1.strip() or pin_node.get(f"{_it.name}__p1", "")
                    if _net:
                        _dig_voltages[_net] = _v
            self._evaluate_digital_gates(pin_node, _dig_voltages, silent=silent, out=out)
            if not silent:
                self.results_text.setPlainText('\n'.join(out))
            self.scene.update()
            return

        # Mostrar netlist extraida antes de simular
        if not silent:
            out_pre = ["═══ NETLIST EXTRAIDA ═══"]
            for item in self.scene.components:
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
            for item in self.scene.components:
                auto_n1 = pin_node.get(f"{item.name}__p1", '')
                auto_n2 = pin_node.get(f"{item.name}__p2", '0')
                n1 = item.node1.strip() if item.node1.strip() else auto_n1
                n2 = item.node2.strip() if item.node2.strip() else auto_n2
                if n1 in result['voltages']:
                    item.result_voltage = result['voltages'][n1]
                else:
                    item.result_voltage = None
                # Encender/apagar LED según corriente que atraviesa el diodo
                if item.comp_type == 'LED':
                    led_on = False
                    # Método 1: usar operating_points del solver (más confiable)
                    op = result.get('operating_points', {}).get(item.name, {})
                    if op:
                        vd = op.get('Vd', op.get('vd', op.get('V', None)))
                        id_ = op.get('Id', op.get('id', op.get('I', None)))
                        if vd is not None:
                            led_on = float(vd) > 0.3
                        elif id_ is not None:
                            led_on = float(id_) > 1e-6
                    # Método 2: diferencia de voltaje entre nodos
                    if not led_on:
                        v_a = result['voltages'].get(n1, None)
                        v_k = result['voltages'].get(n2, None)
                        if v_a is not None and v_k is not None:
                            led_on = (v_a - v_k) > 0.3
                    item.led_on = led_on
                item.update()
                if hasattr(item, 'scene') and item.scene():
                    item.scene().update(item.mapToScene(item.boundingRect()).boundingRect())

            # Debug LED — mostrar info de nodos y voltajes del LED
            led_items = [it for it in self.scene.components if it.comp_type == 'LED']
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
            self._evaluate_digital_gates(pin_node, result['voltages'], silent=silent, out=out)

        if not silent:
            self.results_text.setPlainText('\n'.join(out))
        self.scene.update()


    def _evaluate_digital_gates(self, pin_node, dc_voltages, silent=False, out=None):
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
        gate_items = [it for it in self.scene.components if it.comp_type in _gmap]
        if not gate_items:
            return
        if out is not None and not silent:
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
            for led in self.scene.components:
                if led.comp_type == 'LED':
                    led_anode = led.node1.strip() or pin_node.get(f'{led.name}__p1', '')
                    if led_anode == out_node:
                        led.led_on = (v_out > 0.3)
                        led.update()
            if out is not None and not silent:
                out.append(f"  {item.name}_Y = {y}  ({'HIGH' if y else 'LOW'})")
    # ── Panel de propiedades ─────────────────────
    def _run_simulation_ac(self):
        """Análisis AC de frecuencia única con triángulo de potencia."""
        from PyQt6.QtWidgets import QInputDialog

        # Buscar fuente VAC en el canvas para leer la frecuencia
        vac_items = [it for it in self.scene.components if it.comp_type == 'VAC']
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
        pin_node   = self.scene.extract_netlist()

        for item in self.scene.components:
            auto_n1 = pin_node.get(f"{item.name}__p1", f'iso_{item.name}_p')
            auto_n2 = pin_node.get(f"{item.name}__p2", '0')
            n1 = item.node1.strip() if item.node1.strip() else auto_n1
            n2 = item.node2.strip() if item.node2.strip() else auto_n2

            try:
                if item.comp_type in ComponentItem.DIGITAL_TYPES:
                    continue
                if item.comp_type == 'R':
                    if item.value <= 0:
                        errors.append(f"{item.name}: R debe ser > 0"); continue
                    components.append(Resistor(item.name, n1, n2, item.value))
                elif item.comp_type == 'V':
                    # Fuente DC en análisis AC → contribución 0
                    components.append(VoltageSource(item.name, n1, n2, 0.0))
                elif item.comp_type == 'VAC':
                    components.append(VoltageSourceAC(
                        item.name, n1, n2,
                        amplitude=item.value,
                        frequency=item.frequency,
                        phase_deg=item.phase_deg,
                        mode=item.ac_mode))
                elif item.comp_type == 'I':
                    components.append(CurrentSource(item.name, n1, n2, item.value))
                elif item.comp_type == 'C':
                    if item.value > 0:
                        components.append(Capacitor(item.name, n1, n2, item.value))
                elif item.comp_type == 'L':
                    if item.value > 0:
                        components.append(Inductor(item.name, n1, n2, item.value))
                elif item.comp_type == 'Z':
                    if item.z_mode == 'rect':
                        Z_val = complex(item.z_real, item.z_imag)
                    else:
                        mag = item.z_mag
                        ph_rad = math.radians(item.z_phase)
                        Z_val = complex(mag * math.cos(ph_rad), mag * math.sin(ph_rad))
                    if abs(Z_val) > 1e-12:
                        components.append(Impedance(item.name, n1, n2, Z_val))
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

        pin_node = self.scene.extract_netlist()
        analog_comps = []
        errors = []

        # ── Construir circuito analógico ─────────────────────────────────
        for item in self.scene.components:
            if item.comp_type in ComponentItem.DIGITAL_TYPES:
                continue
            auto_n1 = pin_node.get(f"{item.name}__p1", f'iso_{item.name}_p')
            auto_n2 = pin_node.get(f"{item.name}__p2", '0')
            n1 = item.node1.strip() if item.node1.strip() else auto_n1
            n2 = item.node2.strip() if item.node2.strip() else auto_n2
            try:
                if item.comp_type == 'R' and item.value > 0:
                    analog_comps.append(Resistor(item.name, n1, n2, item.value))
                elif item.comp_type == 'V':
                    analog_comps.append(VoltageSource(item.name, n1, n2, item.value))
                elif item.comp_type == 'VAC':
                    analog_comps.append(VoltageSource(item.name, n1, n2, 0.0))
                elif item.comp_type == 'I':
                    analog_comps.append(CurrentSource(item.name, n1, n2, item.value))
                elif item.comp_type == 'C' and item.value > 0:
                    analog_comps.append(Capacitor(item.name, n1, n2, item.value))
                elif item.comp_type == 'L' and item.value > 0:
                    analog_comps.append(Inductor(item.name, n1, n2, item.value))
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

        for item in self.scene.components:
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
        dlg = PowerTriangleDialog(self._last_ac_result, parent=self)
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

    def _on_component_selected(self, item):
        self.prop_table.setRowCount(0)
        if item is None:
            return

        # Etiquetas de terminales según tipo de componente
        terminal_labels = {
            'R':       ('Nodo 1',            'Nodo 2',             None),
            'C':       ('Nodo 1',            'Nodo 2',             None),
            'L':       ('Nodo 1',            'Nodo 2',             None),
            'V':       ('Nodo + (ánodo)',    'Nodo − (cátodo)',    None),
            'I':       ('Nodo + (salida)',   'Nodo − (entrada)',   None),
            'D':       ('Ánodo (A)',         'Cátodo (K)',         None),
            'LED':     ('Ánodo (A)',         'Cátodo (K)',         None),
            'BJT_NPN': ('Colector (C)',      'Emisor (E)',         'Base (B)'),
            'BJT_PNP': ('Colector (C)',      'Emisor (E)',         'Base (B)'),
            'NMOS':    ('Drain (D)',         'Source (S)',         'Gate (G)'),
            'PMOS':    ('Drain (D)',         'Source (S)',         'Gate (G)'),
            'OPAMP':   ('Salida (OUT)',      'Entrada − (V−)',     'Entrada + (V+)'),
        }
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

        _dig_gate_types_tbl = {'AND','OR','NOT','NAND','NOR','XOR'}
        _dig_ff_types_tbl   = {'DFF','JKFF','TFF','SRFF'}

        if item.comp_type in _dig_gate_types_tbl:
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
        elif item.comp_type in _dig_ff_types_tbl:
            rows.append(("Salida Q",    _node_display(item.node1, f"{item.name}__p1")))
            rows.append(("Dato D / J",  _node_display(item.node2, f"{item.name}__p2")))
            rows.append(("CLK",         _node_display(
                item.node3 if hasattr(item,'node3') else '', f"{item.name}__p3")))
        elif item.comp_type == 'LOGIC_STATE':
            rows.append(("Salida",  _node_display(item.node1, f"{item.name}__p1")))
            rows.append(("Estado",  "1 (HIGH)" if item.value else "0 (LOW)"))
        else:
            lbl1, lbl2, lbl3 = terminal_labels.get(item.comp_type, ('Nodo +', 'Nodo −', None))
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
        s.place_component('V',   QPointF(-120,   0), 'V1', 10.0, 'V',  'A', '0')
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
            "¿Descartar el circuito actual?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._clear_circuit()
            self._current_file = None
            self.setWindowTitle("CircuitSim — Simulador de Circuitos")
            self._load_demo_circuit()

    def _clear_circuit(self):
        for item in self.scene.components + self.scene.wires:
            self.scene.removeItem(item)
        self.scene.components.clear()
        self.scene.wires.clear()
        self.scene._comp_counter.clear()
        self.results_text.clear()
        # Limpiar también el archivo actual para no sobreescribir accidentalmente
        self._current_file = None
        self.setWindowTitle("CircuitSim — Simulador de Circuitos")

    # ── Guardar (.csin) ──────────────────────────
    def _save_circuit(self):
        path = self._current_file
        # Si ya hay un archivo guardado, sobreescribir directamente sin diálogo
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self, "Guardar circuito", "",
                "CircuitSim (*.csin);;Todos los archivos (*)"
            )
        if not path:
            return
        if not path.endswith('.csin'):
            path += '.csin'

        data = {
            'version': '1.0',
            'components': [],
            'wires': []
        }

        for item in self.scene.components:
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
            data['components'].append(entry)

        for wire in self.scene.wires:
            line = wire.line()
            data['wires'].append({
                'x1': line.x1(), 'y1': line.y1(),
                'x2': line.x2(), 'y2': line.y2(),
            })

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self._current_file = path
        self.setWindowTitle(f"CircuitSim — {os.path.basename(path)}")
        self.statusBar().showMessage(f"Guardado: {path}")

    # ── Guardar como (.csin) ─────────────────────
    def _save_circuit_as(self):
        """Siempre abre el diálogo, permite elegir nuevo nombre/ubicación."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Guardar circuito como", "",
            "CircuitSim (*.csin);;Todos los archivos (*)"
        )
        if not path:
            return
        if not path.endswith('.csin'):
            path += '.csin'
        # Reusar la lógica de guardado apuntando al nuevo path
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

        self._clear_circuit()

        for c in data.get('components', []):
            item = self.scene.place_component(
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
            # Restaurar atributos de fuente AC
            if c['type'] == 'VAC':
                item.frequency = c.get('frequency', 60.0)
                item.phase_deg = c.get('phase_deg', 0.0)
                item.ac_mode   = c.get('ac_mode', 'rms')
            if c['type'] == 'LED':
                item.led_color = c.get('led_color', 'red')
            # Restaurar atributos de impedancia
            if c['type'] == 'Z':
                item.z_real  = c.get('z_real',  100.0)
                item.z_imag  = c.get('z_imag',  0.0)
                item.z_mag   = c.get('z_mag',   100.0)
                item.z_phase = c.get('z_phase', 0.0)
                item.z_mode  = c.get('z_mode',  'rect')

        for w in data.get('wires', []):
            wire = WireItem(QPointF(w['x1'], w['y1']), QPointF(w['x2'], w['y2']))
            self.scene.addItem(wire)
            self.scene.wires.append(wire)

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
# DIÁLOGO DE CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════
class SettingsDialog(QDialog):
    """
    Ventana de configuración general de la app.

    Está organizada en secciones:
      • Apariencia → Tema (combo + carpeta de temas externos).

    Pensada para crecer: añadir secciones (Simulación, Atajos, etc.)
    consiste en agregar nuevos QGroupBox dentro de _build_ui.
    """

    def __init__(self,
                 parent=None,
                 current_theme_id: str = DEFAULT_THEME_ID,
                 on_theme_change=None):
        super().__init__(parent)
        self.setWindowTitle("Configuración")
        self.setMinimumSize(560, 380)
        self._current_theme_id = current_theme_id
        self._on_theme_change  = on_theme_change   # callback(theme_id)
        self._build_ui()

    # ── Construcción de la UI ──────────────────────────────────────────────
    def _build_ui(self):
        from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGroupBox,
                                      QPushButton, QLabel, QComboBox,
                                      QDialogButtonBox)
        main = QVBoxLayout(self)
        main.setSpacing(10)

        # ── Sección: Apariencia / Tema ─────────────────────────────────────
        gb_theme = QGroupBox("Apariencia")
        gl = QVBoxLayout(gb_theme)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Tema:"))
        self.theme_combo = QComboBox()
        self.theme_combo.setMinimumWidth(240)
        self.theme_combo.setToolTip(
            "Cambia el esquema de colores de la app.\n"
            "Se aplica al instante y se recuerda entre sesiones.")
        self._populate_theme_combo()
        self.theme_combo.currentIndexChanged.connect(self._on_combo_changed)
        row1.addWidget(self.theme_combo)
        row1.addStretch()
        gl.addLayout(row1)

        # Descripción del tema seleccionado
        self.theme_desc = QLabel("")
        self.theme_desc.setWordWrap(True)
        self.theme_desc.setFont(QFont('Consolas', 9))
        self.theme_desc.setStyleSheet(f"color: {COLORS['text_dim']};")
        gl.addWidget(self.theme_desc)

        # Botones para gestionar temas externos
        row2 = QHBoxLayout()
        btn_open = QPushButton("📁  Abrir carpeta de temas")
        btn_open.setToolTip(
            "Abre la carpeta donde puedes dejar archivos .json\n"
            "para añadir tus propios temas.")
        btn_open.clicked.connect(self._open_themes_folder)
        row2.addWidget(btn_open)

        btn_reload = QPushButton("🔄  Recargar lista")
        btn_reload.setToolTip(
            "Vuelve a escanear las carpetas de temas tras añadir\n"
            "o quitar archivos .json sin reiniciar la app.")
        btn_reload.clicked.connect(self._reload_themes)
        row2.addWidget(btn_reload)

        btn_export = QPushButton("💾  Exportar tema actual…")
        btn_export.setToolTip(
            "Guarda el tema seleccionado como plantilla .json para\n"
            "que puedas modificarlo y crear el tuyo.")
        btn_export.clicked.connect(self._export_current_theme)
        row2.addWidget(btn_export)

        row2.addStretch()
        gl.addLayout(row2)

        # Hint informativo
        hint = QLabel(
            "Para añadir un tema instalable por separado, deja un archivo .json\n"
            "con el formato indicado en themes/README.md dentro de la carpeta\n"
            "y pulsa «Recargar lista» (o reinicia la app)."
        )
        hint.setWordWrap(True)
        hint.setFont(QFont('Consolas', 8))
        hint.setStyleSheet(f"color: {COLORS['text_dim']};")
        gl.addWidget(hint)

        main.addWidget(gb_theme)
        main.addStretch()

        # ── Botón cerrar ───────────────────────────────────────────────────
        bbox = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bbox.rejected.connect(self.accept)
        bbox.accepted.connect(self.accept)
        main.addWidget(bbox)

        # Refresca descripción del tema actual
        self._refresh_theme_description()

    # ── Lógica del combo ───────────────────────────────────────────────────
    def _populate_theme_combo(self):
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        for entry in THEME_MANAGER.list_themes():
            label = entry['name']
            if entry['source'] != 'builtin':
                label += '  (externo)'
            self.theme_combo.addItem(label, entry['id'])
        idx = self.theme_combo.findData(self._current_theme_id)
        if idx < 0:
            idx = 0
        self.theme_combo.setCurrentIndex(idx)
        self.theme_combo.blockSignals(False)

    def _on_combo_changed(self, _index: int):
        tid = self.theme_combo.currentData()
        if not tid or tid == self._current_theme_id:
            self._refresh_theme_description()
            return
        self._current_theme_id = tid
        if self._on_theme_change:
            self._on_theme_change(tid)
        self._refresh_theme_description()

    def _refresh_theme_description(self):
        tid  = self.theme_combo.currentData()
        meta = THEME_MANAGER.get_theme_meta(tid) if tid else None
        if meta is None:
            self.theme_desc.setText("")
            return
        src = ("Origen: built-in" if meta['source'] == 'builtin'
               else f"Origen: {meta['source']}")
        desc = meta.get('description', '')
        self.theme_desc.setText(f"  {desc}\n  {src}" if desc else f"  {src}")

    # ── Acciones ───────────────────────────────────────────────────────────
    def _open_themes_folder(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui  import QDesktopServices
        path = THEME_MANAGER.ensure_user_themes_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _reload_themes(self):
        THEME_MANAGER.refresh()
        self._populate_theme_combo()
        self._refresh_theme_description()
        QMessageBox.information(
            self, "Temas recargados",
            f"Se descubrieron {len(THEME_MANAGER.list_themes())} temas en total."
        )

    def _export_current_theme(self):
        tid = self.theme_combo.currentData()
        if not tid:
            return
        meta = THEME_MANAGER.get_theme_meta(tid)
        suggested = f"{tid}_copia.json"
        # Sugerir guardar en la carpeta de temas de usuario
        default_dir = THEME_MANAGER.ensure_user_themes_dir()
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar tema como plantilla",
            os.path.join(default_dir, suggested),
            "Tema JSON (*.json)")
        if not path:
            return
        ok = THEME_MANAGER.export_theme_template(tid, path)
        if ok:
            QMessageBox.information(
                self, "Tema exportado",
                f"Plantilla guardada en:\n{path}\n\n"
                "Edita los colores y pulsa «Recargar lista» para verlo en el selector.")
        else:
            QMessageBox.warning(
                self, "Error",
                f"No se pudo guardar el archivo:\n{path}")


# ══════════════════════════════════════════════════════════════
# DIÁLOGO TRIÁNGULO DE POTENCIA
# ══════════════════════════════════════════════════════════════
class PowerTriangleDialog(QDialog):
    """
    Ventana emergente con:
      - Triángulo de potencia dibujado (P, Q, S, ángulo φ)
      - Tabla de potencias por componente
      - Corrección de factor de potencia interactiva
    """
    def __init__(self, ac_result: dict, parent=None):
        super().__init__(parent)
        self.ac_result = ac_result
        self.setWindowTitle("Triángulo de Potencia")
        self.setMinimumSize(620, 580)
        self._build_ui()

    def _build_ui(self):
        from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGroupBox,
                                      QDoubleSpinBox, QPushButton, QLabel,
                                      QTextEdit, QSplitter, QComboBox)
        main = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # ── Triángulo ────────────────────────────────────────────────────
        self.canvas = _PowerTriangleCanvas(self.ac_result['total'])
        self.canvas.setMinimumHeight(260)
        splitter.addWidget(self.canvas)

        # ── Corrección de FP ─────────────────────────────────────────────
        box = QGroupBox("Corrección de Factor de Potencia")
        box_layout = QHBoxLayout(box)

        box_layout.addWidget(QLabel("FP objetivo:"))
        self.fp_spin = QDoubleSpinBox()
        self.fp_spin.setRange(0.01, 1.0)
        self.fp_spin.setDecimals(3)
        self.fp_spin.setSingleStep(0.01)
        self.fp_spin.setValue(1.0)
        box_layout.addWidget(self.fp_spin)

        # Selector de tipo de FP resultante
        box_layout.addSpacing(10)
        box_layout.addWidget(QLabel("Tipo:"))
        self.target_combo = QComboBox()
        # (texto visible, valor enviado al solver)
        self.target_combo.addItem("Auto (mismo dominio)", 'auto')
        self.target_combo.addItem("Inductivo (Q > 0)",     'inductive')
        self.target_combo.addItem("Capacitivo (Q < 0)",    'capacitive')
        self.target_combo.setToolTip(
            "Auto: mantiene el dominio actual (capacitivo↔capacitivo,\n"
            "inductivo↔inductivo).\n"
            "Inductivo: fuerza un Q resultante positivo (puede cruzar\n"
            "de capacitivo a inductivo agregando un inductor grande).\n"
            "Capacitivo: fuerza un Q resultante negativo."
        )
        box_layout.addWidget(self.target_combo)

        self.correct_btn = QPushButton("Calcular corrección")
        self.correct_btn.clicked.connect(self._on_correct)
        box_layout.addWidget(self.correct_btn)
        box_layout.addStretch()

        self.corr_label = QLabel("")
        self.corr_label.setWordWrap(True)
        self.corr_label.setFont(QFont('Consolas', 9))

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.addWidget(box)
        bl.addWidget(self.corr_label)
        splitter.addWidget(bottom)

        main.addWidget(splitter)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton("🔍 Restablecer vista")
        reset_btn.clicked.connect(self.canvas.reset_view)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        close_btn = QPushButton("Cerrar")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        main.addLayout(btn_row)

    def _on_correct(self):
        from engine import MNASolver
        solver       = MNASolver()
        total        = self.ac_result['total']
        freq         = self.ac_result['frequency']
        fp_tgt       = self.fp_spin.value()
        target_type  = self.target_combo.currentData() or 'auto'
        res          = solver.correct_power_factor(
            total, freq, fp_tgt, target_type=target_type)

        if 'error' in res:
            self.corr_label.setText(f"⚠ {res['error']}")
            return

        tipo        = res['type']
        val         = res['value']
        Q_corr      = res['Q_corr']
        fp_new      = res['fp_new']
        fp_type_new = res.get('fp_type_new', '')
        note        = res.get('note', '')
        form        = res['formula']
        tt_used     = res.get('target_type', 'auto')

        if tipo == 'capacitor':
            val_str = f"C = {val*1e6:.4f} µF  (normalizado a 1 Vrms)"
            emoji   = "⚡ Capacitor"
        else:
            val_str = f"L = {val*1e3:.4f} mH  (normalizado a 1 Vrms)"
            emoji   = "🔄 Inductor"

        modo_map = {'auto': 'Auto', 'inductive': 'Inductivo',
                    'capacitive': 'Capacitivo'}
        modo_str = modo_map.get(tt_used, tt_used)

        text = (
            f"  Modo objetivo:      {modo_str}\n"
            f"  Elemento corrector: {emoji} en PARALELO\n"
            f"  {val_str}\n"
            f"  Q a compensar:      {Q_corr:.4f} VAR\n"
            f"  FP resultante:      {fp_new:.4f}  ({fp_type_new})\n"
            f"  Fórmula:            {form}\n"
            f"  📌 {note}"
        )
        self.corr_label.setText(text)
        # Actualizar triángulo con la corrección
        self.canvas.set_correction(res)
        self.canvas.update()


class _PowerTriangleCanvas(QWidget):
    """Widget que dibuja el triángulo de potencia S, P, Q, ángulo φ.
    Soporta zoom con rueda del ratón y pan con click+arrastre."""

    def __init__(self, total: dict, parent=None):
        super().__init__(parent)
        self.total       = total
        self._correction = None
        self._zoom       = 1.0
        self._pan_x      = 0.0
        self._pan_y      = 0.0
        self._dragging   = False
        self._last_pos   = None

    def set_correction(self, corr: dict):
        self._correction = corr
        self.update()

    def wheelEvent(self, event):
        """Zoom con rueda del ratón centrado en el cursor."""
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15
        old_zoom = self._zoom
        self._zoom *= factor
        self._zoom = max(0.2, min(self._zoom, 10.0))
        # Zoom centrado en el cursor
        mx = event.position().x()
        my = event.position().y()
        self._pan_x = mx - (mx - self._pan_x) * (self._zoom / old_zoom)
        self._pan_y = my - (my - self._pan_y) * (self._zoom / old_zoom)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_pos = event.pos()

    def mouseMoveEvent(self, event):
        if self._dragging and self._last_pos is not None:
            dx = event.pos().x() - self._last_pos.x()
            dy = event.pos().y() - self._last_pos.y()
            self._pan_x += dx
            self._pan_y += dy
            self._last_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._last_pos = None

    def reset_view(self):
        self._zoom  = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QPen, QColor, QFont, QPolygonF
        import math

        P  = self.total.get('P', 0.0)
        Q  = self.total.get('Q', 0.0)
        S  = self.total.get('S', 1.0)
        fp = self.total.get('fp', 0.0)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#1a1a2e'))

        W = self.width()
        H = self.height()

        # Escala: S ocupa ~55% del ancho disponible
        if S < 1e-12:
            painter.setPen(QPen(QColor('#aaaaaa'), 1))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Sin datos de potencia")
            return

        base_scale = (W * 0.55) / S
        scale = base_scale * self._zoom

        # Origen centrado en el widget, ajustado por pan
        ox = int(W * 0.15) + int(self._pan_x)

        # ── Posicionar oy considerando Q original Y Q corregido ────────────
        # Si la corrección cruza el dominio (p.ej. capacitivo → inductivo)
        # necesitamos espacio arriba Y abajo, así que centramos.
        Q_values = [Q]
        if self._correction is not None:
            Q_values.append(self._correction.get('Q_new', Q))
        has_up   = any(q >  1e-12 for q in Q_values)   # apunta hacia arriba
        has_down = any(q < -1e-12 for q in Q_values)   # apunta hacia abajo

        if has_up and has_down:
            # Cruza dominios → centrar verticalmente
            oy = int(H * 0.5) + int(self._pan_y)
        elif has_down:
            # Sólo capacitivo → origen arriba para dejar espacio abajo
            oy = int(H * 0.35) + int(self._pan_y)
        else:
            # Sólo inductivo (o unitario) → origen abajo para dejar espacio arriba
            oy = int(H * 0.65) + int(self._pan_y)

        # Q>0 inductivo → triángulo hacia ARRIBA (Qy negativo en coords pantalla)
        # Q<0 capacitivo → triángulo hacia ABAJO  (Qy positivo en coords pantalla)
        Px = int(P * scale)
        Qy = int(-Q * scale)   # signo correcto: Q+ → arriba, Q- → abajo

        pen_P   = QPen(QColor('#e74c3c'), 3)   # rojo  — P activa
        pen_Q   = QPen(QColor('#3498db'), 3)   # azul  — Q reactiva
        pen_S   = QPen(QColor('#2ecc71'), 3)   # verde — S aparente
        pen_ax  = QPen(QColor('#444466'), 1)   # gris  — ejes
        pen_c   = QPen(QColor('#f39c12'), 2, Qt.PenStyle.DashLine)

        font_lbl = QFont('Consolas', 9, QFont.Weight.Bold)
        font_ax  = QFont('Consolas', 8)
        painter.setFont(font_lbl)

        def arrow(painter, pen, x1, y1, x2, y2, label='', lside='end'):
            painter.setPen(pen)
            painter.drawLine(x1, y1, x2, y2)
            dx = x2 - x1; dy = y2 - y1
            L  = math.sqrt(dx*dx + dy*dy)
            if L < 1: return
            ux = dx/L; uy = dy/L
            px = -uy;  py = ux
            sz = 8
            tip = QPolygonF([
                QPointF(x2, y2),
                QPointF(x2 - sz*ux + sz*0.4*px, y2 - sz*uy + sz*0.4*py),
                QPointF(x2 - sz*ux - sz*0.4*px, y2 - sz*uy - sz*0.4*py),
            ])
            painter.setBrush(QColor(pen.color()))
            painter.drawPolygon(tip)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if label:
                painter.setPen(QPen(pen.color(), 1))
                if lside == 'end':
                    painter.drawText(int(x2)+6, int(y2)+5, label)
                else:
                    mx = (x1+x2)//2; my = (y1+y2)//2
                    painter.drawText(mx+6, my-4, label)

        # ── Ejes de referencia ────────────────────────────────────────────
        ax_len = max(abs(Px), abs(Qy), 60) + 50
        painter.setFont(font_ax)

        # Eje X (potencia activa — positiva a la derecha)
        arrow(painter, pen_ax, ox - 20, oy, ox + ax_len, oy, '', 'end')
        painter.setPen(QPen(QColor('#666688'), 1))
        painter.drawText(ox + ax_len + 4, oy + 4, "P (W)")

        # Eje Y (potencia reactiva — positiva hacia arriba = Q inductivo)
        arrow(painter, pen_ax, ox, oy + 20, ox, oy - ax_len, '', 'end')
        painter.drawText(ox + 4, oy - ax_len - 4, "Q+ inductivo")
        painter.drawText(ox + 4, oy + 28, "Q− capacitivo")

        # Línea punteada guía en Q− (zona capacitiva)
        painter.setPen(QPen(QColor('#333355'), 1, Qt.PenStyle.DotLine))
        painter.drawLine(ox, oy, ox, oy + ax_len)

        painter.setFont(font_lbl)

        # ── Triángulo ─────────────────────────────────────────────────────
        # P (horizontal)
        arrow(painter, pen_P, ox, oy, ox + Px, oy,
              f"P = {P:.2f} W", 'end')

        # Q (vertical desde punta de P — arriba si inductivo, abajo si capacitivo)
        arrow(painter, pen_Q, ox + Px, oy, ox + Px, oy + Qy,
              f"Q = {Q:.2f} VAR", 'end')

        # S (hipotenusa desde origen)
        arrow(painter, pen_S, ox, oy, ox + Px, oy + Qy,
              f"S = {S:.4f} VA", 'mid')

        # Ángulo φ — signo correcto usando atan2 con Q real
        phi_rad = math.atan2(Q, P)            # positivo inductivo, negativo capacitivo
        phi_deg = math.degrees(phi_rad)
        painter.setPen(QPen(QColor('#f1c40f'), 1))
        r_arc = 40
        # Qt drawArc: 0° = 3 o'clock, span positivo = anti-horario (visualmente hacia ARRIBA),
        # span negativo = horario (visualmente hacia ABAJO).
        # Como phi_deg > 0 cuando Q es inductivo (vector hacia arriba) y phi_deg < 0
        # cuando es capacitivo (vector hacia abajo), pasamos phi_deg directamente
        # para que el arco siga la dirección real del vector Q.
        start_qt  = 0
        span_qt   = int(phi_deg * 16)
        painter.drawArc(ox - r_arc, oy - r_arc, 2*r_arc, 2*r_arc,
                        start_qt * 16, span_qt)
        # Etiqueta del ángulo (encima si Q+ inductivo, debajo si Q− capacitivo)
        label_y = oy - 14 if Q >= 0 else oy + 22
        painter.drawText(ox + r_arc + 4, label_y,
                         f"φ = {phi_deg:+.1f}°  fp={fp:.3f}  ({self.total.get('fp_type','')})")

        # Corrección de FP
        if self._correction:
            Q_new  = self._correction.get('Q_new', Q)
            Qy_new = int(-Q_new * scale)
            arrow(painter, pen_c, ox, oy, ox + Px, oy + Qy_new,
                  f"S' (fp={self._correction['fp_new']:.3f})", 'mid')
            painter.setPen(QPen(QColor('#f39c12'), 1, Qt.PenStyle.DashLine))
            painter.drawLine(ox + Px, oy + Qy, ox + Px, oy + Qy_new)
            painter.setPen(QPen(QColor('#f39c12'), 1))
            painter.drawText(ox + Px + 8, (oy + Qy + oy + Qy_new)//2,
                             f"ΔQ={abs(Q-Q_new):.2f} VAR")


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