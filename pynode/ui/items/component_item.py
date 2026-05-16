"""
ComponentItem — representación visual de un componente en el canvas
de circuitos. Soporta drag, selección, doble-click para editar y
rotación/espejado. Extraído de main.py.
"""
import math
from typing import Optional, List, Tuple

from PyQt6.QtWidgets import QGraphicsItem
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath, QPolygonF,
    QRadialGradient, QTransform,
)
from PyQt6.QtCore import Qt, QPointF, QRectF, QLineF

from pynode.ui.style import COLORS, GRID_SIZE, COMP_W, COMP_H, PIN_RADIUS, _qfont, format_si_value


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
                  'TL082',
                  'XFMR', 'BRIDGE',
                  # ── Instrumentos ──
                  'FGEN', 'OSC', 'MULTIMETER',
                  # ── Digital ──
                  'AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR',
                  'DFF', 'JKFF', 'TFF', 'SRFF',
                  'MUX2', 'COUNTER',
                  'ADC_BRIDGE', 'DAC_BRIDGE', 'COMPARATOR', 'PWM',
                  'CLK',
                  # ── Inter-hoja ──
                  'NET_LABEL_IN', 'NET_LABEL_OUT',
                  # ── Subcircuitos ──
                  'PORT', 'SUBCKT']

    # Instrumentos virtuales (panel frontal independiente).
    INSTRUMENT_TYPES = {'FGEN', 'OSC', 'MULTIMETER'}

    # Tipos analógicos con 4 terminales (necesitan p3 y p4)
    FOUR_PIN_TYPES = {'XFMR', 'BRIDGE', 'OSC'}

    # Tipos analógicos con 5 terminales (necesitan p3, p4 y p5)
    FIVE_PIN_TYPES = {'TL082'}

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
        self._flip_x: bool = False  # invertir en eje X (horizontal)
        self._flip_y: bool = False  # invertir en eje Y (vertical)
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
        # Máscara de negación por entrada (alineada con la lista total de
        # entradas: [entrada1, entrada2, ...]). Si una posición es True el
        # valor de esa entrada se invierte antes de evaluar la compuerta y
        # se dibuja un bubble (círculo) sobre el pin de entrada.
        self.dig_input_neg: list = []

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

        # ── Generador de funciones (FGEN) ───────────────────────────────────
        # Reutiliza self.value (amplitud), self.frequency, self.phase_deg,
        # self.ac_mode ('rms'/'peak'). Estos atributos extra controlan la
        # forma de onda — equivalen a los del VoltageSourceAC del motor.
        self.fgen_waveform: str   = 'sin'   # 'sin' | 'square' | 'triangle'
        self.fgen_offset:   float = 0.0     # V DC sumados a la onda
        self.fgen_duty:     float = 0.5     # ciclo de trabajo (solo square), 0..1

        # ── Osciloscopio (OSC) ──────────────────────────────────────────────
        # Configuración del panel. Buffers de muestras viven en el diálogo
        # (no en el item) para mantener el item liviano.
        self.osc_time_div:    float = 1e-3   # segundos por división (10 div totales)
        self.osc_v_div_a:     float = 1.0    # V por división, canal A
        self.osc_v_div_b:     float = 1.0    # V por división, canal B
        self.osc_pos_a:       float = 0.0    # desplazamiento vertical canal A (divs)
        self.osc_pos_b:       float = 0.0    # desplazamiento vertical canal B (divs)
        self.osc_trig_level:  float = 0.0    # nivel de trigger (V)
        self.osc_trig_source: str   = 'A'    # 'A' o 'B'
        self.osc_trig_edge:   str   = 'rising'   # 'rising' | 'falling'
        self.osc_trig_mode:   str   = 'auto'     # 'auto' | 'normal' | 'single'
        # Última config de hardware (puerto, baud, ganancia, etc.) — vacía
        # mientras no se haya conectado nunca. Se guarda en el .csin para
        # que al reabrir el archivo el panel recuerde el puerto y la
        # calibración del usuario.
        self.osc_hw_config:   dict  = {}

        # ── TL082 (op-amp dual) ─────────────────────────────────────────────
        # Cada instancia representa UNA de las dos unidades del CI.
        # tl082_unit indica cuál ('A' o 'B') — solo informativo/visual.
        # node5 almacena el nodo del pin V− (quinto terminal).
        self.tl082_unit: str = 'A'   # 'A' | 'B'
        self.node5:      str = ''    # V− (sólo TL082)

        # ── Multímetro (instrumento de medición) ─────────────────────────────
        # meter_quantity:  'V' (voltaje), 'A' (corriente), 'OHM' (resistencia)
        # meter_coupling:  'DC' o 'AC' (modo de acoplamiento)
        # meter_reading:   último valor leído (None si aún no se midió)
        # meter_reading_unit_hint: 'V' | 'A' | 'Ω' — para formatear el display
        self.meter_quantity:         str             = 'V'
        self.meter_coupling:         str             = 'DC'
        self.meter_reading:          Optional[float] = None
        self.meter_reading_unit_hint: str            = 'V'

        # ── CLK (reloj digital) ─────────────────────────────────────────────
        # Si está corriendo (oscilando), el timer global lo conmuta a la frecuencia
        # configurada; en caso contrario se comporta como un LOGIC_STATE manual.
        self.clk_running: bool = False

        # ── Estado de memoria de flip-flops ─────────────────────────────────
        # Refleja la salida Q actual del FF (0 ó 1) para visualizarla
        # con un círculo en el centro durante la simulación.
        self.dig_q_state: int = 0

        # ── Puerto de subcircuito (PORT) ────────────────────────────────────
        # Marca un nodo interno que se expone como pin del IC.
        self.port_name: str = 'IN'
        self.port_dir:  str = 'in'    # 'in' | 'out' | 'bidir' (cosmético)

        # ── Instancia de subcircuito (SUBCKT) ───────────────────────────────
        # subckt_name → nombre de la definición en la biblioteca.
        # ic_pins     → lista [{'name': str, 'side': 'left|right|top|bottom'}]
        #               alineada con el orden de los puertos de la definición;
        #               el pin i corresponde al pin de netlist p{i+1}.
        self.subckt_name:    str  = ''
        self.ic_label:       str  = ''     # texto en el cuerpo (vacío → subckt_name)
        self.ic_body_color:  str  = ''     # override (vacío → tema)
        self.ic_text_color:  str  = ''
        self.ic_pins:        list = []     # [{'name','side'}, ...]

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)

    def _apply_transform(self):
        """Aplica rotación y flips combinados como una única QTransform.
        El orden es: primero los flips (escala -1), luego la rotación.
        Esto mantiene la posición del componente intacta."""
        t = QTransform()
        t.rotate(self._angle)
        sx = -1.0 if self._flip_x else 1.0
        sy = -1.0 if self._flip_y else 1.0
        if sx != 1.0 or sy != 1.0:
            t.scale(sx, sy)
        self.setTransform(t)
        self.update()
        # Actualizar cables conectados a este componente tras la transformación
        if self.scene() and hasattr(self.scene(), 'update_wires_for_component'):
            self.scene().update_wires_for_component(self)

    def rotate_90(self, delta: int = 90):
        """Rota el componente `delta` grados (positivo = horario,
        negativo = antihorario)."""
        self._angle = (self._angle + delta) % 360
        self._apply_transform()

    def flip_x(self):
        """Invierte el componente en el eje X (espejo horizontal)."""
        self._flip_x = not self._flip_x
        self._apply_transform()

    def flip_y(self):
        """Invierte el componente en el eje Y (espejo vertical)."""
        self._flip_y = not self._flip_y
        self._apply_transform()

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
        if self.comp_type == 'PORT':
            # Banderín pequeño + etiqueta encima.
            return QRectF(-26, -20, 52, 34)
        if self.comp_type == 'SUBCKT':
            w, h, _ = self._subckt_geometry()
            m = 22
            return QRectF(-w / 2 - 12 - m, -h / 2 - m,
                          w + 24 + 2 * m, h + 2 * m)
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
            return QRectF(-70, -35, 140, 80)
        if self.comp_type == 'BRIDGE':
            return QRectF(-70, -70, 140, 140)
        if self.comp_type == 'OSC':
            return QRectF(-50, -36, 100, 72)
        if self.comp_type == 'MULTIMETER':
            # Cuerpo cuadrado con display + puntas de prueba en la parte inferior
            return QRectF(-44, -50, 88, 100)
        if self.comp_type == 'TL082':
            # Triángulo (−35..+35, −28..+28) + cables V+/V− (±44) + margen etiquetas
            return QRectF(-64, -58, 128, 116)
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
        if self.comp_type == 'TL082':
            # p1 = OUT (derecha-centro), p2 = IN− (izquierda-abajo)
            return QPointF(50, 0), QPointF(-50, 18)
        # ── Transformador: p1=PRI+ (sup-izq), p2=PRI- (inf-izq) ─────────
        if self.comp_type == 'XFMR':
            return QPointF(-60, -20), QPointF(-60, 20)
        # ── Puente rectificador (diamante):
        #     p1 = AC1 (izq),  p2 = AC2 (der)
        #     p3 = DC+ (sup),  p4 = DC− (inf)
        if self.comp_type == 'BRIDGE':
            return QPointF(-60, 0), QPointF(60, 0)
        # ── Osciloscopio:
        #     p1 = A+ (izq-arriba), p2 = A− (izq-abajo)
        #     p3 = B+ (der-arriba), p4 = B− (der-abajo)
        if self.comp_type == 'OSC':
            return QPointF(-40, -20), QPointF(-40, 20)
        # ── Multímetro: puntas de prueba en la parte inferior ────────────
        #     p1 = V+ (rojo, izq-abajo), p2 = V− (negro, der-abajo)
        if self.comp_type == 'MULTIMETER':
            return QPointF(-30, 50), QPointF(30, 50)
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
        if self.comp_type == 'PORT':
            # Único pin a la derecha del banderín.
            return QPointF(16, 0), QPointF(16, 0)
        if self.comp_type == 'SUBCKT':
            pts = self._subckt_pin_points()
            p1 = pts[0] if pts else QPointF(0, 0)
            p2 = pts[1] if len(pts) > 1 else p1
            return p1, p2
        return QPointF(-hw - 10, 0), QPointF(hw + 10, 0)

    # ── Geometría de subcircuitos (SUBCKT) ──────────────────────────────────
    def _subckt_geometry(self):
        """Devuelve (ancho, alto, dist_por_lado) del cuerpo del IC en función
        del número de pines colocados en cada lado."""
        sides = {'left': 0, 'right': 0, 'top': 0, 'bottom': 0}
        for p in (self.ic_pins or []):
            sides[p.get('side', 'left')] = sides.get(p.get('side', 'left'), 0) + 1
        v_max = max(sides['left'], sides['right'], 1)
        h_max = max(sides['top'], sides['bottom'], 1)
        h = max(50, v_max * 24 + 20)
        w = max(70, h_max * 24 + 20, 60)
        return float(w), float(h), 24.0

    def _subckt_pin_points(self):
        """Lista de QPointF (coordenadas locales) alineada con self.ic_pins."""
        w, h, step = self._subckt_geometry()
        pins = self.ic_pins or []
        # contador por lado para repartir uniformemente
        per_side = {'left': [], 'right': [], 'top': [], 'bottom': []}
        for i, p in enumerate(pins):
            per_side.setdefault(p.get('side', 'left'), []).append(i)
        result = [QPointF(0, 0)] * len(pins)
        ext = 12  # longitud del cable que sale del cuerpo

        def spread(n, length):
            if n <= 0:
                return []
            gap = length / (n + 1)
            return [-length / 2 + gap * (k + 1) for k in range(n)]

        for side, idxs in per_side.items():
            if side in ('left', 'right'):
                ys = spread(len(idxs), h)
                x = (-w / 2 - ext) if side == 'left' else (w / 2 + ext)
                for k, gi in enumerate(idxs):
                    result[gi] = QPointF(x, ys[k])
            else:
                xs = spread(len(idxs), w)
                y = (-h / 2 - ext) if side == 'top' else (h / 2 + ext)
                for k, gi in enumerate(idxs):
                    result[gi] = QPointF(xs[k], y)
        return result

    def subckt_pin_positions_scene(self) -> list:
        return [self.mapToScene(p) for p in self._subckt_pin_points()]

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
        if self.comp_type == 'TL082':
            # p3 = IN+ (izquierda-arriba)
            return QPointF(-50, -18)
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
            return QPointF(60, -20)
        # Puente: p3 = DC+ (sup)
        if self.comp_type == 'BRIDGE':
            return QPointF(0, -60)
        # Osciloscopio: p3 = B+ (der-arriba)
        if self.comp_type == 'OSC':
            return QPointF(40, -20)
        return QPointF(0, 0)

    def pin3_position_scene(self) -> QPointF:
        return self.mapToScene(self.pin3_position())

    def pin4_position(self) -> QPointF:
        """Cuarto pin.

          TL082       → V+   (superior-centro)
          XFMR        → SEC− (inferior derecho)
          BRIDGE      → DC−  (inferior)
          DFF/JKFF/TFF/SRFF → SET (parte superior, arriba del centro)
        """
        if self.comp_type == 'TL082':
            # p4 = V+ : sale por la mitad del lado superior del triángulo
            return QPointF(0, -44)
        if self.comp_type == 'XFMR':
            return QPointF(60, 20)
        if self.comp_type == 'BRIDGE':
            return QPointF(0, 60)
        if self.comp_type == 'OSC':
            return QPointF(40, 20)
        if self.comp_type == 'MUX2':
            # p4 = línea de selección (abajo-centro)
            _, hh, _, _ = self._gate_geometry()
            return QPointF(0, hh + 10)
        if self.comp_type in self.FLIPFLOP_TYPES:
            hh_f = COMP_H // 2 + 8
            return QPointF(0, -hh_f - 10)
        return QPointF(0, 0)

    def pin4_position_scene(self) -> QPointF:
        return self.mapToScene(self.pin4_position())

    def pin5_position(self) -> QPointF:
        """Quinto pin.

          TL082               → V−   (inferior-centro)
          DFF/JKFF/TFF/SRFF   → RESET (parte inferior)
        """
        if self.comp_type == 'TL082':
            # p5 = V− : sale por la mitad del lado inferior del triángulo
            return QPointF(0, 44)
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
        if self.comp_type == 'SUBCKT':
            pts = self.subckt_pin_positions_scene()
            return pts if pts else [self.mapToScene(QPointF(0, 0))]
        if self.comp_type == 'PORT':
            p1, _ = self.pin_positions_scene()
            return [p1]
        p1, p2 = self.pin_positions_scene()
        pins = [p1, p2]
        # Pines adicionales según tipo
        if self.comp_type in self.FIVE_PIN_TYPES:
            pins.append(self.pin3_position_scene())  # IN+
            pins.append(self.pin4_position_scene())  # V+
            pins.append(self.pin5_position_scene())  # V−
        elif self.comp_type in ('BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP'):
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
            pins.append(self.pin3_position_scene())  # I1
            pins.append(self.pin4_position_scene())  # SEL
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
        elif self.comp_type == 'TL082':
            self._draw_tl082(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'Z':
            self._draw_impedance(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'XFMR':
            self._draw_transformer(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'BRIDGE':
            self._draw_bridge_rectifier(painter, pen_body, pen_wire, body_color)
        # ── Instrumentos ─────────────────────────────────────────────────
        elif self.comp_type == 'FGEN':
            self._draw_fgen(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'OSC':
            self._draw_osc(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'MULTIMETER':
            self._draw_multimeter(painter, pen_body, pen_wire, body_color)
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
        elif self.comp_type == 'PORT':
            self._draw_port(painter, pen_body, pen_wire, body_color)
        elif self.comp_type == 'SUBCKT':
            self._draw_subcircuit(painter, pen_body, pen_wire, body_color)

        # Nombre y valor
        self._draw_labels(painter, text_color)

        # Pines — los dispositivos de 3 terminales dibujan sus propios pines
        # internamente con etiquetas; solo dibujar pines genéricos para el resto
        three_terminal = ('BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP', 'TL082',
                          'NET_LABEL_IN', 'NET_LABEL_OUT',
                          'DFF', 'JKFF', 'TFF', 'SRFF',
                          'PORT', 'SUBCKT',   # dibujan sus propios pines
                          'MULTIMETER')   # _draw_multimeter pinta sus pines
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
        painter.setFont(_qfont('Consolas', 6))
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
        # Primario (izq): pines a (±60, ±20), bobina a x=-40
        painter.drawLine(QPointF(-60, -20), QPointF(-40, -20))   # p1 → top de bobina
        painter.drawLine(QPointF(-60,  20), QPointF(-40,  20))   # p2 → bot de bobina
        # Secundario (der): bobina a x=40
        painter.drawLine(QPointF(40, -20), QPointF(60, -20))     # p3 → top
        painter.drawLine(QPointF(40,  20), QPointF(60,  20))     # p4 → bot

        # ── Bobinas (semicírculos apilados) ───────────────────────────────
        painter.setPen(QPen(QColor(COLORS['component']), 1.8))
        # Primario: 4 lazos a la izquierda (abren hacia la derecha)
        path_p = QPainterPath()
        path_p.moveTo(-40, -20)
        for i in range(4):
            cy = -20 + i*10 + 5
            path_p.arcTo(QRectF(-45, cy - 5, 10, 10), 90, -180)
        painter.drawPath(path_p)
        # Secundario: 4 lazos a la derecha (abren hacia la izquierda)
        path_s = QPainterPath()
        path_s.moveTo(40, -20)
        for i in range(4):
            cy = -20 + i*10 + 5
            path_s.arcTo(QRectF(35, cy - 5, 10, 10), 90, 180)
        painter.drawPath(path_s)

        # ── Núcleo de hierro: dos líneas verticales paralelas ─────────────
        painter.setPen(QPen(QColor(COLORS['text']), 1.4))
        painter.drawLine(QPointF(-3, -22), QPointF(-3, 22))
        painter.drawLine(QPointF( 3, -22), QPointF( 3, 22))

        # ── Etiqueta de relación ──────────────────────────────────────────
        painter.setFont(_qfont('Consolas', 7))
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        n = self.xfmr_ratio
        if n >= 1:
            label = f"{n:.1f}:1"
        else:
            label = f"1:{1/n:.1f}"
        painter.drawText(QRectF(-30, 24, 60, 10),
                         Qt.AlignmentFlag.AlignCenter, label)
        # Indicar polaridad con un punto en la parte superior de cada bobina
        painter.setPen(QPen(QColor(COLORS['component']), 1))
        painter.setBrush(QColor(COLORS['component']))
        painter.drawEllipse(QPointF(-32, -24), 1.8, 1.8)
        painter.drawEllipse(QPointF( 32, -24), 1.8, 1.8)
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
        painter.drawLine(QPointF(-60, 0),  QPointF(-40, 0))   # AC1
        painter.drawLine(QPointF(40, 0),   QPointF(60, 0))    # AC2
        painter.drawLine(QPointF(0, -60),  QPointF(0, -40))   # DC+
        painter.drawLine(QPointF(0,  40),  QPointF(0,  60))   # DC−

        # ── Diamante ──────────────────────────────────────────────────────
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        diamond = QPolygonF([
            QPointF(0, -40),   # top  (DC+)
            QPointF(40,  0),   # right (AC2)
            QPointF(0,  40),   # bot  (DC-)
            QPointF(-40, 0),   # left (AC1)
        ])
        painter.drawPolygon(diamond)

        # ── 4 diodos dentro del diamante ──────────────────────────────────
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
            cx = (p_from.x() + p_to.x()) / 2
            cy = (p_from.y() + p_to.y()) / 2
            sz = 6
            tri = QPolygonF([
                QPointF(cx + sz*ux,             cy + sz*uy),
                QPointF(cx - sz*ux + sz*0.7*px, cy - sz*uy + sz*0.7*py),
                QPointF(cx - sz*ux - sz*0.7*px, cy - sz*uy - sz*0.7*py),
            ])
            painter.drawPolygon(tri)
            tip_x = cx + sz*ux
            tip_y = cy + sz*uy
            painter.drawLine(
                QPointF(tip_x + 0.7*sz*px, tip_y + 0.7*sz*py),
                QPointF(tip_x - 0.7*sz*px, tip_y - 0.7*sz*py))

        draw_diode_arrow(painter, QPointF(-40, 0), QPointF(0, -40))   # D1: AC1→DC+
        draw_diode_arrow(painter, QPointF(40, 0),  QPointF(0, -40))   # D2: AC2→DC+
        draw_diode_arrow(painter, QPointF(0, 40),  QPointF(-40, 0))   # D3: DC-→AC1
        draw_diode_arrow(painter, QPointF(0, 40),  QPointF(40, 0))    # D4: DC-→AC2
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Etiquetas de pines
        painter.setFont(_qfont('Consolas', 6))
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.drawText(QRectF(-60, -8, 18, 10),  Qt.AlignmentFlag.AlignCenter, '~')
        painter.drawText(QRectF(42,  -8, 18, 10),  Qt.AlignmentFlag.AlignCenter, '~')
        painter.drawText(QRectF(-12, -60, 24, 10), Qt.AlignmentFlag.AlignCenter, '+')
        painter.drawText(QRectF(-12,  50, 24, 10), Qt.AlignmentFlag.AlignCenter, '−')

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

    def _draw_fgen(self, painter, pen_body, pen_wire, body_color):
        """Generador de funciones: caja rectangular tipo instrumento con la
        forma de onda actual dibujada dentro. Pines a izquierda (V+) y
        derecha (V−)."""
        hw = COMP_W // 2 + 4
        hh = COMP_H // 2 + 4
        # Cables a los pines
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-hw - 10, 0), QPointF(-hw, 0))
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        # Cuerpo
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, hw * 2, hh * 2))
        # Forma de onda dentro (eje horizontal = un período)
        painter.setPen(QPen(QColor(COLORS['component']), 1.6))
        path = QPainterPath()
        wf = getattr(self, 'fgen_waveform', 'sin')
        x0, x1 = -hw + 6, hw - 6
        y_amp = hh - 6
        N = 48
        duty = max(0.02, min(0.98, getattr(self, 'fgen_duty', 0.5)))
        for i in range(N + 1):
            frac = i / N
            x = x0 + frac * (x1 - x0)
            if wf == 'square':
                y = -y_amp if frac < duty else y_amp
            elif wf == 'triangle':
                # +1 en frac=0.5, -1 en bordes
                y = -y_amp * (1.0 - 4.0 * abs(frac - 0.5))
            else:
                y = -y_amp * math.sin(2.0 * math.pi * frac)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        painter.drawPath(path)
        # Etiqueta "FGEN" arriba a la izquierda
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.setFont(_qfont('Consolas', 6, QFont.Weight.Bold))
        painter.drawText(QRectF(-hw + 2, -hh + 1, hw * 2 - 4, 8),
                         Qt.AlignmentFlag.AlignLeft, 'FGEN')

    def _draw_osc(self, painter, pen_body, pen_wire, body_color):
        """Osciloscopio: rectángulo con mini-pantalla que muestra una onda
        decorativa y 4 pines etiquetados A+ A− B+ B−."""
        hw, hh = 30, 22
        # Cables a los 4 pines (a ±40, ±20 — múltiplos de GRID_SIZE)
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-40, -20), QPointF(-hw, -10))
        painter.drawLine(QPointF(-40,  20), QPointF(-hw,  10))
        painter.drawLine(QPointF( 40, -20), QPointF( hw, -10))
        painter.drawLine(QPointF( 40,  20), QPointF( hw,  10))
        # Cuerpo del instrumento
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, hw * 2, hh * 2))
        # Pantalla mini (verde fosforescente clásico)
        screen = QRectF(-hw + 4, -hh + 8, hw * 2 - 8, hh * 2 - 14)
        painter.setBrush(QBrush(QColor(20, 30, 22)))
        painter.setPen(QPen(QColor(COLORS['panel_brd']), 1))
        painter.drawRect(screen)
        # Traza decorativa: un período senoidal
        painter.setPen(QPen(QColor(80, 220, 120), 1.2))
        path = QPainterPath()
        N = 36
        for i in range(N + 1):
            frac = i / N
            x = screen.left() + frac * screen.width()
            y = screen.center().y() - (screen.height() * 0.35) * math.sin(2 * math.pi * frac)
            (path.moveTo if i == 0 else path.lineTo)(x, y)
        painter.drawPath(path)
        # Etiqueta "XSC" arriba
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.setFont(_qfont('Consolas', 6, QFont.Weight.Bold))
        painter.drawText(QRectF(-hw, -hh + 1, hw * 2, 8),
                         Qt.AlignmentFlag.AlignCenter, 'XSC')
        # Etiquetas de pines junto al cuerpo
        painter.setFont(_qfont('Consolas', 6))
        painter.setPen(QPen(QColor(COLORS['text']), 1))
        painter.drawText(QRectF(-hw, -16,  8, 10), Qt.AlignmentFlag.AlignLeft,   'A+')
        painter.drawText(QRectF(-hw,   6,  8, 10), Qt.AlignmentFlag.AlignLeft,   'A−')
        painter.drawText(QRectF( hw-9, -16, 9, 10), Qt.AlignmentFlag.AlignRight, 'B+')
        painter.drawText(QRectF( hw-9,   6, 9, 10), Qt.AlignmentFlag.AlignRight, 'B−')

    def _draw_multimeter(self, painter, pen_body, pen_wire, body_color):
        """Multímetro estilo Multisim: cuerpo cuadrado con display, modo
        seleccionado y dos puntas de prueba (V+ rojo, V− negro)."""
        body_w, body_h = 80, 60
        x0, y0 = -body_w / 2, -body_h / 2 - 8

        # ── Cables hacia las puntas de prueba ────────────────────────────
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(-30, body_h / 2 - 8), QPointF(-30, 50))
        painter.drawLine(QPointF( 30, body_h / 2 - 8), QPointF( 30, 50))

        # ── Cuerpo del instrumento ───────────────────────────────────────
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRoundedRect(QRectF(x0, y0, body_w, body_h), 4, 4)

        # ── Display ──────────────────────────────────────────────────────
        disp = QRectF(x0 + 6, y0 + 6, body_w - 12, 24)
        painter.setBrush(QBrush(QColor(COLORS.get('bg', '#1a1a2e'))))
        painter.setPen(QPen(QColor(COLORS.get('panel_brd', '#0f3460')), 1))
        painter.drawRect(disp)

        reading_text = self._format_meter_reading()
        painter.setPen(QPen(QColor(COLORS.get('current', '#0fff50'))))
        painter.setFont(_qfont('Consolas', 10, QFont.Weight.Bold))
        painter.drawText(disp, Qt.AlignmentFlag.AlignCenter, reading_text)

        # ── Etiqueta de modo (V/A/Ω + DC/AC) ─────────────────────────────
        mode_lbl = self._meter_mode_label()
        painter.setPen(QPen(QColor(COLORS.get('text_dim', '#a0a0a0'))))
        painter.setFont(_qfont('Consolas', 8))
        mode_rect = QRectF(x0, y0 + body_h - 22, body_w, 14)
        painter.drawText(mode_rect, Qt.AlignmentFlag.AlignCenter, mode_lbl)

        # ── Pines: V+ rojo, V− blanco/oscuro ─────────────────────────────
        painter.setPen(QPen(QColor('#e94560'), 2))
        painter.setBrush(QBrush(QColor('#e94560')))
        painter.drawEllipse(QPointF(-30, 50), PIN_RADIUS + 1, PIN_RADIUS + 1)
        painter.setPen(QPen(QColor(COLORS.get('text', '#e0e0e0')), 2))
        painter.setBrush(QBrush(QColor(COLORS.get('text', '#e0e0e0'))))
        painter.drawEllipse(QPointF( 30, 50), PIN_RADIUS + 1, PIN_RADIUS + 1)

        # Etiquetas + y − junto a los pines
        painter.setPen(QPen(QColor('#e94560')))
        painter.setFont(_qfont('Consolas', 8, QFont.Weight.Bold))
        painter.drawText(QRectF(-44, 32, 16, 14),
                         Qt.AlignmentFlag.AlignCenter, '+')
        painter.setPen(QPen(QColor(COLORS.get('text', '#e0e0e0'))))
        painter.drawText(QRectF( 28, 32, 16, 14),
                         Qt.AlignmentFlag.AlignCenter, '−')

    def _meter_mode_label(self) -> str:
        qty = getattr(self, 'meter_quantity', 'V')
        cpl = getattr(self, 'meter_coupling', 'DC')
        sym = {'V': 'V', 'A': 'A', 'OHM': 'Ω'}.get(qty, 'V')
        if qty == 'OHM':
            return sym
        return f"{sym} {cpl}"

    def _format_meter_reading(self) -> str:
        v = getattr(self, 'meter_reading', None)
        if v is None:
            return '— — —'
        unit = getattr(self, 'meter_reading_unit_hint', '')
        av = abs(v)
        if av >= 1e6:
            return f"{v/1e6:.3f} M{unit}"
        if av >= 1e3:
            return f"{v/1e3:.3f} k{unit}"
        if av >= 1 or av == 0:
            return f"{v:.3f} {unit}"
        if av >= 1e-3:
            return f"{v*1e3:.3f} m{unit}"
        if av >= 1e-6:
            return f"{v*1e6:.3f} μ{unit}"
        return f"{v:.3e} {unit}"

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
        font = _qfont('Consolas', 7, QFont.Weight.Bold)
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
        font = _qfont('Consolas', 7, QFont.Weight.Bold)
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
        font = _qfont('Consolas', 7, QFont.Weight.Bold)
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


    def _draw_tl082(self, painter, pen_body, pen_wire, body_color):
        """
        Símbolo estándar IEC/IEEE de op-amp con 5 terminales:
          • Triángulo apuntando a la derecha
          • IN+ (no-inversora) — izquierda-arriba
          • IN− (inversora)   — izquierda-abajo
          • OUT                — derecha (ápice)
          • V+                 — sale del punto medio del lado superior
          • V−                 — sale del punto medio del lado inferior

        Geometría del triángulo:
            vértice izq-arriba : (−35, −28)
            vértice izq-abajo  : (−35, +28)
            ápice derecho      : (+35,   0)
        """
        # ── Cuerpo: triángulo ─────────────────────────────────────────────
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        tri = QPolygonF([QPointF(-35, -28), QPointF(-35, 28), QPointF(35, 0)])
        painter.drawPolygon(tri)

        # ── Cables de señal ───────────────────────────────────────────────
        painter.setPen(pen_wire)
        # OUT: ápice → pin externo
        painter.drawLine(QPointF(35, 0),   QPointF(50, 0))
        # IN+: borde izquierdo (-35, -18) → pin externo
        painter.drawLine(QPointF(-50, -18), QPointF(-35, -18))
        # IN−: borde izquierdo (-35, +18) → pin externo
        painter.drawLine(QPointF(-50,  18), QPointF(-35,  18))

        # ── Cables de alimentación ────────────────────────────────────────
        # El punto de salida sobre el triángulo es la mitad geométrica de
        # cada lado inclinado, es decir x=0 → y=±14.
        # V+: (0, −14) → (0, −44)
        painter.drawLine(QPointF(0, -14), QPointF(0, -44))
        # V−: (0, +14) → (0, +44)
        painter.drawLine(QPointF(0,  14), QPointF(0,  44))

        # ── Símbolos + / − dentro del triángulo ──────────────────────────
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        font_sym = _qfont('Consolas', 9, QFont.Weight.Bold)
        painter.setFont(font_sym)
        # "+" cerca de IN+ (arriba-izq)
        painter.drawText(QRectF(-30, -26, 16, 14),
                         Qt.AlignmentFlag.AlignCenter, '+')
        # "−" cerca de IN− (abajo-izq)
        painter.drawText(QRectF(-30,  12, 16, 14),
                         Qt.AlignmentFlag.AlignCenter, '−')

        # ── Letra de unidad (A / B) centrada en el triángulo ─────────────
        unit = getattr(self, 'tl082_unit', 'A')
        font_unit = _qfont('Consolas', 8, QFont.Weight.Bold)
        painter.setFont(font_unit)
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.drawText(QRectF(-8, -8, 16, 16),
                         Qt.AlignmentFlag.AlignCenter, unit)

        # ── Pines con puntos y etiquetas ──────────────────────────────────
        font_lbl = _qfont('Consolas', 7, QFont.Weight.Bold)
        painter.setFont(font_lbl)
        pin_color = QColor(COLORS['pin'])

        # (posición_pin, etiqueta, offset_x, offset_y)
        pin_data = [
            (QPointF( 50,   0), 'OUT',  5,  -5),
            (QPointF(-50, -18), 'IN+', -26, -12),
            (QPointF(-50,  18), 'IN−', -26,   3),
            (QPointF(  0, -44), 'V+',   4,  -12),
            (QPointF(  0,  44), 'V−',   4,    3),
        ]
        for pos, label, ox, oy in pin_data:
            painter.setPen(QPen(pin_color, 2))
            painter.setBrush(QBrush(pin_color))
            painter.drawEllipse(pos, PIN_RADIUS, PIN_RADIUS)
            painter.setPen(QPen(QColor(COLORS['text']), 1))
            painter.drawText(QRectF(pos.x() + ox, pos.y() + oy, 26, 11),
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
        font = _qfont('Consolas', 8, QFont.Weight.Bold)
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

        # Diámetro del bubble en entradas negadas; igual al de salida.
        bubble_d_in = bubble_d
        neg_mask = list(getattr(self, 'dig_input_neg', []) or [])

        def _is_neg(i: int) -> bool:
            return i < len(neg_mask) and bool(neg_mask[i])

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
            input_back_xs = []
            for i, y in enumerate(pin_ys):
                t      = (y + hh) / (2 * hh) if hh > 0 else 0.5
                back_x = -hw - outer_offset + 2 * t * (1 - t) * back_bulge_eff
                input_back_xs.append(back_x)
                end_x = back_x - bubble_d_in if _is_neg(i) else back_x
                painter.drawLine(QPointF(-hw - 10, y), QPointF(end_x, y))
        else:
            input_back_xs = [-hw] * len(pin_ys)
            for i, y in enumerate(pin_ys):
                end_x = -hw - bubble_d_in if _is_neg(i) else -hw
                painter.drawLine(QPointF(-hw - 10, y), QPointF(end_x, y))

        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))

        # ── Bubbles de inversión en entradas negadas ──────────────────────
        if any(_is_neg(i) for i in range(len(pin_ys))):
            painter.setPen(pen_body)
            painter.setBrush(QBrush(body_color))
            for i, y in enumerate(pin_ys):
                if _is_neg(i):
                    cx = input_back_xs[i] - bubble_d_in / 2
                    painter.drawEllipse(QPointF(cx, y),
                                        bubble_d_in / 2, bubble_d_in / 2)

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
        font = _qfont('Consolas', 7, QFont.Weight.Bold)
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
        font2 = _qfont('Consolas', 6)
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
        font_q = _qfont('Consolas', 8, QFont.Weight.Bold)
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
        font_big = _qfont('Consolas', 18, QFont.Weight.Bold)
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
        font = _qfont('Consolas', 9, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        painter.drawText(QRectF(-hw, -hh, hw * 2, hh * 2),
                         Qt.AlignmentFlag.AlignCenter, lbl)
        # Flecha de conversión
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        font2 = _qfont('Consolas', 6)
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
        font_big = _qfont('Consolas', 22, QFont.Weight.Bold)
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
        font = _qfont('Consolas', 7, QFont.Weight.Bold)
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
        """MUX 2:1 — geometría consistente con pin_positions/pin3/pin4."""
        hw = COMP_W // 2
        _, hh, _, _ = self._gate_geometry()
        ys = self._gate_pin_ys()           # [y_I0, y_I1]
        painter.setPen(pen_body)
        painter.setBrush(QBrush(body_color))
        painter.drawRect(QRectF(-hw, -hh, hw * 2, hh * 2))
        font = _qfont('Consolas', 7, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(COLORS['component']), 2))
        painter.drawText(QRectF(-hw, -hh, hw * 2, hh * 2),
                         Qt.AlignmentFlag.AlignCenter, 'MUX 2:1')
        # Cables: I0/I1 a la izquierda, salida a la derecha, SEL abajo-centro
        painter.setPen(pen_wire)
        for y in ys:
            painter.drawLine(QPointF(-hw - 10, y), QPointF(-hw, y))
        painter.drawLine(QPointF(0, hh), QPointF(0, hh + 10))        # sel
        painter.drawLine(QPointF(hw, 0), QPointF(hw + 10, 0))
        # Etiquetas de pin
        painter.setFont(_qfont('Consolas', 6))
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.drawText(QRectF(-hw + 2, ys[0] - 6, 16, 12),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, '0')
        painter.drawText(QRectF(-hw + 2, ys[1] - 6, 16, 12),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, '1')
        painter.drawText(QRectF(-12, hh - 14, 24, 12),
                         Qt.AlignmentFlag.AlignCenter, 'S')
        pin_color = QColor(COLORS['pin'])
        painter.setPen(QPen(pin_color, 2))
        painter.setBrush(QBrush(pin_color))
        for px, py in [(-hw - 10, ys[0]), (-hw - 10, ys[1]),
                       (0, hh + 10), (hw + 10, 0)]:
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
        font = _qfont('Consolas', 7, QFont.Weight.Bold)
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

    def _draw_port(self, painter, pen_body, pen_wire, body_color):
        """Puerto de subcircuito: banderín con un único pin a la derecha."""
        col = QColor(COLORS['comp_sel'] if self.isSelected() else COLORS['component'])
        painter.setPen(QPen(col, 2))
        painter.setBrush(QBrush(QColor(COLORS['comp_body'])))
        # Banderín hexagonal apuntando a la derecha hacia el pin
        flag = QPolygonF([
            QPointF(-22, -8), QPointF(4, -8), QPointF(12, 0),
            QPointF(4, 8), QPointF(-22, 8),
        ])
        painter.drawPolygon(flag)
        # Cable al pin
        painter.setPen(pen_wire)
        painter.drawLine(QPointF(12, 0), QPointF(16, 0))
        # Etiqueta (port_name) dentro / encima
        painter.setFont(_qfont('Consolas', 7, QFont.Weight.Bold))
        painter.setPen(QPen(QColor(COLORS['text']), 1))
        painter.drawText(QRectF(-22, -8, 30, 16),
                         Qt.AlignmentFlag.AlignCenter,
                         self.port_name or self.name)
        # Dirección encima
        painter.setFont(_qfont('Consolas', 6))
        painter.setPen(QPen(QColor(COLORS['text_dim']), 1))
        painter.drawText(QRectF(-26, -20, 52, 10),
                         Qt.AlignmentFlag.AlignCenter,
                         (self.port_dir or 'in').upper())
        # Pin
        painter.setPen(QPen(QColor(COLORS['pin']), 2))
        painter.setBrush(QBrush(QColor(COLORS['pin'])))
        painter.drawEllipse(QPointF(16, 0), PIN_RADIUS, PIN_RADIUS)

    def _draw_subcircuit(self, painter, pen_body, pen_wire, body_color):
        """Bloque tipo circuito integrado con pines configurables."""
        w, h, _ = self._subckt_geometry()
        sel = self.isSelected()
        body = QColor(self.ic_body_color) if self.ic_body_color else None
        if body is None or not body.isValid():
            body = QColor(COLORS['comp_sel'] if sel else COLORS['comp_body'])
        edge = QColor(COLORS['comp_sel'] if sel else COLORS['component'])
        txt = QColor(self.ic_text_color) if self.ic_text_color else None
        if txt is None or not txt.isValid():
            txt = QColor(COLORS['text'])

        rect = QRectF(-w / 2, -h / 2, w, h)
        painter.setPen(QPen(edge, 2))
        painter.setBrush(QBrush(body))
        painter.drawRoundedRect(rect, 4, 4)
        # Muesca superior (orientación del IC)
        painter.setPen(QPen(edge, 1.5))
        painter.drawArc(QRectF(-6, -h / 2 - 6, 12, 12), 180 * 16, 180 * 16)

        pts = self._subckt_pin_points()
        painter.setFont(_qfont('Consolas', 6))
        for i, p in enumerate(pts):
            side = (self.ic_pins[i].get('side', 'left')
                    if i < len(self.ic_pins) else 'left')
            # Cable cuerpo→pin
            painter.setPen(pen_wire)
            if side == 'left':
                painter.drawLine(QPointF(-w / 2, p.y()), p)
                tr = QRectF(-w / 2 + 3, p.y() - 7, w / 2 - 6, 14)
                al = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            elif side == 'right':
                painter.drawLine(QPointF(w / 2, p.y()), p)
                tr = QRectF(3, p.y() - 7, w / 2 - 6, 14)
                al = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            elif side == 'top':
                painter.drawLine(QPointF(p.x(), -h / 2), p)
                tr = QRectF(p.x() - 16, -h / 2 + 2, 32, 12)
                al = Qt.AlignmentFlag.AlignCenter
            else:
                painter.drawLine(QPointF(p.x(), h / 2), p)
                tr = QRectF(p.x() - 16, h / 2 - 14, 32, 12)
                al = Qt.AlignmentFlag.AlignCenter
            painter.setPen(QPen(QColor(COLORS['pin']), 2))
            painter.setBrush(QBrush(QColor(COLORS['pin'])))
            painter.drawEllipse(p, PIN_RADIUS, PIN_RADIUS)
            name = (self.ic_pins[i].get('name', '')
                    if i < len(self.ic_pins) else '')
            painter.setPen(QPen(txt, 1))
            painter.drawText(tr, al, name)

        # Label central
        label = self.ic_label or self.subckt_name or 'SUB'
        painter.setPen(QPen(txt, 1))
        painter.setFont(_qfont('Consolas', 9, QFont.Weight.Bold))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        # Nombre de instancia encima del cuerpo
        painter.setFont(_qfont('Consolas', 8))
        painter.setPen(QPen(QColor(COLORS['text']), 1))
        painter.drawText(QRectF(-w / 2, -h / 2 - 18, w, 16),
                         Qt.AlignmentFlag.AlignCenter, self.name)

    def _draw_labels(self, painter, text_color):
        if self.comp_type in ('GND', 'NODE', 'NET_LABEL_IN', 'NET_LABEL_OUT',
                               'PORT', 'SUBCKT'):
            return
        font = _qfont('Consolas', 8)
        painter.setFont(font)
        painter.setPen(QPen(text_color))

        # TL082: etiquetas desplazadas para no solaparse con los pines de alimentación
        if self.comp_type == 'TL082':
            painter.drawText(QRectF(-35, -56, 70, 13),
                             Qt.AlignmentFlag.AlignCenter, self.name)
            return

        # Multímetro: nombre arriba del cuerpo, sin "valor" abajo (el display
        # del propio cuerpo ya muestra la lectura)
        if self.comp_type == 'MULTIMETER':
            name_rect = QRectF(-50, -50 - 16, 100, 14)
            painter.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, self.name)
            return

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
                if abs(self.z_imag) < 1e-12:
                    return format_si_value(self.z_real, 'Ω')
                r = format_si_value(self.z_real, '')
                x = format_si_value(abs(self.z_imag), 'Ω')
                sign = '+' if self.z_imag >= 0 else '−'
                return f"{r}{sign}{x}j"
            else:
                return f"{format_si_value(self.z_mag, 'Ω')}∠{self.z_phase:.1f}°"
        return format_si_value(self.value, self.unit)

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
