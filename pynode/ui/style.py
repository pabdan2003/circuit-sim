"""
Constantes de estilo, paleta de colores y caches de recursos Qt
compartidos entre `main.py` y los módulos de `ui/`.

Vive aparte para evitar imports circulares: cualquier módulo de UI
puede importar de aquí sin tirar de `main.py`.
"""
from typing import Dict, Tuple, Optional

from PyQt6.QtGui import QFont

from pynode.theme_manager import ThemeManager, DEFAULT_THEME_ID


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


# ══════════════════════════════════════════════════════════════════════════════
# Utilidades de prefijos SI
# parse_si_value / format_si_value  —  compartidas entre el canvas y los
# diálogos de propiedades. No dependen de Qt.
# ══════════════════════════════════════════════════════════════════════════════
import re as _re

# Tabla de conversión prefijo → multiplicador (case-sensitive donde importa)
_SI_PARSE: Dict[str, float] = {
    'f': 1e-15, 'p': 1e-12, 'n': 1e-9,
    'u': 1e-6,  'µ': 1e-6,  'μ': 1e-6,   # micro (varias grafías)
    'm': 1e-3,
    'k': 1e3,   'K': 1e3,
    'M': 1e6,   'G': 1e9,   'T': 1e12,
}

# Patrón: número (decimal / científico) + prefijo SI opcional + unidad (ignorada)
_SI_PAT = _re.compile(
    r'^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)'   # parte numérica
    r'\s*([fpnuµμmkKMGT]?)'                              # prefijo SI (opcional)
    r'[a-zA-ZΩ°µμ²/·]*$'                                 # unidad trailing (ignorada)
)

# Orden de mayor a menor para formatear
_SI_FMT: list = [
    (1e12, 'T'), (1e9, 'G'), (1e6, 'M'), (1e3, 'k'),
    (1.0,  ''),  (1e-3, 'm'), (1e-6, 'μ'), (1e-9, 'n'),
    (1e-12, 'p'), (1e-15, 'f'),
]


def parse_si_value(text: str) -> float:
    """
    Convierte un string con prefijo SI a float.

    Ejemplos
    --------
    '4.7k'   → 4700.0       '100n'  → 1e-7     '1.5M' → 1.5e6
    '22u'    → 22e-6        '1e-9'  → 1e-9     '100'  → 100.0
    '4.7kΩ'  → 4700.0       '100nF' → 1e-7     '-47m' → -0.047
    '1.2 kΩ' → 1200.0       (espacios y unidad ignorados)

    Lanza ValueError si el texto no se puede interpretar.
    """
    # Normalizar: coma decimal → punto, espacios y no-break-spaces fuera
    text = text.strip().replace(',', '.').replace(' ', '').replace(' ', '')
    if not text:
        return 0.0
    # Intento directo: notación científica y enteros / decimales puros
    try:
        return float(text)
    except ValueError:
        pass
    m = _SI_PAT.match(text)
    if m:
        num    = float(m.group(1))
        prefix = m.group(2)
        if prefix:
            mult = _SI_PARSE.get(prefix)
            if mult is not None:
                return num * mult
        return num
    raise ValueError(f"No se puede interpretar como valor SI: {text!r}")


def format_si_value(value: float, unit: str = '') -> str:
    """
    Formatea un float con el prefijo SI más apropiado (4 cifras significativas).

    Ejemplos
    --------
    4700    → '4.7k'     1e-9  → '1n'    1.5e6 → '1.5M'
    22e-6   → '22μ'      0.1   → '100m'  5.0   → '5'
    0       → '0'        -4700 → '-4.7k'
    """
    if value == 0.0:
        return f'0{unit}'
    sign  = '-' if value < 0 else ''
    abs_v = abs(value)
    for mult, prefix in _SI_FMT:
        if abs_v >= mult:
            scaled = abs_v / mult
            s = f'{scaled:.4g}'          # 4 cifras sig., sin ceros innecesarios
            return f'{sign}{s}{prefix}{unit}'
    # Valor más pequeño que femto: notación científica
    return f'{sign}{abs_v:.4g}{unit}'
