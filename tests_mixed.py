"""
Tests del sistema mixto analógico-digital.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
from engine import (
    MNASolver, Resistor, VoltageSource, Capacitor, CurrentSource,
    DigitalSimulator, AND, OR, NOT, DFF, BinaryCounter, MUX, ShiftRegister,
    ADC, DAC, ComparatorBridge, PWMBridge, MixedSignalBus,
    MixedSignalInterface, TimingAnalyzer, Bus, LogicLevel,
)

PASS = "✓"; FAIL = "✗"
results = []

def check(name, cond, detail=''):
    results.append(bool(cond))
    status = PASS if cond else FAIL
    print(f"  {status}  {name}" + (f"  [{detail}]" if detail else ''))

print("=" * 60)
print("  TESTS: Motor Digital + Puentes + Simulación Mixta")
print("=" * 60)

# ─────────────────────────────────────────────────────────────
# TEST D1: Puerta AND básica
# ─────────────────────────────────────────────────────────────
print("\n[D1] Puerta AND(A=1, B=1) → Y=1")
dsim = DigitalSimulator()
dsim.add(AND("U1", inputs=["A","B"], output="Y"))
dsim.set_input("A", 1, at=0)
dsim.set_input("B", 1, at=0)
dsim.run(until=5e-9)
check("Y=1", dsim.final_value("Y") == 1, f"got={dsim.final_value('Y')}")

print("\n[D2] Puerta AND(A=1, B=0) → Y=0")
dsim2 = DigitalSimulator()
dsim2.add(AND("U1", inputs=["A","B"], output="Y"))
dsim2.set_input("A", 1, at=0)
dsim2.set_input("B", 0, at=0)
dsim2.run(until=5e-9)
check("Y=0", dsim2.final_value("Y") == 0, f"got={dsim2.final_value('Y')}")

# ─────────────────────────────────────────────────────────────
# TEST D3: DFF — captura en flanco de subida
# ─────────────────────────────────────────────────────────────
print("\n[D3] DFF: D=1, CLK↑ → Q=1")
dsim3 = DigitalSimulator()
dsim3.add(DFF("FF1", d="D", clk="CLK", q="Q"))
dsim3.set_input("D",   1, at=0)
dsim3.set_input("CLK", 0, at=0)
dsim3.set_input("CLK", 1, at=5e-9)   # flanco subida
dsim3.run(until=20e-9)
check("Q=1 tras CLK↑", dsim3.final_value("Q") == 1, f"got={dsim3.final_value('Q')}")

print("\n[D4] DFF: D=0 antes de CLK↑ → Q=0")
dsim4 = DigitalSimulator()
dsim4.add(DFF("FF1", d="D", clk="CLK", q="Q"))
dsim4.set_input("D",   0, at=0)
dsim4.set_input("CLK", 0, at=0)
dsim4.set_input("CLK", 1, at=5e-9)
dsim4.run(until=20e-9)
check("Q=0 tras CLK↑", dsim4.final_value("Q") == 0)

# ─────────────────────────────────────────────────────────────
# TEST D5: Contador binario 3 bits, 8 flancos → overflow
# ─────────────────────────────────────────────────────────────
print("\n[D5] BinaryCounter 3-bit, 8 pulsos CLK → count=0 (overflow)")
dsim5 = DigitalSimulator()
dsim5.add(BinaryCounter("CNT", n=3, clk="CLK", q_prefix="Q", carry="CARRY"))
# 8 flancos de subida
t = 0
for _ in range(8):
    dsim5.set_input("CLK", 0, at=t)
    dsim5.set_input("CLK", 1, at=t + 1e-9)
    t += 2e-9
dsim5.run(until=t + 1e-9)
cnt = dsim5.components[0]  # BinaryCounter
check("Count=0 tras 8 pulsos (3-bit)", cnt.count == 0, f"count={cnt.count}")

# ─────────────────────────────────────────────────────────────
# TEST D6: ShiftRegister SIPO 4 bits
# ─────────────────────────────────────────────────────────────
print("\n[D6] ShiftRegister 4 bits: secuencia 1,0,1,1 → Q=[1,0,1,1]")
dsim6 = DigitalSimulator()
dsim6.add(ShiftRegister("SR1", n=4, din="DIN", clk="CLK", q_prefix="Q"))
bits_in = [1, 0, 1, 1]
t = 0
for b in bits_in:
    dsim6.set_input("DIN", b, at=t)          # DIN estable antes del flanco
    dsim6.set_input("CLK", 0, at=t + 1e-9)  # CLK baja
    dsim6.set_input("CLK", 1, at=t + 5e-9)  # CLK sube (DIN estable 5ns antes)
    t += 10e-9
dsim6.run(until=t + 5e-9)
# direction='left': reg = reg[1:] + [DIN]  (DIN entra por Q_{n-1})
# Pulse1 DIN=1: [0,0,0,1]  Pulse2 DIN=0: [0,0,1,0]
# Pulse3 DIN=1: [0,1,0,1]  Pulse4 DIN=1: [1,0,1,1]
q = [dsim6.final_value(f"Q{i}") for i in range(4)]
check("ShiftReg salida correcta Q=[1,0,1,1]",
      q == [1, 0, 1, 1],
      f"Q={q}")

# ─────────────────────────────────────────────────────────────
# TEST D7: Bus — lectura de valor entero
# ─────────────────────────────────────────────────────────────
print("\n[D7] Bus 4-bit, write 0b1010=10 → read 10")
dsim7 = DigitalSimulator()
bus = Bus("DATA", ["D0","D1","D2","D3"])
bus.write(dsim7, 10, at=0)   # 0b1010
dsim7.run(until=5e-9)
val = bus.read(dsim7)
check("Bus read=10", val == 10, f"got={val}")

# ─────────────────────────────────────────────────────────────
# TEST M1: ADC — cuantización
# ─────────────────────────────────────────────────────────────
print("\n[M1] ADC 8-bit, Vref=5V, Vin=2.5V → code≈128")
adc = ADC("A1", node="Vout", bits=8, vref=5.0)
v_snap = {"Vout": 2.5, "0": 0.0}
d_snap = {}
out = adc.sample(v_snap, d_snap, 0.0)
check("ADC code≈128", abs(adc.last_code - 128) <= 1, f"code={adc.last_code}")

print("\n[M2] ADC 8-bit, Vref=5V, Vin=0V → code=0")
adc2 = ADC("A2", node="Vout", bits=8, vref=5.0)
out2 = adc2.sample({"Vout": 0.0, "0": 0.0}, {}, 0.0)
check("ADC code=0", adc2.last_code == 0, f"code={adc2.last_code}")

print("\n[M3] ADC 8-bit, Vref=5V, Vin=5.0V (saturación) → code=255")
adc3 = ADC("A3", node="Vout", bits=8, vref=5.0)
out3 = adc3.sample({"Vout": 5.0, "0": 0.0}, {}, 0.0)
check("ADC code=255 (saturado)", adc3.last_code == 255, f"code={adc3.last_code}")

# ─────────────────────────────────────────────────────────────
# TEST M4: DAC — conversión digital→analógico
# ─────────────────────────────────────────────────────────────
print("\n[M4] DAC 8-bit, Vref=5V, code=128 → V≈2.5V")
dac = DAC("D1", bits=8, vref=5.0, in_prefix="D")
# Inyectar código 128 = 0b10000000
d_nets = {f"D{i}": (128 >> i) & 1 for i in range(8)}
v_out = dac.convert(d_nets, 0.0)
check("DAC V≈2.5V", abs(v_out - 2.5) < 0.02, f"V={v_out:.4f}")

print("\n[M5] DAC 8-bit, Vref=5V, code=0 → V=0")
d_nets2 = {f"D{i}": 0 for i in range(8)}
v2 = dac.convert(d_nets2, 1e-9)
check("DAC V=0", abs(v2) < 1e-9, f"V={v2}")

# ─────────────────────────────────────────────────────────────
# TEST M5: ComparatorBridge
# ─────────────────────────────────────────────────────────────
print("\n[M6] Comparador: Vin=3.5V > Vref=2.5V → HIGH")
comp = ComparatorBridge("CMP", node_pos="Vin", vref=2.5, hysteresis=0.1)
out = comp.evaluate({"Vin": 3.5})
check("CMP HIGH", out[comp.output_net] == 1, f"out={out}")

print("\n[M7] Comparador: Vin=1.0V < Vref=2.5V → LOW")
out2 = comp.evaluate({"Vin": 1.0})
check("CMP LOW", out2[comp.output_net] == 0)

# ─────────────────────────────────────────────────────────────
# TEST M8: PWMBridge — duty cycle
# ─────────────────────────────────────────────────────────────
print("\n[M8] PWMBridge: duty=50% → V≈Vmax/2")
pwm = PWMBridge("PWM1", pwm_net="PWM", vmax=3.3, window=1e-3)
# Simular 50% duty: alternar cada 0.5ms
for i in range(10):
    t = i * 0.1e-3
    val = 1 if (i % 2 == 0) else 0
    pwm.update({"PWM": val}, t)
v_pwm = pwm.output_voltage
check("PWM duty≈50%→V≈1.65V", abs(v_pwm - 1.65) < 0.2, f"V={v_pwm:.3f}")

# ─────────────────────────────────────────────────────────────
# TEST I1: Simulación mixta — RC cargando + ADC muestrea
# ─────────────────────────────────────────────────────────────
print("\n[I1] Mixta: RC cargando (τ=1ms) + ADC muestrea al final → code > 0")
mna = MNASolver()
analog_comps = [
    VoltageSource("Vin", "in", "0", 5.0),
    Resistor("R1", "in", "mid", 1000),
    Capacitor("C1", "mid", "0", 1e-6),
]
dsim_i = DigitalSimulator()
adc_i  = ADC("ADC1", node="mid", bits=8, vref=5.0)
iface  = MixedSignalInterface(mna, dsim_i, analog_comps)
iface.add_adc(adc_i)
res = iface.run(t_stop=5e-3, dt_analog=1e-4)
check("Simulación exitosa", res.success, res.error)
if res.success:
    samples = res.adc_samples.get("ADC1", [])
    check("ADC produjo muestras", len(samples) > 0, f"n={len(samples)}")
    if samples:
        last_code = samples[-1][1]
        # RC con τ=1ms, t=5ms → Vc ≈ 5*(1-e^{-5}) ≈ 4.97V → code ≈ 252
        check("Voltaje cargado (code>200)", last_code > 200, f"code={last_code}")

# ─────────────────────────────────────────────────────────────
# TEST I2: TimingAnalyzer — setup violation
# ─────────────────────────────────────────────────────────────
print("\n[I2] TimingAnalyzer: detecta violación de setup")
wf = {
    "CLK": [(0.0, 0), (10e-9, 1), (20e-9, 0), (30e-9, 1)],
    "D":   [(0.0, 0), ( 9e-9, 1)],   # cambia solo 1ns antes del CLK
}
ta = TimingAnalyzer(wf)
violations = ta.check_setup_hold("D", "CLK", t_setup=3e-9, t_hold=1e-9)
check("Detecta violación setup", len(violations) > 0, f"n={len(violations)}")
if violations:
    check("Tipo=setup", violations[0]['type'] == 'setup')

# ─────────────────────────────────────────────────────────────
# RESUMEN
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
passed = sum(results)
total  = len(results)
print(f"  Resultado: {passed}/{total} tests pasados")
if passed == total:
    print("  ✓ Sistema mixto analógico-digital funcionando")
else:
    print("  ✗ Hay errores — revisar los items marcados con ✗")
print("=" * 60)
