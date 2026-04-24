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
    QScrollArea, QGroupBox, QTextEdit, QFileDialog
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
from engine import Diode, BJT, MOSFET, OpAmp, MNASolver


# ══════════════════════════════════════════════════════════════
# CONSTANTES DE ESTILO
# ══════════════════════════════════════════════════════════════
GRID_SIZE   = 20
COMP_W      = 60
COMP_H      = 30
PIN_RADIUS  = 4

COLORS = {
    'bg':         '#1a1a2e',
    'grid':       '#16213e',
    'grid_line':  '#0f3460',
    'component':  '#e94560',
    'comp_body':  '#16213e',
    'comp_sel':   '#f5a623',
    'wire':       '#4ecca3',
    'wire_sel':   '#f5a623',
    'node_dot':   '#4ecca3',
    'text':       '#e0e0e0',
    'text_dim':   '#7f8c8d',
    'pin':        '#4ecca3',
    'gnd':        '#a0a0ff',
    'toolbar':    '#0f3460',
    'panel':      '#16213e',
    'panel_brd':  '#0f3460',
    'voltage':    '#f5a623',
    'current':    '#4ecca3',
}


# ══════════════════════════════════════════════════════════════
# ÍTEM DE COMPONENTE EN EL CANVAS
# ══════════════════════════════════════════════════════════════
class ComponentItem(QGraphicsItem):
    """
    Representación visual de un componente en el canvas.
    Soporta drag, selección y doble-click para editar propiedades.
    """

    COMP_TYPES = ['R', 'V', 'VAC', 'I', 'C', 'L', 'GND', 'NODE',
                  'D', 'BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP']

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

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)

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
        return QRectF(-COMP_W//2 - 10, -COMP_H//2 - 20, COMP_W + 20, COMP_H + 40)

    def pin_positions(self) -> Tuple[QPointF, QPointF]:
        """Retorna posición de los pines principales en coordenadas locales."""
        hw = COMP_W // 2
        hh = COMP_H // 2
        if self.comp_type == 'GND':
            return QPointF(0, -5), QPointF(0, -5)
        if self.comp_type in ('BJT_NPN', 'BJT_PNP'):
            # p1=Colector (der-arriba), p2=Emisor (der-abajo)
            return QPointF(hw + 10, -hh - 6), QPointF(hw + 10, hh + 6)
        if self.comp_type in ('NMOS', 'PMOS'):
            # p1=Drain (der-arriba), p2=Source (der-abajo)
            return QPointF(hw + 10, -hh - 6), QPointF(hw + 10, hh + 6)
        if self.comp_type == 'OPAMP':
            # p1=Salida (der), p2=Entrada− (izq-abajo)
            hh_op = hh + 6
            return QPointF(hw + 10, 0), QPointF(-hw - 10, hh_op // 2)
        return QPointF(-hw - 10, 0), QPointF(hw + 10, 0)

    def pin3_position(self) -> QPointF:
        """Pin de control: Base (BJT), Gate (MOSFET), Entrada+ (OpAmp)."""
        hw = COMP_W // 2
        hh = COMP_H // 2 + 6
        if self.comp_type in ('BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS'):
            return QPointF(-hw - 10, 0)
        if self.comp_type == 'OPAMP':
            return QPointF(-hw - 10, -hh // 2)
        return QPointF(0, 0)

    def pin3_position_scene(self) -> QPointF:
        return self.mapToScene(self.pin3_position())

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
        elif self.comp_type in ('BJT_NPN', 'BJT_PNP'):
            self._draw_bjt(painter, pen_body, pen_wire)
        elif self.comp_type in ('NMOS', 'PMOS'):
            self._draw_mosfet(painter, pen_body, pen_wire)
        elif self.comp_type == 'OPAMP':
            self._draw_opamp(painter, pen_body, pen_wire, body_color)

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
        # Símbolo + / −  o flecha
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        if self.comp_type == 'V':
            painter.drawText(QRectF(-r+4, -r+4, r-4, r*2-8), Qt.AlignmentFlag.AlignCenter, '+')
        else:
            # Flecha de corriente
            painter.drawLine(QPointF(-8, 0), QPointF(8, 0))
            painter.drawLine(QPointF(4, -5), QPointF(8, 0))
            painter.drawLine(QPointF(4, 5), QPointF(8, 0))

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
# ESCENA DEL CIRCUITO
# ══════════════════════════════════════════════════════════════
class CircuitScene(QGraphicsScene):
    component_selected = pyqtSignal(object)
    status_message     = pyqtSignal(str)

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
                        node1: str = '', node2: str = '') -> ComponentItem:
        if not name:
            count = self._comp_counter.get(comp_type, 0) + 1
            self._comp_counter[comp_type] = count
            prefixes = {'R': 'R', 'V': 'V', 'I': 'I', 'C': 'C', 'L': 'L',
                        'GND': 'GND', 'NODE': 'N'}
            name = f"{prefixes.get(comp_type, comp_type)}{count}"

        units = {'R': 'Ω', 'V': 'V', 'VAC': 'V', 'I': 'A', 'C': 'F', 'L': 'H',
                 'D': 'A', 'BJT_NPN': 'hFE', 'BJT_PNP': 'hFE',
                 'NMOS': 'A/V²', 'PMOS': 'A/V²', 'OPAMP': 'V/V'}
        if not unit:
            unit = units.get(comp_type, '')

        defaults = {'R': 1000.0, 'V': 5.0, 'VAC': 120.0, 'I': 0.001, 'C': 1e-6, 'L': 1e-3,
                    'D': 1e-14, 'BJT_NPN': 100.0, 'BJT_PNP': 100.0,
                    'NMOS': 1e-3, 'PMOS': 1e-3, 'OPAMP': 1e5}
        if value == 0.0:
            value = defaults.get(comp_type, 1.0)

        item = ComponentItem(comp_type, name, value, unit, node1, node2)
        snap_x = round(pos.x() / GRID_SIZE) * GRID_SIZE
        snap_y = round(pos.y() / GRID_SIZE) * GRID_SIZE
        item.setPos(snap_x, snap_y)
        self.addItem(item)
        self.components.append(item)
        return item

    # ── Eventos de mouse ────────────────────────
    def mousePressEvent(self, event):
        pos = event.scenePos()

        if self._mode.startswith('place_'):
            comp_type = self._mode.split('_', 1)[1]
            self.place_component(comp_type, pos)
            self.status_message.emit(f"Componente {comp_type} colocado en ({pos.x():.0f}, {pos.y():.0f})")
            return

        if self._mode == 'wire':
            snap = QPointF(round(pos.x()/GRID_SIZE)*GRID_SIZE,
                           round(pos.y()/GRID_SIZE)*GRID_SIZE)
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
            pos = event.scenePos()
            snap = QPointF(round(pos.x()/GRID_SIZE)*GRID_SIZE,
                           round(pos.y()/GRID_SIZE)*GRID_SIZE)
            self._wire_preview.setLine(QLineF(self._wire_start, snap))
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event):
        items = self.items(event.scenePos())
        for item in items:
            if isinstance(item, ComponentItem):
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
        SNAP = GRID_SIZE  # tolerancia = tamaño de celda completo (más robusto)

        # ── 1. Registrar pines de componentes ───────────────────────────
        pins = {}
        for comp in self.components:
            p1, p2 = comp.pin_positions_scene()
            pins[f"{comp.name}__p1"] = p1
            pins[f"{comp.name}__p2"] = p2
            # Registrar pin3 para dispositivos de 3 terminales
            if comp.comp_type in ('BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP'):
                pins[f"{comp.name}__p3"] = comp.pin3_position_scene()

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
                net_counter += 1
                root_to_name[root] = f'net_{net_counter}'

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
            'BJT_NPN': ('Colector (C)', 'Emisor (E)', 'Base (B)'),
            'BJT_PNP': ('Colector (C)', 'Emisor (E)', 'Base (B)'),
            'NMOS':    ('Drain (D)',    'Source (S)', 'Gate (G)'),
            'PMOS':    ('Drain (D)',    'Source (S)', 'Gate (G)'),
            'OPAMP':   ('Salida (OUT)', 'Entrada − (V−)', 'Entrada + (V+)'),
        }
        lbl1, lbl2, lbl3 = node_labels.get(self.item.comp_type, ('Nodo +', 'Nodo −', None))

        self.node1_edit = QLineEdit(self.item.node1)
        self.node2_edit = QLineEdit(self.item.node2)
        layout.addRow(lbl1 + ':', self.node1_edit)
        layout.addRow(lbl2 + ':', self.node2_edit)

        self.node3_edit = None
        if lbl3 is not None:
            self.node3_edit = QLineEdit(self.item.node3)
            layout.addRow(lbl3 + ':', self.node3_edit)

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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self):
        return {
            'name':      self.name_edit.text(),
            'value':     self.value_spin.value(),
            'node1':     self.node1_edit.text(),
            'node2':     self.node2_edit.text(),
            'node3':     self.node3_edit.text() if self.node3_edit else '',
            'frequency': self._freq_spin.value()  if self._freq_spin  else 60.0,
            'phase_deg': self._phase_spin.value() if self._phase_spin else 0.0,
            'ac_mode':   self._mode_combo.currentText() if self._mode_combo else 'rms',
        }


# ══════════════════════════════════════════════════════════════
# VENTANA PRINCIPAL
# ══════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CircuitSim — Simulador de Circuitos")
        self.resize(1280, 800)
        self.solver = MNASolver()
        self._build_ui()
        self._apply_style()
        self._load_demo_circuit()

    # ── Construcción UI ──────────────────────────
    def _build_ui(self):
        # Escena + Vista
        self.scene = CircuitScene()
        self.scene.component_selected.connect(self._on_component_selected)
        self.scene.status_message.connect(self.statusBar().showMessage)

        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        # Zoom con rueda
        self.view.wheelEvent = self._wheel_zoom

        # Toolbar izquierda (paleta de componentes)
        self._build_left_panel()

        # Panel derecho (propiedades + resultados)
        self._build_right_panel()

        # Layout central
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.left_panel)
        splitter.addWidget(self.view)
        splitter.addWidget(self.right_panel)
        splitter.setSizes([180, 860, 240])

        self.setCentralWidget(splitter)

        # Toolbar superior
        self._build_toolbar()

        # Status bar
        self.statusBar().showMessage("Listo — Doble-click sobre un componente para editar")

    def _build_left_panel(self):
        self.left_panel = QWidget()
        self.left_panel.setFixedWidth(175)
        layout = QVBoxLayout(self.left_panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("COMPONENTES")
        title.setFont(QFont('Consolas', 9, QFont.Weight.Bold))
        layout.addWidget(title)

        components = [
            ('R',       'Resistor',    '━┤ZZZ├━'),
            ('C',       'Capacitor',   '━┤  ├━'),
            ('L',       'Inductor',    '━⌒⌒⌒━'),
            ('V',       'Fuente VDC',  '━(+)━'),
            ('VAC',     'Fuente VAC',  '━(~)━'),
            ('I',       'Fuente I',    '━(→)━'),
            ('GND',     'Tierra',      '⏚'),
            ('NODE',    'Nodo',        '•'),
            ('D',       'Diodo',       '━|▷|━'),
            ('BJT_NPN', 'BJT NPN',     '━(NPN)'),
            ('BJT_PNP', 'BJT PNP',     '━(PNP)'),
            ('NMOS',    'MOSFET N',    '━[N]━'),
            ('PMOS',    'MOSFET P',    '━[P]━'),
            ('OPAMP',   'Op-Amp',      '━[▷]━'),
        ]

        for ctype, label, sym in components:
            btn = QPushButton(f"  {sym}  {label}")
            btn.setCheckable(True)
            btn.setFont(QFont('Consolas', 9))
            btn.clicked.connect(lambda checked, t=ctype: self._set_place_mode(t))
            layout.addWidget(btn)
            setattr(self, f'btn_{ctype}', btn)

        layout.addSpacing(10)

        wire_btn = QPushButton("✏  Cable (Wire)")
        wire_btn.setCheckable(True)
        wire_btn.setFont(QFont('Consolas', 9))
        wire_btn.clicked.connect(lambda: self._set_wire_mode())
        self.btn_wire = wire_btn
        layout.addWidget(wire_btn)

        select_btn = QPushButton("↖  Seleccionar")
        select_btn.setFont(QFont('Consolas', 9))
        select_btn.clicked.connect(lambda: self._set_select_mode())
        layout.addWidget(select_btn)

        layout.addStretch()

        from PyQt6.QtWidgets import QComboBox, QHBoxLayout
        sim_row = QHBoxLayout()

        self.sim_mode_combo = QComboBox()
        self.sim_mode_combo.addItems(['DC', 'AC'])
        self.sim_mode_combo.setFont(QFont('Consolas', 10))
        self.sim_mode_combo.setFixedHeight(40)
        self.sim_mode_combo.setFixedWidth(70)
        self.sim_mode_combo.currentTextChanged.connect(self._on_sim_mode_changed)
        sim_row.addWidget(self.sim_mode_combo)

        self.run_btn = QPushButton("▶  SIMULAR DC")
        self.run_btn.setFont(QFont('Consolas', 10, QFont.Weight.Bold))
        self.run_btn.setFixedHeight(40)
        self.run_btn.clicked.connect(self._run_simulation)
        sim_row.addWidget(self.run_btn)

        layout.addLayout(sim_row)

    def _build_right_panel(self):
        self.right_panel = QWidget()
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

    def _build_toolbar(self):
        tb = self.addToolBar("Principal")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))

        actions = [
            ("Nuevo",        "Ctrl+N", self._new_circuit),
            ("Abrir",        "Ctrl+O", self._open_circuit),
            ("Guardar",      "Ctrl+S", self._save_circuit),
            ("Exportar SPICE", "Ctrl+E", self._export_spice),
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

        self._current_file: Optional[str] = None

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
            QToolBar QToolButton, QToolBar::separator {{
                color: {COLORS['text']};
                padding: 4px 8px;
            }}
            QPushButton {{
                background: {COLORS['comp_body']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['panel_brd']};
                border-radius: 4px;
                padding: 5px 8px;
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
        """)

    # ── Modos ────────────────────────────────────
    def _set_place_mode(self, comp_type: str):
        self._deselect_all_mode_buttons()
        btn = getattr(self, f'btn_{comp_type}', None)
        if btn:
            btn.setChecked(True)
        self.scene.set_mode(f'place_{comp_type}')
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.statusBar().showMessage(f"Click en el canvas para colocar: {comp_type}")

    def _set_wire_mode(self):
        self._deselect_all_mode_buttons()
        self.btn_wire.setChecked(True)
        self.scene.set_mode('wire')
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.statusBar().showMessage("Wire: click para iniciar, click para terminar, ESC para cancelar")

    def _set_select_mode(self):
        self._deselect_all_mode_buttons()
        self.scene.set_mode('select')
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.statusBar().showMessage("Modo selección")

    def _deselect_all_mode_buttons(self):
        for t in ['R', 'C', 'L', 'V', 'VAC', 'I', 'GND', 'NODE',
                  'D', 'BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP']:
            btn = getattr(self, f'btn_{t}', None)
            if btn:
                btn.setChecked(False)
        self.btn_wire.setChecked(False)

    def _on_sim_mode_changed(self, mode: str):
        self.run_btn.setText(f"▶  SIMULAR {mode}")

    # ── Simulación ───────────────────────────────
    def _run_simulation(self):
        """
        Despachador: corre análisis DC o AC según el selector de modo.
        """
        mode = self.sim_mode_combo.currentText()
        if mode == 'AC':
            self._run_simulation_ac()
        else:
            self._run_simulation_dc()

    def _run_simulation_dc(self):
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
                elif item.comp_type == 'D':
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
            except Exception as e:
                errors.append(f"{item.name}: {e}")

        if not components:
            self.results_text.setPlainText("⚠  No hay componentes en el canvas.")
            return

        # Mostrar netlist extraida antes de simular
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

            # Actualizar canvas con voltajes (usando nodos automaticos)
            for item in self.scene.components:
                auto_n1 = pin_node.get(f"{item.name}__p1", '')
                n1 = item.node1.strip() if item.node1.strip() else auto_n1
                if n1 in result['voltages']:
                    item.result_voltage = result['voltages'][n1]
                else:
                    item.result_voltage = None
                item.update()

        else:
            out.append(f"✗ Error de simulación:\n{result['error']}")
            out.append("\nVerifica que el circuito tenga:")
            out.append("  • Al menos una fuente de voltaje")
            out.append("  • Nodo de tierra (nodo '0')")
            out.append("  • Nodos asignados a cada componente")

        if errors:
            out.append("\n── Advertencias ──")
            out.extend([f"  ⚠ {e}" for e in errors])

        self.results_text.setPlainText('\n'.join(out))
        self.scene.update()

    # ── Panel de propiedades ─────────────────────
    def _run_simulation_ac(self):
        """Análisis AC de frecuencia única con triángulo de potencia."""
        from PyQt6.QtWidgets import QInputDialog

        # Buscar fuente VAC en el canvas para leer la frecuencia
        vac_items = [it for it in self.scene.components if it.comp_type == 'VAC']
        if not vac_items:
            self.results_text.setPlainText(
                "⚠  No hay fuentes VAC en el circuito.\n"
                "Agrega una fuente VAC (paleta izquierda) para el análisis AC.")
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

    def _show_power_triangle(self):
        if not self._last_ac_result:
            return
        dlg = PowerTriangleDialog(self._last_ac_result, parent=self)
        dlg.exec()

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
            'BJT_NPN': ('Colector (C)',      'Emisor (E)',         'Base (B)'),
            'BJT_PNP': ('Colector (C)',      'Emisor (E)',         'Base (B)'),
            'NMOS':    ('Drain (D)',         'Source (S)',         'Gate (G)'),
            'PMOS':    ('Drain (D)',         'Source (S)',         'Gate (G)'),
            'OPAMP':   ('Salida (OUT)',      'Entrada − (V−)',     'Entrada + (V+)'),
        }
        lbl1, lbl2, lbl3 = terminal_labels.get(item.comp_type, ('Nodo +', 'Nodo −', None))

        # Nodos automáticos desde cables
        pin_node  = self.scene.extract_netlist()
        auto_n1   = pin_node.get(f"{item.name}__p1", '—')
        auto_n2   = pin_node.get(f"{item.name}__p2", '—')
        auto_n3   = pin_node.get(f"{item.name}__p3", '—')
        n1_display = item.node1.strip() or f"{auto_n1} (auto)"
        n2_display = item.node2.strip() or f"{auto_n2} (auto)"
        n3_display = item.node3.strip() or f"{auto_n3} (auto)"

        rows = [
            ("Tipo",     item.comp_type),
            ("Nombre",   item.name),
            ("Valor",    f"{item.value} {item.unit}"),
            ("Rotación", f"{item._angle}°"),
            (lbl1,       n1_display),
            (lbl2,       n2_display),
        ]
        if lbl3 is not None:
            rows.append((lbl3, n3_display))

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
            "Presiona ▶ SIMULAR DC para verificar.\n\n"
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

        data = {
            'version': '1.0',
            'components': [],
            'wires': []
        }

        for item in self.scene.components:
            data['components'].append({
                'type':  item.comp_type,
                'name':  item.name,
                'value': item.value,
                'unit':  item.unit,
                'node1': item.node1,
                'node2': item.node2,
                'x':     item.pos().x(),
                'y':     item.pos().y(),
                'angle': item._angle,
            })

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
                node2=c.get('node2', '')
            )
            angle = c.get('angle', 0)
            if angle:
                item._angle = angle
                item.setRotation(angle)

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

        type_map = {'R': 'R', 'C': 'C', 'L': 'L', 'V': 'V', 'I': 'I'}

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
                                      QTextEdit, QSplitter)
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

        close_btn = QPushButton("Cerrar")
        close_btn.clicked.connect(self.accept)
        main.addWidget(close_btn)

    def _on_correct(self):
        from engine import MNASolver
        solver  = MNASolver()
        total   = self.ac_result['total']
        freq    = self.ac_result['frequency']
        fp_tgt  = self.fp_spin.value()
        res     = solver.correct_power_factor(total, freq, fp_tgt)

        if 'error' in res:
            self.corr_label.setText(f"⚠ {res['error']}")
            return

        tipo   = res['type']
        val    = res['value']
        Q_corr = res['Q_corr']
        fp_new = res['fp_new']
        note   = res.get('note', '')
        form   = res['formula']

        if tipo == 'capacitor':
            val_str = f"C = {val*1e6:.4f} µF  (normalizado a 1 Vrms)"
            emoji   = "⚡ Capacitor"
        else:
            val_str = f"L = {val*1e3:.4f} mH  (normalizado a 1 Vrms)"
            emoji   = "🔄 Inductor"

        text = (
            f"  Elemento corrector: {emoji} en PARALELO\n"
            f"  {val_str}\n"
            f"  Q a compensar:  {Q_corr:.4f} VAR\n"
            f"  FP resultante:  {fp_new:.4f}\n"
            f"  Fórmula:        {form}\n"
            f"  📌 {note}"
        )
        self.corr_label.setText(text)
        # Actualizar triángulo con la corrección
        self.canvas.set_correction(res)
        self.canvas.update()


class _PowerTriangleCanvas(QWidget):
    """Widget que dibuja el triángulo de potencia S, P, Q, ángulo φ."""

    def __init__(self, total: dict, parent=None):
        super().__init__(parent)
        self.total      = total
        self._correction = None

    def set_correction(self, corr: dict):
        self._correction = corr

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

        # Escala: S ocupa 60% del ancho
        if S < 1e-12:
            painter.setPen(QPen(QColor('#aaaaaa'), 1))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Sin datos de potencia")
            return

        scale = (W * 0.58) / S

        # Origen en centro-izquierda
        ox = int(W * 0.12)
        oy = int(H * 0.65)

        Px = int(P * scale)
        Qy = int(-Q * scale)   # Q positivo → hacia arriba en pantalla

        # ── Vectores del triángulo ────────────────────────────────────────
        # P: horizontal (rojo)
        # Q: vertical   (azul, arriba si inductivo)
        # S: hipotenusa (verde)

        pen_P = QPen(QColor('#e74c3c'), 3)   # rojo — potencia activa
        pen_Q = QPen(QColor('#3498db'), 3)   # azul — potencia reactiva
        pen_S = QPen(QColor('#2ecc71'), 3)   # verde — potencia aparente
        pen_c = QPen(QColor('#f39c12'), 2, Qt.PenStyle.DashLine)  # naranja corrección

        font_lbl = QFont('Consolas', 9, QFont.Weight.Bold)
        painter.setFont(font_lbl)

        def arrow(painter, pen, x1, y1, x2, y2, label='', lside='end'):
            painter.setPen(pen)
            painter.drawLine(x1, y1, x2, y2)
            # Punta de flecha
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

        # P (horizontal)
        arrow(painter, pen_P, ox, oy, ox + Px, oy,
              f"P = {P:.2f} W", 'end')

        # Q (vertical desde punta de P)
        arrow(painter, pen_Q, ox + Px, oy, ox + Px, oy + Qy,
              f"Q = {Q:.2f} VAR", 'end')

        # S (hipotenusa desde origen)
        arrow(painter, pen_S, ox, oy, ox + Px, oy + Qy,
              f"S = {S:.2f} VA", 'mid')

        # Ángulo φ
        phi = math.acos(min(abs(fp), 1.0)) * 180 / math.pi
        painter.setPen(QPen(QColor('#f1c40f'), 1))
        r_arc = 40
        start_angle = 0
        span_angle  = int(-math.degrees(math.atan2(Q, P)) * 16) if S > 1e-12 else 0
        painter.drawArc(ox - r_arc, oy - r_arc, 2*r_arc, 2*r_arc,
                        start_angle * 16, span_angle)
        painter.setPen(QPen(QColor('#f1c40f'), 1))
        painter.drawText(ox + r_arc + 4, oy - 6,
                         f"φ = {phi:.1f}°  fp={fp:.3f}  ({self.total.get('fp_type','')})")

        # Corrección de FP (si existe)
        if self._correction:
            Q_new = self._correction.get('Q_new', Q)
            Qy_new = int(-Q_new * scale)
            arrow(painter, pen_c, ox, oy, ox + Px, oy + Qy_new,
                  f"S' (fp={self._correction['fp_new']:.3f})", 'mid')
            painter.setPen(QPen(QColor('#f39c12'), 1, Qt.PenStyle.DashLine))
            painter.drawLine(ox + Px, oy + Qy, ox + Px, oy + Qy_new)
            painter.setPen(QPen(QColor('#f39c12'), 1))
            painter.drawText(ox + Px + 8, (oy + Qy + oy + Qy_new)//2,
                             f"ΔQ={abs(Q-Q_new):.2f} VAR")

if __name__ == '__main__':
    main()

