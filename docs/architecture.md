# Arquitectura de PyNode

Este documento explica cómo encajan las piezas del proyecto. Está pensado
para alguien que clona el repositorio por primera vez y quiere saber:
qué módulo hace qué, qué convenciones asume, y dónde están los puntos de
extensión.

Para detalles de cada clase o método, ir directo al docstring del código.

---

## 1. Tres motores, una UI

PyNode tiene **tres motores de simulación independientes** que se
comunican por interfaces explícitas. No comparten matrices ni estado
interno. Esto es deliberado: cada uno se puede testear, ajustar o
reemplazar sin tocar los otros dos.

```
       ┌────────────────────────────────────────────────────────────┐
       │                        UI (PyQt6)                          │
       │   main.py + pynode/ui/                                     │
       │   - Canvas, paleta, instrumentos, diálogos                 │
       └─────────┬─────────────────────────┬────────────────────────┘
                 │ build_engine_components │
                 ▼                         ▼
       ┌──────────────────┐      ┌──────────────────┐
       │  MNA Solver      │      │ DigitalSimulator │
       │  (analógico)     │      │  (digital)       │
       │  pynode/engine/  │      │  pynode/engine/  │
       │    mna.py        │      │  digital_engine  │
       │    components.py │      │      .py         │
       └────────┬─────────┘      └──────────┬───────┘
                │                           │
                └────────┐         ┌────────┘
                         ▼         ▼
                ┌────────────────────────┐
                │ MixedSignalInterface   │
                │ + bridges (ADC/DAC/…)  │
                │ pynode/engine/         │
                │   mixed_signal.py      │
                │   bridges.py           │
                └────────────────────────┘
```

- **El motor analógico (MNA)** resuelve ecuaciones algebraicas en
  variables continuas (voltajes nodales y corrientes de rama).
- **El motor digital** es a eventos discretos sobre niveles `0/1/X/Z`.
- **El coordinador mixto** los hace avanzar en lock-step y traduce
  voltajes ↔ niveles lógicos en los puntos de cruce.

---

## 2. Motor MNA (`pynode/engine/mna.py`)

### Idea

Modified Nodal Analysis arma un sistema lineal

```
G · x = I
```

donde `x = [V_nodos | I_ramas]`. Las incógnitas son los voltajes en cada
nodo (excepto GND, que es referencia) y la corriente en cada componente
que requiere variable de rama (fuentes de voltaje, inductores en DC,
op-amps, transformadores).

Cada componente sabe estampar (`stamp`) su contribución en `G` e `I`.
El solver no conoce la física — solo orquesta el ensamblaje y la
resolución del sistema.

### Modos de análisis

| Modo | Método | Qué resuelve |
|---|---|---|
| DC lineal | `solve_dc` | Punto de operación con sólo R, V, I, L (corto), C (abierto) |
| DC no-lineal | `solve_dc_nonlinear` | Igual + diodos, BJT, MOSFET (Newton-Raphson con damping y continuación de fuente) |
| AC | `solve_ac`, `solve_ac_single` | Barrido en frecuencia. Matrices complejas. LU cacheada por frecuencia |
| Transitorio | `solve_transient` | Integración temporal con paso adaptativo y control de error LTE |

### Trucos numéricos

- **`gmin`** (1 nS por defecto): conductancia mínima añadida en cada
  nodo a tierra para que la matriz nunca sea singular. Truco clásico
  de SPICE. Eléctricamente invisible (12 V / 1 GΩ = 12 nA), pero
  evita NaN cuando el circuito tiene nodos flotantes momentáneamente
  (LEDs apagados, diodos en corte, etc.).
- **Diode limiting**: dentro de NR, el cambio de `Vd` entre iteraciones
  se acota con la fórmula de Vlach/SPICE para evitar saltos a la zona
  saturada de la exponencial. Ver `Diode._vd_limit`.
- **Fingerprint del circuito**: la caché de LU se indexa por
  `(_circuit_fingerprint, omega)`. Cambiar el wiper de un potenciómetro
  o la relación de un transformador invalida automáticamente el caché.

### Convenciones

Documentadas en el docstring de [pynode/engine/components.py](../pynode/engine/components.py).
Lo más importante:

- Nodo "0" es siempre GND.
- Unidades SI puras (V, A, Ω, F, H, s, Hz).
- Sign convention de `CurrentSource` es **SPICE** — si tu intuición
  dice "la fuente entrega +I a n_pos", probablemente tengas que invertir
  los nodos. Ver docstring de `CurrentSource` para el ejemplo.

---

## 3. Motor digital (`pynode/engine/digital_engine.py`)

### Idea

Simulación a eventos discretos sobre una priority-queue. Cada compuerta
o flip-flop tiene un retardo de propagación `t_pd`; cuando una entrada
cambia, se programa una evaluación a `t + t_pd`. Cero matrices.

### Modelo de señal

```
LogicLevel = { Z=-2, X=0, L=0, H=1 }
```

`X` y `L` son ambos 0 internamente — se distinguen sólo por contexto
(propagación vs. valor estable). `Z` es tristate.

### Componentes

Combinacionales (AND, OR, NOT, NAND, NOR, XOR, XNOR, BUF, TristateBuffer),
flip-flops (DFF, SRFF, JKFF, TFF), registros de desplazamiento, contadores
binarios, MUX/DEMUX, ROM, RAM. Ver módulo para la lista completa.

### Interfaz mínima

```python
sim = DigitalSimulator()
sim.add(AND("U1", inputs=["A", "B"], output="Y"))
sim.set_input("A", 1, at=0)
sim.set_input("B", 0, at=5e-9)
sim.run(until=50e-9)
sim.final_value("Y")     # estado final
sim.waveform("Y")        # [(t, valor), ...]
```

---

## 4. Coordinación mixta (`pynode/engine/mixed_signal.py` + `bridges.py`)

### Algoritmo

Co-simulación por partición relajada (relaxation-based co-simulation).
En cada ventana `[t, t + dt_analog]`:

1. MNA avanza el dominio analógico con `solve_transient`.
2. ADCs / comparadores leen los voltajes finales y publican señales
   digitales (eventos).
3. DigitalSimulator avanza hasta `t + dt_analog`.
4. DACs / PWM leen el estado digital final y actualizan las fuentes
   del MNA para la siguiente ventana.

No hay iteración entre dominios dentro de una ventana — el `dt_analog`
debe elegirse suficientemente chico como para que esto sea correcto
(regla práctica: 10× menor que el menor retardo digital).

### Puentes disponibles

| Puente | Dirección | Para qué |
|---|---|---|
| `ADC` | analógico → digital | Sample-and-hold + quantización n-bit |
| `DAC` | digital → analógico | Decodificación R-2R conceptual |
| `ComparatorBridge` | analógico → digital (1 bit) | Histéresis configurable |
| `PWMBridge` | digital → analógico | Filtra señal PWM a su nivel DC promedio |
| `SampleAndHold` | analógico → analógico congelado | Para cadenas ADC |

---

## 5. UI (`main.py` + `pynode/ui/`)

### Capas

| Capa | Archivo(s) | Responsabilidad |
|---|---|---|
| Ítems gráficos | `pynode/ui/items/` | `ComponentItem`, `WireItem` — dibujo y picking |
| Escena | `pynode/ui/scene.py` | `CircuitScene` — grid, snapping, conexión de pines, ruteo, construcción del netlist (`build_engine_components_for_item`) |
| Diálogos | `pynode/ui/dialogs/` | Editor de valores, instrumentos (multímetro, osciloscopio, generador), calculadora, ajustes |
| Estilo | `pynode/ui/style.py` | Colores del tema activo, fuentes, constantes geométricas, parseo SI |
| Metadata | `pynode/ui/component_metadata.py` | Etiquetas de pines, prefijos, listas de tipos digitales |
| Ventana | `main.py` | `MainWindow`, toolbar, loop de simulación live, persistencia de circuitos |

### El puente UI → motor

`build_engine_components_for_item` en `pynode/ui/scene.py` traduce un
`ComponentItem` de la escena al objeto correspondiente del motor
(`Resistor`, `VoltageSource`, etc.) usando los pines conectados como
nombres de nodo.

### Live simulation

`MainWindow._tick_live` corre cada `_LIVE_TICK_MS` (50 ms = 20 Hz).
Las constantes `_LIVE_*` documentadas en `main.py` controlan el
trade-off CPU/precisión en tiempo real (tolerancias relajadas, muestras
por período, cota de pasos por tick).

---

## 6. Temas (`pynode/theme_manager.py` + `themes/`)

`ThemeManager` carga colores desde JSON. Hay dos fuentes:

- Built-ins definidos en el propio módulo.
- Archivos `*.json` en `themes/` o `~/.pynode/themes/`.

Cualquier módulo de UI accede a la paleta vía
`from pynode.ui.style import COLORS`. Los colores son **propiedades**
del módulo de estilo, no constantes capturadas — al cambiar de tema
se actualizan transparentemente.

---

## 7. Firmware (`firmware/`)

Independiente del resto: protocolo binario para alimentar el
osciloscopio con muestras reales desde un microcontrolador (Pico,
STM32) por USB-CDC. Especificación completa en `firmware/README.md`.

El receptor vive en `pynode/engine/hw_stream.py` (decoder del frame
`0xAA 0x55 …`) y la integración con el dialogo del osciloscopio en
`pynode/ui/dialogs/hardware_source_dialog.py`.

---

## 8. Tests (`tests/`)

Suite pytest. Dos archivos hoy:

- `test_engine.py` — divisores, paralelos, mallas, filtro RC en frecuencia,
  impedancia compleja, factor de potencia.
- `test_mixed.py` — compuertas, flip-flops, contador con overflow, registro
  de desplazamiento, bus, ADC/DAC/comparador/PWM, integración RC + ADC,
  análisis de timing (setup/hold).

Convención: cada caso compara contra una solución analítica conocida
con tolerancia explícita (`pytest.approx`). Tests que dependen de una
convención del motor (como `test_current_source_parallel_resistors`)
documentan en el docstring de qué convención hablan y por qué el valor
esperado tiene el signo que tiene.

---

## 9. Cómo extender

### Añadir un componente analógico

1. Crear clase en `pynode/engine/components.py` heredando de `Component`.
2. Implementar `stamp` (DC) y opcionalmente `stamp_ac`, `stamp_transient`,
   `stamp_linear` según corresponda.
3. Exportar en `pynode/engine/__init__.py`.
4. Añadir tests en `tests/test_engine.py` con un caso analítico cerrado.
5. Para que aparezca en la UI: definir su entrada en
   `pynode/ui/component_metadata.py` y registrar el dibujo en
   `ComponentItem`.

### Añadir una compuerta digital

1. Subclase de `Gate` en `pynode/engine/digital_engine.py`.
2. Implementar `_evaluate(inputs)`.
3. Exportar en `__init__.py` del paquete.
4. Test en `tests/test_mixed.py`.

### Añadir un instrumento

Diálogo nuevo en `pynode/ui/dialogs/`. Si lee del circuito (multímetro,
scope) consume el resultado del último `solve_*`. Si inyecta señal
(función generator), registra una `VoltageSourceAC` en la lista activa.
