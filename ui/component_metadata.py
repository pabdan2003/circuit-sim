from __future__ import annotations

from typing import Dict, Optional, Tuple


DEFAULT_NODE_LABELS = ('Nodo +', 'Nodo −', None)

COMPONENT_NODE_LABELS: Dict[str, Tuple[str, str, Optional[str]]] = {
    'R':       ('Nodo 1',       'Nodo 2',            None),
    'POT':     ('Nodo 1',       'Nodo 2 (cursor)',   None),
    'C':       ('Nodo 1',       'Nodo 2',            None),
    'L':       ('Nodo 1',       'Nodo 2',            None),
    'V':       ('Nodo + (ánodo)',  'Nodo − (cátodo)', None),
    'I':       ('Nodo + (salida)', 'Nodo − (entrada)', None),
    'D':       ('Ánodo (A)',    'Cátodo (K)',        None),
    'LED':     ('Ánodo (A)',    'Cátodo (K)',        None),
    'BJT_NPN': ('Colector (C)', 'Emisor (E)',        'Base (B)'),
    'BJT_PNP': ('Colector (C)', 'Emisor (E)',        'Base (B)'),
    'NMOS':    ('Drain (D)',    'Source (S)',        'Gate (G)'),
    'PMOS':    ('Drain (D)',    'Source (S)',        'Gate (G)'),
    'OPAMP':   ('Salida (OUT)', 'Entrada − (V−)',    'Entrada + (V+)'),
    # Instrumentos
    'FGEN':    ('Salida + (V+)', 'Salida − (V−)',    None),
    'MULTIMETER': ('Punta + (rojo)', 'Punta − (negro)', None),
}

FIVE_PIN_NODE_LABELS: Dict[str, Tuple[str, str, str, str, str]] = {
    # TL082 dual op-amp: OUT, IN−, IN+, V+, V−
    'TL082': ('Salida (OUT)', 'Entrada − (IN−)', 'Entrada + (IN+)',
              'Alimentación V+', 'Alimentación V−'),
}

FOUR_PIN_NODE_LABELS: Dict[str, Tuple[str, str, str, str]] = {
    'XFMR':   ('Primario + (P1)', 'Primario − (P2)',
               'Secundario + (S1)', 'Secundario − (S2)'),
    'BRIDGE': ('AC1 (entrada ~)', 'AC2 (entrada ~)',
               'DC + (salida +)', 'DC − (salida −)'),
    # Osciloscopio: 2 canales diferenciales
    'OSC':    ('Canal A +', 'Canal A −',
               'Canal B +', 'Canal B −'),
}

VALUE_LABELS = {
    'R': 'Resistencia (Ω)',
    'V': 'Voltaje (V)',
    'I': 'Corriente (A)',
    'C': 'Capacitancia (F)',
    'L': 'Inductancia (H)',
    'POT': 'R total (Ω)',
    'D': 'Is — Corriente saturación (A)',
    'LED': 'Valor (no usado — Vf según color)',
    'BJT_NPN': 'hFE — Ganancia β',
    'BJT_PNP': 'hFE — Ganancia β',
    'NMOS': 'Kn — Transconductancia (A/V²)',
    'PMOS': 'Kp — Transconductancia (A/V²)',
    'OPAMP': 'A — Ganancia lazo abierto (V/V)',
    'TL082': 'A — Ganancia lazo abierto (V/V)',
    'XFMR': 'V_pri nominal (V) — informativo',
    'BRIDGE': 'V_f por diodo (V) — informativo',
    'FGEN': 'Amplitud (V)',
}

DIGITAL_GATE_TYPES = {'AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR'}
DIGITAL_FLIPFLOP_TYPES = {'DFF', 'JKFF', 'TFF', 'SRFF'}
DIGITAL_BRIDGE_TYPES = {'ADC_BRIDGE', 'DAC_BRIDGE', 'COMPARATOR'}
DIGITAL_COUNT_TYPES = {'COUNTER', 'MUX2'}
