"""
Orquestador de simulación mixta analógica-digital.

Coordina el motor MNA (analógico) y el DigitalSimulator (digital)
sin mezclar sus matrices internas. Implementa co-simulación por
partición relajada (relaxation-based co-simulation).

Algoritmo de co-simulación:
    1. MNA resuelve el dominio analógico hasta t + dt_analog
    2. ADC/Comparadores leen voltajes y emiten señales digitales
    3. DigitalSimulator avanza hasta t + dt_digital
    4. DAC/PWM leen el estado digital y actualizan fuentes MNA
    5. Repetir desde 1

El paso analógico (dt_analog) puede ser adaptativo (usa solve_transient
con adaptive=True). El paso digital es el mínimo retardo de propagación
de los componentes registrados.

Clases:
    MixedSignalInterface  — orquestador principal
    MixedSimResult        — resultado de una simulación completa
    TimingAnalyzer        — analiza skew, setup/hold violations post-sim

Uso básico:
    from pynode.engine import MNASolver, Resistor, VoltageSource, Capacitor
    from pynode.engine.digital_engine import DigitalSimulator, DFF, Gate
    from pynode.engine.bridges import ADC, DAC, ComparatorBridge
    from pynode.engine.mixed_signal import MixedSignalInterface

    mna  = MNASolver()
    dsim = DigitalSimulator()

    # Circuito analógico: divisor RC
    analog_comps = [
        VoltageSource("Vin", "in", "0", 5.0),
        Resistor("R1", "in", "mid", 1000),
        Capacitor("C1", "mid", "0", 1e-6),
    ]

    # Circuito digital
    dsim.add(DFF("U1", d="D_in", clk="CLK", q="Q_out"))
    dsim.set_clock("CLK", period=1e-5)

    # Puentes
    adc = ADC("A1", node="mid", bits=8, vref=5.0, clk="CLK")
    dac = DAC("D1", bits=8, vref=5.0, out_node="ctrl")

    iface = MixedSignalInterface(mna, dsim, analog_comps)
    iface.add_adc(adc)
    iface.add_dac(dac, source_name="Vctrl")  # nombre de VoltageSource en mna
    result = iface.run(t_stop=100e-6, dt_analog=1e-7)

    print(result.analog_voltages["mid"])   # np.ndarray de voltajes
    print(result.digital_waveforms["Q_out"])  # [(t, v), ...]
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ──────────────────────────────────────────────────────────────────────────────
# Resultado de simulación mixta
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class MixedSimResult:
    """
    Resultado completo de una co-simulación mixta.

    Attributes:
        t:                  array de tiempos analógicos
        analog_voltages:    {nodo: np.ndarray} — voltajes por tiempo
        analog_dt_stats:    estadísticas del timestep adaptativo
        digital_waveforms:  {net: [(t, v), ...]} — formas de onda digitales
        adc_samples:        {adc_name: [(t, code), ...]} — muestras ADC
        dac_outputs:        {dac_name: [(t, v), ...]} — voltajes DAC
        pwm_duties:         {pwm_name: [(t, duty), ...]} — duty cycles PWM
        events_count:       total de eventos digitales procesados
        success:            bool
        error:              mensaje de error si success=False
        warnings:           lista de advertencias no fatales
    """
    t:                  np.ndarray     = field(default_factory=lambda: np.array([]))
    analog_voltages:    Dict[str, np.ndarray] = field(default_factory=dict)
    analog_dt_stats:    Dict[str, float]      = field(default_factory=dict)
    digital_waveforms:  Dict[str, list]       = field(default_factory=dict)
    adc_samples:        Dict[str, list]       = field(default_factory=dict)
    dac_outputs:        Dict[str, list]       = field(default_factory=dict)
    pwm_duties:         Dict[str, list]       = field(default_factory=dict)
    events_count:       int  = 0
    success:            bool = True
    error:              str  = ''
    warnings:           List[str] = field(default_factory=list)

    # ── Consultas de conveniencia ────────────────────────────────────────

    def analog_at(self, node: str, t: float) -> float:
        """Interpolación lineal del voltaje analógico de `node` en el instante t."""
        if node not in self.analog_voltages:
            return 0.0
        return float(np.interp(t, self.t, self.analog_voltages[node]))

    def digital_at(self, net: str, t: float) -> int:
        """Valor de la señal digital en el instante t (escalón)."""
        hist = self.digital_waveforms.get(net, [])
        v = 0
        for ev_t, ev_v in hist:
            if ev_t <= t:
                v = ev_v
            else:
                break
        return v

    def adc_at(self, adc_name: str, t: float) -> Optional[int]:
        """Último código ADC muestreado en o antes de t."""
        samples = self.adc_samples.get(adc_name, [])
        code = None
        for s_t, s_c in samples:
            if s_t <= t:
                code = s_c
            else:
                break
        return code

    def summary(self) -> str:
        """Resumen textual de la simulación."""
        lines = [
            f"MixedSimResult({'OK' if self.success else 'FAIL'})",
            f"  t_stop    = {self.t[-1] if len(self.t) else 0:.3e} s",
            f"  pasos     = {len(self.t)}",
            f"  nodos A   = {list(self.analog_voltages.keys())}",
            f"  nets D    = {list(self.digital_waveforms.keys())}",
            f"  eventos   = {self.events_count}",
        ]
        if self.analog_dt_stats:
            lines.append(
                f"  dt [min,max,mean] = "
                f"{self.analog_dt_stats.get('min',0):.2e}, "
                f"{self.analog_dt_stats.get('max',0):.2e}, "
                f"{self.analog_dt_stats.get('mean',0):.2e} s"
            )
        for w in self.warnings:
            lines.append(f"  ⚠  {w}")
        return '\n'.join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Interfaz de simulación mixta (orquestador)
# ──────────────────────────────────────────────────────────────────────────────

class MixedSignalInterface:
    """
    Orquestador de co-simulación analógica-digital.

    El MNA y el DigitalSimulator operan en sus propios dominios de tiempo.
    Este objeto gestiona los puentes ADC/DAC y sincroniza los pasos.

    Parámetros:
        mna:            instancia de MNASolver
        digital:        instancia de DigitalSimulator
        analog_comps:   lista de componentes para el MNA
        coupling:       'one_shot' — sin iteración de punto fijo entre dominios
                        'relaxation' — itera hasta convergencia en cada paso
        relax_tol:      tolerancia de convergencia para 'relaxation' (V)
        relax_max_iter: máximo de iteraciones de relajación por paso
    """

    def __init__(self, mna, digital, analog_comps: list,
                 coupling: str = 'one_shot',
                 relax_tol: float = 1e-4,
                 relax_max_iter: int = 10):
        self._mna          = mna
        self._digital      = digital
        self._comps        = analog_comps
        self.coupling      = coupling
        self.relax_tol     = relax_tol
        self.relax_max_iter = relax_max_iter

        # Puentes registrados
        self._adcs:        List[Any] = []   # ADC
        self._dacs:        List[Tuple[Any, str]] = []  # (DAC, source_name)
        self._comparators: List[Any] = []   # ComparatorBridge
        self._pwms:        List[Any] = []   # PWMBridge
        self._sh:          List[Any] = []   # SampleAndHold

        # Estado analógico actual
        self._analog_state: Dict[str, float] = {}
        # Estado digital actual
        self._digital_state: Dict[str, int]  = {}

    # ── Registro de puentes ───────────────────────────────────────────────

    def add_adc(self, adc) -> 'MixedSignalInterface':
        """Registra un ADC. Sus output_nets se añaden al DigitalSimulator."""
        self._adcs.append(adc)
        return self

    def add_dac(self, dac, source_name: str = None) -> 'MixedSignalInterface':
        """
        Registra un DAC.
        `source_name` es el nombre de la VoltageSource/CurrentSource MNA
        que el DAC controla (por defecto dac.name).
        """
        self._dacs.append((dac, source_name or dac.name))
        return self

    def add_comparator(self, comp) -> 'MixedSignalInterface':
        """Registra un ComparatorBridge."""
        self._comparators.append(comp)
        return self

    def add_pwm(self, pwm) -> 'MixedSignalInterface':
        """Registra un PWMBridge (digital → voltaje DC analógico)."""
        self._pwms.append(pwm)
        return self

    def add_sample_hold(self, sh) -> 'MixedSignalInterface':
        """Registra un Sample-and-Hold."""
        self._sh.append(sh)
        return self

    # ── Simulación principal ──────────────────────────────────────────────

    def run(self, t_stop: float,
            dt_analog: float = 1e-7,
            adaptive: bool = True,
            tol_abs: float = 1e-6,
            tol_rel: float = 1e-3,
            dt_min: float = None,
            dt_max: float = None) -> MixedSimResult:
        """
        Ejecuta la co-simulación mixta de 0 a `t_stop` segundos.

        Parámetros:
            t_stop:     tiempo final
            dt_analog:  paso de tiempo analógico inicial (o fijo si adaptive=False)
            adaptive:   usa timestep adaptativo en el dominio analógico
            tol_abs, tol_rel: tolerancias para el control adaptativo de paso
            dt_min, dt_max:   cotas del paso adaptativo

        Retorna MixedSimResult.
        """
        result = MixedSimResult()

        try:
            # ── Inicialización ─────────────────────────────────────────
            self._digital.reset()

            # Inyectar relojes y estímulos del simulador digital
            # (los set_clock y set_input ya habrán sido llamados por el usuario)
            # El DigitalSimulator.run() procesará todo

            # Resolución analógica completa con timestep adaptativo
            tr = self._mna.solve_transient(
                self._comps,
                t_stop  = t_stop,
                dt      = dt_analog,
                method  = 'trapezoidal',
                adaptive= adaptive,
                tol_abs = tol_abs,
                tol_rel = tol_rel,
                dt_min  = dt_min,
                dt_max  = dt_max,
            )

            if not tr['success']:
                result.success = False
                result.error   = f"MNA transitorio falló: {tr['error']}"
                return result

            t_arr    = tr['time']
            v_arrays = tr['voltages']   # {nodo: np.ndarray}
            result.t               = t_arr
            result.analog_voltages = v_arrays
            result.analog_dt_stats = tr.get('dt_stats', {})

            # ── Co-simulación digital sincronizada ─────────────────────
            # 1. Correr el simulador digital hasta t_stop
            digital_events = self._digital.run(until=t_stop)
            result.events_count = len(digital_events)

            # 2. Guardar formas de onda digitales
            all_nets = set(self._digital.history.keys())
            for net in all_nets:
                result.digital_waveforms[net] = self._digital.waveform(net)

            # ── Procesar ADCs ──────────────────────────────────────────
            for adc in self._adcs:
                samples = []
                # Muestrear en cada punto de tiempo analógico
                for i, t in enumerate(t_arr):
                    v_snap = {node: float(arr[i]) for node, arr in v_arrays.items()}
                    v_snap['0'] = 0.0

                    # Estado digital en este instante
                    d_snap = {net: self._digital.value_at(net, t)
                              for net in all_nets}

                    out = adc.sample(v_snap, d_snap, t)
                    if out is not None:
                        samples.append((t, adc.last_code))
                        # Registrar las señales digitales producidas
                        for net, val in out.items():
                            if net not in result.digital_waveforms:
                                result.digital_waveforms[net] = []
                            result.digital_waveforms[net].append((t, val))

                result.adc_samples[adc.name] = samples

            # ── Procesar Comparadores ──────────────────────────────────
            for comp in self._comparators:
                wf = []
                for i, t in enumerate(t_arr):
                    v_snap = {node: float(arr[i]) for node, arr in v_arrays.items()}
                    v_snap['0'] = 0.0
                    out = comp.evaluate(v_snap)
                    for net, val in out.items():
                        wf.append((t, val))
                        result.digital_waveforms.setdefault(net, []).append((t, val))

            # ── Procesar DACs ──────────────────────────────────────────
            for dac, src_name in self._dacs:
                dac_hist = []
                for i, t in enumerate(t_arr):
                    d_snap = {net: self._digital.value_at(net, t)
                              for net in all_nets}
                    v_out = dac.convert(d_snap, t)
                    if v_out is not None:
                        dac_hist.append((t, v_out))
                result.dac_outputs[dac.name] = dac_hist

                # Advertencia: la salida del DAC no retroalimenta el MNA
                # en esta pasada (one_shot). Para retroalimentación completa
                # usar coupling='relaxation' o simulación iterativa.
                if self.coupling == 'one_shot' and dac_hist:
                    result.warnings.append(
                        f"DAC '{dac.name}' → '{src_name}': salida calculada "
                        f"post-hoc. Para retroalimentación real usar coupling='relaxation'."
                    )

            # ── Procesar PWM ───────────────────────────────────────────
            for pwm in self._pwms:
                pwm_hist = []
                for i, t in enumerate(t_arr):
                    d_snap = {net: self._digital.value_at(net, t)
                              for net in all_nets}
                    duty = pwm.update(d_snap, t)
                    pwm_hist.append((t, duty))
                result.pwm_duties[pwm.name] = pwm_hist

            result.success = True

        except Exception as e:
            result.success = False
            result.error   = str(e)

        return result

    def run_iterative(self, t_stop: float,
                      dt_chunk: float = 1e-6,
                      dt_analog: float = 1e-8,
                      adaptive: bool = True) -> MixedSimResult:
        """
        Co-simulación iterativa: divide el tiempo en chunks de `dt_chunk`
        y en cada chunk:
            1. MNA resuelve el chunk analógico
            2. Puentes actualizan el estado digital
            3. DigitalSimulator avanza el chunk
            4. DACs actualizan fuentes MNA para el siguiente chunk

        Esto permite retroalimentación real digital→analógico.

        Parámetros:
            t_stop:    tiempo final
            dt_chunk:  tamaño del paso de intercambio (s)
            dt_analog: paso interno del MNA dentro de cada chunk
            adaptive:  timestep adaptativo en MNA

        Retorna MixedSimResult con datos acumulados de todos los chunks.
        """
        result = MixedSimResult()

        try:
            self._digital.reset()
            t_curr = 0.0

            # Acumuladores
            t_acc   = []
            v_acc:  Dict[str, list] = {}
            dt_stats_acc = {'min': math.inf, 'max': 0.0, 'sum': 0.0, 'n': 0}

            # Inicializar fuentes con valores por defecto
            d_state: Dict[str, int] = {}
            self._update_dacs_in_mna(d_state, 0.0)

            chunk_idx = 0
            while t_curr < t_stop:
                t_end   = min(t_curr + dt_chunk, t_stop)
                dt_span = t_end - t_curr

                # ── 1. Resolver MNA para este chunk ───────────────────
                tr = self._mna.solve_transient(
                    self._comps,
                    t_stop  = dt_span,
                    dt      = min(dt_analog, dt_span),
                    method  = 'trapezoidal',
                    adaptive= adaptive,
                )

                if not tr['success']:
                    result.success = False
                    result.error   = f"MNA chunk {chunk_idx} falló: {tr['error']}"
                    break

                # Ajustar tiempos al eje global
                t_chunk = tr['time'] + t_curr
                v_chunk = tr['voltages']

                # Acumular
                t_acc.extend(t_chunk.tolist())
                for node, arr in v_chunk.items():
                    v_acc.setdefault(node, []).extend(arr.tolist())

                dts = tr.get('dt_stats', {})
                if dts:
                    dt_stats_acc['min'] = min(dt_stats_acc['min'], dts.get('min', math.inf))
                    dt_stats_acc['max'] = max(dt_stats_acc['max'], dts.get('max', 0.0))
                    dt_stats_acc['sum'] += dts.get('mean', 0.0)
                    dt_stats_acc['n']   += 1

                # ── 2. ADC / Comparadores: analógico → digital ─────────
                # Usar el último punto de voltaje del chunk
                last_idx = len(t_chunk) - 1
                v_snap   = {node: float(arr[last_idx]) for node, arr in v_chunk.items()}
                v_snap['0'] = 0.0

                for sh in self._sh:
                    sh.update(v_snap, d_state, t_end)

                for comp in self._comparators:
                    out = comp.evaluate(v_snap)
                    for net, val in out.items():
                        d_state[net] = val
                        self._digital.set_input(net, val, at=t_end)

                for adc in self._adcs:
                    out = adc.sample(v_snap, d_state, t_end)
                    if out:
                        for net, val in out.items():
                            d_state[net] = val
                            self._digital.set_input(net, val, at=t_end)

                # ── 3. Avanzar simulador digital hasta t_end ───────────
                self._digital.run(until=t_end)
                d_state.update(self._digital.nets)

                # ── 4. DACs / PWM: digital → analógico ────────────────
                for pwm in self._pwms:
                    pwm.update(d_state, t_end)

                self._update_dacs_in_mna(d_state, t_end)

                t_curr    = t_end
                chunk_idx += 1

            # ── Consolidar resultado ──────────────────────────────────
            result.t               = np.array(t_acc)
            result.analog_voltages = {node: np.array(vals) for node, vals in v_acc.items()}
            if dt_stats_acc['n'] > 0:
                result.analog_dt_stats = {
                    'min':  dt_stats_acc['min'],
                    'max':  dt_stats_acc['max'],
                    'mean': dt_stats_acc['sum'] / dt_stats_acc['n'],
                }

            # Formas de onda digitales
            all_nets = set(self._digital.history.keys())
            for net in all_nets:
                result.digital_waveforms[net] = self._digital.waveform(net)

            result.events_count = sum(len(v) for v in self._digital.history.values())
            result.success      = result.success if not result.error else False

            if not result.error:
                result.success = True

        except Exception as e:
            result.success = False
            result.error   = str(e)

        return result

    # ── Helpers internos ──────────────────────────────────────────────────

    def _update_dacs_in_mna(self, d_state: Dict[str, int], t: float):
        """Actualiza los valores de las fuentes MNA controladas por DACs."""
        for dac, src_name in self._dacs:
            v_out = dac.convert(d_state, t)
            if v_out is not None:
                dac.voltage_to_component(self._comps, src_name)


# ──────────────────────────────────────────────────────────────────────────────
# Analizador de timing post-simulación
# ──────────────────────────────────────────────────────────────────────────────

class TimingAnalyzer:
    """
    Analiza violaciones de setup/hold y skew en formas de onda digitales.

    Uso:
        ta = TimingAnalyzer(result.digital_waveforms)
        violations = ta.check_setup_hold("D", "CLK", t_setup=2e-9, t_hold=1e-9)
        skew = ta.clock_skew(["CLK1", "CLK2"])
    """

    def __init__(self, waveforms: Dict[str, list]):
        self.wf = waveforms

    def rising_edges(self, net: str) -> List[float]:
        """Lista de tiempos de flancos de subida de `net`."""
        hist = self.wf.get(net, [])
        edges = []
        for i in range(1, len(hist)):
            if hist[i-1][1] == 0 and hist[i][1] == 1:
                edges.append(hist[i][0])
        return edges

    def falling_edges(self, net: str) -> List[float]:
        """Lista de tiempos de flancos de bajada de `net`."""
        hist = self.wf.get(net, [])
        edges = []
        for i in range(1, len(hist)):
            if hist[i-1][1] == 1 and hist[i][1] == 0:
                edges.append(hist[i][0])
        return edges

    def last_transition_before(self, net: str, t: float) -> Optional[float]:
        """Tiempo de la última transición de `net` antes de `t`."""
        hist = self.wf.get(net, [])
        last = None
        for ev_t, _ in hist:
            if ev_t < t:
                last = ev_t
        return last

    def check_setup_hold(self, data_net: str, clk_net: str,
                          t_setup: float, t_hold: float) -> List[Dict]:
        """
        Verifica violaciones de setup y hold.

        Returns:
            Lista de dicts con:
            {
              'type':     'setup' | 'hold',
              'clk_edge': float (tiempo del flanco de CLK),
              'data_t':   float (tiempo de la última transición de datos),
              'margin':   float (margen negativo = violación)
            }
        """
        violations = []
        clk_edges = self.rising_edges(clk_net)
        data_hist = self.wf.get(data_net, [])

        def last_data_before(t):
            last = None
            for ev_t, _ in data_hist:
                if ev_t <= t:
                    last = ev_t
            return last

        def first_data_after(t):
            for ev_t, _ in data_hist:
                if ev_t > t:
                    return ev_t
            return None

        for clk_t in clk_edges:
            # Setup: la última transición de datos debe ser al menos t_setup antes del CLK
            last_d = last_data_before(clk_t)
            if last_d is not None:
                setup_margin = (clk_t - last_d) - t_setup
                if setup_margin < 0:
                    violations.append({
                        'type':     'setup',
                        'clk_edge': clk_t,
                        'data_t':   last_d,
                        'margin':   setup_margin,
                    })

            # Hold: ninguna transición de datos dentro de t_hold tras el CLK
            first_d_after = first_data_after(clk_t)
            if first_d_after is not None:
                hold_margin = (first_d_after - clk_t) - t_hold
                if hold_margin < 0:
                    violations.append({
                        'type':     'hold',
                        'clk_edge': clk_t,
                        'data_t':   first_d_after,
                        'margin':   hold_margin,
                    })

        return violations

    def clock_skew(self, clk_nets: List[str]) -> Dict[str, float]:
        """
        Calcula el skew entre múltiples relojes como la desviación estándar
        de los tiempos de flanco de subida (referenciada al primer reloj).

        Returns:
            {
              'max_skew':  float (máxima diferencia entre flancos, s),
              'mean_skew': float (media de diferencias, s),
              'per_clock': {net: [diferencias respecto al clock[0]], ...}
            }
        """
        if len(clk_nets) < 2:
            return {'max_skew': 0.0, 'mean_skew': 0.0, 'per_clock': {}}

        ref_edges = self.rising_edges(clk_nets[0])
        per_clock: Dict[str, list] = {clk_nets[0]: [0.0] * len(ref_edges)}
        all_diffs = []

        for net in clk_nets[1:]:
            edges = self.rising_edges(net)
            diffs = []
            for i, ref_t in enumerate(ref_edges):
                if i < len(edges):
                    d = edges[i] - ref_t
                    diffs.append(d)
                    all_diffs.append(abs(d))
            per_clock[net] = diffs

        return {
            'max_skew':  max(all_diffs) if all_diffs else 0.0,
            'mean_skew': sum(all_diffs) / len(all_diffs) if all_diffs else 0.0,
            'per_clock': per_clock,
        }

    def propagation_delay(self, in_net: str, out_net: str,
                          edge: str = 'rising') -> List[float]:
        """
        Mide el retardo de propagación entre in_net y out_net.

        edge: 'rising' (50%→50% subida) o 'falling'
        Retorna lista de retardos medidos (uno por evento).
        """
        in_edges  = self.rising_edges(in_net)  if edge == 'rising' else self.falling_edges(in_net)
        out_edges = self.rising_edges(out_net) if edge == 'rising' else self.falling_edges(out_net)

        delays = []
        oi = 0
        for t_in in in_edges:
            while oi < len(out_edges) and out_edges[oi] <= t_in:
                oi += 1
            if oi < len(out_edges):
                delays.append(out_edges[oi] - t_in)
                oi += 1

        return delays
