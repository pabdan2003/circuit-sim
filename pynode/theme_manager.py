"""
Sistema de temas.

Cada tema es un diccionario con las mismas claves que el COLORS
original de main.py.  Hay dos tipos de temas:

  • Built-in        — definidos en BUILTIN_THEMES (este archivo).
  • Externos        — archivos *.json en alguna de las carpetas
                      de búsqueda (ver `theme_search_paths`).

Para crear un tema instalable por separado basta con:

  1. Copiar uno de los temas built-in a un archivo JSON, por ejemplo
     `mi_tema.json`, y modificar los colores que quieras.
  2. Colocarlo en una de las carpetas de búsqueda:
       - `<directorio_del_programa>/themes/`
       - `~/.pynode/themes/`
  3. Reiniciar la app — el tema aparecerá en el selector "Tema".

Uso desde código:

    from pynode.theme_manager import ThemeManager
    tm = ThemeManager()
    tm.refresh()                        # descubre temas externos
    tm.list_themes()                    # [{'id', 'name', 'source'}, …]
    colors = tm.load_theme('light')
    tm.save_selection('dark')           # persistencia en config.json
    sel = tm.load_selection()           # 'dark' por defecto
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Claves de color que cada tema DEBE proveer
# ──────────────────────────────────────────────────────────────────────────────
REQUIRED_KEYS = (
    'bg', 'grid', 'grid_line',
    'component', 'comp_body', 'comp_sel',
    'wire', 'wire_sel', 'node_dot',
    'text', 'text_dim', 'pin', 'gnd',
    'toolbar', 'panel', 'panel_brd',
    'voltage', 'current',
)


# ──────────────────────────────────────────────────────────────────────────────
# Temas integrados
# ──────────────────────────────────────────────────────────────────────────────

#: Tema oscuro original — paleta azul-medianoche con acentos rojo/verde.
THEME_DARK: Dict[str, str] = {
    'bg':         '#1a1a2e',
    'grid':       '#16213e',
    'grid_line':  '#0f3460',
    'component':  '#e94560',
    'comp_body':  '#16213e',
    'comp_sel':   '#f5a623',
    'wire':       '#4ecca3',
    'wire_sel':   '#f5a623',
    'node_dot':   '#4ecca3',
    'text':       '#e0e0e0',
    'text_dim':   '#7f8c8d',
    'pin':        '#4ecca3',
    'gnd':        '#a0a0ff',
    'toolbar':    '#0f3460',
    'panel':      '#16213e',
    'panel_brd':  '#0f3460',
    'voltage':    '#f5a623',
    'current':    '#4ecca3',
}

#: Tema Python  — azul y amarillo característicos del logo de Python.
THEME_PYTHON: Dict[str, str] = {
    'bg':         '#1e2a3a',   # azul medianoche Python
    'grid':       '#26344a',
    'grid_line':  '#306998',   # azul Python oscuro
    'component':  '#ffd43b',   # amarillo Python
    'comp_body':  '#26344a',
    'comp_sel':   '#ffe873',   # amarillo claro para selección
    'wire':       '#4b8bbe',   # azul Python claro
    'wire_sel':   '#ffd43b',
    'node_dot':   '#4b8bbe',
    'text':       '#e8eef5',
    'text_dim':   '#7a8aa0',
    'pin':        '#ffd43b',
    'gnd':        '#4b8bbe',
    'toolbar':    '#306998',   # azul Python
    'panel':      '#26344a',
    'panel_brd':  '#306998',
    'voltage':    '#ffd43b',
    'current':    '#4b8bbe',
}

#: Tema claro  — fondo blanco, líneas oscuras.
THEME_LIGHT: Dict[str, str] = {
    'bg':         '#ffffff',   # papel blanco
    'grid':       '#fafafa',
    'grid_line':  '#d0d0d0',   # cuadrícula gris suave
    'component':  '#1a4f8a',   # cuerpo de componente azul
    'comp_body':  '#ffffff',
    'comp_sel':   '#e67e22',   # naranja para selección
    'wire':       '#0a4d2e',   # cables verde oscuro
    'wire_sel':   '#e67e22',
    'node_dot':   '#0a4d2e',
    'text':       '#202020',
    'text_dim':   '#707070',
    'pin':        '#1a4f8a',
    'gnd':        '#404040',
    'toolbar':    '#ececec',
    'panel':      '#f5f5f5',
    'panel_brd':  '#b0b0b0',
    'voltage':    '#c0392b',
    'current':    '#1a4f8a',
}


BUILTIN_THEMES: Dict[str, Dict] = {
    'python': {
        'name':        'Python (predeterminado)',
        'description': 'Azul y amarillo característicos de Python.',
        'colors':      THEME_PYTHON,
    },
    'dark': {
        'name':        'Oscuro',
        'description': 'Paleta azul-medianoche, contraste alto.',
        'colors':      THEME_DARK,
    },
    'light': {
        'name':        'Claro',
        'description': 'Fondo blanco con líneas oscuras',
        'colors':      THEME_LIGHT,
    },
}

#: Id del tema que se usa cuando no hay configuración previa.
DEFAULT_THEME_ID = 'python'


# ──────────────────────────────────────────────────────────────────────────────
# Utilidades de validación
# ──────────────────────────────────────────────────────────────────────────────

def _validate_colors(colors: Dict[str, str]) -> List[str]:
    """Devuelve la lista de claves faltantes; lista vacía si está bien."""
    return [k for k in REQUIRED_KEYS if k not in colors]


def _coerce_theme_dict(d: Dict, source: str) -> Optional[Dict]:
    """
    Convierte un dict cargado de JSON en una entrada compatible con BUILTIN_THEMES.

    Acepta dos formatos:
      Formato A (recomendado):
        { "name": "...", "description": "...", "colors": { ... } }
      Formato B (compacto):
        { "key1": "#aabbcc", "key2": "#...", ... }
        — se asume que TODO el dict es el mapa de colores y se infiere el nombre.
    """
    if not isinstance(d, dict):
        return None

    if 'colors' in d and isinstance(d['colors'], dict):
        colors      = {k: str(v) for k, v in d['colors'].items()}
        name        = str(d.get('name', '<sin nombre>'))
        description = str(d.get('description', ''))
    else:
        colors      = {k: str(v) for k, v in d.items() if isinstance(v, str)}
        name        = '<sin nombre>'
        description = ''

    if _validate_colors(colors):
        # Faltan claves obligatorias — descartamos el tema.
        return None

    return {
        'name':        name,
        'description': description,
        'colors':      colors,
        'source':      source,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Administrador de temas
# ──────────────────────────────────────────────────────────────────────────────

class ThemeManager:
    """
    Localiza, carga y guarda temas de la app.

    Búsqueda de temas externos en (en orden):
        1. <directorio_del_programa>/themes/*.json
        2. ~/.pynode/themes/*.json
    Si dos temas externos comparten id (nombre del archivo sin extensión)
    gana el primero encontrado.
    """

    CONFIG_FILENAME = 'config.json'

    def __init__(self, app_dir: Optional[str] = None):
        if app_dir is None:
            # theme_manager.py vive dentro del paquete `pynode/`. La carpeta
            # `themes/` está al lado del paquete, en la raíz del proyecto,
            # así que subimos un nivel.
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.app_dir = app_dir

        self.user_dir   = os.path.join(os.path.expanduser('~'), '.pynode')
        self.config_path = os.path.join(self.user_dir, self.CONFIG_FILENAME)

        # Caché de temas externos {id: theme_dict}
        self._external: Dict[str, Dict] = {}

    # ── Búsqueda de carpetas ────────────────────────────────────────────────
    def theme_search_paths(self) -> List[str]:
        return [
            os.path.join(self.app_dir,  'themes'),
            os.path.join(self.user_dir, 'themes'),
        ]

    # ── Descubrimiento ──────────────────────────────────────────────────────
    def refresh(self) -> None:
        """Re-escanea las carpetas de temas externos."""
        self._external.clear()
        for folder in self.theme_search_paths():
            if not os.path.isdir(folder):
                continue
            for fn in sorted(os.listdir(folder)):
                if not fn.lower().endswith('.json'):
                    continue
                tid = os.path.splitext(fn)[0]
                if tid in BUILTIN_THEMES or tid in self._external:
                    # No reemplaza built-in ni el primero ya encontrado
                    continue
                path = os.path.join(folder, fn)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        raw = json.load(f)
                except (OSError, json.JSONDecodeError):
                    continue
                t = _coerce_theme_dict(raw, source=path)
                if t is not None:
                    self._external[tid] = t

    # ── Listado completo ────────────────────────────────────────────────────
    def list_themes(self) -> List[Dict]:
        """
        Lista combinada de built-in + externos.

        Returns:
            Lista de dicts: {'id', 'name', 'description', 'source'}.
            'source' = 'builtin' o ruta absoluta del archivo JSON.
        """
        result = []
        for tid, t in BUILTIN_THEMES.items():
            result.append({
                'id':          tid,
                'name':        t['name'],
                'description': t.get('description', ''),
                'source':      'builtin',
            })
        for tid, t in self._external.items():
            result.append({
                'id':          tid,
                'name':        t['name'],
                'description': t.get('description', ''),
                'source':      t.get('source', '<externo>'),
            })
        return result

    # ── Carga de un tema concreto ───────────────────────────────────────────
    def load_theme(self, theme_id: str) -> Dict[str, str]:
        """
        Devuelve el dict de colores del tema. Si el id no existe,
        devuelve el tema por defecto.
        """
        if theme_id in BUILTIN_THEMES:
            return dict(BUILTIN_THEMES[theme_id]['colors'])
        if theme_id in self._external:
            return dict(self._external[theme_id]['colors'])
        return dict(BUILTIN_THEMES[DEFAULT_THEME_ID]['colors'])

    def get_theme_meta(self, theme_id: str) -> Optional[Dict]:
        if theme_id in BUILTIN_THEMES:
            t = BUILTIN_THEMES[theme_id]
            return {'id': theme_id, 'name': t['name'],
                    'description': t.get('description', ''),
                    'source': 'builtin'}
        if theme_id in self._external:
            t = self._external[theme_id]
            return {'id': theme_id, 'name': t['name'],
                    'description': t.get('description', ''),
                    'source': t.get('source', '<externo>')}
        return None

    # ── Persistencia ────────────────────────────────────────────────────────
    def load_selection(self) -> str:
        """Devuelve el id del tema guardado o el default."""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            tid = cfg.get('theme', DEFAULT_THEME_ID)
            if isinstance(tid, str):
                return tid
        except (OSError, json.JSONDecodeError):
            pass
        return DEFAULT_THEME_ID

    def save_selection(self, theme_id: str) -> bool:
        """Persiste la selección. Devuelve True si se guardó OK."""
        try:
            os.makedirs(self.user_dir, exist_ok=True)
            cfg = {}
            if os.path.exists(self.config_path):
                try:
                    with open(self.config_path, 'r', encoding='utf-8') as f:
                        cfg = json.load(f)
                    if not isinstance(cfg, dict):
                        cfg = {}
                except (OSError, json.JSONDecodeError):
                    cfg = {}
            cfg['theme'] = theme_id
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            return True
        except OSError:
            return False

    # ── Helpers para el usuario ─────────────────────────────────────────────
    def ensure_user_themes_dir(self) -> str:
        """Crea ~/.pynode/themes/ si no existe y la devuelve."""
        path = os.path.join(self.user_dir, 'themes')
        os.makedirs(path, exist_ok=True)
        return path

    def export_theme_template(self, theme_id: str, dest_path: str) -> bool:
        """
        Exporta un tema (built-in o externo) a un archivo JSON
        — útil para que el usuario lo use de plantilla.
        """
        meta = self.get_theme_meta(theme_id)
        if meta is None:
            return False
        colors = self.load_theme(theme_id)
        payload = {
            'name':        meta['name'] + ' (copia)',
            'description': meta.get('description', ''),
            'colors':      colors,
        }
        try:
            with open(dest_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            return True
        except OSError:
            return False
