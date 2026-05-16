"""
Biblioteca de subcircuitos.

Un subcircuito es un circuito completo (componentes + cables + puertos)
empaquetado en un archivo `*.sub.json` reutilizable.  Se coloca en otra
hoja como un único bloque tipo circuito integrado (componente 'SUBCKT').

Carpetas de búsqueda (en orden):
    1. <directorio_del_programa>/subcircuits/*.sub.json
    2. ~/.pynode/subcircuits/*.sub.json

Formato del archivo `.sub.json`:

    {
      "name": "MiAmp",
      "ports": [ {"name": "IN", "dir": "in"}, {"name": "OUT", "dir": "out"} ],
      "components": [ <entradas serializadas, mismo esquema que el .csin> ],
      "wires":      [ {"x1":..,"y1":..,"x2":..,"y2":..} ],
      "port_nets":  { "IN": "net_x", "OUT": "0" },
      "internal_nets": { "R1__p1": "net_x", ... },
      "appearance": { "label": "MiAmp", "body_color": "", "text_color": "",
                      "pins": [ {"name":"IN","side":"left"},
                                {"name":"OUT","side":"right"} ] }
    }

`internal_nets` y `port_nets` se resuelven UNA vez al crear el subcircuito
(corriendo extract_netlist sobre la hoja), de modo que el aplanado en la
simulación no necesita re-derivar la geometría.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional


class SubcircuitManager:
    """Localiza, carga y guarda definiciones de subcircuitos."""

    EXT = '.sub.json'

    def __init__(self, app_dir: Optional[str] = None):
        if app_dir is None:
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.app_dir = app_dir
        self.user_dir = os.path.join(os.path.expanduser('~'), '.pynode')
        self._cache: Dict[str, Dict] = {}   # name -> definition dict
        self._paths: Dict[str, str] = {}    # name -> source path

    # ── Carpetas de búsqueda ────────────────────────────────────────────────
    def search_paths(self) -> List[str]:
        return [
            os.path.join(self.app_dir, 'subcircuits'),
            os.path.join(self.user_dir, 'subcircuits'),
        ]

    def user_subcircuits_dir(self) -> str:
        path = os.path.join(self.user_dir, 'subcircuits')
        os.makedirs(path, exist_ok=True)
        return path

    # ── Descubrimiento ──────────────────────────────────────────────────────
    def refresh(self) -> None:
        self._cache.clear()
        self._paths.clear()
        for folder in self.search_paths():
            if not os.path.isdir(folder):
                continue
            for fn in sorted(os.listdir(folder)):
                if not fn.lower().endswith(self.EXT):
                    continue
                path = os.path.join(folder, fn)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        raw = json.load(f)
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get('name') or os.path.splitext(fn)[0])
                if name in self._cache:
                    continue  # gana el primero encontrado
                self._cache[name] = raw
                self._paths[name] = path

    # ── Consulta ────────────────────────────────────────────────────────────
    def list_subcircuits(self) -> List[Dict]:
        out = []
        for name, d in self._cache.items():
            out.append({
                'name': name,
                'ports': d.get('ports', []),
                'source': self._paths.get(name, '<externo>'),
            })
        return out

    def get(self, name: str) -> Optional[Dict]:
        return self._cache.get(name)

    def exists(self, name: str) -> bool:
        return name in self._cache

    # ── Guardado ────────────────────────────────────────────────────────────
    @staticmethod
    def _safe_filename(name: str) -> str:
        base = re.sub(r'[^A-Za-z0-9_\-]+', '_', name).strip('_') or 'subckt'
        return base + SubcircuitManager.EXT

    def save(self, definition: Dict, overwrite: bool = True) -> Optional[str]:
        """Guarda una definición en la carpeta de usuario.

        Devuelve la ruta del archivo o None si falla / ya existe y
        overwrite=False.
        """
        name = str(definition.get('name', '')).strip()
        if not name:
            return None
        folder = self.user_subcircuits_dir()
        path = os.path.join(folder, self._safe_filename(name))
        if os.path.exists(path) and not overwrite:
            return None
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(definition, f, indent=2, ensure_ascii=False)
        except OSError:
            return None
        # Actualizar caché
        self._cache[name] = definition
        self._paths[name] = path
        return path


#: Singleton compartido por la app.
SUBCIRCUIT_MANAGER = SubcircuitManager()
SUBCIRCUIT_MANAGER.refresh()
