# `pynode.ui` — Mapa rápido

Toda la capa gráfica (PyQt6). Para la arquitectura completa ver
[`docs/architecture.md`](../../docs/architecture.md).

## Capas

```
                       MainWindow (main.py)
                       │
                       │  contiene una
                       ▼
       ┌────────────────────────────────────┐
       │  CircuitScene  (ui/scene.py)       │
       │  Maneja grid, snapping, picking,   │
       │  conexiones, ruteo y construcción  │
       │  del netlist para el motor MNA.    │
       └─────────┬───────────────┬──────────┘
                 │               │
        contiene │               │ abre
                 ▼               ▼
       ┌──────────────────┐  ┌──────────────────┐
       │  Items gráficos  │  │  Diálogos        │
       │  (ui/items/)     │  │  (ui/dialogs/)   │
       │  ComponentItem,  │  │  Editor, scope,  │
       │  WireItem        │  │  multímetro, …   │
       └──────────────────┘  └──────────────────┘
```

## Archivos

| Archivo | Qué contiene | Cuándo leerlo |
|---|---|---|
| `scene.py` | `CircuitScene` y `build_engine_components_for_item`: la traducción de la escena a netlist del motor. | Entender cómo se convierten los dibujos en simulación. |
| `style.py` | Paleta de colores activa (`COLORS`), helpers de fuentes, constantes geométricas (`GRID_SIZE`, `COMP_W`, `PIN_RADIUS`), parsers SI (`parse_si_value`, `format_si_value`). | Cambiar look-and-feel global. |
| `component_metadata.py` | Etiquetas de pines por tipo, lista de tipos digitales, etiquetas del campo "valor" en el dialogo de edición. | Añadir un componente nuevo al catálogo. |
| `items/component_item.py` | Dibujo y comportamiento de cada componente en el canvas: forma, pines, rotación, drag. | Cambiar el dibujo de un componente o cómo se interactúa con él. |
| `items/wire_item.py` | Cable ortogonal entre pines, soporta segmentos editables. | Trabajar en auto-routing. |
| `dialogs/component_dialog.py` | Editor genérico de valor/parámetros de un componente. | Añadir un parámetro nuevo. |
| `dialogs/component_picker_dialog.py` | Catálogo desde el cual se arrastran componentes a la escena. | Reorganizar el catálogo. |
| `dialogs/oscilloscope_dialog.py` | Osciloscopio 2-canales: base de tiempo, cursores, trigger, soporte de fuente HW. | Mejorar el scope. |
| `dialogs/multimeter_dialog.py` | Multímetro V/I/Ω. | |
| `dialogs/function_generator_dialog.py` | Generador de senoidal/cuadrada/triangular. | |
| `dialogs/bode_dialog.py` | Diagrama de Bode sobre `solve_ac`. En construcción. | |
| `dialogs/power_triangle_dialog.py` | P, Q, S y factor de potencia desde un análisis AC. | |
| `dialogs/resistor_calc_dialog.py` | Conversor código de colores ↔ valor, series E12/E24/E96. | |
| `dialogs/circuit_analyzer_dialog.py` | UI sobre `pynode.circuit_analyzer` — validación topológica y detección de puentes implícitos. | |
| `dialogs/tl082_unit_dialog.py` | Selector de "unidad A/B" del TL082 dual. | |
| `dialogs/hardware_source_dialog.py` | Configuración del puerto serie para la sonda física. Ver `firmware/README.md`. | |
| `dialogs/settings_dialog.py` | Ajustes generales (tema, estándares lógicos, etc.). | |

## Convenciones

- Cualquier módulo necesita la paleta → importa `from pynode.ui.style import COLORS`.
  `COLORS` es **dinámico**: cambiar de tema actualiza el dict in-place
  para que las re-pintadas siguientes tomen los nuevos colores. No
  capturarlo en variables locales que sobrevivan a un cambio de tema.
- Las constantes geométricas (`GRID_SIZE`, `COMP_W`, etc.) son fijas
  para que el snap-to-grid funcione coherentemente.
- Cualquier valor numérico de usuario pasa por `parse_si_value` antes
  de llegar al motor — el motor SOLO usa unidades SI sin prefijos.
