"""
Motor de simulación digital de eventos discretos.

Modelo: cada compuerta/flip-flop tiene un retardo de propagación (t_pd).
La simulación avanza en orden cronológico de eventos, nunca construye
matrices — es ortogonal al motor MNA.

Tipos de señal: 0 (LOW), 1 (HIGH), X (indefinido), Z (alta impedancia)
Representados con el enum LogicLevel para tipado seguro.

Componentes disponibles:
    Puertas:       AND, OR, NOT, NAND, NOR, XOR, XNOR, BUF, TRI (tristate)
    Flip-flops:    DFF (D con CLK y reset), SRFF, JKFF, TFF
    Registros:     ShiftRegister (SISO / SIPO / PISO / PIPO)
    Contadores:    BinaryCounter (síncrono, n bits)
    Multiplexores: MUX2, MUX4, MUX8, DEMUX2
    Codificadores: Encoder4to2, Decoder2to4
    Memorias:      ROM (tabla verdad), RAM (lectura/escritura)
    Buses:         Bus (agrupación de señales)
    Nodo de señal: SignalNode (fuente/sumidero externo, útil para test bench)

Interfaz principal:
    sim = DigitalSimulator()
    sim.add(AND("U1", inputs=["A","B"], output="Y"))
    sim.set_input("A", 1, at=0)
    sim.set_input("B", 0, at=5e-9)
    events = sim.run(until=50e-9)
    waveform = sim.waveform("Y")
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


# ──────────────────────────────────────────────────────────────────────────────
# Tipo de señal lógica
# ──────────────────────────────────────────────────────────────────────────────

class LogicLevel(IntEnum):
    Z = -2   # Alta impedancia (tristate flotante)
    X =  0   # Indefinido / conflicto
    L =  0   # LOW  (alias, mismo entero que X — distinguibles solo por contexto)
    H =  1   # HIGH

# Mapeo rápido int → LogicLevel (entradas externas)
def to_logic(val) -> int:
    """Convierte 0/1/True/False/LogicLevel a int de señal (0 ó 1).
    Devuelve 0 si indefinido."""
    if val is None:
        return 0
    if isinstance(val, bool):
        return int(val)
    return int(bool(val))


# ──────────────────────────────────────────────────────────────────────────────
# Evento de simulación
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(order=True)
class Event:
    time:   float          # segundos
    net:    str  = field(compare=False)   # nombre de la señal que cambia
    value:  int  = field(compare=False)   # nuevo valor (0 ó 1)
    source: str  = field(compare=False, default='')  # componente origen


# ──────────────────────────────────────────────────────────────────────────────
# Simulador de eventos discretos
# ──────────────────────────────────────────────────────────────────────────────

class DigitalSimulator:
    """
    Motor de simulación digital basado en lista de eventos.

    Attributes:
        nets:       estado actual de cada señal {nombre: int (0/1)}
        history:    {nombre: [(tiempo, valor), ...]}  historial de cambios
        components: lista de componentes registrados
    """

    def __init__(self):
        self.nets:       Dict[str, int]                  = {}
        self.history:    Dict[str, List[Tuple[float,int]]] = {}
        self.components: List[DigitalComponent]           = []
        self._queue:     List[Event]                      = []  # heap
        self._time:      float                            = 0.0
        self._listeners: Dict[str, List[DigitalComponent]] = {}  # net → comps

    # ── Construcción ───────────────────────────────────────────────────────

    def add(self, comp: 'DigitalComponent') -> 'DigitalSimulator':
        """Registra un componente y suscribe sus entradas al simulador."""
        comp.attach(self)
        self.components.append(comp)
        for net in comp.inputs:
            self._listeners.setdefault(net, []).append(comp)
        return self

    def set_input(self, net: str, value: int, at: float = 0.0) -> 'DigitalSimulator':
        """Programa un cambio externo en la señal `net` en el tiempo `at`."""
        heapq.heappush(self._queue, Event(at, net, int(bool(value)), 'stimulus'))
        return self

    def set_clock(self, net: str, period: float,
                  duty: float = 0.5, until: float = None) -> 'DigitalSimulator':
        """
        Genera un reloj cuadrado en `net` con el período y duty cycle dados.
        Si `until` es None se limita al tiempo máximo de la simulación.
        """
        t_high = period * duty
        t = 0.0
        stop = until if until else 1.0  # se ajusta en run()
        self._pending_clocks = getattr(self, '_pending_clocks', [])
        self._pending_clocks.append((net, period, duty))
        return self

    def _inject_clock(self, net: str, period: float, duty: float, until: float):
        t_high = period * duty
        t = 0.0
        v = 1
        while t <= until:
            heapq.heappush(self._queue, Event(t, net, v, 'clock'))
            t += t_high if v == 1 else (period - t_high)
            v = 1 - v

    # ── Ejecución ──────────────────────────────────────────────────────────

    def run(self, until: float) -> List[Event]:
        """
        Avanza la simulación hasta `until` segundos.
        Devuelve la lista de todos los eventos procesados.
        """
        # Inyectar relojes pendientes con el tiempo final correcto
        for net, period, duty in getattr(self, '_pending_clocks', []):
            self._inject_clock(net, period, duty, until)
        self._pending_clocks = []

        processed: List[Event] = []

        while self._queue:
            if self._queue[0].time > until:  # peek sin consumir
                break
            ev = heapq.heappop(self._queue)

            self._time = ev.time

            # Ignorar eventos redundantes (misma señal ya tiene ese valor)
            if self.nets.get(ev.net) == ev.value:
                continue

            # Actualizar estado y registrar historia
            self.nets[ev.net] = ev.value
            self.history.setdefault(ev.net, []).append((ev.time, ev.value))
            processed.append(ev)

            # Propagar a todos los componentes que escuchan este net
            for comp in self._listeners.get(ev.net, []):
                new_events = comp.evaluate(self._time, self.nets)
                for ne in new_events:
                    heapq.heappush(self._queue, ne)

        return processed

    # ── Consultas ──────────────────────────────────────────────────────────

    def waveform(self, net: str, t_start: float = 0.0,
                 t_stop: float = None) -> List[Tuple[float, int]]:
        """
        Devuelve la forma de onda de `net` como lista [(tiempo, valor), ...].
        Incluye el punto t=0 con el valor inicial 0 si no hay eventos previos.
        """
        hist = self.history.get(net, [])
        if not hist or hist[0][0] > 0:
            hist = [(t_start, self.nets.get(net, 0))] + hist
        if t_stop is not None:
            hist = [(t, v) for t, v in hist if t <= t_stop]
        return hist

    def final_value(self, net: str) -> int:
        """Valor actual (al final de la simulación) de una señal."""
        return self.nets.get(net, 0)

    def value_at(self, net: str, t: float) -> int:
        """Valor de la señal en un instante específico (interpolación escalón)."""
        hist = self.history.get(net, [])
        v = 0
        for ev_t, ev_v in hist:
            if ev_t <= t:
                v = ev_v
            else:
                break
        return v

    def reset(self):
        """Reinicia el simulador para una nueva simulación."""
        self.nets.clear()
        self.history.clear()
        self._queue.clear()
        self._time = 0.0
        for comp in self.components:
            comp.reset()


# ──────────────────────────────────────────────────────────────────────────────
# Clase base de componente digital
# ──────────────────────────────────────────────────────────────────────────────

class DigitalComponent:
    """
    Interfaz base para todos los componentes digitales.

    Subclases deben implementar `evaluate()`.
    """

    def __init__(self, name: str, inputs: List[str], outputs: List[str],
                 t_pd: float = 1e-9):
        self.name    = name
        self.inputs  = inputs
        self.outputs = outputs
        self.t_pd    = t_pd    # retardo de propagación en segundos
        self._sim: Optional[DigitalSimulator] = None

    def attach(self, sim: DigitalSimulator):
        self._sim = sim

    def reset(self):
        """Hook para reiniciar estado interno (flip-flops, registros, etc.)."""
        pass

    def evaluate(self, t: float, nets: Dict[str, int]) -> List[Event]:
        """
        Calcula las salidas dado el estado actual de las señales.
        Devuelve una lista de eventos futuros (programados t + t_pd).
        """
        raise NotImplementedError

    def _emit(self, t: float, net: str, value: int) -> Event:
        """Helper para crear un evento de salida con retardo."""
        return Event(t + self.t_pd, net, int(bool(value)), self.name)

    def _get(self, nets: Dict[str, int], key: str, default: int = 0) -> int:
        return nets.get(key, default)


# ──────────────────────────────────────────────────────────────────────────────
# Puertas lógicas
# ──────────────────────────────────────────────────────────────────────────────

class Gate(DigitalComponent):
    """Puerta lógica genérica de N entradas → 1 salida."""

    _FUNCS: Dict[str, Callable] = {
        'AND':  lambda vals: all(vals),
        'OR':   lambda vals: any(vals),
        'NAND': lambda vals: not all(vals),
        'NOR':  lambda vals: not any(vals),
        'XOR':  lambda vals: bool(sum(vals) % 2),
        'XNOR': lambda vals: not bool(sum(vals) % 2),
        'BUF':  lambda vals: bool(vals[0]),
        'NOT':  lambda vals: not bool(vals[0]),
    }

    def __init__(self, name: str, gate_type: str,
                 inputs: List[str], output: str,
                 t_pd: float = 1e-9,
                 input_invert: Optional[List[bool]] = None):
        super().__init__(name, inputs, [output], t_pd)
        gt = gate_type.upper()
        if gt not in self._FUNCS:
            raise ValueError(f"Tipo de puerta desconocido: {gate_type}")
        self.gate_type = gt
        self.output    = output
        self._func     = self._FUNCS[gt]
        # Máscara opcional de negación por entrada (alineada con `inputs`).
        # True ⇒ esa entrada se invierte antes de aplicar la función.
        self._input_invert = list(input_invert) if input_invert else []

    def evaluate(self, t, nets):
        vals = [self._get(nets, inp) for inp in self.inputs]
        if self._input_invert:
            vals = [
                (1 - int(bool(v))) if (i < len(self._input_invert)
                                       and self._input_invert[i]) else v
                for i, v in enumerate(vals)
            ]
        out  = int(self._func(vals))
        return [self._emit(t, self.output, out)]


# Alias de conveniencia
def AND(name, inputs, output, t_pd=1e-9):
    return Gate(name, 'AND',  inputs, output, t_pd)
def OR(name, inputs, output, t_pd=1e-9):
    return Gate(name, 'OR',   inputs, output, t_pd)
def NOT(name, inp, output, t_pd=1e-9):
    return Gate(name, 'NOT',  [inp],  output, t_pd)
def NAND(name, inputs, output, t_pd=1e-9):
    return Gate(name, 'NAND', inputs, output, t_pd)
def NOR(name, inputs, output, t_pd=1e-9):
    return Gate(name, 'NOR',  inputs, output, t_pd)
def XOR(name, inputs, output, t_pd=1e-9):
    return Gate(name, 'XOR',  inputs, output, t_pd)
def XNOR(name, inputs, output, t_pd=1e-9):
    return Gate(name, 'XNOR', inputs, output, t_pd)
def BUF(name, inp, output, t_pd=1e-9):
    return Gate(name, 'BUF',  [inp],  output, t_pd)


class TristateBuffer(DigitalComponent):
    """Buffer tristate: si enable=1 → output=input; si enable=0 → Z (se modela como 0)."""

    def __init__(self, name: str, inp: str, enable: str, output: str,
                 active_high: bool = True, t_pd: float = 1e-9):
        super().__init__(name, [inp, enable], [output], t_pd)
        self.inp        = inp
        self.enable     = enable
        self.output     = output
        self.active_high = active_high

    def evaluate(self, t, nets):
        en  = self._get(nets, self.enable)
        val = self._get(nets, self.inp)
        if self.active_high:
            out = val if en else 0
        else:
            out = val if not en else 0
        return [self._emit(t, self.output, out)]


# ──────────────────────────────────────────────────────────────────────────────
# Flip-flops
# ──────────────────────────────────────────────────────────────────────────────

class DFF(DigitalComponent):
    """
    Flip-flop D disparado por flanco de subida de CLK.
    Entradas opcionales: reset (síncrono, active-high), set (síncrono, active-high).
    Salidas: Q, Qn (complemento).
    """

    def __init__(self, name: str, d: str, clk: str, q: str,
                 qn: str = None, reset: str = None, set_: str = None,
                 t_pd: float = 2e-9):
        inputs = [d, clk]
        if reset: inputs.append(reset)
        if set_:  inputs.append(set_)
        outputs = [q] + ([qn] if qn else [])
        super().__init__(name, inputs, outputs, t_pd)
        self.d     = d
        self.clk   = clk
        self.q     = q
        self.qn    = qn
        self.reset = reset
        self.set_  = set_
        self._Q    = 0
        self._last_clk = 0

    def reset(self):
        self._Q = 0
        self._last_clk = 0

    def evaluate(self, t, nets):
        clk_now = self._get(nets, self.clk)
        events  = []

        # Solo actuar en flanco de subida
        if clk_now == 1 and self._last_clk == 0:
            if self.reset and self._get(nets, self.reset):
                self._Q = 0
            elif self.set_ and self._get(nets, self.set_):
                self._Q = 1
            else:
                self._Q = self._get(nets, self.d)

            events.append(self._emit(t, self.q, self._Q))
            if self.qn:
                events.append(self._emit(t, self.qn, 1 - self._Q))

        self._last_clk = clk_now
        return events


class SRFF(DigitalComponent):
    """
    Flip-flop SR (Set-Reset) asíncrono.
    S=1, R=0 → Q=1 | S=0, R=1 → Q=0 | S=R=1 → estado no permitido (Q=X→0)
    """

    def __init__(self, name: str, s: str, r: str, q: str, qn: str = None,
                 t_pd: float = 2e-9):
        super().__init__(name, [s, r], [q] + ([qn] if qn else []), t_pd)
        self.s  = s;  self.r  = r
        self.q  = q;  self.qn = qn
        self._Q = 0

    def reset(self):
        self._Q = 0

    def evaluate(self, t, nets):
        S = self._get(nets, self.s)
        R = self._get(nets, self.r)
        events = []
        if S and R:
            self._Q = 0   # estado no permitido → 0
        elif S:
            self._Q = 1
        elif R:
            self._Q = 0
        # else: mantener estado

        events.append(self._emit(t, self.q, self._Q))
        if self.qn:
            events.append(self._emit(t, self.qn, 1 - self._Q))
        return events


class JKFF(DigitalComponent):
    """Flip-flop JK disparado por flanco de subida.
    SET y RESET asíncronos opcionales, activos en alto.
    """

    def __init__(self, name: str, j: str, k: str, clk: str,
                 q: str, qn: str = None,
                 reset: str = None, set_: str = None,
                 t_pd: float = 2e-9):
        inputs = [j, k, clk]
        if reset: inputs.append(reset)
        if set_:  inputs.append(set_)
        super().__init__(name, inputs, [q] + ([qn] if qn else []), t_pd)
        self.j  = j;  self.k  = k;  self.clk = clk
        self.q  = q;  self.qn = qn
        self.reset = reset; self.set_ = set_
        self._Q = 0;  self._last_clk = 0

    def reset(self):
        self._Q = 0; self._last_clk = 0

    def evaluate(self, t, nets):
        clk_now = self._get(nets, self.clk)
        events  = []
        # Asíncronos: tienen prioridad sobre el reloj
        if self.reset and self._get(nets, self.reset):
            self._Q = 0
        elif self.set_ and self._get(nets, self.set_):
            self._Q = 1
        elif clk_now == 1 and self._last_clk == 0:
            J = self._get(nets, self.j)
            K = self._get(nets, self.k)
            if   J == 0 and K == 0: pass          # hold
            elif J == 0 and K == 1: self._Q = 0   # reset
            elif J == 1 and K == 0: self._Q = 1   # set
            else:                   self._Q ^= 1   # toggle

        events.append(self._emit(t, self.q, self._Q))
        if self.qn:
            events.append(self._emit(t, self.qn, 1 - self._Q))
        self._last_clk = clk_now
        return events


class TFF(DigitalComponent):
    """Flip-flop T (Toggle): en cada flanco de CLK si T=1 → invierte Q.
    SET y RESET asíncronos opcionales, activos en alto.
    """

    def __init__(self, name: str, t_in: str, clk: str,
                 q: str, qn: str = None,
                 reset: str = None, set_: str = None,
                 t_pd: float = 2e-9):
        inputs = [t_in, clk]
        if reset: inputs.append(reset)
        if set_:  inputs.append(set_)
        super().__init__(name, inputs, [q] + ([qn] if qn else []), t_pd)
        self.t_in = t_in; self.clk = clk
        self.q    = q;    self.qn  = qn
        self.reset = reset; self.set_ = set_
        self._Q   = 0;    self._last_clk = 0

    def reset(self):
        self._Q = 0; self._last_clk = 0

    def evaluate(self, t, nets):
        clk_now = self._get(nets, self.clk)
        events  = []
        if self.reset and self._get(nets, self.reset):
            self._Q = 0
        elif self.set_ and self._get(nets, self.set_):
            self._Q = 1
        elif clk_now == 1 and self._last_clk == 0:
            if self._get(nets, self.t_in):
                self._Q ^= 1
        events.append(self._emit(t, self.q, self._Q))
        if self.qn:
            events.append(self._emit(t, self.qn, 1 - self._Q))
        self._last_clk = clk_now
        return events


# ──────────────────────────────────────────────────────────────────────────────
# Registro de desplazamiento
# ──────────────────────────────────────────────────────────────────────────────

class ShiftRegister(DigitalComponent):
    """
    Registro de desplazamiento de N bits.

    Modos:
        SISO: serial in, serial out   (din → ... → dout)
        SIPO: serial in, parallel out (din → Q[0..n-1])
        PISO: parallel in, serial out (load → D[0..n-1] → dout)
        PIPO: parallel in, parallel out

    Parámetros:
        n:        número de bits
        din:      nombre de señal de entrada serial
        clk:      nombre de señal de reloj
        q_prefix: prefijo de salidas paralelas (ej: 'Q' → Q0, Q1, ...)
        dout:     nombre de salida serial (último bit)
        load:     nombre de señal de carga paralela (para PISO/PIPO)
        d_prefix: prefijo de entradas paralelas (para PISO/PIPO)
        direction:'left' o 'right'
    """

    def __init__(self, name: str, n: int, din: str, clk: str,
                 q_prefix: str = 'Q', dout: str = None,
                 load: str = None, d_prefix: str = 'D',
                 direction: str = 'left', t_pd: float = 2e-9):
        self.n         = n
        self.din       = din
        self.clk_net   = clk
        self.q_prefix  = q_prefix
        self.dout_net  = dout or f'{q_prefix}{n-1}'
        self.load      = load
        self.d_prefix  = d_prefix
        self.direction = direction
        self._reg      = [0] * n
        self._last_clk = 0

        q_nets  = [f'{q_prefix}{i}' for i in range(n)]
        d_nets  = [f'{d_prefix}{i}' for i in range(n)] if load else []
        inputs  = [din, clk] + ([load] if load else []) + d_nets
        outputs = q_nets + ([dout] if dout and dout not in q_nets else [])
        super().__init__(name, inputs, outputs, t_pd)

    def reset(self):
        self._reg = [0] * self.n
        self._last_clk = 0

    def evaluate(self, t, nets):
        clk_now = self._get(nets, self.clk_net)
        events  = []
        if clk_now == 1 and self._last_clk == 0:
            if self.load and self._get(nets, self.load):
                # Carga paralela
                self._reg = [self._get(nets, f'{self.d_prefix}{i}')
                             for i in range(self.n)]
            else:
                # Desplazamiento
                new_bit = self._get(nets, self.din)
                if self.direction == 'left':
                    self._reg = self._reg[1:] + [new_bit]
                else:
                    self._reg = [new_bit] + self._reg[:-1]

            for i, v in enumerate(self._reg):
                events.append(self._emit(t, f'{self.q_prefix}{i}', v))

        self._last_clk = clk_now
        return events


# ──────────────────────────────────────────────────────────────────────────────
# Contador binario
# ──────────────────────────────────────────────────────────────────────────────

class BinaryCounter(DigitalComponent):
    """
    Contador binario síncrono de N bits.

    Señales:
        clk:   reloj (flanco de subida)
        en:    enable (opcional, default siempre activo)
        reset: reset síncrono (opcional)
        q_prefix: prefijo de salidas (Q0 = LSB)
        carry: salida de acarreo (overflow)
    """

    def __init__(self, name: str, n: int, clk: str,
                 en: str = None, reset: str = None,
                 q_prefix: str = 'Q', carry: str = None,
                 t_pd: float = 2e-9):
        self.n         = n
        self.clk_net   = clk
        self.en_net    = en
        self.reset_net = reset
        self.q_prefix  = q_prefix
        self.carry_net = carry
        self._count    = 0
        self._last_clk = 0
        self._max      = (1 << n) - 1

        inputs  = [clk] + ([en] if en else []) + ([reset] if reset else [])
        outputs = [f'{q_prefix}{i}' for i in range(n)] + ([carry] if carry else [])
        super().__init__(name, inputs, outputs, t_pd)

    def reset(self):
        self._count = 0
        self._last_clk = 0

    def evaluate(self, t, nets):
        clk_now = self._get(nets, self.clk_net)
        events  = []
        if clk_now == 1 and self._last_clk == 0:
            en = self._get(nets, self.en_net, 1) if self.en_net else 1
            if self.reset_net and self._get(nets, self.reset_net):
                self._count = 0
            elif en:
                self._count = (self._count + 1) & self._max

            for i in range(self.n):
                bit = (self._count >> i) & 1
                events.append(self._emit(t, f'{self.q_prefix}{i}', bit))
            if self.carry_net:
                events.append(self._emit(t, self.carry_net,
                                         int(self._count == self._max and en)))

        self._last_clk = clk_now
        return events

    @property
    def count(self) -> int:
        return self._count


# ──────────────────────────────────────────────────────────────────────────────
# Multiplexores y Demultiplexores
# ──────────────────────────────────────────────────────────────────────────────

class MUX(DigitalComponent):
    """
    Multiplexor de N entradas con ceil(log2(N)) bits de selección.
    inputs:  [in0, in1, ..., in_{N-1}]
    sel:     [sel0 (LSB), sel1, ...]  lista de señales de selección
    output:  nombre de señal de salida
    """

    def __init__(self, name: str, inputs: List[str], sel: List[str],
                 output: str, t_pd: float = 1e-9):
        super().__init__(name, inputs + sel, [output], t_pd)
        self.data_inputs = inputs
        self.sel         = sel
        self.output      = output

    def evaluate(self, t, nets):
        idx = sum(self._get(nets, s) << i for i, s in enumerate(self.sel))
        idx = min(idx, len(self.data_inputs) - 1)
        out = self._get(nets, self.data_inputs[idx])
        return [self._emit(t, self.output, out)]


class DEMUX(DigitalComponent):
    """
    Demultiplexor: 1 entrada → 2^N salidas.
    Canaliza `input` hacia la salida indexada por `sel`.
    """

    def __init__(self, name: str, inp: str, sel: List[str],
                 out_prefix: str = 'Y', t_pd: float = 1e-9):
        n       = len(sel)
        outputs = [f'{out_prefix}{i}' for i in range(1 << n)]
        super().__init__(name, [inp] + sel, outputs, t_pd)
        self.inp        = inp
        self.sel        = sel
        self.out_prefix = out_prefix
        self.n_out      = 1 << n

    def evaluate(self, t, nets):
        idx = sum(self._get(nets, s) << i for i, s in enumerate(self.sel))
        val = self._get(nets, self.inp)
        events = []
        for i in range(self.n_out):
            events.append(self._emit(t, f'{self.out_prefix}{i}',
                                     val if i == idx else 0))
        return events


# ──────────────────────────────────────────────────────────────────────────────
# ROM / RAM
# ──────────────────────────────────────────────────────────────────────────────

class ROM(DigitalComponent):
    """
    ROM combinacional: addr_bits entradas de dirección, data_bits de datos.
    `table` es un dict {dirección_int: valor_int} o lista de valores.
    """

    def __init__(self, name: str, addr: List[str], data_prefix: str,
                 table: Any, data_bits: int = 8, t_pd: float = 3e-9):
        data_nets = [f'{data_prefix}{i}' for i in range(data_bits)]
        super().__init__(name, addr, data_nets, t_pd)
        self.addr       = addr
        self.data_prefix = data_prefix
        self.data_bits  = data_bits
        if isinstance(table, (list, tuple)):
            self._table = {i: v for i, v in enumerate(table)}
        else:
            self._table = dict(table)

    def evaluate(self, t, nets):
        addr = sum(self._get(nets, a) << i for i, a in enumerate(self.addr))
        val  = self._table.get(addr, 0)
        return [self._emit(t, f'{self.data_prefix}{i}', (val >> i) & 1)
                for i in range(self.data_bits)]


class RAM(DigitalComponent):
    """
    RAM de lectura/escritura.
    wr=1 → escritura (en flanco de CLK), wr=0 → lectura combinacional.
    """

    def __init__(self, name: str, addr: List[str], data_in: List[str],
                 data_out_prefix: str, clk: str, wr: str,
                 data_bits: int = 8, t_pd: float = 5e-9):
        self.addr          = addr
        self.data_in_nets  = data_in
        self.data_out_pref = data_out_prefix
        self.clk_net       = clk
        self.wr_net        = wr
        self.data_bits     = data_bits
        self._mem: Dict[int, int] = {}
        self._last_clk     = 0

        data_out = [f'{data_out_prefix}{i}' for i in range(data_bits)]
        super().__init__(name, addr + data_in + [clk, wr], data_out, t_pd)

    def reset(self):
        self._mem.clear()
        self._last_clk = 0

    def evaluate(self, t, nets):
        clk_now = self._get(nets, self.clk_net)
        addr    = sum(self._get(nets, a) << i for i, a in enumerate(self.addr))
        events  = []

        if clk_now == 1 and self._last_clk == 0:
            if self._get(nets, self.wr_net):
                # Escritura
                val = sum(self._get(nets, d) << i
                          for i, d in enumerate(self.data_in_nets))
                self._mem[addr] = val & ((1 << self.data_bits) - 1)

        # Lectura combinacional (siempre)
        val = self._mem.get(addr, 0)
        for i in range(self.data_bits):
            events.append(self._emit(t, f'{self.data_out_pref}{i}', (val >> i) & 1))

        self._last_clk = clk_now
        return events


# ──────────────────────────────────────────────────────────────────────────────
# Bus (agrupación de señales)
# ──────────────────────────────────────────────────────────────────────────────

class Bus:
    """
    Agrupación lógica de señales. No es un componente de simulación;
    es una utilidad para leer/escribir valores enteros en un grupo de nets.
    """

    def __init__(self, name: str, nets: List[str]):
        self.name = name
        self.nets = nets  # nets[0] = LSB

    def read(self, sim: DigitalSimulator) -> int:
        """Lee el valor entero del bus del estado actual del simulador."""
        return sum(sim.final_value(n) << i for i, n in enumerate(self.nets))

    def read_at(self, sim: DigitalSimulator, t: float) -> int:
        return sum(sim.value_at(n, t) << i for i, n in enumerate(self.nets))

    def write(self, sim: DigitalSimulator, value: int, at: float = 0.0):
        """Programa un evento en cada bit del bus."""
        for i, net in enumerate(self.nets):
            sim.set_input(net, (value >> i) & 1, at=at)

    @property
    def width(self) -> int:
        return len(self.nets)
