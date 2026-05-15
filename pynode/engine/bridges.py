"""
Puentes ADC y DAC para simulación mixta analógica-digital.

Filosofía de diseño:
    El motor MNA (analógico) y el DigitalSimulator (digital) corren
    en dominios de tiempo independientes y se comunican a través de
    adaptadores — nunca se mezclan sus matrices internas.

    ┌──────────────┐       ┌──────────┐       ┌─────────────────┐
    │  MNA Solver  │──────▶│  ADC     │──────▶│ DigitalSimulator│
    │  (voltajes)  │       │ (bridge) │       │  (señales 0/1)  │
    └──────────────┘       └──────────┘       └────────┬────────┘
                                                        │
    ┌──────────────┐       ┌──────────┐                 │
    │  MNA Solver  │◀──────│  DAC     │◀────────────────┘
    │  (fuentes)   │       │ (bridge) │
    └──────────────┘       └──────────┘

Clases:
    ADC               — Convierte voltaje analógico → código digital en n bits
    DAC               — Convierte código digital → voltaje analógico (o corriente)
    ComparatorBridge  — Un bit: Vanalog > Vref → HIGH, sirve como 1-bit ADC
    PWMBridge         — Motor digital genera PWM, DAC lo filtra a nivel DC promedio
    SampleAndHold     — Congela Vanalog en flanco de CLK digital para el ADC
    MixedSignalBus    — Agrupa múltiples bits digitales en un bus MNA-visible

Flujo de uso típico (ver docstring de MixedSignalInterface):
    iface = MixedSignalInterface(mna_solver, digital_sim)
    iface.add_adc(ADC("U_ADC", node="Vout", bits=8, vref=3.3, clk="CLK"))
    iface.add_dac(DAC("U_DAC", bits=8, vref=3.3, out_node="Vdac"))
    iface.step(dt=1e-6)
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np


# ──────────────────────────────────────────────────────────────────────────────
# ADC: Analógico → Digital
# ──────────────────────────────────────────────────────────────────────────────

class ADC:
    """
    Convertidor Analógico-Digital de N bits.

    Muestrea el voltaje de `node` en el instante indicado y produce
    un código binario de `bits` bits en las señales digitales
    `{out_prefix}0` (LSB) … `{out_prefix}{bits-1}` (MSB).

    Parámetros:
        name:        identificador único del ADC
        node:        nombre del nodo analógico de entrada
        bits:        resolución en bits (1–32)
        vref:        voltaje de referencia (escala completa), en voltios
        out_prefix:  prefijo de las señales digitales de salida
        clk:         si se especifica, solo muestrea en flanco de subida
                     de esta señal digital; si None, muestrea cada llamada
        offset:      voltaje de offset (default 0 V)
        encoding:    'binary' (natural), 'twos_complement' o 'gray'
        differential: si True, la entrada es `node` − `node_neg`
        node_neg:    segundo nodo para modo diferencial (default '0'/GND)
    """

    def __init__(self, name: str, node: str, bits: int = 8,
                 vref: float = 3.3,
                 out_prefix: str = None,
                 clk: str = None,
                 offset: float = 0.0,
                 encoding: str = 'binary',
                 differential: bool = False,
                 node_neg: str = '0'):
        self.name        = name
        self.node        = node
        self.bits        = bits
        self.vref        = vref
        self.out_prefix  = out_prefix or f'{name}_D'
        self.clk         = clk
        self.offset      = offset
        self.encoding    = encoding.lower()
        self.differential = differential
        self.node_neg    = node_neg

        self._last_clk   = 0
        self._last_code  = 0
        self._lsb        = vref / (2 ** bits)       # voltios por LSB
        self._max_code   = (1 << bits) - 1

        # Señales de salida digitales
        self.output_nets: List[str] = [f'{self.out_prefix}{i}' for i in range(bits)]
        # Señal de datos válidos (strobe)
        self.valid_net   = f'{name}_VALID'

    @property
    def lsb(self) -> float:
        """Voltaje de 1 LSB."""
        return self._lsb

    def sample(self, analog_voltages: Dict[str, float],
               digital_nets: Dict[str, int],
               t: float) -> Optional[Dict[str, int]]:
        """
        Intenta realizar una conversión.

        Args:
            analog_voltages: voltajes actuales del dominio MNA {nodo: V}
            digital_nets:    estado actual de señales digitales
            t:               tiempo de simulación actual (s)

        Returns:
            dict {net: bit} con los cambios de señales digitales,
            o None si no hay conversión (CLK no ha disparado).
        """
        # ── Control de CLK ─────────────────────────────────────────────
        if self.clk is not None:
            clk_now = digital_nets.get(self.clk, 0)
            rising  = (clk_now == 1 and self._last_clk == 0)
            self._last_clk = clk_now
            if not rising:
                return None

        # ── Lectura del voltaje ─────────────────────────────────────────
        v_in  = analog_voltages.get(self.node, 0.0)
        if self.differential:
            v_neg = analog_voltages.get(self.node_neg, 0.0)
            v_in  = v_in - v_neg

        v_in -= self.offset

        # ── Cuantización ───────────────────────────────────────────────
        code = self._quantize(v_in)
        self._last_code = code

        # ── Codificación ───────────────────────────────────────────────
        encoded = self._encode(code)

        # ── Construir mapa de señales digitales ────────────────────────
        result: Dict[str, int] = {}
        for i, net in enumerate(self.output_nets):
            result[net] = (encoded >> i) & 1
        result[self.valid_net] = 1

        return result

    def _quantize(self, v: float) -> int:
        """
        Cuantiza v en rango [0, vref] a un entero [0, 2^bits-1].
        v >= vref → código máximo (saturación).
        v <  0   → código 0 (saturación negativa).
        """
        if v >= self.vref:
            return self._max_code
        v_clipped = max(0.0, v)
        return min(int(v_clipped / self._lsb), self._max_code)

    def _encode(self, code: int) -> int:
        if self.encoding == 'gray':
            return code ^ (code >> 1)
        elif self.encoding == 'twos_complement':
            # Rango: [−vref/2, +vref/2) → desplazar y mapear
            half = 1 << (self.bits - 1)
            code = code - half
            return code & self._max_code
        else:  # 'binary'
            return code

    @property
    def last_voltage(self) -> float:
        """Voltaje analógico correspondiente al último código muestreado."""
        return self._last_code * self._lsb

    @property
    def last_code(self) -> int:
        return self._last_code

    def code_to_voltage(self, code: int) -> float:
        return code * self._lsb

    def voltage_to_code(self, v: float) -> int:
        return self._quantize(v)


# ──────────────────────────────────────────────────────────────────────────────
# DAC: Digital → Analógico
# ──────────────────────────────────────────────────────────────────────────────

class DAC:
    """
    Convertidor Digital-Analógico de N bits.

    Lee las señales digitales `{in_prefix}0` (LSB) … `{in_prefix}{bits-1}` (MSB)
    y produce un voltaje analógico equivalente que se inyecta en `out_node`
    como una fuente de voltaje o corriente en el dominio MNA.

    La inyección se realiza actualizando el valor de una `VoltageSource`
    o `CurrentSource` ya existente en el circuito, sin alterar la topología.

    Parámetros:
        name:        identificador
        bits:        resolución en bits
        vref:        voltaje de referencia (fondo de escala)
        out_node:    nodo analógico donde se inyecta el voltaje
        in_prefix:   prefijo de señales digitales de entrada
        encoding:    'binary', 'twos_complement' o 'gray'
        output_type: 'voltage' o 'current' (en Amperios si current)
        r_out:       resistencia de salida del DAC en Ω (para modelo Thevenin)
        clk:         señal de reloj; si None → actualización combinacional
        settling_time: tiempo de asentamiento del DAC (para cálculo de t_valid)
    """

    def __init__(self, name: str, bits: int = 8,
                 vref: float = 3.3,
                 out_node: str = 'Vdac',
                 in_prefix: str = None,
                 encoding: str = 'binary',
                 output_type: str = 'voltage',
                 r_out: float = 0.0,
                 clk: str = None,
                 settling_time: float = 1e-7):
        self.name         = name
        self.bits         = bits
        self.vref         = vref
        self.out_node     = out_node
        self.in_prefix    = in_prefix or f'{name}_D'
        self.encoding     = encoding.lower()
        self.output_type  = output_type
        self.r_out        = r_out
        self.clk          = clk
        self.settling_time = settling_time

        self._lsb         = vref / (2 ** bits)
        self._max_code    = (1 << bits) - 1
        self._last_clk    = 0
        self._current_v   = 0.0
        self._settled_at  = -1.0

        # Señales de entrada digitales
        self.input_nets: List[str] = [f'{self.in_prefix}{i}' for i in range(bits)]

    @property
    def lsb(self) -> float:
        return self._lsb

    def convert(self, digital_nets: Dict[str, int],
                t: float) -> Optional[float]:
        """
        Calcula el voltaje analógico de salida basado en el estado digital.

        Args:
            digital_nets: estado actual de señales digitales
            t:            tiempo de simulación actual (s)

        Returns:
            voltaje (o corriente) analógico de salida, o None si CLK no disparó.
        """
        # ── Control de CLK ─────────────────────────────────────────────
        if self.clk is not None:
            clk_now = digital_nets.get(self.clk, 0)
            rising  = (clk_now == 1 and self._last_clk == 0)
            self._last_clk = clk_now
            if not rising:
                return None

        # ── Leer código digital ─────────────────────────────────────────
        raw_code = sum(digital_nets.get(net, 0) << i
                       for i, net in enumerate(self.input_nets))

        # ── Decodificación ─────────────────────────────────────────────
        code = self._decode(raw_code)

        # ── Conversión D→A ─────────────────────────────────────────────
        v_out = code * self._lsb
        self._current_v  = v_out
        self._settled_at = t + self.settling_time
        return v_out

    def _decode(self, raw: int) -> int:
        if self.encoding == 'gray':
            # Decodificar Gray a binario
            mask = raw >> 1
            code = raw
            while mask:
                code ^= mask
                mask >>= 1
            return code
        elif self.encoding == 'twos_complement':
            half = 1 << (self.bits - 1)
            if raw >= half:
                return raw - (1 << self.bits)
            return raw
        else:  # 'binary'
            return raw

    def is_settled(self, t: float) -> bool:
        """True si el DAC ha terminado de asentarse."""
        return t >= self._settled_at

    @property
    def current_voltage(self) -> float:
        return self._current_v

    def voltage_to_component(self, mna_components: list,
                              source_name: str = None):
        """
        Busca en `mna_components` la fuente de voltaje/corriente cuyo
        nombre coincide con `source_name` (o self.name) y actualiza su valor.
        Retorna el componente encontrado o None.
        """
        target = source_name or self.name
        from pynode.engine.components import VoltageSource, CurrentSource
        for c in mna_components:
            if c.name == target:
                if isinstance(c, VoltageSource):
                    c.V = self._current_v
                elif isinstance(c, CurrentSource):
                    c.I_val = self._current_v
                return c
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Comparador (1-bit ADC)
# ──────────────────────────────────────────────────────────────────────────────

class ComparatorBridge:
    """
    Comparador analógico que genera una señal digital.

    Si V(node_pos) − V(node_neg) > hysteresis/2 → output = HIGH
    Si V(node_pos) − V(node_neg) < −hysteresis/2 → output = LOW
    (banda de histéresis para evitar oscilaciones en el umbral)

    Útil como 1-bit ADC o para detectar cruce por cero.
    """

    def __init__(self, name: str, node_pos: str, node_neg: str = '0',
                 output_net: str = None,
                 vref: float = 0.0,
                 hysteresis: float = 0.0):
        self.name       = name
        self.node_pos   = node_pos
        self.node_neg   = node_neg
        self.output_net = output_net or f'{name}_OUT'
        self.vref       = vref        # umbral de comparación
        self.hysteresis = hysteresis
        self._last_out  = 0

    def evaluate(self, analog_voltages: Dict[str, float]) -> Dict[str, int]:
        """
        Evalúa el comparador con los voltajes analógicos actuales.
        Retorna {output_net: 0 o 1}.
        """
        vp = analog_voltages.get(self.node_pos, 0.0)
        vn = analog_voltages.get(self.node_neg, 0.0)
        diff = vp - vn - self.vref

        hys = self.hysteresis / 2.0
        if diff > hys:
            self._last_out = 1
        elif diff < -hys:
            self._last_out = 0
        # else: mantener estado (histéresis)

        return {self.output_net: self._last_out}


# ──────────────────────────────────────────────────────────────────────────────
# PWM Bridge (Digital → Analógico por modulación de ancho de pulso)
# ──────────────────────────────────────────────────────────────────────────────

class PWMBridge:
    """
    Convierte una señal PWM digital en su valor DC promedio equivalente.

    Calcula el duty cycle como la fracción de tiempo en HIGH dentro
    de una ventana de `window` segundos y escala a [0, vmax].

    Parámetros:
        name:       identificador
        pwm_net:    nombre de la señal PWM digital
        vmax:       voltaje DC equivalente a duty=100%
        window:     ventana de integración para calcular duty cycle (s)
    """

    def __init__(self, name: str, pwm_net: str,
                 vmax: float = 3.3,
                 window: float = 1e-3):
        self.name    = name
        self.pwm_net = pwm_net
        self.vmax    = vmax
        self.window  = window

        # Historial de (tiempo, valor) para calcular duty cycle
        self._history: List[Tuple[float, int]] = []
        self._v_out   = 0.0

    def update(self, digital_nets: Dict[str, int], t: float) -> float:
        """
        Registra el estado actual del PWM y actualiza el voltaje DC promedio.
        Llama a esto en cada paso de simulación.
        """
        val = digital_nets.get(self.pwm_net, 0)
        self._history.append((t, val))

        # Eliminar puntos fuera de la ventana
        t_start = t - self.window
        self._history = [(ti, vi) for ti, vi in self._history if ti >= t_start]

        # Calcular duty cycle (tiempo en HIGH / tiempo total)
        if len(self._history) < 2:
            self._v_out = val * self.vmax
            return self._v_out

        t_high = 0.0
        for i in range(len(self._history) - 1):
            ti, vi = self._history[i]
            tn, _  = self._history[i + 1]
            if vi == 1:
                t_high += tn - ti

        t_total   = self._history[-1][0] - self._history[0][0]
        duty      = t_high / t_total if t_total > 0 else 0.0
        self._v_out = duty * self.vmax
        return self._v_out

    @property
    def duty_cycle(self) -> float:
        """Último duty cycle calculado (0.0 a 1.0)."""
        if len(self._history) < 2:
            return 0.0
        t_high = 0.0
        for i in range(len(self._history) - 1):
            ti, vi = self._history[i]
            tn, _  = self._history[i + 1]
            if vi == 1:
                t_high += tn - ti
        t_total = self._history[-1][0] - self._history[0][0]
        return t_high / t_total if t_total > 0 else 0.0

    @property
    def output_voltage(self) -> float:
        return self._v_out


# ──────────────────────────────────────────────────────────────────────────────
# Sample-and-Hold
# ──────────────────────────────────────────────────────────────────────────────

class SampleAndHold:
    """
    Congela el voltaje analógico de `node` en el flanco de subida de `clk`.

    El valor congelado se mantiene hasta el siguiente flanco.
    Útil para desacoplar el tiempo de muestreo del ADC del transitorio MNA.
    """

    def __init__(self, name: str, node: str, clk: str):
        self.name      = name
        self.node      = node
        self.clk       = clk
        self._held     = 0.0
        self._last_clk = 0
        self._sampled_at = -1.0

    def update(self, analog_voltages: Dict[str, float],
               digital_nets: Dict[str, int], t: float) -> float:
        """
        Actualiza el S/H. Retorna el voltaje congelado actual.
        """
        clk_now = digital_nets.get(self.clk, 0)
        if clk_now == 1 and self._last_clk == 0:
            self._held       = analog_voltages.get(self.node, 0.0)
            self._sampled_at = t
        self._last_clk = clk_now
        return self._held

    @property
    def held_voltage(self) -> float:
        return self._held

    @property
    def last_sample_time(self) -> float:
        return self._sampled_at


# ──────────────────────────────────────────────────────────────────────────────
# MixedSignalBus — bus entero disponible para ambos dominios
# ──────────────────────────────────────────────────────────────────────────────

class MixedSignalBus:
    """
    Bus de N bits que puede leerse como entero desde ambos dominios.
    La escritura se hace a través del DAC o directamente inyectando señales.

    Atributos computados útiles:
        voltage: valor en voltios escalado a [0, vref]
        code:    valor entero sin procesar
    """

    def __init__(self, name: str, nets: List[str], vref: float = 3.3):
        self.name  = name
        self.nets  = nets       # nets[0] = LSB
        self.vref  = vref
        self._lsb  = vref / (1 << len(nets))

    @property
    def width(self) -> int:
        return len(self.nets)

    def read_code(self, digital_nets: Dict[str, int]) -> int:
        return sum(digital_nets.get(n, 0) << i for i, n in enumerate(self.nets))

    def read_voltage(self, digital_nets: Dict[str, int]) -> float:
        return self.read_code(digital_nets) * self._lsb

    def write_code(self, sim, value: int, at: float = 0.0):
        """Inyecta un valor entero en el bus del simulador digital."""
        from .digital_engine import DigitalSimulator
        for i, net in enumerate(self.nets):
            sim.set_input(net, (value >> i) & 1, at=at)

    def write_voltage(self, sim, voltage: float, at: float = 0.0):
        code = min(int(voltage / self._lsb), (1 << self.width) - 1)
        self.write_code(sim, code, at)
