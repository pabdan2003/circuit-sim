"""
Modelos de componentes para el motor MNA.

Cada componente sabe "estampar" (stamp) su contribución sobre la matriz
del sistema y el vector de excitación. El solver no necesita conocer la
física interna del componente; basta con que cada uno implemente las
ecuaciones de su modelo en formato matricial.

Convenciones globales del motor
-------------------------------
Nodos
    Los nodos son strings arbitrarios. El nodo "0" (cero) es siempre
    masa (GND, 0 V de referencia). Cualquier otro literal sirve como
    nombre de nodo ("in", "out", "n1", "A", "Vcc", etc.).

Unidades
    Todas las magnitudes están en SI sin prefijos: V, A, Ω, F, H, s, Hz.
    El parseo de "1k", "10u", "2.2n" lo hace la UI antes de instanciar.

Convención de signos
    · VoltageSource(n_pos, n_neg, V) y VoltageSourceAC: V positivo
      significa V(n_pos) − V(n_neg) = +V.
    · CurrentSource(n_pos, n_neg, I): convención SPICE — I positivo
      bombea corriente internamente desde n_pos hacia n_neg, así que
      EXTERNAMENTE la corriente sale por n_neg y entra por n_pos. Ver
      docstring de la clase para el ejemplo numérico.
    · Diodo / LED: ánodo a n_a, cátodo a n_k. Conducción cuando
      V(n_a) − V(n_k) > Vf.
    · BJT y MOSFET: la clase normaliza internamente para que las
      ecuaciones de NMOS/NPN sigan siendo válidas en PMOS/PNP por
      multiplicación de signo. Los `operating_point` devuelven los
      voltajes con el signo "físico" del usuario, no el interno.

Métodos que cada componente puede implementar
---------------------------------------------
    stamp(G, I, node_map, branch_idx)
        Caso DC y caso por defecto del transitorio. Obligatorio.

    stamp_ac(G, I, node_map, omega, branch_idx)
        Estampado para análisis en frecuencia. Solo lo implementan los
        componentes reactivos (C, L, Impedance) o los que tienen
        comportamiento distinto en AC (XFMR).

    stamp_transient(G, I, node_map, t, branch_idx)
        Para fuentes que dependen del tiempo (VoltageSourceAC con
        forma de onda) o componentes con comportamiento dinámico que
        difieren del DC (XFMR, que en DC desacopla devanados).
        Si no se define, se usa stamp() — válido para R, V, I, etc.

    stamp_linear(G, I, node_map, V)
        Componentes no-lineales (Diode, BJT, MOSFET). El solver llama
        a este método dentro de cada iteración Newton-Raphson pasando
        el vector de estado actual V; el método linealiza el modelo
        alrededor de ese punto (equivalente de Norton) y lo estampa.

    needs_branch() -> bool
        True si el componente requiere una variable extra de rama
        (corriente). Aplica a fuentes de voltaje, inductores en DC,
        op-amps (VCVS interna) y transformadores.

    operating_point -> dict
        Propiedad opcional con valores físicamente relevantes tras un
        solve_dc (voltajes de juntura, regiones de operación, etc.).
"""
from dataclasses import dataclass, field
from typing import Optional
import numpy as np


# ──────────────────────────────────────────────
# Clase base
# ──────────────────────────────────────────────
class Component:
    """Interfaz base para todos los componentes."""
    _id_counter = 0

    def __init__(self, name: str):
        self.name = name
        Component._id_counter += 1
        self.id = Component._id_counter

    def stamp(self, G: np.ndarray, I: np.ndarray, node_map: dict, branch_idx: Optional[int] = None):
        """Estampa la contribución del componente en G e I."""
        raise NotImplementedError

    def stamp_transient(self, G: np.ndarray, I: np.ndarray, node_map: dict,
                        t: float, branch_idx: Optional[int] = None):
        """Estampa la contribución en un instante `t` del análisis transitorio.

        Por defecto delega a stamp() — los componentes constantes en el tiempo
        (R, V, I, etc.) no necesitan sobreescribirlo. Solo los que varían con
        el tiempo (VoltageSourceAC, fuentes pulsadas, etc.) lo redefinen.
        """
        self.stamp(G, I, node_map, branch_idx=branch_idx)

    def needs_branch(self) -> bool:
        """True si el componente requiere variable de rama (ej: fuente de voltaje)."""
        return False


# ──────────────────────────────────────────────
# Resistencia
# ──────────────────────────────────────────────
class Resistor(Component):
    """
    R entre nodos n1 y n2.
    Estampa conductancia G = 1/R en la submatriz nodal.
    """
    def __init__(self, name: str, n1: str, n2: str, resistance: float):
        super().__init__(name)
        self.n1 = n1
        self.n2 = n2
        self.R = resistance

    def stamp(self, G, I, node_map, branch_idx=None):
        g = 1.0 / self.R
        n1 = node_map.get(self.n1)
        n2 = node_map.get(self.n2)

        if n1 is not None:
            G[n1, n1] += g
        if n2 is not None:
            G[n2, n2] += g
        if n1 is not None and n2 is not None:
            G[n1, n2] -= g
            G[n2, n1] -= g


# ──────────────────────────────────────────────
# Fuente de voltaje independiente
# ──────────────────────────────────────────────
class VoltageSource(Component):
    """
    Fuente de voltaje ideal entre n_pos y n_neg.
    Requiere variable de rama (corriente de la fuente).
    Estampa en las filas/columnas extra de la matriz MNA.
    """
    def __init__(self, name: str, n_pos: str, n_neg: str, voltage: float):
        super().__init__(name)
        self.n_pos = n_pos
        self.n_neg = n_neg
        self.V = voltage

    def needs_branch(self) -> bool:
        return True

    def stamp(self, G, I, node_map, branch_idx=None):
        np_ = node_map.get(self.n_pos)
        nn  = node_map.get(self.n_neg)
        k   = branch_idx  # fila/columna de la variable de rama

        if k is None:
            raise ValueError(f"VoltageSource {self.name}: branch_idx requerido")

        # Columna k: ±1 en filas de nodos
        if np_ is not None:
            G[np_, k] += 1.0
            G[k, np_] += 1.0
        if nn is not None:
            G[nn, k] -= 1.0
            G[k, nn] -= 1.0

        # RHS: V estampado en la fila de la rama
        I[k] += self.V


# ──────────────────────────────────────────────
# Fuente de corriente independiente
# ──────────────────────────────────────────────
class CurrentSource(Component):
    """
    Fuente de corriente independiente — convención SPICE.

    Para CurrentSource(n_pos, n_neg, I) con I positivo:
        · La fuente bombea I amperios INTERNAMENTE desde n_pos hacia n_neg.
        · Externamente (a través del circuito) la corriente vuelve de n_neg
          a n_pos. Es decir: en el lado externo, la corriente SALE por n_neg
          y ENTRA por n_pos.

    Consecuencia práctica en KCL:
        · En n_pos la fuente CONSUME I amperios   → I[n_pos] -= I_val
        · En n_neg la fuente INYECTA I amperios   → I[n_neg] += I_val

    Esto importa al interpretar resultados. Ejemplo: una fuente Is=2A entre
    "A" y "0" con dos resistores de 4Ω en paralelo entre A y "0" (Req=2Ω)
    da V_A = -4V, no +4V. La corriente atraviesa los resistores de "0" a
    "A" en sentido externo, y por la ley de Ohm V_A = V_0 - I·Req = -4V.
    Para obtener V_A = +4V hay que invertir los nodos: CurrentSource("Is", "0", "A", 2.0).

    Es la misma convención que usa SPICE para la directiva "Iname n+ n-".
    """
    def __init__(self, name: str, n_pos: str, n_neg: str, current: float):
        super().__init__(name)
        self.n_pos = n_pos
        self.n_neg = n_neg
        self.I_val = current

    def stamp(self, G, I, node_map, branch_idx=None):
        np_ = node_map.get(self.n_pos)
        nn  = node_map.get(self.n_neg)

        # KCL en cada nodo: la fuente consume I en n_pos y la inyecta en n_neg.
        # Mover los signos al lado derecho de Gv = I → I[n_pos] -= I_val, I[n_neg] += I_val.
        if np_ is not None:
            I[np_] -= self.I_val
        if nn is not None:
            I[nn] += self.I_val


# ──────────────────────────────────────────────
# Capacitor (para AC y transitorio)
# ──────────────────────────────────────────────
class Capacitor(Component):
    """
    C entre n1 y n2.
    En AC: admitancia Y = jωC
    En transitorio: se maneja con integración numérica (ver solver)
    """
    def __init__(self, name: str, n1: str, n2: str, capacitance: float):
        super().__init__(name)
        self.n1 = n1
        self.n2 = n2
        self.C = capacitance

    def stamp_ac(self, G, I, node_map, omega: float, branch_idx=None):
        """Estampa admitancia compleja jωC para análisis AC."""
        y = 1j * omega * self.C
        n1 = node_map.get(self.n1)
        n2 = node_map.get(self.n2)

        if n1 is not None:
            G[n1, n1] += y
        if n2 is not None:
            G[n2, n2] += y
        if n1 is not None and n2 is not None:
            G[n1, n2] -= y
            G[n2, n1] -= y

    def stamp(self, G, I, node_map, branch_idx=None):
        # En DC el capacitor es circuito abierto → no estampa nada
        pass


# ──────────────────────────────────────────────
# Inductor (para AC y transitorio)
# ──────────────────────────────────────────────
class Inductor(Component):
    """
    L entre n1 y n2.
    En AC: impedancia Z = jωL → admitancia Y = 1/(jωL)
    En DC: cortocircuito (fuente de voltaje V=0)
    """
    def __init__(self, name: str, n1: str, n2: str, inductance: float):
        super().__init__(name)
        self.n1 = n1
        self.n2 = n2
        self.L = inductance

    def needs_branch(self) -> bool:
        # En DC actúa como cortocircuito → necesita variable de rama
        return True

    def stamp_ac(self, G, I, node_map, omega: float, branch_idx=None):
        """Estampa admitancia 1/(jωL) para análisis AC."""
        if omega == 0:
            return  # DC: manejado como cortocircuito
        y = 1.0 / (1j * omega * self.L)
        n1 = node_map.get(self.n1)
        n2 = node_map.get(self.n2)

        if n1 is not None:
            G[n1, n1] += y
        if n2 is not None:
            G[n2, n2] += y
        if n1 is not None and n2 is not None:
            G[n1, n2] -= y
            G[n2, n1] -= y

    def stamp(self, G, I, node_map, branch_idx=None):
        # En DC: cortocircuito = fuente de voltaje 0V
        np_ = node_map.get(self.n1)
        nn  = node_map.get(self.n2)
        k   = branch_idx

        if k is None:
            return

        if np_ is not None:
            G[np_, k] += 1.0
            G[k, np_] += 1.0
        if nn is not None:
            G[nn, k] -= 1.0
            G[k, nn] -= 1.0
        # V = 0 → I[k] += 0 (no hace falta)


# ──────────────────────────────────────────────
# Impedancia genérica (para AC y DC)
# ──────────────────────────────────────────────
class Impedance(Component):
    """
    Impedancia genérica Z = R + jX entre n1 y n2.
    En AC: estampa admitancia Y = 1/Z (compleja).
    En DC: estampa solo la parte real como conductancia.
    """
    def __init__(self, name: str, n1: str, n2: str, Z: complex):
        super().__init__(name)
        self.n1 = n1
        self.n2 = n2
        self.Z = Z

    def stamp(self, G, I, node_map, branch_idx=None):
        """En DC solo la parte real importa."""
        R = self.Z.real
        if abs(R) < 1e-12:
            return  # circuito abierto en DC
        g = 1.0 / R
        n1 = node_map.get(self.n1)
        n2 = node_map.get(self.n2)
        if n1 is not None:
            G[n1, n1] += g
        if n2 is not None:
            G[n2, n2] += g
        if n1 is not None and n2 is not None:
            G[n1, n2] -= g
            G[n2, n1] -= g

    def stamp_ac(self, G, I, node_map, omega: float, branch_idx=None):
        """En AC estampa la admitancia compleja Y = 1/Z."""
        if abs(self.Z) < 1e-12:
            return
        Y = 1.0 / self.Z
        n1 = node_map.get(self.n1)
        n2 = node_map.get(self.n2)
        if n1 is not None:
            G[n1, n1] += Y
        if n2 is not None:
            G[n2, n2] += Y
        if n1 is not None and n2 is not None:
            G[n1, n2] -= Y
            G[n2, n1] -= Y


# ──────────────────────────────────────────────
# Fuente de voltaje AC independiente

class VoltageSourceAC(Component):
    """
    Fuente de voltaje periódica.

    Por defecto es senoidal: v(t) = offset + Vpeak * sin(2π·f·t + φ).
    Si waveform='square' o 'triangle' la forma de onda cambia
    (esto se usa para modelar un generador de funciones tipo Multisim).

    Parámetros:
        n_pos, n_neg : nodos
        amplitude    : valor ingresado por el usuario (peak o RMS según `mode`)
        frequency    : frecuencia en Hz
        phase_deg    : fase en grados (default 0)
        mode         : 'peak' o 'rms'. Si 'rms', amplitude es Vrms → Vpeak = Vrms·√2
        waveform     : 'sin' | 'square' | 'triangle'  (default 'sin')
        offset       : tensión DC sumada a la onda (V), default 0.0
        duty         : ciclo de trabajo en [0,1] (solo aplica a 'square'), default 0.5
    """
    _ALLOWED_WAVEFORMS = ('sin', 'square', 'triangle')

    def __init__(self, name: str, n_pos: str, n_neg: str,
                 amplitude: float, frequency: float,
                 phase_deg: float = 0.0, mode: str = 'rms',
                 waveform: str = 'sin', offset: float = 0.0,
                 duty: float = 0.5):
        super().__init__(name)
        self.n_pos      = n_pos
        self.n_neg      = n_neg
        self.amplitude  = amplitude
        self.frequency  = frequency
        self.phase_deg  = phase_deg
        self.mode       = mode   # 'rms' o 'peak'
        if waveform not in self._ALLOWED_WAVEFORMS:
            waveform = 'sin'
        self.waveform   = waveform
        self.offset     = float(offset)
        self.duty       = max(0.0, min(1.0, float(duty)))

    @property
    def Vpeak(self) -> float:
        """Amplitud de pico."""
        if self.mode == 'rms':
            return self.amplitude * (2 ** 0.5)
        return self.amplitude

    @property
    def Vrms(self) -> float:
        """Valor RMS."""
        if self.mode == 'rms':
            return self.amplitude
        return self.amplitude / (2 ** 0.5)

    @property
    def phasor(self) -> complex:
        """Fasor en notación compleja: Vrms * e^(jφ)."""
        import cmath
        return self.Vrms * cmath.exp(1j * self.phase_deg * cmath.pi / 180)

    def needs_branch(self) -> bool:
        return True

    def _dc_average(self) -> float:
        """Valor medio temporal de la onda (lo que ve un análisis DC)."""
        if self.waveform == 'square':
            # v promedio = offset + Vpeak·(2·duty − 1)
            return self.offset + self.Vpeak * (2.0 * self.duty - 1.0)
        # sin y triangle son simétricas → promedio = offset
        return self.offset

    def waveform_at(self, t: float) -> float:
        """Valor instantáneo v(t) según la forma de onda configurada."""
        import math
        # Fase normalizada θ en [0, 2π)
        theta = 2.0 * math.pi * self.frequency * t + math.radians(self.phase_deg)
        if self.waveform == 'square':
            # Posición dentro del período en [0,1)
            frac = (theta / (2.0 * math.pi)) % 1.0
            v_norm = 1.0 if frac < self.duty else -1.0
            return self.offset + self.Vpeak * v_norm
        if self.waveform == 'triangle':
            # Triangular simétrica: |2·frac − 1| va de 0 a 1; escalado a [-1, +1]
            frac = (theta / (2.0 * math.pi)) % 1.0
            v_norm = 1.0 - 4.0 * abs(frac - 0.5)   # +1 en frac=0.5, −1 en frac=0,1
            return self.offset + self.Vpeak * v_norm
        # sin (default)
        return self.offset + self.Vpeak * math.sin(theta)

    def stamp(self, G, I, node_map, branch_idx=None):
        """En DC la fuente vale su valor medio temporal."""
        np_ = node_map.get(self.n_pos)
        nn  = node_map.get(self.n_neg)
        k   = branch_idx
        if k is None:
            raise ValueError(f"VoltageSourceAC {self.name}: branch_idx requerido")
        if np_ is not None:
            G[np_, k] += 1.0
            G[k, np_] += 1.0
        if nn is not None:
            G[nn, k] -= 1.0
            G[k, nn] -= 1.0
        I[k] += self._dc_average()

    def stamp_ac(self, G, I, node_map, omega: float, branch_idx=None):
        """En AC estampa el fasor de la fuente."""
        np_ = node_map.get(self.n_pos)
        nn  = node_map.get(self.n_neg)
        k   = branch_idx
        if k is None:
            raise ValueError(f"VoltageSourceAC {self.name}: branch_idx requerido")
        if np_ is not None:
            G[np_, k] += 1.0
            G[k, np_] += 1.0
        if nn is not None:
            G[nn, k] -= 1.0
            G[k, nn] -= 1.0
        I[k] += self.phasor

    def stamp_transient(self, G, I, node_map, t: float, branch_idx=None):
        """En transitorio estampa v(t) según la forma de onda configurada."""
        np_ = node_map.get(self.n_pos)
        nn  = node_map.get(self.n_neg)
        k   = branch_idx
        if k is None:
            raise ValueError(f"VoltageSourceAC {self.name}: branch_idx requerido")
        if np_ is not None:
            G[np_, k] += 1.0
            G[k, np_] += 1.0
        if nn is not None:
            G[nn, k] -= 1.0
            G[k, nn] -= 1.0
        I[k] += self.waveform_at(t)


class Diode(Component):
    """
    Diodo con modelo de Shockley: I = Is·(exp(Vd / (n·Vt)) − 1)

    Parámetros:
        n_a:    nodo ánodo
        n_k:    nodo cátodo
        Is:     corriente de saturación (A)
                  · Diodo Si genérico  → 1e-14
                  · LED rojo ~1.8 V   → ~1e-18
                  · LED azul/blanco ~3 V → ~1e-26
        n:      factor de idealidad
                  · Diodo Si  ≈ 1.0
                  · LED       ≈ 2.0–3.0
        Vt:     voltaje térmico (default 25.85 mV a 300 K)
        Vd_init: estimado inicial del Vd en el punto de operación.
                 Importa para que Newton-Raphson arranque cerca del Vf real
                 (e.g. ~0.6 V para Si, ~1.8 V para LED rojo, ~3 V para LED blanco).
        Vd_max:  cota superior usada al evaluar la exponencial — protege
                 contra overflow numérico cuando NR lanza un Vd disparatado
                 antes de converger.  Para Si basta 1 V; los LEDs llegan a
                 ~3.5 V por lo que se sube a 5 V por defecto.
    """
    def __init__(self, name: str, n_a: str, n_k: str,
                 Is: float = 1e-14, n: float = 1.0, Vt: float = 0.02585,
                 Vd_init: float = 0.6, Vd_max: float = 5.0):
        super().__init__(name)
        self.n_a    = n_a
        self.n_k    = n_k
        self.Is     = Is
        self.n      = n
        self.Vt     = Vt
        self.Vd_max = Vd_max
        self._Vd    = Vd_init

    def _clamp_vd(self, Vd: float) -> float:
        """Limita Vd para evitar overflow en exp()."""
        if Vd > self.Vd_max:
            return self.Vd_max
        # En reverso muy negativo, exp() → 0; lo limitamos para evitar nan.
        if Vd < -50.0:
            return -50.0
        return Vd

    def _id(self, Vd: float) -> float:
        """Corriente del diodo."""
        Vd = self._clamp_vd(Vd)
        return self.Is * (np.exp(Vd / (self.n * self.Vt)) - 1.0)

    def _gd(self, Vd: float) -> float:
        """Conductancia dinámica dId/dVd (para linealización)."""
        Vd = self._clamp_vd(Vd)
        # Mínima conductancia para que la matriz no sea singular en reverso
        return max(self.Is * np.exp(Vd / (self.n * self.Vt)) / (self.n * self.Vt),
                   1e-12)

    # ── SPICE-style diode limiting ────────────────────────────────────────
    def _vd_limit(self, Vd_new: float, Vd_old: float) -> float:
        """
        Limita el cambio de Vd entre iteraciones de Newton-Raphson.
        Evita que el solver "salte" al rincón saturado de la exponencial
        cuando arranca lejos del punto de operación real (típico con
        fuentes de corriente que fuerzan I a través del diodo).

        Implementación clásica (Vlach & Singhal §7.3.2 / SPICE):
          Si Vd_new > V_crit  Y  |Vd_new − Vd_old| > 2·n·Vt:
              Vd_new ← Vd_old + n·Vt · log(1 + (Vd_new − Vd_old)/(n·Vt))
          (cuando la nueva tiene sentido; si no, se ancla a V_crit).
        """
        nVt   = self.n * self.Vt
        V_crit = nVt * np.log(nVt / (np.sqrt(2) * max(self.Is, 1e-30)))
        if Vd_new > V_crit and abs(Vd_new - Vd_old) > 2 * nVt:
            if Vd_old > 0:
                arg = 1.0 + (Vd_new - Vd_old) / nVt
                if arg > 0:
                    return Vd_old + nVt * np.log(arg)
                return V_crit
            return nVt * np.log(max(Vd_new / nVt, 1.0))
        return Vd_new

    def stamp_linear(self, G: np.ndarray, I: np.ndarray, node_map: dict, V: np.ndarray):
        """
        Estampa modelo linealizado en el punto de operación actual.
        Equivalente Norton: conductancia gd en paralelo con fuente Ieq.
        """
        na = node_map.get(self.n_a)
        nk = node_map.get(self.n_k)

        Va = V[na] if na is not None else 0.0
        Vk = V[nk] if nk is not None else 0.0
        Vd_new   = Va - Vk
        # Diode limiting con respecto al Vd guardado de la iteración anterior
        self._Vd = self._vd_limit(Vd_new, self._Vd)

        gd  = self._gd(self._Vd)
        Id  = self._id(self._Vd)
        Ieq = Id - gd * self._Vd  # corriente de Norton

        if na is not None:
            G[na, na] += gd
            I[na]     -= Ieq
        if nk is not None:
            G[nk, nk] += gd
            I[nk]     += Ieq
        if na is not None and nk is not None:
            G[na, nk] -= gd
            G[nk, na] -= gd

    def stamp(self, G, I, node_map, branch_idx=None):
        # En la primera iteración usamos el estimado inicial
        na = node_map.get(self.n_a)
        nk = node_map.get(self.n_k)
        V_dummy = np.zeros(max((na or 0), (nk or 0)) + 1)
        if na is not None:
            V_dummy[na] = self._Vd
        self.stamp_linear(G, I, node_map, V_dummy)

    @property
    def operating_point(self):
        return {'Vd': self._Vd, 'Id': self._id(self._Vd)}


# ──────────────────────────────────────────────
# Transistor BJT (NPN y PNP) — modelo Ebers-Moll simplificado
# ──────────────────────────────────────────────
class BJT(Component):
    """
    Transistor BJT con modelo Ebers-Moll de transporte (nivel 1).

    Terminales: n_c (colector), n_b (base), n_e (emisor)
    type_: 'NPN' o 'PNP'

    Parámetros:
        Is:   corriente de saturación (default 1e-15 A)
        Bf:   ganancia de corriente directa hFE (default 100)
        Br:   ganancia de corriente inversa (default 1)
        Vt:   voltaje térmico (default 25.85 mV)
    """
    def __init__(self, name: str, n_c: str, n_b: str, n_e: str,
                 type_: str = 'NPN',
                 Is: float = 1e-15, Bf: float = 100.0,
                 Br: float = 1.0,   Vt: float = 0.02585):
        super().__init__(name)
        self.n_c   = n_c
        self.n_b   = n_b
        self.n_e   = n_e
        self.type_ = type_.upper()
        self.Is    = Is
        self.Bf    = Bf
        self.Br    = Br
        self.Vt    = Vt
        self.af    = Bf / (Bf + 1.0)   # ganancia de corriente en base común (directa)
        self.ar    = Br / (Br + 1.0)   # ganancia de corriente en base común (inversa)
        # Estimados iniciales para NPN activo directo
        self._Vbe = 0.65
        self._Vbc = -5.0

    def _sign(self):
        """+1 para NPN, -1 para PNP (invierte polaridades)."""
        return 1.0 if self.type_ == 'NPN' else -1.0

    def _clamp(self, V: float) -> float:
        return min(V, 1.0)

    def _If(self, Vbe: float) -> float:
        """Corriente de difusión directa."""
        return (self.Is / self.af) * (np.exp(self._clamp(Vbe) / self.Vt) - 1.0)

    def _Ir(self, Vbc: float) -> float:
        """Corriente de difusión inversa."""
        return (self.Is / self.ar) * (np.exp(self._clamp(Vbc) / self.Vt) - 1.0)

    def _gf(self, Vbe: float) -> float:
        return (self.Is / self.af) * np.exp(self._clamp(Vbe) / self.Vt) / self.Vt

    def _gr(self, Vbc: float) -> float:
        return (self.Is / self.ar) * np.exp(self._clamp(Vbc) / self.Vt) / self.Vt

    def stamp_linear(self, G: np.ndarray, I: np.ndarray, node_map: dict, V: np.ndarray):
        """
        Modelo simplificado estándar SPICE (Gummel-Poon nivel 1).

        El BJT se descompone en tres contribuciones independientes:

        1. Diodo BE:  corriente Ibe = Is/Bf * (exp(Vbe/Vt) - 1)
                      entre nodos B y E — controla la base
        2. Diodo BC:  corriente Ibc = Is/Br * (exp(Vbc/Vt) - 1)
                      entre nodos B y C — activo en saturación
        3. Fuente de corriente controlada:
                      Ic_ctrl = Is * (exp(Vbe/Vt) - exp(Vbc/Vt))
                      de C a E — la corriente principal del transistor

        Cada término se linealiza por separado → Norton equivalente.
        NPN:  Vbe = Vb-Ve,  Vbc = Vb-Vc
        PNP:  Vbe = Ve-Vb,  Vbc = Vc-Vb  (se invierte el signo)
        """
        s  = self._sign()   # +1 NPN, -1 PNP
        nc = node_map.get(self.n_c)
        nb = node_map.get(self.n_b)
        ne = node_map.get(self.n_e)

        Vc = float(V[nc]) if nc is not None else 0.0
        Vb = float(V[nb]) if nb is not None else 0.0
        Ve = float(V[ne]) if ne is not None else 0.0

        # Voltajes de juntura (siempre positivos en conducción directa para NPN)
        self._Vbe = s * (Vb - Ve)
        self._Vbc = s * (Vb - Vc)

        # ── Clamp para evitar overflow en exp() ──────────────────────────
        Vbe_c = min(self._Vbe, 0.85)
        Vbc_c = min(self._Vbc, 0.85)

        # ── Parámetros de los diodos internos ────────────────────────────
        Is_be = self.Is / self.Bf   # corriente saturación diodo BE
        Is_bc = self.Is / self.Br   # corriente saturación diodo BC

        # ── 1. Diodo BE (entre B y E) ────────────────────────────────────
        Ibe  = Is_be * (np.exp(Vbe_c / self.Vt) - 1.0)
        gbe  = Is_be * np.exp(Vbe_c / self.Vt) / self.Vt   # conductancia dinámica
        Ieq_be = Ibe - gbe * self._Vbe   # corriente Norton

        # Estampar gbe entre b y e
        if nb is not None: G[nb, nb] += gbe
        if ne is not None: G[ne, ne] += gbe
        if nb is not None and ne is not None:
            G[nb, ne] -= gbe
            G[ne, nb] -= gbe
        # Fuente Norton del diodo BE
        if nb is not None: I[nb] -= s * Ieq_be
        if ne is not None: I[ne] += s * Ieq_be

        # ── 2. Diodo BC (entre B y C) ────────────────────────────────────
        Ibc  = Is_bc * (np.exp(Vbc_c / self.Vt) - 1.0)
        gbc  = Is_bc * np.exp(Vbc_c / self.Vt) / self.Vt
        Ieq_bc = Ibc - gbc * self._Vbc

        # Estampar gbc entre b y c
        if nb is not None: G[nb, nb] += gbc
        if nc is not None: G[nc, nc] += gbc
        if nb is not None and nc is not None:
            G[nb, nc] -= gbc
            G[nc, nb] -= gbc
        # Fuente Norton del diodo BC
        if nb is not None: I[nb] -= s * Ieq_bc
        if nc is not None: I[nc] += s * Ieq_bc

        # ── 3. Fuente de corriente controlada C→E ────────────────────────
        # Ic_ctrl = Is*(exp(Vbe/Vt) - exp(Vbc/Vt))
        #
        # Linealización alrededor del punto (Vbe0, Vbc0):
        #   Ic ≈ Ic0 + gm_be*(Vbe-Vbe0) - gm_bc*(Vbc-Vbc0)
        # donde Vbe = Vb-Ve,  Vbc = Vb-Vc
        #
        # Estampado MNA (corriente de C a E):
        #   Contribución de gm_be*(Vb-Ve):
        #     +gm_be en G[c,b],  -gm_be en G[c,e]
        #     -gm_be en G[e,b],  +gm_be en G[e,e]   (KCL en e)
        #   Contribución de -gm_bc*(Vb-Vc):
        #     -gm_bc en G[c,b],  +gm_bc en G[c,c]
        #     +gm_bc en G[e,b],  -gm_bc en G[e,c]   (KCL en e)
        #
        # Para PNP se invierte el sentido (s=-1): la corriente va de E a C
        gm_be = self.Is * np.exp(Vbe_c / self.Vt) / self.Vt
        gm_bc = self.Is * np.exp(Vbc_c / self.Vt) / self.Vt

        Ic_ctrl = self.Is * (np.exp(Vbe_c / self.Vt) - np.exp(Vbc_c / self.Vt))
        Ieq_ctrl = Ic_ctrl - gm_be * self._Vbe + gm_bc * self._Vbc

        # Contribución gm_be*(Vb-Ve) en fila C
        if nc is not None:
            if nb is not None: G[nc, nb] += s * gm_be
            if ne is not None: G[nc, ne] -= s * gm_be
            else:              G[nc, nc] += 0   # Ve=GND: el -gm_be*Ve ya es 0, no hace falta
        # Contrapartida KCL en fila E
        if ne is not None:
            if nb is not None: G[ne, nb] -= s * gm_be
            if ne is not None: G[ne, ne] += s * gm_be

        # Contribución -gm_bc*(Vb-Vc) en fila C
        if nc is not None:
            if nb is not None: G[nc, nb] -= s * gm_bc
            G[nc, nc]          += s * gm_bc
        # Contrapartida KCL en fila E
        if ne is not None:
            if nb is not None: G[ne, nb] += s * gm_bc
            if nc is not None: G[ne, nc] -= s * gm_bc

        # Fuente Norton de la corriente controlada
        # Convencion MNA: I[n] += corriente que ENTRA al nodo desde la fuente
        # La VCCS lleva corriente de E→C (sentido convencional NPN),
        # por tanto SALE del nodo C y ENTRA al nodo E
        if nc is not None: I[nc] -= s * Ieq_ctrl
        if ne is not None: I[ne] += s * Ieq_ctrl

    def stamp(self, G, I, node_map, branch_idx=None):
        size = G.shape[0]
        V_dummy = np.zeros(size)
        nc = node_map.get(self.n_c)
        nb = node_map.get(self.n_b)
        ne = node_map.get(self.n_e)
        s  = self._sign()
        if nb is not None: V_dummy[nb] = s * 0.65
        if nc is not None: V_dummy[nc] = s * 5.0
        self.stamp_linear(G, I, node_map, V_dummy)

    @property
    def operating_point(self):
        s    = self._sign()
        Vbe_c = min(self._Vbe, 0.85)
        Vbc_c = min(self._Vbc, 0.85)
        Ibe   = (self.Is / self.Bf) * (np.exp(Vbe_c / self.Vt) - 1.0)
        Ibc   = (self.Is / self.Br) * (np.exp(Vbc_c / self.Vt) - 1.0)
        Ic_ctrl = self.Is * (np.exp(Vbe_c / self.Vt) - np.exp(Vbc_c / self.Vt))
        Ic = s * (Ic_ctrl - Ibc)
        Ib = s * (Ibe + Ibc)
        Ie = -(Ic + Ib)
        Vce = s * (self._Vbe - self._Vbc)  # Vce = Vbe - Vbc para NPN
        region = ('corte' if self._Vbe < 0.5
                  else 'saturacion' if self._Vbc > 0.1
                  else 'activo')
        return {'Vbe': s * self._Vbe, 'Vce': Vce,
                'Ic':  Ic, 'Ib': Ib, 'Ie': Ie,
                'region': region}


# ──────────────────────────────────────────────
# MOSFET (NMOS y PMOS) — modelo nivel 1 (cuadrático)
# ──────────────────────────────────────────────
class MOSFET(Component):
    """
    MOSFET de enriquecimiento con modelo cuadrático nivel 1.

    Terminales: n_d (drain), n_g (gate), n_s (source)
    type_: 'NMOS' o 'PMOS'

    Parámetros:
        Vth:  voltaje de umbral (default 1.0 V para NMOS, -1.0 para PMOS)
        Kn:   parámetro de transconductancia W/L*μCox/2 (default 1e-3 A/V²)
        lam:  modulación de canal λ (default 0 = sin modulación)
    """
    def __init__(self, name: str, n_d: str, n_g: str, n_s: str,
                 type_: str = 'NMOS',
                 Vth: float = None, Kn: float = 1e-3, lam: float = 0.0):
        super().__init__(name)
        self.n_d   = n_d
        self.n_g   = n_g
        self.n_s   = n_s
        self.type_ = type_.upper()
        self.Kn    = Kn
        self.lam   = lam
        self._sign = 1.0 if self.type_ == 'NMOS' else -1.0
        if Vth is None:
            self.Vth = 1.0 if self.type_ == 'NMOS' else -1.0
        else:
            self.Vth = Vth
        self._Vgs = 0.0
        self._Vds = 0.0

    def _ids_and_gm_gds(self, Vgs: float, Vds: float):
        """
        Retorna (Ids, gm, gds) según región de operación.
        Todo en magnitudes positivas para NMOS; PMOS maneja signo externo.
        """
        Vov = Vgs - self.Vth
        if Vov <= 0:
            # Corte
            return 0.0, 0.0, 0.0
        elif Vds < Vov:
            # Triodo (lineal)
            Ids = self.Kn * ((Vov * Vds) - 0.5 * Vds**2) * (1 + self.lam * Vds)
            gm  = self.Kn * Vds * (1 + self.lam * Vds)
            gds = self.Kn * (Vov - Vds) * (1 + self.lam * Vds) + self.Kn * ((Vov * Vds) - 0.5 * Vds**2) * self.lam
        else:
            # Saturación
            Ids = 0.5 * self.Kn * Vov**2 * (1 + self.lam * Vds)
            gm  = self.Kn * Vov * (1 + self.lam * Vds)
            gds = 0.5 * self.Kn * Vov**2 * self.lam
        return Ids, gm, gds

    def stamp_linear(self, G: np.ndarray, I: np.ndarray, node_map: dict, V: np.ndarray):
        s  = self._sign
        nd = node_map.get(self.n_d)
        ng = node_map.get(self.n_g)
        ns = node_map.get(self.n_s)

        Vd = V[nd] if nd is not None else 0.0
        Vg = V[ng] if ng is not None else 0.0
        Vs = V[ns] if ns is not None else 0.0

        self._Vgs = s * (Vg - Vs)
        self._Vds = s * (Vd - Vs)

        # Para PMOS con Vgs<0 y Vds<0, aseguramos cálculo correcto
        Ids, gm, gds = self._ids_and_gm_gds(self._Vgs, self._Vds)
        Ids *= s
        gm  *= 1.0   # gm es siempre positivo
        gds *= 1.0

        # Corriente de Norton: Ids0 - gm*Vgs0 - gds*Vds0
        Ids_eq = Ids - s * gm * self._Vgs - s * gds * self._Vds

        # gds entre drain y source
        if nd is not None:
            G[nd, nd] += gds
        if ns is not None:
            G[ns, ns] += gds
        if nd is not None and ns is not None:
            G[nd, ns] -= gds
            G[ns, nd] -= gds

        # gm: corriente controlada por Vgs = Vg - Vs
        # I_drain += s*gm*(Vg - Vs)
        if nd is not None:
            if ng is not None:
                G[nd, ng] += s * gm
            if ns is not None:
                G[nd, ns] -= s * gm
        if ns is not None:
            if ng is not None:
                G[ns, ng] -= s * gm
            if ns is not None:
                G[ns, ns] += s * gm  # contrapartida en source

        # Fuente de Norton
        if nd is not None:
            I[nd] -= s * Ids_eq
        if ns is not None:
            I[ns] += s * Ids_eq

    def stamp(self, G, I, node_map, branch_idx=None):
        size = G.shape[0]
        V_dummy = np.zeros(size)
        nd = node_map.get(self.n_d)
        ng = node_map.get(self.n_g)
        ns = node_map.get(self.n_s)
        s  = self._sign
        if ng is not None: V_dummy[ng] = s * 2.5
        if nd is not None: V_dummy[nd] = s * 5.0
        self.stamp_linear(G, I, node_map, V_dummy)

    @property
    def operating_point(self):
        Ids, _, _ = self._ids_and_gm_gds(self._Vgs, self._Vds)
        Vov = self._Vgs - self.Vth
        region = 'corte' if Vov <= 0 else ('triodo' if self._Vds < Vov else 'saturacion')
        return {'Vgs': self._sign * self._Vgs, 'Vds': self._sign * self._Vds,
                'Ids': self._sign * Ids, 'region': region}


# ──────────────────────────────────────────────
# Op-Amp ideal (modelo macromodelo lineal)
# ──────────────────────────────────────────────
class OpAmp(Component):
    """
    Op-Amp ideal con ganancia de lazo abierto finita.

    Terminales: n_out (salida), n_p (entrada +), n_n (entrada -)
    Opcionalmente: n_vp / n_vn para rieles de alimentación (default: sin límite)

    Modelo: Vout = A * (Vp - Vn), implementado como fuente de voltaje
    controlada por voltaje (VCVS) usando variables de rama MNA.

    Parámetros:
        A:      ganancia de lazo abierto (default 1e5)
        Rin:    resistencia de entrada diferencial (default 1e12 Ω → casi ideal)
        Rout:   resistencia de salida (default 75 Ω)
    """
    def __init__(self, name: str, n_out: str, n_p: str, n_n: str,
                 n_ref: str = '0',
                 A: float = 1e5, Rin: float = 1e12, Rout: float = 75.0):
        super().__init__(name)
        self.n_out = n_out
        self.n_p   = n_p
        self.n_n   = n_n
        self.n_ref = n_ref
        self.A     = A
        self.Rin   = Rin
        self.Rout  = Rout

    def needs_branch(self) -> bool:
        # Necesita variable de rama para la VCVS interna
        return True

    def stamp(self, G: np.ndarray, I: np.ndarray, node_map: dict, branch_idx=None):
        """
        Macromodelo:
          1. Resistencia de entrada Rin entre n_p y n_n
          2. Fuente de voltaje controlada: Vout - Vref = A*(Vp - Vn)
             implementada como ecuación de rama extra (branch_idx k):
               G[k, n_out] = 1, G[k, n_ref] = -1
               G[k, n_p]   = -A, G[k, n_n]   = +A
               G[n_out, k] = 1,  G[n_ref, k]  = -1
          3. Resistencia de salida Rout en serie (implícita en el modelo)
        """
        k    = branch_idx
        no   = node_map.get(self.n_out)
        np_  = node_map.get(self.n_p)
        nn   = node_map.get(self.n_n)
        nref = node_map.get(self.n_ref)

        # 1. Resistencia diferencial de entrada entre n_p y n_n
        if self.Rin > 0:
            gin = 1.0 / self.Rin
            if np_ is not None:
                G[np_, np_] += gin
            if nn is not None:
                G[nn, nn]   += gin
            if np_ is not None and nn is not None:
                G[np_, nn]  -= gin
                G[nn, np_]  -= gin

        if k is None:
            return

        # 2. VCVS: Vout = A*(Vp - Vn) → ecuación en fila k:
        #    Vout - Vref - A*Vp + A*Vn = 0
        if no   is not None:
            G[k, no]   += 1.0
            G[no,   k] += 1.0
        if nref is not None:
            G[k, nref] -= 1.0
            G[nref, k] -= 1.0
        if np_  is not None:
            G[k, np_]  -= self.A
        if nn   is not None:
            G[k, nn]   += self.A

        # 3. Resistencia de salida: reducir la ganancia efectiva
        #    Se modela añadiendo Rout entre el nodo interno de la fuente y n_out
        #    Simplificación: se añade como conductancia en paralelo con la rama
        if self.Rout > 0 and no is not None:
            gout = 1.0 / self.Rout
            G[no, no] += gout
            if nref is not None:
                G[no,   nref] -= gout
                G[nref, no]   -= gout
                G[nref, nref] += gout

    @property
    def operating_point(self):
        return {'A': self.A, 'Rin': self.Rin, 'Rout': self.Rout}


# ──────────────────────────────────────────────
# Potenciómetro (resistencia variable)
# ──────────────────────────────────────────────
class Potentiometer(Component):
    """
    Potenciómetro modelado como reóstato (2 terminales) con resistencia
    variable R_eff = R_total * wiper, donde wiper ∈ [0, 1].

    En DC y AC se comporta exactamente como un Resistor con R = R_eff,
    pero permite cambiar su valor "en tiempo real" durante la simulación
    sin re-ensamblar el circuito (basta con actualizar self.wiper y volver
    a resolver).

    Atributos:
        n1, n2:   nodos
        R_total:  resistencia total entre extremos (Ω)
        wiper:    fracción del recorrido [0, 1] — 0 = corto, 1 = R_total
        R_min:    resistencia mínima (Ω) para evitar div/0 cuando wiper→0
    """
    def __init__(self, name: str, n1: str, n2: str,
                 R_total: float = 10_000.0,
                 wiper: float = 0.5,
                 R_min: float = 1e-3):
        super().__init__(name)
        self.n1      = n1
        self.n2      = n2
        self.R_total = float(R_total)
        self.wiper   = max(0.0, min(1.0, float(wiper)))
        self.R_min   = float(R_min)

    @property
    def R(self) -> float:
        """Resistencia efectiva actual."""
        return max(self.R_min, self.R_total * self.wiper)

    def stamp(self, G, I, node_map, branch_idx=None):
        g  = 1.0 / self.R
        n1 = node_map.get(self.n1)
        n2 = node_map.get(self.n2)
        if n1 is not None:
            G[n1, n1] += g
        if n2 is not None:
            G[n2, n2] += g
        if n1 is not None and n2 is not None:
            G[n1, n2] -= g
            G[n2, n1] -= g

    def stamp_ac(self, G, I, node_map, omega: float, branch_idx=None):
        # Resistivo puro → comportamiento idéntico en AC
        self.stamp(G, I, node_map, branch_idx)


# ──────────────────────────────────────────────
# Transformador ideal con resistencia de bobinado en DC
# ──────────────────────────────────────────────
class Transformer(Component):
    """
    Transformador ideal de dos devanados acoplados magnéticamente.

    Terminales:
        n_p1, n_p2   →  primario    (lado de entrada,  N1 vueltas)
        n_s1, n_s2   →  secundario  (lado de salida,   N2 vueltas)

    Parámetros:
        ratio:        n = N1/N2   (turns ratio)
                      → V_p / V_s = n   y   I_s / I_p = -n  (ideal)
        I_max:        corriente máxima primaria nominal (A)  — sólo informativa
        R_winding:    resistencia óhmica de cada devanado (Ω)
                      → en DC el transformador deja pasar la corriente con
                        baja resistencia en cada bobinado, pero NO la acopla
                        entre primario y secundario (un xfmr ideal no transmite DC).

    Modelo MNA:
        - DC:  cada devanado es un Resistor de R_winding entre sus 2 terminales,
               sin acoplamiento entre primario y secundario.  La rama auxiliar
               se "ancla" a 0 con G[k,k]=1 para mantener el sistema bien planteado.
        - AC:  con UNA rama auxiliar I_p (corriente primaria) basta.  La
               relación I_s = -n·I_p se impone estampando ±n en las columnas
               del nodo secundario (column k), de modo que la KCL en los
               nodos del secundario inyecta automáticamente la corriente
               -n·I_p sin necesitar otra incógnita.  La constitutiva
               V_p − n·V_s = 0 va en la fila k.
    """
    def __init__(self, name: str,
                 n_p1: str, n_p2: str,
                 n_s1: str, n_s2: str,
                 ratio: float = 1.0,
                 I_max: float = 1.0,
                 R_winding: float = 1e-3):
        super().__init__(name)
        self.n_p1      = n_p1
        self.n_p2      = n_p2
        self.n_s1      = n_s1
        self.n_s2      = n_s2
        self.ratio     = float(ratio)
        self.I_max     = float(I_max)
        self.R_winding = float(R_winding)

    def needs_branch(self) -> bool:
        # En AC necesita variable de rama (I_p).  En DC también se le asigna
        # una pero queda anclada a 0; ver `stamp` más abajo.
        return True

    # ── DC: cada devanado se comporta como una resistencia baja ──────────
    def stamp(self, G, I, node_map, branch_idx=None):
        g = 1.0 / self.R_winding
        for (na, nb) in ((self.n_p1, self.n_p2), (self.n_s1, self.n_s2)):
            ia = node_map.get(na)
            ib = node_map.get(nb)
            if ia is not None:
                G[ia, ia] += g
            if ib is not None:
                G[ib, ib] += g
            if ia is not None and ib is not None:
                G[ia, ib] -= g
                G[ib, ia] -= g
        # Anclar la rama auxiliar (no se usa en DC)
        if branch_idx is not None:
            G[branch_idx, branch_idx] += 1.0
            # I[branch_idx] ya es 0 → x[branch_idx] = 0

    # ── AC: ecuaciones de transformador ideal ────────────────────────────
    def stamp_ac(self, G, I, node_map, omega: float, branch_idx=None):
        if branch_idx is None:
            return
        n  = self.ratio
        a  = node_map.get(self.n_p1)   # primario +
        b  = node_map.get(self.n_p2)   # primario −
        c  = node_map.get(self.n_s1)   # secundario +
        d  = node_map.get(self.n_s2)   # secundario −
        k  = branch_idx                 # I_p

        # Columna k (KCL):  I_p en primario, -n·I_p en secundario
        if a is not None: G[a, k] += 1.0
        if b is not None: G[b, k] -= 1.0
        if c is not None: G[c, k] -= n
        if d is not None: G[d, k] += n

        # Fila k (constitutiva):  V_a − V_b − n·V_c + n·V_d = 0
        if a is not None: G[k, a] += 1.0
        if b is not None: G[k, b] -= 1.0
        if c is not None: G[k, c] -= n
        if d is not None: G[k, d] += n
        # I[k] = 0 (no excitación)

    # ── Transitorio: mismas ecuaciones del transformador ideal ──────────
    def stamp_transient(self, G, I, node_map, t: float, branch_idx=None):
        """
        En transitorio, el transformador ideal usa las MISMAS ecuaciones
        que en AC (la relación V_p = n·V_s y I_s = -n·I_p es independiente
        de la frecuencia para un transformador ideal acoplado magnéticamente).

        Sin este método se cae al `stamp` DC, que NO acopla primario y
        secundario y deja la salida en 0V — rompiendo cualquier circuito
        rectificador en simulación live.

        Además se añade una pequeña conductancia de fuga a tierra (g_leak)
        en cada terminal de los devanados.  Físicamente representa la
        capacitancia parásita / resistencia de aislamiento al núcleo.
        Numéricamente, evita la singularidad cuando el secundario está
        "flotando" antes de que los diodos del puente conduzcan.
        """
        if branch_idx is None:
            return
        n  = self.ratio
        a  = node_map.get(self.n_p1)
        b  = node_map.get(self.n_p2)
        c  = node_map.get(self.n_s1)
        d  = node_map.get(self.n_s2)
        k  = branch_idx

        if a is not None: G[a, k] += 1.0
        if b is not None: G[b, k] -= 1.0
        if c is not None: G[c, k] -= n
        if d is not None: G[d, k] += n

        if a is not None: G[k, a] += 1.0
        if b is not None: G[k, b] -= 1.0
        if c is not None: G[k, c] -= n
        if d is not None: G[k, d] += n

        g_leak = 1e-9
        for nd in (a, b, c, d):
            if nd is not None:
                G[nd, nd] += g_leak
