"""
WireItem — segmento de cable que conecta dos pines (o un pin y un punto
libre) en el canvas. Extraído de main.py.
"""
from PyQt6.QtWidgets import QGraphicsLineItem, QGraphicsItem
from PyQt6.QtGui import QPen, QColor
from PyQt6.QtCore import Qt, QPointF, QLineF

from ui.style import COLORS


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
