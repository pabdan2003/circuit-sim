"""
Tests del motor MNA — DC y AC contra soluciones analíticas conocidas.
"""
import numpy as np
import pytest

from pynode.engine import (
    Resistor, VoltageSource, VoltageSourceAC, CurrentSource,
    Capacitor, Impedance, MNASolver,
)


@pytest.fixture
def solver():
    return MNASolver()


# ──────────────────────────────────────────────
# DC
# ──────────────────────────────────────────────

def test_voltage_divider(solver):
    """Divisor 10V con R1=R2=1kΩ → V_A=5V, I=5mA."""
    comps = [
        VoltageSource("V1", "n1", "0", 10.0),
        Resistor("R1", "n1", "A", 1000.0),
        Resistor("R2", "A", "0", 1000.0),
    ]
    r = solver.solve_dc(comps)
    assert r["success"], r.get("error")
    assert r["voltages"]["n1"] == pytest.approx(10.0, abs=1e-6)
    assert r["voltages"]["A"] == pytest.approx(5.0, abs=1e-6)
    assert abs(r["branch_currents"]["V1"]) == pytest.approx(0.005, abs=1e-6)


def test_two_sources_in_series(solver):
    """5V + 3V sobre R=100Ω → V_n2=8V, I=80mA."""
    comps = [
        VoltageSource("V1", "n1", "0", 5.0),
        VoltageSource("V2", "n2", "n1", 3.0),
        Resistor("R1", "n2", "0", 100.0),
    ]
    r = solver.solve_dc(comps)
    assert r["success"], r.get("error")
    assert r["voltages"]["n2"] == pytest.approx(8.0, abs=1e-6)
    assert abs(r["branch_currents"]["V1"]) == pytest.approx(0.08, abs=1e-6)


def test_current_source_parallel_resistors(solver):
    """Convención SPICE: Is(A, 0, 2A) bombea 2A internamente A→0; externamente
    la corriente sube por R1||R2 desde "0" hacia "A", así que la caída sobre
    Req=2Ω deja V_A = -4V. Ver docstring de CurrentSource."""
    comps = [
        CurrentSource("Is", "A", "0", 2.0),
        Resistor("R1", "A", "0", 4.0),
        Resistor("R2", "A", "0", 4.0),
    ]
    r = solver.solve_dc(comps)
    assert r["success"], r.get("error")
    assert r["voltages"]["A"] == pytest.approx(-4.0, abs=1e-6)


def test_three_node_mesh(solver):
    """V=12V, R1=2Ω, R2=3Ω || R3=6Ω → V_n2=6V."""
    comps = [
        VoltageSource("V1", "n1", "0", 12.0),
        Resistor("R1", "n1", "n2", 2.0),
        Resistor("R2", "n2", "0", 3.0),
        Resistor("R3", "n2", "0", 6.0),
    ]
    r = solver.solve_dc(comps)
    assert r["success"], r.get("error")
    assert r["voltages"]["n1"] == pytest.approx(12.0, abs=1e-6)
    assert r["voltages"]["n2"] == pytest.approx(6.0, abs=1e-6)


# ──────────────────────────────────────────────
# AC
# ──────────────────────────────────────────────

def test_rc_lowpass_at_cutoff(solver):
    """Filtro RC paso-bajo (R=1kΩ, C=1μF): en f_c, |H|=1/√2."""
    R_val = 1000.0
    C_val = 1e-6
    f_c = 1.0 / (2 * np.pi * R_val * C_val)

    comps = [
        VoltageSource("Vin", "in", "0", 1.0),
        Resistor("R1", "in", "out", R_val),
        Capacitor("C1", "out", "0", C_val),
    ]
    r = solver.solve_ac(comps, f_c, f_c, points=1, scale="linear")
    assert r["success"], r.get("error")
    assert "out" in r["voltages"]
    gain = abs(r["voltages"]["out"][0])
    assert gain == pytest.approx(1 / np.sqrt(2), abs=1e-3)


def test_ac_complex_impedance_divider(solver):
    """Divisor con Z=100+j100Ω y R=100Ω → |Vout|=100/|200+j100|."""
    comps = [
        VoltageSourceAC("Vin", "in", "0", amplitude=1.0, frequency=1000.0, mode="rms"),
        Impedance("Z1", "in", "out", 100 + 100j),
        Resistor("Rload", "out", "0", 100.0),
    ]
    r = solver.solve_ac_single(comps, 1000.0)
    assert r["success"], r.get("error")
    assert "out" in r["voltages"]

    v_out = r["voltages"]["out"]
    expected_mag = 100.0 / abs(200 + 100j)
    assert abs(v_out) == pytest.approx(expected_mag, abs=1e-3)

    pz = r["powers"]["Z1"]
    assert pz["P"] == pytest.approx(0.002, abs=1e-4)
    assert pz["Q"] > 0  # carga inductiva
