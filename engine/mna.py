"""
Motor de simulación: Modified Nodal Analysis (MNA).

Resuelve el sistema:
    G · x = I

donde:
    G  = matriz de conductancias + variables de rama
    x  = [V_nodos | I_ramas]
    I  = vector de excitaciones

Optimizaciones implementadas:
    1. Matrices sparse (scipy.sparse + SuperLU)
    2. Reutilización de factorización LU en barrido AC
    3. Timestep adaptativo en análisis transitorio

Referencia: Vlach & Singhal, "Computer Methods for Circuit Analysis and Design"
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
from scipy.sparse import lil_matrix, csc_matrix
from scipy.sparse.linalg import splu, spsolve

from .components import (
    Component, VoltageSource, VoltageSourceAC, CurrentSource,
    Capacitor, Inductor, Diode, BJT, MOSFET, OpAmp, Impedance,
)
from .components import Resistor as Resistor_cls


# ──────────────────────────────────────────────────────────────────────────────
# Utilidades sparse
# ──────────────────────────────────────────────────────────────────────────────

def _sparse_solve(G_lil, I_vec):
    """
    Convierte LIL → CSC y resuelve con SuperLU (spsolve).
    Retorna el vector solución x.
    """
    G_csc = csc_matrix(G_lil)
    return spsolve(G_csc, I_vec)


def _sparse_factor(G_lil):
    """
    Factoriza G (LIL → CSC → LU).
    Retorna el objeto SuperLU que expone .solve(b).
    """
    G_csc = csc_matrix(G_lil)
    return splu(G_csc)


def _component_fingerprint(c) -> tuple:
    """
    Devuelve una huella estable que identifica unívocamente un componente
    incluyendo SUS PARÁMETROS NUMÉRICOS, no sólo su nombre/tipo.

    Esencial para que la caché de LU del solver AC se invalide cuando el
    usuario cambia un valor (p.ej. el wiper de un potenciómetro o la
    relación de un transformador en tiempo real durante la simulación).
    """
    parts = [c.name, type(c).__name__]
    # Recorrer los atributos numéricos / strings (los nodos cuentan también
    # porque cambiar un nodo cambia la topología).
    for k in sorted(vars(c).keys()):
        if k.startswith('_'):
            continue
        v = getattr(c, k)
        if isinstance(v, bool):
            parts.append((k, bool(v)))
        elif isinstance(v, (int, float)):
            parts.append((k, round(float(v), 12)))
        elif isinstance(v, complex):
            parts.append((k, round(v.real, 12), round(v.imag, 12)))
        elif isinstance(v, str):
            parts.append((k, v))
        # Otros tipos (numpy arrays, callables, etc.) se ignoran
    return tuple(parts)


def _circuit_fingerprint(components) -> tuple:
    """Huella del circuito completo para cachear factorizaciones."""
    return tuple(_component_fingerprint(c) for c in components)


# ──────────────────────────────────────────────────────────────────────────────
# Solver principal
# ──────────────────────────────────────────────────────────────────────────────

class MNASolver:
    """
    Solver MNA para análisis DC, AC y transitorio.

    Interfaz estable: si en el futuro el interior se reescribe en C++
    vía pybind11, el resto del proyecto no cambia nada.

    Optimizaciones internas:
        · Matrices sparse (scipy LIL para construcción, CSC para solve)
        · Factorización LU cacheada en solve_ac (un factor por frecuencia
          cuando la topología no cambia entre llamadas consecutivas)
        · Timestep adaptativo en solve_transient con control de error LTE
    """

    def __init__(self):
        self._last_result: Optional[Dict] = None
        # Cache LU para barrido AC: (fingerprint_circuito, omega) → SuperLU
        self._lu_cache: Dict[Tuple, object] = {}

    # ──────────────────────────────────────────────
    # API pública
    # ──────────────────────────────────────────────

    def solve_dc(self, components: List[Component]) -> Dict:
        """
        Análisis DC: resuelve tensiones nodales y corrientes de rama.
        Detecta automáticamente si el circuito tiene componentes no-lineales
        y usa Newton-Raphson en ese caso.
        """
        _nonlinear_types = (Diode, BJT, MOSFET)
        has_nonlinear = any(isinstance(c, _nonlinear_types) for c in components)

        if has_nonlinear:
            return self.solve_dc_nonlinear(components)

        try:
            node_map, branch_map, size = self._build_maps(components)
            G, I = self._build_matrices(components, node_map, branch_map, size)
            x = _sparse_solve(G, I)
            return self._parse_result(x, node_map, branch_map, components)
        except Exception as e:
            return {'success': False, 'error': str(e),
                    'voltages': {}, 'branch_currents': {}}

    def solve_dc_nonlinear(self, components: List[Component],
                           max_iter: int = 200,
                           tol: float = 1e-6) -> Dict:
        """
        Análisis DC con Newton-Raphson + damping adaptativo.

        Estrategia:
          1. Punto de partida con source stepping (rampa de voltaje)
          2. Newton-Raphson con damping adaptativo: si la norma crece,
             reducir el paso a la mitad hasta 8 veces antes de continuar
        """
        _nonlinear_types = (Diode, BJT, MOSFET)

        try:
            node_map, branch_map, size = self._build_maps(components)
            n_nodes = len(node_map)

            x = self._source_stepping(components, node_map, branch_map, size, n_nodes)

            branch_components = [c for c in components if c.needs_branch()]
            branch_idx_map    = {c.name: branch_map[c.name] for c in branch_components}

            for iteration in range(max_iter):
                V_cur = x[:n_nodes]

                G     = lil_matrix((size, size), dtype=float)
                I_vec = np.zeros(size)

                for c in components:
                    b_idx = branch_idx_map.get(c.name)
                    if isinstance(c, _nonlinear_types):
                        # stamp_linear trabaja sobre ndarray dense → convertir y reconvertir
                        G_dense = G.toarray()
                        c.stamp_linear(G_dense, I_vec, node_map, V_cur)
                        G = lil_matrix(G_dense)
                    else:
                        G_dense = G.toarray()
                        c.stamp(G_dense, I_vec, node_map, branch_idx=b_idx)
                        G = lil_matrix(G_dense)

                try:
                    x_new = _sparse_solve(G, I_vec)
                except Exception:
                    return {'success': False,
                            'error': 'Sistema singular en iteración NR',
                            'voltages': {}, 'branch_currents': {}}

                delta = x_new - x

                step  = 1.0
                norm0 = np.linalg.norm(delta[:n_nodes])
                x_try = x + step * delta
                for _ in range(8):
                    if np.linalg.norm((x_try - x)[:n_nodes]) <= norm0 * 2 + 1e-12:
                        break
                    step  *= 0.5
                    x_try  = x + step * delta

                x = x_try

                if np.linalg.norm(step * delta[:n_nodes]) < tol:
                    break
            else:
                result = self._parse_result(x, node_map, branch_map, components)
                result['warning'] = (f'Newton-Raphson no convergió en {max_iter} '
                                     f'iteraciones. Resultado puede ser inexacto.')
                return result

            result = self._parse_result(x, node_map, branch_map, components)
            result['iterations'] = iteration + 1

            op_points = {}
            for c in components:
                if isinstance(c, _nonlinear_types) and hasattr(c, 'operating_point'):
                    op_points[c.name] = c.operating_point
            if op_points:
                result['operating_points'] = op_points

            return result

        except Exception as e:
            return {'success': False, 'error': str(e),
                    'voltages': {}, 'branch_currents': {}}

    def _source_stepping(self, components, node_map, branch_map, size, n_nodes,
                         steps: int = 10) -> np.ndarray:
        """
        Rampa las fuentes independientes de 0 → valor_final en `steps` pasos.
        """
        from .components import VoltageSource, CurrentSource, Diode, BJT, MOSFET

        x = np.zeros(size)
        branch_components = [c for c in components if c.needs_branch()]
        branch_idx_map    = {c.name: branch_map[c.name] for c in branch_components}

        for step in range(1, steps + 1):
            alpha = step / steps

            G_dense = np.zeros((size, size))
            I_vec   = np.zeros(size)
            V_cur   = x[:n_nodes]

            for c in components:
                b_idx = branch_idx_map.get(c.name)
                if isinstance(c, (Diode, BJT, MOSFET)):
                    c.stamp_linear(G_dense, I_vec, node_map, V_cur)
                elif isinstance(c, VoltageSource):
                    orig_V = c.V
                    c.V    = orig_V * alpha
                    c.stamp(G_dense, I_vec, node_map, branch_idx=b_idx)
                    c.V    = orig_V
                elif isinstance(c, CurrentSource):
                    orig_I  = c.I_val
                    c.I_val = orig_I * alpha
                    c.stamp(G_dense, I_vec, node_map, branch_idx=b_idx)
                    c.I_val = orig_I
                else:
                    c.stamp(G_dense, I_vec, node_map, branch_idx=b_idx)

            try:
                G_sp = lil_matrix(G_dense)
                x    = _sparse_solve(G_sp, I_vec)
            except Exception:
                break

        return x

    # ──────────────────────────────────────────────
    # AC con reutilización de LU
    # ──────────────────────────────────────────────

    def solve_ac(self, components: List[Component],
                 freq_start: float, freq_stop: float,
                 points: int = 100,
                 scale: str = 'log') -> Dict:
        """
        Análisis AC: barre frecuencia y calcula fasores nodales.

        Optimización LU:
            Para circuitos lineales (sin fuentes dependientes de frecuencia
            salvo C/L) la ESTRUCTURA de G es idéntica en todos los puntos.
            Factorizamos una vez por punto de frecuencia y reutilizamos el
            objeto SuperLU si la topología no cambia entre llamadas.

        Args:
            freq_start, freq_stop: rango en Hz
            points: número de puntos
            scale: 'log' o 'linear'

        Returns:
            {
              'frequencies': np.ndarray,
              'voltages': {'nodo': np.ndarray de fasores, ...},
              'success': bool
            }
        """
        try:
            if scale == 'log':
                freqs = np.logspace(np.log10(freq_start), np.log10(freq_stop), points)
            else:
                freqs = np.linspace(freq_start, freq_stop, points)

            node_map, branch_map, size = self._build_maps(components)
            results = {n: np.zeros(points, dtype=complex) for n in node_map}

            # Fingerprint que incluye PARÁMETROS — invalida la caché si
            # algún valor cambia (potenciómetro, ratio del xfmr, etc.).
            topo_key = _circuit_fingerprint(components)

            for i, f in enumerate(freqs):
                omega   = 2 * np.pi * f
                G, I    = self._build_matrices_ac(components, node_map, branch_map, size, omega)
                cache_k = (topo_key, round(omega, 10))

                if cache_k not in self._lu_cache:
                    # ── Factorizar y cachear ──────────────────────────────
                    G_csc = csc_matrix(G)
                    self._lu_cache[cache_k] = splu(G_csc)

                lu  = self._lu_cache[cache_k]
                x   = lu.solve(I)

                for node, idx in node_map.items():
                    results[node][i] = x[idx]

            return {'success': True, 'frequencies': freqs,
                    'voltages': results, 'error': None}
        except Exception as e:
            return {'success': False, 'error': str(e),
                    'frequencies': np.array([]), 'voltages': {}}

    def solve_ac_single(self, components: List[Component],
                        frequency: float) -> Dict:
        """
        Análisis AC a una frecuencia fija. Calcula fasores nodales,
        corrientes de rama y potencias (real, reactiva, aparente) por
        componente y para el circuito total.

        Returns:
            {
              'success': bool,
              'frequency': float,
              'voltages':  {'nodo': complex (fasor Vrms), ...},
              'currents':  {'comp': complex (fasor Irms), ...},
              'powers':    {'comp': {'P': W, 'Q': VAR, 'S': VA, 'fp': float}, ...},
              'total':     {'P': W, 'Q': VAR, 'S': VA, 'fp': float, 'fp_type': str},
              'error': str | None
            }
        """
        try:
            omega = 2 * np.pi * frequency
            node_map, branch_map, size = self._build_maps(components)
            n_nodes = len(node_map)

            ac_branch     = [c for c in components
                             if c.needs_branch() and not isinstance(c, Inductor)]
            ac_branch_idx = {c.name: n_nodes + i for i, c in enumerate(ac_branch)}

            G, I_vec = self._build_matrices_ac(components, node_map, branch_map, size, omega)

            # Reutilizar LU si está en caché — la huella INCLUYE los valores
            # de los componentes, así que cambiar (p.ej.) el wiper de un
            # potenciómetro fuerza re-factorización.
            topo_key = _circuit_fingerprint(components)
            cache_k  = (topo_key, round(omega, 10))
            if cache_k not in self._lu_cache:
                # Limitar tamaño de la caché para no crecer sin límite
                # cuando los valores cambian continuamente (slider en vivo).
                if len(self._lu_cache) >= 64:
                    self._lu_cache.pop(next(iter(self._lu_cache)))
                G_csc = csc_matrix(G)
                self._lu_cache[cache_k] = splu(G_csc)

            x = self._lu_cache[cache_k].solve(I_vec)

            voltages: Dict[str, complex] = {n: complex(x[i]) for n, i in node_map.items()}
            voltages['0'] = 0.0 + 0.0j

            branch_currents: Dict[str, complex] = {
                name: complex(x[idx]) for name, idx in ac_branch_idx.items()
                if idx < len(x)
            }

            # ── Potencias por componente ──────────────────────────────────
            powers: Dict[str, Dict] = {}

            def V_node(n: str) -> complex:
                return voltages.get(n, 0.0 + 0.0j)

            def _power_entry(S: complex) -> Dict:
                P  = S.real
                Q  = S.imag
                Sm = abs(S)
                fp = P / Sm if Sm > 1e-12 else 0.0
                return {'P': P, 'Q': Q, 'S': Sm, 'fp': fp}

            for c in components:
                n1 = getattr(c, 'n1',    getattr(c, 'n_pos', None))
                n2 = getattr(c, 'n2',    getattr(c, 'n_neg', None))
                if n1 is None or n2 is None:
                    continue

                V1    = V_node(n1)
                V2    = V_node(n2)
                Vcomp = V1 - V2

                if c.name in branch_currents:
                    Icomp = branch_currents[c.name]
                elif isinstance(c, Resistor_cls):
                    Icomp = Vcomp / c.R if c.R != 0 else 0.0
                elif isinstance(c, Capacitor):
                    Icomp = Vcomp * (1j * omega * c.C)
                elif isinstance(c, Inductor):
                    Y     = 1.0 / (1j * omega * c.L) if omega != 0 else 0.0
                    Icomp = Vcomp * Y
                elif isinstance(c, Impedance):
                    Icomp = Vcomp / c.Z if abs(c.Z) > 1e-12 else 0.0
                else:
                    continue

                S = Vcomp * Icomp.conjugate()
                powers[c.name] = _power_entry(S)

            # ── Potencia total ────────────────────────────────────────────
            P_total = Q_total = 0.0
            for c in components:
                if isinstance(c, VoltageSourceAC) and c.name in branch_currents:
                    V_src  = V_node(c.n_pos) - V_node(c.n_neg)
                    I_src  = branch_currents[c.name]
                    S_src  = V_src * I_src.conjugate()
                    P_total += S_src.real
                    Q_total += S_src.imag

            if abs(P_total) < 1e-12 and abs(Q_total) < 1e-12:
                for name, pw in powers.items():
                    P_total += pw['P']
                    Q_total += pw['Q']

            if P_total < 0:
                P_total = -P_total
                Q_total = -Q_total

            S_total = complex(P_total, Q_total)
            Sm      = abs(S_total)
            fp      = P_total / Sm if Sm > 1e-12 else 0.0
            fp_type = ('inductivo'  if Q_total > 0
                       else 'capacitivo' if Q_total < 0
                       else 'unitario')

            total = {'P': P_total, 'Q': Q_total, 'S': Sm, 'fp': fp, 'fp_type': fp_type}

            return {
                'success':   True,
                'error':     None,
                'frequency': frequency,
                'voltages':  voltages,
                'currents':  branch_currents,
                'powers':    powers,
                'total':     total,
            }

        except np.linalg.LinAlgError as e:
            return {'success': False, 'error': f'Sistema singular: {e}',
                    'voltages': {}, 'currents': {}, 'powers': {}, 'total': {}}
        except Exception as e:
            return {'success': False, 'error': str(e),
                    'voltages': {}, 'currents': {}, 'powers': {}, 'total': {}}

    def correct_power_factor(self, total: Dict,
                              frequency: float,
                              fp_target: float = 1.0,
                              connection: str = 'parallel',
                              target_type: str = 'auto') -> Dict:
        """
        Calcula la reactancia correctora de FP.

        Dado el triángulo de potencia actual (P, Q, S, fp) y un fp objetivo,
        determina el valor de C o L a colocar en paralelo con la carga.

        Args:
            total:        diccionario con P, Q, S, fp (resultado de solve_ac).
            frequency:    frecuencia de la red en Hz.
            fp_target:    factor de potencia objetivo en (0, 1].
            connection:   'parallel' (única topología soportada por ahora).
            target_type:  signo deseado del Q resultante:
                'auto'        → conserva el dominio original (default).
                              Carga inductiva (Q>0) → compensa con capacitor
                              y deja resultado inductivo.
                              Carga capacitiva (Q<0) → compensa con inductor
                              y deja resultado capacitivo.
                'inductive'   → fuerza Q_new > 0 (resultado inductivo).
                              Permite cruzar de capacitivo a inductivo.
                'capacitive'  → fuerza Q_new < 0 (resultado capacitivo).
                              Permite cruzar de inductivo a capacitivo.

        Returns:
            {
              'type':         'capacitor' | 'inductor',
              'value':        valor en F o H (normalizado a Vrms=1 V),
              'Q_corr':       |ΔQ| compensado (VAR),
              'Q_new':        nuevo Q tras la corrección,
              'fp_new':       FP resultante,
              'fp_type_new':  'inductivo' | 'capacitivo' | 'unitario',
              'formula':      fórmula usada,
              'note':         observación de escala con Vrms,
              'target_type':  el target_type efectivo,
            }
        """
        import math
        P = total.get('P', 0.0)
        Q = total.get('Q', 0.0)

        if abs(P) < 1e-12:
            return {'error': 'Potencia activa nula — no se puede corregir FP'}

        omega = 2 * math.pi * frequency
        if omega <= 0:
            return {'error': 'Frecuencia inválida para corrección de FP'}

        # ── Magnitud del Q objetivo a partir del fp objetivo ─────────────
        fp_clip    = max(0.01, min(abs(fp_target), 1.0))
        phi_target = math.acos(fp_clip)
        Q_mag      = abs(P) * math.tan(phi_target)   # ≥ 0

        # ── Signo del Q objetivo según el tipo deseado ───────────────────
        tt = (target_type or 'auto').lower()
        if tt == 'inductive':
            Q_target = +Q_mag                 # resultado inductivo (Q>0)
        elif tt == 'capacitive':
            Q_target = -Q_mag                 # resultado capacitivo (Q<0)
        else:
            # Auto: conserva el signo del Q actual
            if Q > 0:
                Q_target = +Q_mag
            elif Q < 0:
                Q_target = -Q_mag
            else:
                Q_target = 0.0
            tt = 'auto'

        # ── ΔQ: positivo → quitar Q (capacitor); negativo → añadir Q (inductor)
        delta_Q = Q - Q_target

        if abs(delta_Q) < 1e-12:
            return {'error': 'No se requiere corrección — el FP ya coincide con el objetivo'}

        if delta_Q > 0:
            # Sobra Q inductivo (o falta capacitivo) → CAPACITOR en paralelo
            # Q_C = -V²·ω·C  →  C = ΔQ / (V²·ω) con Vrms=1 V (normalizado)
            comp_type  = 'capacitor'
            formula    = 'C = ΔQ / (V² · ω)'
            value_norm = delta_Q / omega
            note = ('Multiplica C por (1/Vrms²) con tu tensión real de línea. '
                    'Ej: para Vrms=120V → C_real = C_norm / 120²')
        else:
            # Hace falta más Q inductivo → INDUCTOR en paralelo
            # Q_L = V²/(ω·L)  →  L = V² / (|ΔQ|·ω)
            comp_type  = 'inductor'
            formula    = 'L = V² / (|ΔQ| · ω)'
            value_norm = 1.0 / (omega * abs(delta_Q))
            note = ('Multiplica L por Vrms² con tu tensión real de línea. '
                    'Ej: para Vrms=120V → L_real = L_norm · 120²')

        Q_new       = Q_target
        S_new       = math.sqrt(P*P + Q_new*Q_new)
        fp_new      = abs(P) / S_new if S_new > 1e-12 else 1.0
        fp_type_new = ('inductivo'  if Q_new >  1e-9
                       else 'capacitivo' if Q_new < -1e-9
                       else 'unitario')

        return {
            'type':         comp_type,
            'value':        value_norm,
            'Q_corr':       abs(delta_Q),
            'Q_new':        Q_new,
            'fp_new':       fp_new,
            'fp_type_new':  fp_type_new,
            'formula':      formula,
            'note':         note,
            'target_type':  tt,
        }

    # ──────────────────────────────────────────────
    # Transitorio con timestep adaptativo
    # ──────────────────────────────────────────────

    def solve_transient(self, components: List[Component],
                        t_stop: float, dt: float,
                        method: str = 'trapezoidal',
                        adaptive: bool = True,
                        tol_abs: float = 1e-6,
                        tol_rel: float = 1e-3,
                        dt_min: float = None,
                        dt_max: float = None,
                        t_start: float = 0.0,
                        nr_tol: float = 1e-5,
                        nr_max_iter: int = 25,
                        initial_state: Optional[Dict] = None) -> Dict:
        """
        Análisis transitorio con:

        - Modelo compañero trapezoidal para C y L (sin atajo solo-nodal —
          el sistema se resuelve con corrientes de rama incluidas).
        - Fuentes con `stamp_transient(t)`: la VAC produce v(t) = Vpeak·sin(ωt+φ).
        - Newton-Raphson anidado por paso para Diodo/BJT/MOSFET (con damping
          y diode-limiting heredados de `stamp_linear`).
        - Timestep adaptativo opcional comparando trapezoidal vs Euler.

        Args:
            t_stop:        duración a simular (segundos, relativa a t_start)
            dt:            paso de tiempo inicial
            method:        'trapezoidal' o 'euler'
            adaptive:      ajustar dt según LTE estimado
            tol_abs/rel:   tolerancia LTE
            dt_min/max:    cotas de paso adaptativo
            t_start:       tiempo absoluto inicial (default 0)
            nr_tol:        tolerancia de Newton-Raphson para no-lineales
            nr_max_iter:   máximo iteraciones NR por paso
            initial_state: dict {'x', 'cap_states', 'ind_states', 't'} para
                           reanudar desde un final_state previo. Si se da,
                           se omite el cálculo del DC OP inicial. La
                           topología (componentes y nodos) DEBE ser idéntica
                           a la del estado guardado.

        Returns:
            {
              'time': np.ndarray,
              'voltages': {nodo: np.ndarray},
              'branch_currents': {nombre: np.ndarray},
              'success': bool, 'error': str|None,
              'steps': int, 'dt_stats': {...},
              'final_state': dict   # para reanudar
            }
        """
        _nonlinear_types = (Diode, BJT, MOSFET)
        has_nonlinear = any(isinstance(c, _nonlinear_types) for c in components)

        try:
            if dt_min is None:
                dt_min = dt / 1000.0
            if dt_max is None:
                dt_max = dt * 10.0

            node_map, branch_map, size = self._build_maps(components)
            n_nodes = len(node_map)

            branch_components = [c for c in components if c.needs_branch()]
            branch_idx_map    = {c.name: branch_map[c.name] for c in branch_components}

            # ── Estado inicial: o bien reanudar, o bien calcular DC OP ───
            if initial_state is not None:
                x = np.asarray(initial_state['x'], dtype=float).copy()
                if x.shape[0] != size:
                    raise ValueError(
                        f"initial_state.x tiene tamaño {x.shape[0]} pero el "
                        f"sistema actual requiere {size}. Topología cambió.")
                cap_states: Dict[str, Tuple[float, float]] = dict(
                    initial_state.get('cap_states', {}))
                ind_states: Dict[str, Tuple[float, float]] = dict(
                    initial_state.get('ind_states', {}))
                # Asegurar entradas para componentes que no aparecen en estado
                for c in components:
                    if isinstance(c, Capacitor) and c.name not in cap_states:
                        V_C0 = self._v_diff(c.n1, c.n2, x, node_map)
                        cap_states[c.name] = (V_C0, 0.0)
                    elif isinstance(c, Inductor) and c.name not in ind_states:
                        bidx = branch_map.get(c.name)
                        i_L0 = float(x[bidx]) if bidx is not None else 0.0
                        ind_states[c.name] = (i_L0, 0.0)
            else:
                x = self._initial_state_transient(
                    components, node_map, branch_map, size, t_start)

                # Estados de elementos con memoria (modelo compañero trapezoidal)
                #   capacitor: (V_C(t), i_C(t))
                #   inductor:  (i_L(t), V_L(t)) — V_L medido en dirección de i_k
                cap_states = {}
                ind_states = {}
                for c in components:
                    if isinstance(c, Capacitor):
                        V_C0 = self._v_diff(c.n1, c.n2, x, node_map)
                        cap_states[c.name] = (V_C0, 0.0)
                    elif isinstance(c, Inductor):
                        bidx = branch_map.get(c.name)
                        i_L0 = float(x[bidx]) if bidx is not None else 0.0
                        ind_states[c.name] = (i_L0, 0.0)

            times_list   = [t_start]
            volt_lists   = {node: [float(x[idx])] for node, idx in node_map.items()}
            branch_lists = {name: [float(x[idx])] for name, idx in branch_map.items()}
            dt_list: List[float] = []

            t_abs    = t_start
            t_local  = 0.0
            dt_cur   = dt
            steps    = 0
            last_err = None

            while t_local < t_stop:
                dt_cur = min(dt_cur, t_stop - t_local)
                if dt_cur < dt_min:
                    dt_cur = dt_min
                t_new_abs = t_abs + dt_cur

                # ── Newton-Raphson dentro del paso ────────────────────────
                # Para circuitos lineales basta una iteración (NR converge
                # en 1 paso porque ya estamos resolviendo el sistema lineal).
                x_iter      = x.copy()
                converged   = not has_nonlinear
                nr_used     = 0
                singular    = False

                max_nr = nr_max_iter if has_nonlinear else 1
                for nr_i in range(max_nr):
                    nr_used = nr_i + 1
                    G, I_vec = self._build_matrices_transient(
                        components, node_map, branch_map, size,
                        t_new_abs, dt_cur, x_iter, cap_states, ind_states,
                        branch_idx_map, _nonlinear_types)

                    try:
                        x_new = np.linalg.solve(G, I_vec)
                    except np.linalg.LinAlgError:
                        singular = True
                        break

                    if not has_nonlinear:
                        x_iter = x_new
                        converged = True
                        break

                    err = float(np.max(np.abs(x_new[:n_nodes] - x_iter[:n_nodes])))
                    last_err = err
                    x_iter = x_new
                    if err < nr_tol:
                        converged = True
                        break

                if singular or not converged:
                    # Reducir dt y reintentar
                    if dt_cur > dt_min + 1e-15:
                        dt_cur = max(dt_cur * 0.5, dt_min)
                        continue
                    return {
                        'success': False,
                        'error': ('Sistema singular' if singular
                                  else f'NR no convergió en {max_nr} iter (err={last_err:.2e})'),
                        'time': np.array(times_list),
                        'voltages': {n: np.array(v) for n, v in volt_lists.items()},
                        'branch_currents': {n: np.array(v) for n, v in branch_lists.items()},
                    }

                # ── Estimación LTE (solo lineal o tras NR convergido) ─────
                if adaptive and method == 'trapezoidal':
                    # Construir paso Backward-Euler equivalente con el x_iter
                    # convergido para comparar con trapezoidal (LTE ≈ |Δ|/3).
                    G_be, I_be = self._build_matrices_transient(
                        components, node_map, branch_map, size,
                        t_new_abs, dt_cur, x_iter, cap_states, ind_states,
                        branch_idx_map, _nonlinear_types,
                        integration='backward_euler')
                    try:
                        x_be = np.linalg.solve(G_be, I_be)
                        lte  = float(np.max(np.abs(x_iter[:n_nodes] - x_be[:n_nodes])) / 3.0)
                        ref  = float(np.max(np.abs(x_iter[:n_nodes])) + 1e-12)
                        tol_mix = tol_abs + tol_rel * ref
                        if lte > tol_mix and dt_cur > dt_min + 1e-15:
                            dt_cur = max(dt_cur * 0.5, dt_min)
                            continue
                        if lte < tol_mix / 4.0:
                            dt_cur = min(dt_cur * 1.5, dt_max)
                    except np.linalg.LinAlgError:
                        pass  # ignorar control adaptativo si BE falla

                # ── Aceptar el paso: actualizar estados de C, L ──────────
                for c in components:
                    if isinstance(c, Capacitor):
                        V_C_new = self._v_diff(c.n1, c.n2, x_iter, node_map)
                        V_C_old, i_C_old = cap_states[c.name]
                        # i_C(t+dt) = -i_C(t) + (2C/dt)·(V(t+dt) - V(t))
                        i_C_new = -i_C_old + (2.0 * c.C / dt_cur) * (V_C_new - V_C_old)
                        cap_states[c.name] = (V_C_new, i_C_new)
                    elif isinstance(c, Inductor):
                        bidx = branch_map.get(c.name)
                        if bidx is None:
                            continue
                        i_L_new = float(x_iter[bidx])
                        i_L_old, V_L_old = ind_states[c.name]
                        # V_L(t+dt) = -V_L(t) + (2L/dt)·(i(t+dt) - i(t))
                        V_L_new = -V_L_old + (2.0 * c.L / dt_cur) * (i_L_new - i_L_old)
                        ind_states[c.name] = (i_L_new, V_L_new)

                x        = x_iter
                t_abs    = t_new_abs
                t_local += dt_cur
                steps   += 1
                dt_list.append(dt_cur)

                times_list.append(t_abs)
                for node, idx in node_map.items():
                    volt_lists[node].append(float(x[idx]))
                for name, idx in branch_map.items():
                    branch_lists[name].append(float(x[idx]))

            times_arr = np.array(times_list)
            volt_arrs = {node: np.array(vals) for node, vals in volt_lists.items()}
            br_arrs   = {name: np.array(vals) for name, vals in branch_lists.items()}

            dt_arr   = np.array(dt_list) if dt_list else np.array([dt])
            dt_stats = {
                'min':  float(dt_arr.min()),
                'max':  float(dt_arr.max()),
                'mean': float(dt_arr.mean()),
            }

            final_state = {
                't': t_abs,
                'x': x.copy(),
                'cap_states': dict(cap_states),
                'ind_states': dict(ind_states),
            }

            return {
                'success':  True,
                'time':     times_arr,
                'voltages': volt_arrs,
                'branch_currents': br_arrs,
                'error':    None,
                'steps':    steps,
                'dt_stats': dt_stats,
                'final_state': final_state,
            }

        except Exception as e:
            return {'success': False, 'error': str(e),
                    'time': np.array([]), 'voltages': {},
                    'branch_currents': {}}

    # ──────────────────────────────────────────────
    # Auxiliares de transitorio
    # ──────────────────────────────────────────────

    def _v_diff(self, n1: str, n2: str, x: np.ndarray, node_map: dict) -> float:
        """V(n1) - V(n2) leyendo del vector solución (GND = 0)."""
        v1 = float(x[node_map[n1]]) if n1 in node_map else 0.0
        v2 = float(x[node_map[n2]]) if n2 in node_map else 0.0
        return v1 - v2

    def _initial_state_transient(self, components, node_map, branch_map,
                                 size: int, t_start: float) -> np.ndarray:
        """
        Estado inicial: DC sustituyendo cada VAC por una fuente DC
        de valor v(t_start). Esto da un punto de partida físicamente
        razonable y evita el "encendido golpe" que destruye la
        convergencia de NR cuando arrancamos en t=0 con VAC=0.
        """
        from .components import VoltageSource as _VS
        import math as _m

        dc_comps = []
        for c in components:
            if isinstance(c, VoltageSourceAC):
                v_t = c.Vpeak * _m.sin(2.0 * _m.pi * c.frequency * t_start
                                       + _m.radians(c.phase_deg))
                vs = _VS(c.name, c.n_pos, c.n_neg, v_t)
                dc_comps.append(vs)
            else:
                dc_comps.append(c)

        dc = self.solve_dc(dc_comps)
        x  = np.zeros(size)
        if dc.get('success'):
            for node, idx in node_map.items():
                x[idx] = float(dc.get('voltages', {}).get(node, 0.0))
            for cname, idx in branch_map.items():
                x[idx] = float(dc.get('branch_currents', {}).get(cname, 0.0))
        return x

    def _build_matrices_transient(self, components, node_map, branch_map,
                                  size: int, t: float, dt: float,
                                  x_iter: np.ndarray,
                                  cap_states: Dict[str, Tuple[float, float]],
                                  ind_states: Dict[str, Tuple[float, float]],
                                  branch_idx_map: dict,
                                  nonlinear_types: tuple,
                                  integration: str = 'trapezoidal'):
        """
        Construye G e I para un paso transitorio en tiempo `t`.

        - Capacitores e inductores: modelo compañero (`trapezoidal` o
          `backward_euler`, este último solo para estimar LTE).
        - No-lineales: linealizados en V_iter via `stamp_linear`.
        - Resto (R, V, I, VAC, etc.): `stamp_transient(t)`.
        """
        n_nodes = len(node_map)
        G       = np.zeros((size, size))
        I_vec   = np.zeros(size)
        V_cur   = x_iter[:n_nodes]

        for c in components:
            b_idx = branch_idx_map.get(c.name)

            if isinstance(c, nonlinear_types):
                c.stamp_linear(G, I_vec, node_map, V_cur)
            elif isinstance(c, Capacitor):
                self._stamp_capacitor_companion(
                    G, I_vec, c, node_map, dt, cap_states, integration)
            elif isinstance(c, Inductor):
                self._stamp_inductor_companion(
                    G, I_vec, c, node_map, branch_map, dt, ind_states,
                    integration)
            else:
                c.stamp_transient(G, I_vec, node_map, t, branch_idx=b_idx)

        return G, I_vec

    def _stamp_capacitor_companion(self, G, I_vec, c, node_map,
                                   dt: float, cap_states: dict,
                                   integration: str):
        """
        Modelo compañero del capacitor:

          Trapezoidal: Yeq = 2C/dt
                       Ieq = (2C/dt)·V(t) + i_C(t)
          Backward Euler: Yeq = C/dt
                       Ieq = (C/dt)·V(t)

        Ecuación: i_C(t+dt) = Yeq · V(t+dt) - Ieq
        """
        V_old, i_old = cap_states.get(c.name, (0.0, 0.0))
        if integration == 'backward_euler':
            Yeq = c.C / dt
            Ieq = Yeq * V_old
        else:
            Yeq = 2.0 * c.C / dt
            Ieq = Yeq * V_old + i_old

        n1 = node_map.get(c.n1)
        n2 = node_map.get(c.n2)
        if n1 is not None:
            G[n1, n1] += Yeq
            I_vec[n1] += Ieq
        if n2 is not None:
            G[n2, n2] += Yeq
            I_vec[n2] -= Ieq
        if n1 is not None and n2 is not None:
            G[n1, n2] -= Yeq
            G[n2, n1] -= Yeq

    def _stamp_inductor_companion(self, G, I_vec, c, node_map, branch_map,
                                  dt: float, ind_states: dict,
                                  integration: str):
        """
        Modelo compañero del inductor (con variable de rama k).

        Convención: i_k es la corriente que entra en n1 desde el inductor
        (igual que en VoltageSource.stamp existente).

        Trapezoidal:
            V_L(t+dt) = -V_L(t) + (2L/dt)·(i_k(t+dt) - i_k(t))
        Backward Euler:
            V_L(t+dt) = (L/dt)·(i_k(t+dt) - i_k(t))

        donde V_L = V_n2 - V_n1 (caída en dirección de i_k).
        Reordenando para fila k del sistema MNA:
            (V_n1 - V_n2) - Req·i_k = -Veq_prev   (trapezoidal)
        """
        i_old, V_old = ind_states.get(c.name, (0.0, 0.0))
        if integration == 'backward_euler':
            Req = c.L / dt
            rhs = -Req * i_old
        else:
            Req = 2.0 * c.L / dt
            rhs = -V_old - Req * i_old

        np_ = node_map.get(c.n1)
        nn  = node_map.get(c.n2)
        k   = branch_map.get(c.name)
        if k is None:
            return

        # Topología: i_k entra a n1, sale por n2 (convención de VoltageSource)
        if np_ is not None:
            G[np_, k] += 1.0
            G[k, np_] += 1.0
        if nn is not None:
            G[nn, k] -= 1.0
            G[k, nn] -= 1.0

        # V_n1 - V_n2 - Req·i_k = rhs
        G[k, k]  -= Req
        I_vec[k] += rhs

    # ──────────────────────────────────────────────
    # Métodos internos
    # ──────────────────────────────────────────────

    def _solve_dense_or_sparse(self, G_dense: np.ndarray,
                               I_vec: np.ndarray,
                               n_nodes: int) -> np.ndarray:
        """
        Resuelve G·x = I.
        Para matrices pequeñas (≤ 50 nodos) usa numpy directo;
        para más grandes usa SuperLU sobre sparse.
        """
        if n_nodes <= 50:
            if np.linalg.matrix_rank(G_dense) < n_nodes:
                return np.zeros(n_nodes)
            return np.linalg.solve(G_dense, I_vec)
        G_sp = lil_matrix(G_dense)
        return _sparse_solve(G_sp, I_vec)

    def _build_maps(self, components: List[Component]) -> Tuple[Dict, Dict, int]:
        """
        Construye:
          node_map:   {nombre_nodo: índice_fila}  (excluye GND=0)
          branch_map: {nombre_componente: índice_fila}  para fuentes de voltaje
        """
        nodes = set()
        all_node_attrs = [
            'n1', 'n2', 'n_pos', 'n_neg',
            'n_a', 'n_k',
            'n_p1', 'n_p2', 'n_s1', 'n_s2',
            'n_base', 'n_collector', 'n_emitter',
            'n_gate', 'n_drain', 'n_source',
            'n_p', 'n_n', 'n_out',
        ]
        for c in components:
            for attr in all_node_attrs:
                if hasattr(c, attr):
                    nodes.add(getattr(c, attr))

        gnd_names = {'0', 'gnd', 'GND', 'ground'}
        nodes -= gnd_names

        node_map = {n: i for i, n in enumerate(sorted(nodes))}
        n_nodes  = len(node_map)

        branch_components = [c for c in components if c.needs_branch()]
        branch_map = {c.name: n_nodes + i for i, c in enumerate(branch_components)}

        size = n_nodes + len(branch_components)
        return node_map, branch_map, size

    def _build_matrices(self, components, node_map, branch_map, size):
        """
        Construye G e I para DC usando arrays densos (compatibles con stamp()).
        Los componentes estampan directamente sobre np.ndarray.
        """
        G = np.zeros((size, size))
        I = np.zeros(size)

        branch_components = [c for c in components if c.needs_branch()]
        branch_idx_map    = {c.name: branch_map[c.name] for c in branch_components}

        for c in components:
            b_idx = branch_idx_map.get(c.name)
            c.stamp(G, I, node_map, branch_idx=b_idx)

        # Convertir a LIL para devolución uniforme (spsolve lo convertirá a CSC)
        return lil_matrix(G), I

    def _build_matrices_ac(self, components, node_map, branch_map, size, omega):
        """
        Construye G e I para AC (admitancias complejas).
        En AC los inductores se tratan como admitancias (stamp_ac),
        NO como variables de rama, así que se excluyen del branch_map AC.
        Devuelve arrays complejos densos (los componentes estampan en ndarray).
        """
        ac_branch     = [c for c in components
                         if c.needs_branch() and not isinstance(c, Inductor)]
        n_nodes_ac    = len(node_map)
        ac_branch_idx = {c.name: n_nodes_ac + i for i, c in enumerate(ac_branch)}
        size_ac       = n_nodes_ac + len(ac_branch)

        G = np.zeros((size_ac, size_ac), dtype=complex)
        I = np.zeros(size_ac, dtype=complex)

        for c in components:
            b_idx = ac_branch_idx.get(c.name)
            if hasattr(c, 'stamp_ac'):
                c.stamp_ac(G, I, node_map, omega, branch_idx=b_idx)
            elif not isinstance(c, Inductor):
                c.stamp(G, I, node_map, branch_idx=b_idx)

        return G, I

    def _build_capacitance_matrix(self, components, node_map, n_nodes):
        """Matriz C para integración numérica en transitorio."""
        C = np.zeros((n_nodes, n_nodes))
        for c in components:
            if isinstance(c, Capacitor):
                n1 = node_map.get(c.n1)
                n2 = node_map.get(c.n2)
                if n1 is not None:
                    C[n1, n1] += c.C
                if n2 is not None:
                    C[n2, n2] += c.C
                if n1 is not None and n2 is not None:
                    C[n1, n2] -= c.C
                    C[n2, n1] -= c.C
        return C

    def _parse_result(self, x, node_map, branch_map, components) -> Dict:
        """Convierte el vector solución en un diccionario legible."""
        voltages = {node: float(x[idx]) for node, idx in node_map.items()}
        voltages['0'] = 0.0

        branch_currents = {}
        for comp_name, idx in branch_map.items():
            branch_currents[comp_name] = float(x[idx])

        return {
            'success': True,
            'error':   None,
            'voltages': voltages,
            'branch_currents': branch_currents,
            'raw': x,
        }
