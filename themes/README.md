# Temas externos de CircuitSim

Esta carpeta contiene **temas instalables por separado**.  Cada archivo
`*.json` que dejes aquí aparecerá automáticamente en el menú
**Tema** de la app la próxima vez que la abras.

## Cómo crear tu propio tema

1. Copia `solarized_dark.json` a, por ejemplo, `mi_tema.json`.
2. Edita los colores (formato hex `#rrggbb`).
3. Reinicia la app — `Mi Tema` aparecerá en el selector.

## Formato

```json
{
  "name":        "Nombre visible",
  "description": "Descripción opcional",
  "colors": {
    "bg":         "#…",
    "grid":       "#…",
    "grid_line":  "#…",
    "component":  "#…",
    "comp_body":  "#…",
    "comp_sel":   "#…",
    "wire":       "#…",
    "wire_sel":   "#…",
    "node_dot":   "#…",
    "text":       "#…",
    "text_dim":   "#…",
    "pin":        "#…",
    "gnd":        "#…",
    "toolbar":    "#…",
    "panel":      "#…",
    "panel_brd":  "#…",
    "voltage":    "#…",
    "current":    "#…"
  }
}
```

Todas las claves de `colors` son obligatorias; si falta alguna, el
tema se ignora silenciosamente.

## Carpetas alternativas

CircuitSim también lee temas desde `~/.circuit-sim/themes/`
(útil si quieres compartir un tema entre instalaciones).
