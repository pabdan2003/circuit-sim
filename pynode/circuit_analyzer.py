"""
Analizador estático de circuitos y detector de fronteras implícitas.

Antes de correr cualquier solver, el motor inspecciona el netlist y decide:
  - ¿Qué análisis son necesarios? (DC, AC, digital, mixto)
  - ¿Hay nodos frontera entre dominios analógico y digital?

Filosofía de frontera implícita (como Multisim internamente):
    El usuario conecta un cable entre la salida de un op-amp y la entrada
    de un DFF. El motor detecta que ese nodo es "frontera" y aplica la
    conversión usando los umbrales lógicos del estándar configurado
    (CMOS 3.3V, CMOS 5V, TTL, etc.) — sin requerir un bloque ADC/DAC
    explícito en el canvas.

Clases:
    LogicStandard      — umbrales Vil/Vih/Vol/Voh por estándar
    AnalysisFlags      — resultado del análisis estático
    CircuitAnalyzer    — analiza un netlist y produce AnalysisFlags
    ImplicitBridgeDetector  — detecta y aplica conversiones implícitas
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# ──────────────────────────────────────────────────────────────────────────────
# Estándares lógicos (umbrales de tensión)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class LogicStandard:
    """
    Umbrales de tensión para un estándar lógico.

    Atributos:
        name:   Nombre del estándar (ej: 'CMOS_3V3')
        Vdd:    Tensión de alimentación (V)
        Vil:    Tensión máxima de entrada considerada LOW  (V)
        Vih:    Tensión mínima de entrada considerada HIGH (V)
        Vol:    Tensión máxima de salida LOW  (V)
        Voh:    Tensión mínima de salida HIGH (V)
    """
    name: str
    Vdd:  float
    Vil:  float
    Vih:  float
    Vol:  float
    Voh:  float

    def voltage_to_logic(self, v: float) -> Optional[int]:
        """
        Convierte voltaje a nivel lógico aplicando histéresis de Schmitt.

        Returns:
            0   si v ≤ Vil   → LOW definitivo
            1   si v ≥ Vih   → HIGH definitivo
            None si Vil < v < Vih  → zona indeterminada
        """
        if v <= self.Vil:
            return 0
        if v >= self.Vih:
            return 1
        return None  # zona de transición

    def logic_to_voltage(self, level: int) -> float:
        """Convierte nivel lógico a voltaje de salida típico."""
        return self.Voh if level else self.Vol


# Tabla de estándares predefinidos
LOGIC_STANDARDS: Dict[str, LogicStandard] = {
    'CMOS_3V3': LogicStandard('CMOS_3V3', Vdd=3.3,  Vil=1.0,  Vih=2.3,  Vol=0.1,  Voh=3.2),
    'CMOS_5V':  LogicStandard('CMOS_5V',  Vdd=5.0,  Vil=1.5,  Vih=3.5,  Vol=0.1,  Voh=4.9),
    'TTL':      LogicStandard('TTL',       Vdd=5.0,  Vil=0.8,  Vih=2.0,  Vol=0.4,  Voh=2.4),
    'LVTTL':    LogicStandard('LVTTL',     Vdd=3.3,  Vil=0.8,  Vih=2.0,  Vol=0.4,  Voh=2.4),
    'LVDS':     LogicStandard('LVDS',      Vdd=2.5,  Vil=0.9,  Vih=1.1,  Vol=0.9,  Voh=1.1),
}

DEFAULT_STANDARD = LOGIC_STANDARDS['CMOS_3V3']


# ──────────────────────────────────────────────────────────────────────────────
# Resultado del análisis estático
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class AnalysisFlags:
    """
    Resultado del análisis estático del circuito.

    El CircuitAnalyzer rellena estos flags inspeccionando el netlist.
    El dispatcher de simulación los usa para decidir qué solvers activar.
    """
    # Qué dominios están presentes
    has_dc:      bool = False   # Hay fuentes V/I o componentes R/C/L
    has_ac:      bool = False   # Hay fuentes VAC
    has_digital: bool = False   # Hay puertas o flip-flops
    has_bridges: bool = False   # Hay ADC_BRIDGE / DAC_BRIDGE explícitos

    # Frontera implícita detectada
    implicit_boundary_nodes: List[str] = field(default_factory=list)
    # {nodo: {'analog_comps': [...], 'digital_comps': [...]}}
    boundary_detail: Dict[str, dict] = field(default_factory=dict)

    # ¿Hay componentes no-lineales?
    has_nonlinear: bool = False

    # ¿El circuito tiene tierra?
    has_gnd: bool = False

    # Diagnóstico
    warnings: List[str] = field(default_factory=list)
    errors:   List[str] = field(default_factory=list)

    # ── Propiedades derivadas ─────────────────────────────────────────────

    @property
    def needs_mixed(self) -> bool:
        """True si se requiere co-simulación mixta."""
        return (
            (self.has_digital and self.has_dc)
            or self.has_bridges
            or bool(self.implicit_boundary_nodes)
        )

    @property
    def needs_dc_only(self) -> bool:
        return self.has_dc and not self.has_ac and not self.has_digital

    @property
    def needs_ac_only(self) -> bool:
        return self.has_ac and not self.has_digital

    @property
    def needs_dc_and_ac(self) -> bool:
        return self.has_dc and self.has_ac and not self.has_digital

    def summary(self) -> str:
        modes = []
        if self.has_dc:      modes.append('DC')
        if self.has_ac:      modes.append('AC')
        if self.has_digital: modes.append('Digital')
        if self.needs_mixed: modes.append('→ Co-simulación mixta')
        if not modes:
            return 'Circuito vacío o sin componentes reconocidos'
        s = 'Análisis detectado: ' + ' + '.join(modes)
        if self.implicit_boundary_nodes:
            s += f'\n  Nodos frontera implícitos: {self.implicit_boundary_nodes}'
        return s


# ──────────────────────────────────────────────────────────────────────────────
# Clasificación de tipos de componentes del canvas
# ──────────────────────────────────────────────────────────────────────────────

# Tipos que implican dominio analógico
_ANALOG_TYPES: Set[str] = {
    'R', 'C', 'L', 'V', 'I', 'VAC', 'D', 'LED',
    'BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP', 'TL082', 'Z',
    # Instrumentos analógicos
    'FGEN',   # genera tensión (fuente)
    'OSC',    # sólo lee — no aporta al MNA pero su presencia implica análisis temporal
}

# Tipos analógicos que actúan como drivers activos (pueden crear frontera implícita real).
# LED/Diodo son consumidores pasivos: si están en la salida de una puerta digital,
# se manejan con _evaluate_digital_gates, NO requieren co-simulación transitoria.
_ANALOG_DRIVER_TYPES: Set[str] = {
    'R', 'C', 'L', 'V', 'I', 'VAC',
    'BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS', 'OPAMP', 'TL082', 'Z',
    'FGEN',
}

# Tipos que son fuentes AC / dependientes del tiempo (disparan live transient)
_AC_SOURCE_TYPES: Set[str] = {'VAC', 'FGEN'}

# La sola presencia de estos componentes obliga al solver a correr en
# modo temporal (live transient) aunque no haya fuentes AC explícitas.
# Un osciloscopio sin fuentes activas no tendría sentido, pero conectado
# entre dos nodos DC podría querer ver la traza estática igual.
_TIME_DOMAIN_HINT_TYPES: Set[str] = {'OSC'}

# Tipos no-lineales
_NONLINEAR_TYPES: Set[str] = {'D', 'LED', 'BJT_NPN', 'BJT_PNP', 'NMOS', 'PMOS'}

# Tipos que son puertas / flip-flops / bloques digitales puros
_DIGITAL_GATE_TYPES: Set[str] = {
    'AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR',
    'DFF', 'JKFF', 'TFF', 'SRFF',
    'MUX2', 'COUNTER',
}

# Tipos de puentes explícitos
_BRIDGE_TYPES: Set[str] = {'ADC_BRIDGE', 'DAC_BRIDGE', 'COMPARATOR', 'PWM'}

# Nombres canónicos de tierra
_GND_NAMES: Set[str] = {'0', 'gnd', 'GND', 'ground'}


# ──────────────────────────────────────────────────────────────────────────────
# Analizador principal
# ──────────────────────────────────────────────────────────────────────────────

class CircuitAnalyzer:
    """
    Analiza estáticamente un netlist (lista de ComponentItem del canvas)
    y produce AnalysisFlags.

    Uso:
        analyzer = CircuitAnalyzer(logic_standard='CMOS_3V3')
        flags = analyzer.analyze(scene_components, pin_node_map)
        print(flags.summary())
        # → Análisis detectado: DC + AC
    """

    def __init__(self, logic_standard: str = 'CMOS_3V3'):
        self.std = LOGIC_STANDARDS.get(logic_standard, DEFAULT_STANDARD)

    def analyze(self,
                scene_components: list,
                pin_node_map: Dict[str, str]) -> AnalysisFlags:
        """
        Inspecciona el circuito y devuelve AnalysisFlags con todos los
        análisis necesarios ya determinados.

        Args:
            scene_components: lista de ComponentItem del canvas
            pin_node_map:     {'{name}__p{i}': nodo_str} del netlist extraído
        """
        flags = AnalysisFlags()

        # ── Paso 1: clasificar cada componente ───────────────────────────
        analog_nodes:  Dict[str, List[str]] = {}  # nodo → [comp_names]
        digital_nodes: Dict[str, List[str]] = {}  # nodo → [comp_names]

        for item in scene_components:
            ct = item.comp_type

            # Nodos del componente
            n1 = (item.node1.strip()
                  or pin_node_map.get(f'{item.name}__p1', ''))
            n2 = (item.node2.strip()
                  or pin_node_map.get(f'{item.name}__p2', ''))
            n3 = (item.node3.strip() if hasattr(item, 'node3') else ''
                  or pin_node_map.get(f'{item.name}__p3', ''))

            comp_nodes = [n for n in (n1, n2, n3) if n and n not in _GND_NAMES]

            # ── Detectar tierra ───────────────────────────────────────────
            all_nodes_with_gnd = {n1, n2, n3}
            if all_nodes_with_gnd & _GND_NAMES:
                flags.has_gnd = True

            # ── Clasificar dominio ────────────────────────────────────────
            if ct in _ANALOG_TYPES:
                flags.has_dc = True
                if ct in _AC_SOURCE_TYPES:
                    flags.has_ac = True
                # OSC obliga a transient (queremos ver la traza temporal aunque
                # la fuente sea DC). Reusa `has_ac` porque ese flag rutea al
                # live transient en MainWindow._toggle_simulation.
                if ct in _TIME_DOMAIN_HINT_TYPES:
                    flags.has_ac = True
                if ct in _NONLINEAR_TYPES:
                    flags.has_nonlinear = True
                # Solo drivers activos crean fronteras implícitas.
                # LED/Diodo en salida de puerta digital se manejan con
                # _evaluate_digital_gates, sin necesitar co-simulación.
                if ct in _ANALOG_DRIVER_TYPES:
                    for n in comp_nodes:
                        analog_nodes.setdefault(n, []).append(item.name)

            elif ct in _DIGITAL_GATE_TYPES:
                flags.has_digital = True
                
                out_pin = f'{item.name}__p1'
                out_node = (item.node1.strip()
                            or pin_node_map.get(out_pin, ''))
                
                if out_node and out_node not in _GND_NAMES:
                    digital_nodes.setdefault(out_node, []).append(item.name)
                    
            elif ct in _BRIDGE_TYPES:
                flags.has_bridges = True
                flags.has_digital = True
                flags.has_dc = True

            elif ct == 'GND':
                flags.has_gnd = True

        # ── Paso 2: detectar nodos frontera implícitos ───────────────────
        shared = set(analog_nodes.keys()) & set(digital_nodes.keys())
        for node in sorted(shared):
            flags.implicit_boundary_nodes.append(node)
            flags.boundary_detail[node] = {
                'analog_comps':  analog_nodes[node],
                'digital_comps': digital_nodes[node],
                'standard':      self.std.name,
            }

        # ── Paso 3: validaciones básicas ─────────────────────────────────
        if flags.has_dc and not flags.has_gnd:
            flags.warnings.append(
                'No se encontró nodo de tierra (GND). '
                'La simulación puede fallar o dar resultados incorrectos.')

        if flags.has_ac and not flags.has_dc:
            # Circuito solo con VAC sin resistencias — inusual
            flags.warnings.append(
                'Solo se encontraron fuentes VAC, sin componentes pasivos. '
                'El análisis AC puede no converger.')

        return flags


# ──────────────────────────────────────────────────────────────────────────────
# Detector y aplicador de fronteras implícitas
# ──────────────────────────────────────────────────────────────────────────────

class ImplicitBridgeDetector:
    """
    Detecta nodos frontera y aplica conversión analógico↔digital
    sin ADC/DAC explícitos en el canvas.

    Implementación equivalente a la conversión interna de Multisim:
        - Al extraer el netlist, busca nodos compartidos entre componentes
          analógicos y digitales.
        - En esos nodos aplica la conversión usando los umbrales lógicos
          del estándar configurado (Vdd, Vil, Vih...).
        - El "puente" existe solo durante la simulación, no en el netlist.

    Uso en co-simulación:
        detector = ImplicitBridgeDetector(flags, standard='CMOS_3V3')

        # En cada paso analógico → digital:
        d_updates = detector.analog_to_digital(v_snap)
        # d_updates: {net: 0|1|None} para cada nodo frontera

        # En cada paso digital → analógico:
        v_updates = detector.digital_to_analog(d_snap)
        # v_updates: {nodo_analógico: voltaje} para fuentes controladas
    """

    def __init__(self,
                 flags: AnalysisFlags,
                 standard: str = 'CMOS_3V3'):
        self.flags = flags
        self.std   = LOGIC_STANDARDS.get(standard, DEFAULT_STANDARD)
        # Cache del último nivel lógico por nodo
        self._last_logic: Dict[str, Optional[int]] = {}

    def analog_to_digital(self,
                           analog_voltages: Dict[str, float]
                           ) -> Dict[str, Optional[int]]:
        """
        Para cada nodo frontera, convierte su voltaje al nivel lógico.

        Returns:
            {nodo: 0|1|None}
            None indica zona de transición (indeterminado).
        """
        result: Dict[str, Optional[int]] = {}
        for node in self.flags.implicit_boundary_nodes:
            v = analog_voltages.get(node, 0.0)
            level = self.std.voltage_to_logic(v)

            # Histéresis: si el nodo estaba determinado y entra en zona
            # indeterminada, mantener el último nivel conocido
            if level is None and node in self._last_logic:
                level = self._last_logic[node]

            self._last_logic[node] = level
            result[node] = level
        return result

    def digital_to_analog(self,
                           digital_nets: Dict[str, int]
                           ) -> Dict[str, float]:
        """
        Para cada nodo frontera con salida digital, calcula el voltaje
        analógico de acuerdo al estándar (Vol/Voh).

        Returns:
            {nodo: voltaje_V}
        """
        result: Dict[str, float] = {}
        for node in self.flags.implicit_boundary_nodes:
            if node in digital_nets:
                level = digital_nets[node]
                result[node] = self.std.logic_to_voltage(level)
        return result

    def indeterminate_nodes(self,
                             analog_voltages: Dict[str, float]
                             ) -> List[str]:
        """Lista de nodos en zona de transición (Vil < V < Vih)."""
        return [
            node
            for node in self.flags.implicit_boundary_nodes
            if self.std.Vil < analog_voltages.get(node, 0.0) < self.std.Vih
        ]
