"""
Componentes del circuito.
Cada componente sabe estampar su contribución en la matriz MNA.
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
    Fuente de corriente de n_neg → n_pos (convención: corriente sale de n_pos).
    """
    def __init__(self, name: str, n_pos: str, n_neg: str, current: float):
        super().__init__(name)
        self.n_pos = n_pos
        self.n_neg = n_neg
        self.I_val = current

    def stamp(self, G, I, node_map, branch_idx=None):
        np_ = node_map.get(self.n_pos)
        nn  = node_map.get(self.n_neg)

        if np_ is not None:
            I[np_] += self.I_val
        if nn is not None:
            I[nn] -= self.I_val


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
