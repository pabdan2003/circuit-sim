"""
Tests del motor MNA.
Verifica resultados contra soluciones analíticas conocidas.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
from engine import Resistor, VoltageSource, CurrentSource, Capacitor, MNASolver

solver = MNASolver()

PASS = "✓"
FAIL = "✗"
results = []

def check(name, got, expected, tol=1e-6):
    ok = abs(got - expected) < tol
    status = PASS if ok else FAIL
    results.append(ok)
    print(f"  {status}  {name}: got={got:.6f}, expected={expected:.6f}")


print("=" * 55)
print("  TESTS DEL MOTOR MNA")
print("=" * 55)

# ──────────────────────────────────────────────
# TEST 1: Divisor de voltaje simple
# Circuito: 10V → R1=1kΩ → nodo A → R2=1kΩ → GND
# Resultado esperado: V_A = 5V
# ──────────────────────────────────────────────
print("\n[1] Divisor de voltaje (R1=R2=1kΩ, Vs=10V)")
comps = [
    VoltageSource("V1", "n1", "0", 10.0),
    Resistor("R1", "n1", "A", 1000.0),
    Resistor("R2", "A", "0", 1000.0),
]
r = solver.solve_dc(comps)
print(f"    success={r['success']}, error={r['error']}")
check("V_A", r['voltages']['A'], 5.0)
check("V_n1", r['voltages']['n1'], 10.0)
check("I_V1", abs(r['branch_currents']['V1']), 0.005)


# ──────────────────────────────────────────────
# TEST 2: Dos fuentes en serie
# 5V + 3V = 8V a través de R=100Ω → I=80mA
# ──────────────────────────────────────────────
print("\n[2] Dos fuentes en serie (5V + 3V, R=100Ω)")
comps = [
    VoltageSource("V1", "n1", "0",  5.0),
    VoltageSource("V2", "n2", "n1", 3.0),
    Resistor("R1", "n2", "0", 100.0),
]
r = solver.solve_dc(comps)
print(f"    success={r['success']}, error={r['error']}")
check("V_n2", r['voltages']['n2'], 8.0)
check("I (80mA)", abs(r['branch_currents']['V1']), 0.08)


# ──────────────────────────────────────────────
# TEST 3: Fuente de corriente con dos resistencias en paralelo
# Is=2A, R1=4Ω || R2=4Ω → Req=2Ω → V=4V
# ──────────────────────────────────────────────
print("\n[3] Fuente de corriente + R paralelas (Is=2A, R=4Ω||4Ω)")
comps = [
    CurrentSource("Is", "A", "0", 2.0),
    Resistor("R1", "A", "0", 4.0),
    Resistor("R2", "A", "0", 4.0),
]
r = solver.solve_dc(comps)
print(f"    success={r['success']}, error={r['error']}")
check("V_A", r['voltages']['A'], 4.0)


# ──────────────────────────────────────────────
# TEST 4: Circuito con 3 nodos (malla de Kirchhoff)
# V=12V, R1=2Ω, R2=3Ω, R3=6Ω
#
#   n1──R1──n2──R2──n3──R3──GND
#   |                         |
#   V1(+)                    GND
#
# I = 12V / (2+3+6) = 1.0909A... pero R2 y R3 en paralelo:
# Req = 2 + (3||6) = 2 + 2 = 4Ω → I = 3A, V_n2 = 6V
# ──────────────────────────────────────────────
print("\n[4] Circuito 3 nodos (V=12V, R1=2Ω, R2=3Ω||R3=6Ω)")
comps = [
    VoltageSource("V1", "n1", "0", 12.0),
    Resistor("R1", "n1", "n2", 2.0),
    Resistor("R2", "n2", "0", 3.0),
    Resistor("R3", "n2", "0", 6.0),
]
r = solver.solve_dc(comps)
print(f"    success={r['success']}, error={r['error']}")
# Req_parallel = (3*6)/(3+6) = 2Ω; Vtotal = 12V; Itotal = 12/4 = 3A
# V_n2 = 12 - 3*2 = 6V
check("V_n2", r['voltages']['n2'], 6.0)
check("V_n1", r['voltages']['n1'], 12.0)


# ──────────────────────────────────────────────
# TEST 5: AC — Filtro RC paso-bajas
# R=1kΩ, C=1μF → f_c = 1/(2πRC) ≈ 159.15 Hz
# En f_c: |Vout/Vin| = 1/√2 ≈ 0.7071
# ──────────────────────────────────────────────
print("\n[5] AC — Filtro RC paso-bajas (R=1kΩ, C=1μF)")
R_val = 1000.0
C_val = 1e-6
f_c = 1.0 / (2 * np.pi * R_val * C_val)

comps = [
    VoltageSource("Vin", "in", "0", 1.0),
    Resistor("R1", "in", "out", R_val),
    Capacitor("C1", "out", "0", C_val),
]
r = solver.solve_ac(comps, f_c, f_c, points=1, scale='linear')
print(f"    success={r['success']}, f_c={f_c:.2f} Hz")
if r['success'] and 'out' in r['voltages']:
    gain = abs(r['voltages']['out'][0])
    check("|Vout/Vin| en f_c", gain, 1/np.sqrt(2), tol=1e-3)
else:
    print(f"  {FAIL}  AC solver error: {r.get('error')}")
    results.append(False)


# ──────────────────────────────────────────────
# Resumen
# ──────────────────────────────────────────────
print("\n" + "=" * 55)
passed = sum(results)
total = len(results)
print(f"  Resultado: {passed}/{total} tests pasados")
if passed == total:
    print("  ✓ Motor MNA funcionando correctamente")
else:
    print("  ✗ Hay errores en el motor")
print("=" * 55)
