"""
Motor de simulación: Modified Nodal Analysis (MNA).

Resuelve el sistema:
    G · x = I

donde:
    G  = matriz de conductancias + variables de rama
    x  = [V_nodos | I_ramas]
    I  = vector de excitaciones

Referencia: Vlach & Singhal, "Computer Methods for Circuit Analysis and Design"
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
from .components import Component, VoltageSource, VoltageSourceAC, CurrentSource, Capacitor, Inductor, Diode, BJT, MOSFET, OpAmp, Impedance
from .components import Resistor as Resistor_cls


class MNASolver:
    """
    Solver MNA para análisis DC, AC y transitorio.

    Interfaz estable: si en el futuro el interior se reescribe en C++
    vía pybind11, el resto del proyecto no cambia nada.
    """

    def __init__(self):
        self._last_result: Optional[Dict] = None

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
            x = np.linalg.solve(G, I)
            return self._parse_result(x, node_map, branch_map, components)
        except np.linalg.LinAlgError as e:
            return {'success': False, 'error': f'Sistema singular: {e}',
                    'voltages': {}, 'branch_currents': {}}
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

            # ── Punto de partida: source stepping ──────────────────────────
            # Resolver primero con fuentes al 1% para dar voltajes iniciales
            # razonables a los componentes no-lineales
            x = self._source_stepping(components, node_map, branch_map, size, n_nodes)

            # ── Newton-Raphson con damping adaptativo ───────────────────────
            branch_components = [c for c in components if c.needs_branch()]
            branch_idx_map    = {c.name: branch_map[c.name] for c in branch_components}

            for iteration in range(max_iter):
                V_cur = x[:n_nodes]

                G     = np.zeros((size, size))
                I_vec = np.zeros(size)

                for c in components:
                    b_idx = branch_idx_map.get(c.name)
                    if isinstance(c, _nonlinear_types):
                        c.stamp_linear(G, I_vec, node_map, V_cur)
                    else:
                        c.stamp(G, I_vec, node_map, branch_idx=b_idx)

                try:
                    x_new = np.linalg.solve(G, I_vec)
                except np.linalg.LinAlgError:
                    return {'success': False,
                            'error': 'Sistema singular en iteración NR',
                            'voltages': {}, 'branch_currents': {}}

                delta = x_new - x

                # Damping adaptativo: reducir paso si la norma crece
                step   = 1.0
                norm0  = np.linalg.norm(delta[:n_nodes])
                x_try  = x + step * delta
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
        Da voltajes iniciales razonables a los no-lineales antes del NR completo.
        """
        from .components import VoltageSource, CurrentSource, Diode, BJT, MOSFET

        x = np.zeros(size)
        branch_components = [c for c in components if c.needs_branch()]
        branch_idx_map    = {c.name: branch_map[c.name] for c in branch_components}

        for step in range(1, steps + 1):
            alpha = step / steps  # 0.1 … 1.0

            G     = np.zeros((size, size))
            I_vec = np.zeros(size)
            V_cur = x[:n_nodes]

            for c in components:
                b_idx = branch_idx_map.get(c.name)
                if isinstance(c, (Diode, BJT, MOSFET)):
                    c.stamp_linear(G, I_vec, node_map, V_cur)
                elif isinstance(c, VoltageSource):
                    # Escalar el voltaje de la fuente
                    orig_V = c.V
                    c.V    = orig_V * alpha
                    c.stamp(G, I_vec, node_map, branch_idx=b_idx)
                    c.V    = orig_V
                elif isinstance(c, CurrentSource):
                    orig_I = c.I_val
                    c.I_val = orig_I * alpha
                    c.stamp(G, I_vec, node_map, branch_idx=b_idx)
                    c.I_val = orig_I
                else:
                    c.stamp(G, I_vec, node_map, branch_idx=b_idx)

            try:
                x = np.linalg.solve(G, I_vec)
            except np.linalg.LinAlgError:
                break  # continuar con el x que tenemos

        return x

    def solve_ac(self, components: List[Component],
                 freq_start: float, freq_stop: float,
                 points: int = 100,
                 scale: str = 'log') -> Dict:
        """
        Análisis AC: barre frecuencia y calcula fasores nodales.

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

            for i, f in enumerate(freqs):
                omega = 2 * np.pi * f
                G, I = self._build_matrices_ac(components, node_map, branch_map, size, omega)
                x = np.linalg.solve(G, I)
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

            # En AC los inductores son admitancias — recalcular branch map AC
            ac_branch = [c for c in components
                         if c.needs_branch() and not isinstance(c, Inductor)]
            ac_branch_idx = {c.name: n_nodes + i for i, c in enumerate(ac_branch)}

            G, I_vec = self._build_matrices_ac(components, node_map, branch_map, size, omega)
            x = np.linalg.solve(G, I_vec)

            # Fasores de voltaje nodal (Vrms)
            voltages: Dict[str, complex] = {n: complex(x[i]) for n, i in node_map.items()}
            voltages['0'] = 0.0 + 0.0j

            # Fasores de corriente de rama (solo los AC branches)
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
                # Voltaje terminal del componente
                n1 = getattr(c, 'n1',    getattr(c, 'n_pos', None))
                n2 = getattr(c, 'n2',    getattr(c, 'n_neg', None))
                if n1 is None or n2 is None:
                    continue

                V1 = V_node(n1)
                V2 = V_node(n2)
                Vcomp = V1 - V2   # voltaje a través del componente

                # Corriente a través (de rama si existe, si no calcular desde admitancia)
                if c.name in branch_currents:
                    Icomp = branch_currents[c.name]
                elif isinstance(c, Resistor_cls):
                    Icomp = Vcomp / c.R if c.R != 0 else 0.0
                elif isinstance(c, Capacitor):
                    Y = 1j * omega * c.C
                    Icomp = Vcomp * Y
                elif isinstance(c, Inductor):
                    Y = 1.0 / (1j * omega * c.L) if omega != 0 else 0.0
                    Icomp = Vcomp * Y
                elif isinstance(c, Impedance):
                    Icomp = Vcomp / c.Z if abs(c.Z) > 1e-12 else 0.0
                else:
                    continue

                # S = V * I*  (potencia compleja, convencion generador: S = V·I*)
                S = Vcomp * Icomp.conjugate()
                powers[c.name] = _power_entry(S)

            # ── Potencia total desde las fuentes AC ───────────────────────
            P_total = Q_total = 0.0
            for c in components:
                if isinstance(c, (VoltageSourceAC,)):
                    if c.name in branch_currents:
                        V_src = V_node(c.n_pos) - V_node(c.n_neg)
                        I_src = branch_currents[c.name]
                        # Potencia entregada a la carga = P absorbida por el circuito
                        # Convencion: S = V_fuente * I_fuente* (positivo = entregada)
                        S_src = V_src * I_src.conjugate()
                        P_total += S_src.real
                        Q_total += S_src.imag

            if abs(P_total) < 1e-12 and abs(Q_total) < 1e-12:
                # Fallback: sumar potencias absorbidas por elementos pasivos
                for name, pw in powers.items():
                    P_total += pw['P']
                    Q_total += pw['Q']

            # La potencia activa entregada por la fuente es positiva por convención
            # Si resulta negativa, invertir (depende de orientación de la fuente)
            if P_total < 0:
                P_total = -P_total
                Q_total = -Q_total

            S_total = complex(P_total, Q_total)
            Sm      = abs(S_total)
            fp      = P_total / Sm if Sm > 1e-12 else 0.0
            fp_type = 'inductivo' if Q_total > 0 else ('capacitivo' if Q_total < 0 else 'unitario')

            total = {'P': P_total, 'Q': Q_total, 'S': Sm,
                     'fp': fp, 'fp_type': fp_type}

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
                              connection: str = 'parallel') -> Dict:
        """
        Calcula la reactancia correctora de FP.

        Dado el triángulo de potencia actual (P, Q, S, fp) y un fp objetivo,
        determina el valor de C o L a colocar en paralelo con la carga.

        Returns:
            {
              'type':      'capacitor' | 'inductor',
              'value':     valor en F o H,
              'Q_corr':    VAR a compensar,
              'Q_new':     nuevo Q tras corrección,
              'fp_new':    fp resultante,
              'formula':   str con la fórmula usada,
            }
        """
        import math
        P  = total.get('P', 0.0)
        Q  = total.get('Q', 0.0)
        fp = total.get('fp', 0.0)

        if abs(P) < 1e-12:
            return {'error': 'Potencia activa nula — no se puede corregir FP'}

        omega = 2 * math.pi * frequency

        # Ángulo objetivo
        phi_target = math.acos(min(abs(fp_target), 1.0))
        Q_target   = P * math.tan(phi_target)  # Q que queremos

        # Si el circuito es inductivo (Q>0), compensar con capacitor (Q_c < 0)
        # Si el circuito es capacitivo (Q<0), compensar con inductor (Q_L > 0)
        if Q > 0:
            # Carga inductiva → agregar capacitor en paralelo
            Q_corr = Q - Q_target          # VAR a eliminar (positivo)
            # Para un cap en paralelo: Q_c = V²·ω·C  → C = Q_corr / (V²·ω)
            # Usamos V=1 en pu; el usuario escala según su tensión real
            comp_type  = 'capacitor'
            formula    = 'C = Q_corr / (V² · ω)'
            # Estimado con V_rms = 1 V (normalizado)
            value_norm = Q_corr / omega if omega > 0 else 0.0
            note = ('Multiplica C por (1/Vrms²) con tu tensión real de línea. '
                    'Ej: para Vrms=120V → C_real = C_norm / 120²')
        else:
            # Carga capacitiva → agregar inductor en paralelo
            Q_corr = abs(Q) - abs(Q_target)
            comp_type  = 'inductor'
            formula    = 'L = V² / (Q_corr · ω)'
            value_norm = 1.0 / (omega * Q_corr) if (omega > 0 and Q_corr > 1e-12) else 0.0
            note = ('Multiplica 1/L por (1/Vrms²) con tu tensión real. '
                    'Ej: para Vrms=120V → L_real = L_norm · 120²')

        Q_new  = Q - (Q_corr if Q > 0 else -Q_corr)
        S_new  = math.sqrt(P**2 + Q_new**2)
        fp_new = P / S_new if S_new > 1e-12 else 1.0

        return {
            'type':      comp_type,
            'value':     value_norm,
            'Q_corr':    Q_corr,
            'Q_new':     Q_new,
            'fp_new':    fp_new,
            'formula':   formula,
            'note':      note,
        }

    def solve_transient(self, components: List[Component],
                        t_stop: float, dt: float,
                        method: str = 'trapezoidal') -> Dict:
        """
        Análisis transitorio con integración numérica.

        Args:
            t_stop: tiempo final en segundos
            dt: paso de tiempo
            method: 'euler' o 'trapezoidal' (BE)

        Returns:
            {
              'time': np.ndarray,
              'voltages': {'nodo': np.ndarray, ...},
              'success': bool
            }
        """
        try:
            times = np.arange(0, t_stop + dt, dt)
            node_map, branch_map, size = self._build_maps(components)
            n_nodes = len(node_map)

            # Estado inicial: análisis DC
            dc = self.solve_dc(components)
            V_prev = np.zeros(n_nodes)
            for node, idx in node_map.items():
                V_prev[idx] = dc['voltages'].get(node, 0.0)

            history = {node: np.zeros(len(times)) for node in node_map}

            # Matrices C (capacitancias) y L (inductancias)
            C_mat = self._build_capacitance_matrix(components, node_map, n_nodes)
            # Por ahora inductores tratados como cortocircuito en transitorio

            for ti, t in enumerate(times):
                for node, idx in node_map.items():
                    history[node][ti] = V_prev[idx]

                if ti == len(times) - 1:
                    break

                G_base, I_base = self._build_matrices(components, node_map, branch_map, size)

                if method == 'euler':
                    # Euler hacia adelante: C/dt * (V_next - V_prev) = I_total
                    G_eff = G_base[:n_nodes, :n_nodes] + C_mat / dt
                    I_eff = I_base[:n_nodes] + (C_mat / dt) @ V_prev
                else:
                    # Trapezoidal (más estable)
                    G_eff = G_base[:n_nodes, :n_nodes] + 2 * C_mat / dt
                    I_eff = I_base[:n_nodes] + (2 * C_mat / dt) @ V_prev

                if np.linalg.matrix_rank(G_eff) < n_nodes:
                    V_next = V_prev.copy()
                else:
                    V_next = np.linalg.solve(G_eff, I_eff)

                V_prev = V_next

            return {'success': True, 'time': times,
                    'voltages': history, 'error': None}
        except Exception as e:
            return {'success': False, 'error': str(e),
                    'time': np.array([]), 'voltages': {}}

    # ──────────────────────────────────────────────
    # Métodos internos
    # ──────────────────────────────────────────────

    def _build_maps(self, components: List[Component]) -> Tuple[Dict, Dict, int]:
        """
        Construye:
          node_map:   {nombre_nodo: índice_fila}  (excluye GND=0)
          branch_map: {nombre_componente: índice_fila}  para fuentes de voltaje
        """
        nodes = set()
        for c in components:
            for attr in ['n1', 'n2', 'n_pos', 'n_neg']:
                if hasattr(c, attr):
                    nodes.add(getattr(c, attr))

        # GND (nodo '0' o 'GND') es la referencia → no entra en la matriz
        gnd_names = {'0', 'gnd', 'GND', 'ground'}
        nodes -= gnd_names

        node_map = {n: i for i, n in enumerate(sorted(nodes))}
        n_nodes = len(node_map)

        # Variables de rama: fuentes de voltaje e inductores (en DC)
        branch_components = [c for c in components if c.needs_branch()]
        branch_map = {c.name: n_nodes + i for i, c in enumerate(branch_components)}

        size = n_nodes + len(branch_components)
        return node_map, branch_map, size

    def _build_matrices(self, components, node_map, branch_map, size):
        """Construye G e I para DC."""
        G = np.zeros((size, size))
        I = np.zeros(size)

        branch_components = [c for c in components if c.needs_branch()]
        branch_idx_map = {c.name: branch_map[c.name] for c in branch_components}

        for c in components:
            b_idx = branch_idx_map.get(c.name)
            c.stamp(G, I, node_map, branch_idx=b_idx)

        return G, I

    def _build_matrices_ac(self, components, node_map, branch_map, size, omega):
        """Construye G e I para AC (admitancias complejas).
        En AC los inductores se tratan como admitancias (stamp_ac),
        NO como variables de rama, así que se excluyen del branch_map AC.
        """
        # Reconstruir branch_map excluyendo Inductores (que en AC son admitancias)
        ac_branch = [c for c in components
                     if c.needs_branch() and not isinstance(c, Inductor)]
        n_nodes_ac = len(node_map)
        ac_branch_idx = {c.name: n_nodes_ac + i for i, c in enumerate(ac_branch)}
        size_ac = n_nodes_ac + len(ac_branch)

        G = np.zeros((size_ac, size_ac), dtype=complex)
        I = np.zeros(size_ac, dtype=complex)

        for c in components:
            b_idx = ac_branch_idx.get(c.name)
            if hasattr(c, 'stamp_ac'):
                c.stamp_ac(G, I, node_map, omega, branch_idx=b_idx)
            elif not isinstance(c, Inductor):
                # Resistores y otros lineales sin stamp_ac
                c.stamp(G, I, node_map, branch_idx=b_idx)
            # Inductores sin stamp_ac (no debería ocurrir) → ignorar

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
        voltages['0'] = 0.0  # GND siempre es 0

        branch_currents = {}
        for comp_name, idx in branch_map.items():
            branch_currents[comp_name] = float(x[idx])

        return {
            'success': True,
            'error': None,
            'voltages': voltages,
            'branch_currents': branch_currents,
            'raw': x
        }
