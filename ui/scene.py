"""
CircuitScene — QGraphicsScene del canvas de circuitos. Maneja:
  • colocación, movimiento, rotación y borrado de componentes
  • dibujo de cables (modo wire) con preview
  • snap a grilla y a pines
  • selección, copy/paste y undo
  • dibujado de grid (drawBackground) y puntos de unión (drawForeground)

Y `build_engine_components_for_item`: helper para traducir un
ComponentItem analógico a los objetos del motor MNA.

Extraído de main.py.
"""
import math
from typing import Optional, List, Dict, Tuple

from PyQt6.QtWidgets import QGraphicsScene, QMenu, QDialog
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor
from PyQt6.QtCore import Qt, QPointF, QRectF, QLineF, pyqtSignal

from ui.style import COLORS, GRID_SIZE, PIN_RADIUS, theme_revision
from ui.items.component_item import ComponentItem
from ui.dialogs.component_dialog import ComponentDialog
from ui.items.wire_item import WireItem


# ══════════════════════════════════════════════════════════════
# ESCENA DEL CIRCUITO
# ══════════════════════════════════════════════════════════════
class CircuitScene(QGraphicsScene):
    component_selected   = pyqtSignal(object)
    status_message       = pyqtSignal(str)
    logic_state_toggled  = pyqtSignal(object)   # emitido cuando LOGIC_STATE cambia

    # Portapapeles compartido entre escenas (todas las hojas) — guarda un
    # snapshot de la selección.
    _clipboard: Optional[dict] = None

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

        # Stack de Ctrl+Z (undo). Cada entrada es un snapshot serializado.
        self._undo_stack: List[dict] = []
        self._undo_max: int = 50

    # ── Grid (dibujado en drawBackground para que sea independiente del zoom) ──
    def _grid_pens(self) -> Tuple[QPen, QPen]:
        """Devuelve (pen_minor, pen_major) cacheados; se reconstruyen sólo
        cuando cambia el tema (detectado vía theme_revision())."""
        rev = theme_revision()
        if getattr(self, '_grid_pens_rev', None) != rev:
            color = QColor(COLORS['grid_line'])
            pen_minor = QPen(color, 0)
            pen_minor.setCosmetic(True)
            pen_minor.setStyle(Qt.PenStyle.DotLine)
            pen_major = QPen(color, 0)
            pen_major.setCosmetic(True)
            pen_major.setStyle(Qt.PenStyle.SolidLine)
            self._grid_pen_minor = pen_minor
            self._grid_pen_major = pen_major
            self._grid_pens_rev = rev
        return self._grid_pen_minor, self._grid_pen_major

    def drawBackground(self, painter: QPainter, rect: QRectF):
        super().drawBackground(painter, rect)

        left   = int(math.floor(rect.left()  / GRID_SIZE)) * GRID_SIZE
        right  = int(math.ceil(rect.right()  / GRID_SIZE)) * GRID_SIZE
        top    = int(math.floor(rect.top()   / GRID_SIZE)) * GRID_SIZE
        bottom = int(math.ceil(rect.bottom() / GRID_SIZE)) * GRID_SIZE

        pen_minor, pen_major = self._grid_pens()

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
        
        return (best_comp, best_pin_idx) if best_comp else (None, 0)

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
            self.push_undo()
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
                self.push_undo()
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
                    # Snapshot previo al drag → Ctrl+Z revierte posiciones.
                    self.push_undo()
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
                    self.push_undo()
                    item.value = 0.0 if item.value else 1.0
                    item.update()
                    self.logic_state_toggled.emit(item)
                    return
                if item.comp_type == 'CLK':
                    # Doble-click conmuta manualmente y detiene la oscilación
                    # automática (entra en modo manual como un LOGIC_STATE).
                    self.push_undo()
                    item.clk_running = False
                    item.value = 0.0 if item.value else 1.0
                    item.update()
                    self.logic_state_toggled.emit(item)
                    return
                self._edit_component(item)
                return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        mod = event.modifiers()
        has_ctrl = bool(mod & Qt.KeyboardModifier.ControlModifier)
        key = event.key()

        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            sel = list(self.selectedItems())
            if sel:
                self.push_undo()
                for item in sel:
                    if isinstance(item, ComponentItem) and item in self.components:
                        self.components.remove(item)
                    elif isinstance(item, WireItem) and item in self.wires:
                        self.wires.remove(item)
                    self.removeItem(item)
                self.status_message.emit("Selección eliminada")
        elif has_ctrl and key == Qt.Key.Key_Z:
            if self.undo():
                self.status_message.emit("Acción deshecha (Ctrl+Z)")
            else:
                self.status_message.emit("Nada que deshacer")
        elif has_ctrl and key == Qt.Key.Key_C:
            if self.copy_selected():
                self.status_message.emit("Selección copiada (Ctrl+C)")
        elif has_ctrl and key == Qt.Key.Key_X:
            if self.cut_selected():
                self.status_message.emit("Selección cortada (Ctrl+X)")
        elif has_ctrl and key == Qt.Key.Key_V:
            if self.paste():
                self.status_message.emit("Pegado (Ctrl+V)")
            else:
                self.status_message.emit("Portapapeles vacío")
        elif has_ctrl and key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            # Ctrl++ → rotar 90° a la derecha (horario).
            # Aceptamos también Ctrl+= para teclados donde + requiere Shift.
            if self.rotate_selected(delta=90):
                self.status_message.emit("Rotado 90° a la derecha (Ctrl++)")
            event.accept()
        elif has_ctrl and key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            # Ctrl+- → rotar 90° a la izquierda (antihorario).
            if self.rotate_selected(delta=-90):
                self.status_message.emit("Rotado 90° a la izquierda (Ctrl+-)")
            event.accept()
        elif key == Qt.Key.Key_Escape:
            if self._wire_preview:
                self.removeItem(self._wire_preview)
                self._wire_preview = None
            self._wire_start = None
            self.set_mode('select')
        else:
            super().keyPressEvent(event)

    def update_wires_for_component(self, comp: 'ComponentItem'):
        """Actualiza todos los cables conectados al componente dado."""
        for wire in self.wires:
            if wire.start_comp is comp or wire.end_comp is comp:
                wire.update_from_pins()

    # ── Serialización para undo / clipboard ─────────────────────────────

    # Atributos opcionales que persistimos por componente. Para mantener el
    # snapshot pequeño, sólo se guardan si el componente los tiene.
    _SNAP_ATTRS = (
        'sheet_label', 'pot_wiper', 'led_color',
        'frequency', 'phase_deg', 'ac_mode',
        'z_real', 'z_imag', 'z_mag', 'z_phase', 'z_mode',
        'xfmr_ratio', 'xfmr_imax', 'bridge_vf',
        'node4', 'clk_running',
        'dig_inputs', 'dig_tpd_ns', 'dig_clk', 'dig_analog_node',
        'dig_bits', 'dig_bits_adc', 'dig_vref',
    )

    def _serialize_component(self, c: 'ComponentItem') -> dict:
        e = {
            'type':  c.comp_type, 'name': c.name, 'value': c.value,
            'unit':  c.unit, 'node1': c.node1, 'node2': c.node2,
            'node3': c.node3,
            'x':     c.pos().x(), 'y': c.pos().y(),
            'angle': c._angle,
            'flip_x': c._flip_x,
            'flip_y': c._flip_y,
        }
        for attr in self._SNAP_ATTRS:
            if hasattr(c, attr):
                e[attr] = getattr(c, attr)
        if hasattr(c, 'dig_input_nodes'):
            e['dig_input_nodes'] = list(c.dig_input_nodes or [])
        if hasattr(c, 'dig_input_neg'):
            e['dig_input_neg'] = list(c.dig_input_neg or [])
        return e

    def _serialize_wire(self, w: WireItem,
                        comp_filter: Optional[set] = None) -> dict:
        line = w.line()
        sn = w.start_comp.name if (
            w.start_comp and (comp_filter is None
                              or w.start_comp.name in comp_filter)) else None
        en = w.end_comp.name if (
            w.end_comp and (comp_filter is None
                            or w.end_comp.name in comp_filter)) else None
        return {
            'x1': line.x1(), 'y1': line.y1(),
            'x2': line.x2(), 'y2': line.y2(),
            'start': sn, 'spi': w.start_pin_idx,
            'end':   en, 'epi': w.end_pin_idx,
        }

    def _snapshot(self) -> dict:
        """Estado serializado completo de la hoja, listo para undo."""
        return {
            'components': [self._serialize_component(c) for c in self.components],
            'wires':      [self._serialize_wire(w) for w in self.wires],
            'counter':    dict(self._comp_counter),
        }

    def _instantiate_component(self, c: dict,
                               offset_x: float = 0.0, offset_y: float = 0.0,
                               keep_name: bool = True) -> 'ComponentItem':
        item = self.place_component(
            c['type'],
            QPointF(c['x'] + offset_x, c['y'] + offset_y),
            name=(c['name'] if keep_name else ''),
            value=c.get('value', 0.0),
            unit=c.get('unit', ''),
            node1=c.get('node1', ''),
            node2=c.get('node2', ''),
            node3=c.get('node3', ''))
        angle = c.get('angle', 0)
        flip_x = bool(c.get('flip_x', False))
        flip_y = bool(c.get('flip_y', False))
        if angle or flip_x or flip_y:
            item._angle = angle
            item._flip_x = flip_x
            item._flip_y = flip_y
            item._apply_transform()
        for attr in self._SNAP_ATTRS:
            if attr in c:
                setattr(item, attr, c[attr])
        if 'dig_input_nodes' in c:
            item.dig_input_nodes = list(c['dig_input_nodes'])
        if 'dig_input_neg' in c:
            item.dig_input_neg = list(c['dig_input_neg'])
        item.update()
        return item

    def _clear_all(self):
        """Vacía la escena (componentes + cables) preparando un restore."""
        for it in list(self.components):
            if it.scene() is self:
                self.removeItem(it)
        self.components.clear()
        for w in list(self.wires):
            if w.scene() is self:
                self.removeItem(w)
        self.wires.clear()
        self._comp_counter.clear()

    def _restore(self, snap: dict):
        """Reemplaza el contenido actual con el del snapshot."""
        self._clear_all()
        name_to_comp: Dict[str, ComponentItem] = {}
        for c in snap.get('components', []):
            item = self._instantiate_component(c, keep_name=True)
            name_to_comp[c['name']] = item
        for w in snap.get('wires', []):
            sc = name_to_comp.get(w['start']) if w.get('start') else None
            ec = name_to_comp.get(w['end'])   if w.get('end')   else None
            wire = WireItem(
                QPointF(w['x1'], w['y1']), QPointF(w['x2'], w['y2']),
                start_comp=sc, start_pin_idx=w.get('spi', 0),
                end_comp=ec,   end_pin_idx=w.get('epi', 0))
            self.addItem(wire)
            self.wires.append(wire)
        # Restaurar contador de nombres para no chocar con autogenerados
        self._comp_counter = dict(snap.get('counter', {}))
        self.update()

    # ── Undo (Ctrl+Z) ───────────────────────────────────────────────────
    def push_undo(self):
        """Captura el estado actual y lo apila para Ctrl+Z. Llamar ANTES
        de cualquier mutación del canvas."""
        self._undo_stack.append(self._snapshot())
        if len(self._undo_stack) > self._undo_max:
            self._undo_stack.pop(0)

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        snap = self._undo_stack.pop()
        self._restore(snap)
        # Cualquier estado de drag en curso queda invalidado tras un restore.
        self._group_drag_active = False
        self._group_drag_wires = []
        self.component_selected.emit(None)
        return True

    # ── Copy / Cut / Paste (Ctrl+C / Ctrl+X / Ctrl+V) ───────────────────
    def copy_selected(self) -> bool:
        sel = self.selectedItems()
        sel_comps = [it for it in sel if isinstance(it, ComponentItem)]
        sel_wires = [it for it in sel if isinstance(it, WireItem)]
        if not sel_comps and not sel_wires:
            return False
        sel_names = {c.name for c in sel_comps}
        comps = [self._serialize_component(c) for c in sel_comps]
        wires = [self._serialize_wire(w, comp_filter=sel_names)
                 for w in sel_wires]
        CircuitScene._clipboard = {'components': comps, 'wires': wires}
        return True

    def cut_selected(self) -> bool:
        if not self.copy_selected():
            return False
        self.push_undo()
        for it in list(self.selectedItems()):
            if isinstance(it, ComponentItem) and it in self.components:
                self.components.remove(it)
            elif isinstance(it, WireItem) and it in self.wires:
                self.wires.remove(it)
            self.removeItem(it)
        return True

    def paste(self, offset_x: float = GRID_SIZE * 2,
              offset_y: float = GRID_SIZE * 2) -> bool:
        cb = CircuitScene._clipboard
        if not cb or (not cb.get('components') and not cb.get('wires')):
            return False
        self.push_undo()
        self.clearSelection()
        name_map: Dict[str, ComponentItem] = {}
        for c in cb['components']:
            item = self._instantiate_component(
                c, offset_x=offset_x, offset_y=offset_y, keep_name=False)
            name_map[c['name']] = item
            item.setSelected(True)
        for w in cb['wires']:
            sc = name_map.get(w['start']) if w.get('start') else None
            ec = name_map.get(w['end'])   if w.get('end')   else None
            wire = WireItem(
                QPointF(w['x1'] + offset_x, w['y1'] + offset_y),
                QPointF(w['x2'] + offset_x, w['y2'] + offset_y),
                start_comp=sc, start_pin_idx=w.get('spi', 0),
                end_comp=ec,   end_pin_idx=w.get('epi', 0))
            self.addItem(wire)
            self.wires.append(wire)
            wire.setSelected(True)
        return True

    def rotate_selected(self, delta: int = 90) -> bool:
        items = [it for it in self.selectedItems()
                 if isinstance(it, ComponentItem)]
        if not items:
            return False
        self.push_undo()
        for it in items:
            it.rotate_90(delta=delta)
        return True

    def flip_selected_x(self) -> bool:
        items = [it for it in self.selectedItems()
                 if isinstance(it, ComponentItem)]
        if not items:
            return False
        self.push_undo()
        for it in items:
            it.flip_x()
        return True

    def flip_selected_y(self) -> bool:
        items = [it for it in self.selectedItems()
                 if isinstance(it, ComponentItem)]
        if not items:
            return False
        self.push_undo()
        for it in items:
            it.flip_y()
        return True

    # ── Menú contextual (click derecho sobre un componente) ──
    def contextMenuEvent(self, event):
        items = self.items(event.scenePos())
        comp = next((it for it in items if isinstance(it, ComponentItem)), None)
        if comp is None:
            super().contextMenuEvent(event)
            return

        # Si el componente clickeado no estaba seleccionado, seleccionarlo
        # (y limpiar la selección anterior) para que las acciones del menú
        # operen sobre él.
        if not comp.isSelected():
            for it in self.selectedItems():
                it.setSelected(False)
            comp.setSelected(True)

        menu = QMenu()
        act_props    = menu.addAction("Propiedades…")
        menu.addSeparator()
        act_rot_left  = menu.addAction("Rotar 90° izquierda")
        act_rot_right = menu.addAction("Rotar 90° derecha")
        menu.addSeparator()
        act_flip_x = menu.addAction("Invertir en eje X")
        act_flip_y = menu.addAction("Invertir en eje Y")

        chosen = menu.exec(event.screenPos())
        if chosen is None:
            return
        if chosen is act_props:
            self._edit_component(comp)
        elif chosen is act_rot_left:
            self.rotate_selected(delta=-90)
        elif chosen is act_rot_right:
            self.rotate_selected(delta=90)
        elif chosen is act_flip_x:
            self.flip_selected_x()
        elif chosen is act_flip_y:
            self.flip_selected_y()

    # ── Extraccion de netlist por Union-Find ─────
    def extract_netlist(self) -> Dict[str, str]:
        """
        Analiza los cables del canvas y asigna nodos automaticamente.
        Union-Find: une pines conectados por cables en el mismo nodo.
        GND se mapea al nodo 0. Retorna {CompNombre__p1: net_X, ...}
        """
        SNAP = 12

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
            # Snapshot pre-edición para que Ctrl+Z revierta los cambios.
            self.push_undo()
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
                if 'dig_input_neg'   in data: item.dig_input_neg   = data['dig_input_neg']
                # Normalizar la máscara de negación al nº actual de entradas
                if item.comp_type in ComponentItem.DIGITAL_TYPES:
                    n_in_now = 1 if item.comp_type == 'NOT' else max(1, item.dig_inputs)
                    neg = list(getattr(item, 'dig_input_neg', []) or [])
                    if len(neg) < n_in_now:
                        neg.extend([False] * (n_in_now - len(neg)))
                    item.dig_input_neg = neg[:n_in_now]
            item.update()

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


