"""
Constantes de estilo, paleta de colores y caches de recursos Qt
compartidos entre `main.py` y los módulos de `ui/`.

Vive aparte para evitar imports circulares: cualquier módulo de UI
puede importar de aquí sin tirar de `main.py`.
"""
from typing import Dict, Tuple, Optional

from PyQt6.QtGui import QFont

from themes import ThemeManager, DEFAULT_THEME_ID


# ── Constantes geométricas ───────────────────────────────────────────────
GRID_SIZE   = 20
COMP_W      = 60
COMP_H      = 30
PIN_RADIUS  = 4

# Estándar lógico digital usado en TODA la app (no es configurable por UI).
DEFAULT_LOGIC_STANDARD = 'CMOS_5V'


# ── Paleta de colores (mutable, compartida en toda la app) ───────────────
# Diccionario MUTABLE: todos los módulos leen la misma instancia, así
# `apply_theme_to_colors` puede cambiar el tema en vivo sin reasignar.
COLORS: Dict[str, str] = {}

# Manager global de temas — carga la selección guardada y aplica al iniciar.
THEME_MANAGER = ThemeManager()
THEME_MANAGER.refresh()


# ── Cache de recursos QPaint ─────────────────────────────────────────────
# Contador que se incrementa cada vez que cambia COLORS (vía apply_theme_to_colors).
# Sirve para invalidar caches de QPen/QBrush dependientes del tema.
_THEME_REVISION: int = 0


def apply_theme_to_colors(theme_id: str) -> str:
    """
    Reemplaza in-place el contenido de COLORS con la paleta del tema.
    Si el id no existe, cae al tema por defecto.

    Returns:
        El id del tema realmente aplicado.
    """
    global _THEME_REVISION
    meta   = THEME_MANAGER.get_theme_meta(theme_id)
    if meta is None:
        theme_id = DEFAULT_THEME_ID
    palette = THEME_MANAGER.load_theme(theme_id)
    COLORS.clear()
    COLORS.update(palette)
    _THEME_REVISION += 1
    return theme_id


# Cache de QFont — las fuentes en hot paths de paint usan literales constantes
# y nunca se mutan después de crearse, así que se pueden compartir globalmente.
_FONT_CACHE: Dict[Tuple[str, int, int], 'QFont'] = {}


def _qfont(family: str, size: int, weight: Optional[int] = None) -> 'QFont':
    """Devuelve una QFont memoizada por (family, size, weight).

    Uso obligatorio: NO mutar la fuente devuelta. Está diseñado para
    `painter.setFont(...)` / `widget.setFont(...)`, que copian internamente.
    """
    key = (family, size, weight if weight is not None else -1)
    f = _FONT_CACHE.get(key)
    if f is None:
        f = QFont(family, size, weight) if weight is not None else QFont(family, size)
        _FONT_CACHE[key] = f
    return f


def theme_revision() -> int:
    """Lectura segura del contador de revisión del tema (para caches externos)."""
    return _THEME_REVISION


# Carga inicial: lee la selección persistida (o 'dark' por defecto) y
# rellena COLORS antes de que cualquier widget se construya.
_INITIAL_THEME_ID = apply_theme_to_colors(THEME_MANAGER.load_selection())
