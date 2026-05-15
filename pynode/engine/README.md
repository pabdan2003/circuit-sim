# `pynode.engine` — Mapa rápido

Tres motores independientes de simulación, sin estado compartido entre
ellos. Para la visión arquitectónica completa ver
[`docs/architecture.md`](../../docs/architecture.md).

| Archivo | Qué contiene | Cuándo leerlo |
|---|---|---|
| `components.py` | Modelos físicos: R, V, I, C, L, Diodo, BJT, MOSFET, Op-Amp, TL082, Transformer, Bridge rectifier, Potenciómetro, Impedancia. Cada uno sabe estampar su contribución en la matriz MNA. | Añadir un componente analógico nuevo. Entender las convenciones globales (docstring del módulo). |
| `mna.py` | `MNASolver` — el corazón analógico. Resuelve DC (lineal y Newton-Raphson), AC (barrido con LU cacheada) y transitorio (paso adaptativo con control LTE). | Tocar el solver, debuggear convergencia, entender los trucos numéricos (`gmin`, diode limiting, fingerprint del circuito). |
| `digital_engine.py` | `DigitalSimulator` con compuertas, flip-flops, registros, contadores, MUX/DEMUX, ROM, RAM, buses. Simulación a eventos discretos (priority queue). | Añadir un componente digital o modificar reglas de propagación. |
| `bridges.py` | Adaptadores analógico ↔ digital: `ADC`, `DAC`, `ComparatorBridge`, `PWMBridge`, `SampleAndHold`, `MixedSignalBus`. | Conectar nuevos dominios mixtos o cambiar la cuantización. |
| `mixed_signal.py` | `MixedSignalInterface` — orquesta MNA + digital + puentes con co-simulación por partición relajada. `TimingAnalyzer` analiza setup/hold post-simulación. | Co-simular circuitos mixtos, analizar timing. |
| `hw_stream.py` | Decoder del protocolo binario del firmware externo (sonda física de osciloscopio). | Trabajar con el hardware real — ver `firmware/README.md`. |

## Punto de entrada público

`pynode/engine/__init__.py` reexporta todo lo que se usa desde fuera del
paquete. Si añadís una clase pública, añadila también ahí.

## Reglas de oro

1. **Una clase de componente = un archivo de tests con un caso analítico
   cerrado.** Sin caso de referencia (ej: `V_out = V_in · R2/(R1+R2)`)
   no hay forma de detectar regresiones en el motor.
2. **Cambiar una convención (signos, unidades, nombres de nodos) es
   romper la API pública.** Documentarlo en el docstring del módulo Y
   en `docs/architecture.md`.
3. **Antes de añadir un parámetro nuevo a un componente existente**,
   chequear que la UI (`pynode/ui/component_metadata.py` + dialogo)
   sepa pedirlo. Un parámetro que nadie puede configurar es código
   muerto.
