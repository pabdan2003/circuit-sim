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
from .components import Component, VoltageSource, Capacitor, Inductor


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

        Returns:
            {
              'voltages': {'nodo': valor, ...},
              'branch_currents': {'componente': valor, ...},
              'success': bool,
              'error': str | None
            }
        """
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
        """Construye G e I para AC (admitancias complejas)."""
        G = np.zeros((size, size), dtype=complex)
        I = np.zeros(size, dtype=complex)

        branch_components = [c for c in components if c.needs_branch()]
        branch_idx_map = {c.name: branch_map[c.name] for c in branch_components}

        for c in components:
            b_idx = branch_idx_map.get(c.name)
            # Componentes reactivos usan stamp_ac si está disponible
            if hasattr(c, 'stamp_ac'):
                c.stamp_ac(G, I, node_map, omega, branch_idx=b_idx)
            else:
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
