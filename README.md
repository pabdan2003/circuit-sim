# PyNode

**Simulador de circuitos electrónicos open source — analógico, digital y señal mixta.**

PyNode es un entorno de captura de esquemáticos y simulación construido en Python + PyQt6, con un motor MNA (Modified Nodal Analysis) propio que resuelve DC, AC y transitorios sobre el mismo netlist, e instrumentos virtuales (multímetro, osciloscopio de 2 canales y generador de funciones) integrados en el canvas.

![Captura principal](docs/img/screenshot-main.png)

---

## Características

### Motor de simulación

- **Análisis DC** — lineal y no-lineal (Newton-Raphson) con continuación de fuente para circuitos con diodos, LEDs, BJT, MOSFET y op-amps.
- **Análisis AC** — barrido en frecuencia con factorización LU cacheada por punto de frecuencia.
- **Análisis transitorio** — paso adaptativo con control de error LTE y refinamiento automático ante conmutaciones (LED, diodos, comparadores).
- **Motor digital** — simulación a eventos con propagación de niveles lógicos y estados X/Z.
- **Señal mixta** — puentes ADC, DAC, comparador, PWM y sample-and-hold acoplan el dominio analógico con el digital sobre un mismo timestep.

### Componentes soportados

| Categoría | Componentes |
|---|---|
| Pasivos | Resistor, Potenciómetro, Capacitor, Inductor, Impedancia genérica |
| Fuentes | Voltaje DC, Voltaje AC, Corriente, Generador de funciones |
| Semiconductores | Diodo, LED (Vf por color), BJT NPN/PNP, MOSFET N/P, Op-Amp ideal, TL082 dual |
| Conversores | Transformador ideal, Puente de diodos rectificador |
| Digital | AND, OR, NOT, NAND, NOR, XOR, XNOR, BUF, buffer tristate |
| Memoria/secuencial | DFF, JKFF, TFF, SRFF, registros de desplazamiento, contadores binarios |
| Combinacional | MUX, DEMUX, ROM, RAM |
| Bridges A/D | ADC, DAC, Comparador, PWM, Sample-and-Hold |

### Instrumentos virtuales

- **Multímetro** — V DC/AC, corriente, resistencia con sondas posicionables en el esquemático.
- **Osciloscopio** — 2 canales diferenciales, base de tiempo y escala vertical configurable, cursores.
- **Generador de funciones** — senoidal, cuadrada, triangular y diente de sierra con control de amplitud, frecuencia y offset.

### Herramientas auxiliares

- **Analizador de circuito** — detección de puentes implícitos y validación topológica previa a la simulación.
- **Calculadora de resistencias** — código de colores ↔ valor, serie E12/E24/E96.
- **Triángulo de potencia** — P, Q, S y factor de potencia para análisis AC.
- **Temas** — soporte para temas JSON personalizables. Ver [`themes/README.md`](themes/README.md) para crear el tuyo propio.

---

## Instalación

**Requisitos:** Python 3.10 o superior, Windows / Linux / macOS.

```bash
git clone https://github.com/pabdan2003/PyNode.git
cd PyNode
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

---

## Uso rápido

1. Abre PyNode con `python main.py`.
2. Arrastra componentes desde el panel lateral al canvas.
3. Conecta pines haciendo clic en un pin y luego en otro (cables ortogonales).
4. Haz doble clic en un componente para editar su valor.
5. Pulsa **Simular DC** para el punto de operación, o **Simular en vivo** para ver la evolución temporal con instrumentos conectados.

![Demo de simulación en vivo](docs/img/demo-live.gif)

### Ejemplo mínimo (motor desde Python)

```python
from pynode.engine import Resistor, VoltageSource, MNASolver

solver = MNASolver()
circuit = [
    VoltageSource("V1", "in", "0", 10.0),
    Resistor("R1", "in", "out", 1000.0),
    Resistor("R2", "out", "0", 1000.0),
]
result = solver.solve_dc(circuit)
print(result["nodes"]["out"])  # 5.0 V
```

---

## Estructura del proyecto

```
PyNode/
├── main.py                  # Entrypoint (lanza la ventana principal)
├── pynode/                  # Paquete principal
│   ├── circuit_analyzer.py  # Validación topológica y detección de puentes
│   ├── theme_manager.py     # Carga y persistencia de temas
│   ├── engine/
│   │   ├── mna.py           # Solver MNA (DC, AC, transitorio)
│   │   ├── components.py    # Modelos de componentes analógicos
│   │   ├── digital_engine.py# Simulador digital a eventos
│   │   ├── bridges.py       # Conversores analógico ↔ digital
│   │   └── mixed_signal.py  # Coordinador de simulación mixta
│   └── ui/
│       ├── scene.py         # Escena QGraphics y construcción del netlist
│       ├── items/           # ComponentItem, WireItem
│       ├── dialogs/         # Instrumentos y diálogos de configuración
│       └── style.py         # Tema, fuentes y constantes visuales
├── themes/                  # Temas JSON (datos)
├── firmware/                # Firmware de referencia para sonda física
└── tests/                   # Suite pytest (motor + digital + mixto)
```

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

Cada push y pull request ejecuta la suite contra Python 3.10, 3.11 y 3.12 en GitHub Actions (ver `.github/workflows/ci.yml`).

---

## Roadmap

- [x] Migración de tests a `pytest` + CI en GitHub Actions.
- [ ] Diagramas de Bode (magnitud y fase) sobre el análisis AC existente.
- [ ] FFT en el osciloscopio.
- [ ] Exportación a netlist SPICE (interoperabilidad con ngspice / LTspice).
- [ ] Subcircuitos reutilizables (encapsulado de selección).
- [ ] Undo/Redo con `QUndoStack`.
- [ ] Auto-ruteo ortogonal de cables.
- [ ] Sondas persistentes en el esquemático.

---

## Contribuciones

Las contribuciones son bienvenidas. Antes de abrir un PR:

1. Lee [`docs/architecture.md`](docs/architecture.md) para entender la separación entre motores y las convenciones globales (signos, unidades, nombres de nodos).
2. Ejecuta los tests existentes y añade los que correspondan al cambio.
3. Para cambios en el motor, incluye un caso de validación contra una solución analítica conocida.
4. Para cambios visuales, adjunta una captura antes/después.

Mapas rápidos por paquete:

- [`pynode/engine/README.md`](pynode/engine/README.md) — qué hace cada archivo del motor.
- [`pynode/ui/README.md`](pynode/ui/README.md) — qué hace cada archivo de la UI.
- [`themes/README.md`](themes/README.md) — formato JSON de los temas y cómo crear el tuyo.
- [`firmware/README.md`](firmware/README.md) — protocolo binario para la sonda física del osciloscopio.

---

## Licencia

Licencia por definir. Hasta entonces, todos los derechos reservados por los autores.
