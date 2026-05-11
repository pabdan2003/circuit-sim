"""
Diálogo del Analizador de Circuitos Digitales (Quine-McCluskey, Karnaugh,
auto-construcción de circuitos a partir de tabla de verdad).

Extraído de main.py para reducir el tamaño del archivo monolítico.
"""
import re
import html
from itertools import combinations
from typing import List, Dict, Tuple, Optional

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QTableWidget, QTableWidgetItem, QListWidget, QLineEdit, QPushButton,
    QLabel, QComboBox, QCheckBox, QGroupBox, QFormLayout, QTextEdit,
    QFileDialog, QMessageBox, QDialogButtonBox, QStyledItemDelegate,
)
from PyQt6.QtGui import QColor, QPen, QBrush, QFont
from PyQt6.QtCore import Qt, QPointF

from ui.style import COLORS, GRID_SIZE, _qfont
from ui.items.component_item import ComponentItem
from ui.items.wire_item import WireItem



# ══════════════════════════════════════════════════════════════
# ANALIZADOR DE CIRCUITOS DIGITALES
# ══════════════════════════════════════════════════════════════
# Quine-McCluskey con soporte de don't cares: implementación independiente
# y ligera (sin dependencias externas).  Se utiliza desde el diálogo
# CircuitAnalyzerDialog para reducir tablas de verdad a SOP / POS mínimas.

def _qm_combine(a: str, b: str):
    """Combina dos cubos si difieren en exactamente 1 bit; retorna el cubo
    fusionado (con '-' en la posición que difería) o None."""
    if len(a) != len(b):
        return None
    diff = 0
    out = []
    for x, y in zip(a, b):
        if x != y:
            diff += 1
            if diff > 1:
                return None
            out.append('-')
        else:
            out.append(x)
    return ''.join(out) if diff == 1 else None


def _qm_prime_implicants(terms, num_vars: int):
    """Quine-McCluskey: encuentra todos los implicantes primos del conjunto
    de términos (mintérminos + don't cares).  Cada cubo es un string binario
    de longitud num_vars con caracteres en {'0','1','-'}."""
    if not terms:
        return []
    cubes = {format(t, f'0{num_vars}b') for t in terms}
    primes = set()
    while cubes:
        groups = {}
        for c in cubes:
            ones = c.replace('-', '').count('1')
            groups.setdefault(ones, []).append(c)
        used = set()
        next_cubes = set()
        keys = sorted(groups)
        for i in range(len(keys) - 1):
            for a in groups[keys[i]]:
                for b in groups[keys[i + 1]]:
                    combined = _qm_combine(a, b)
                    if combined is not None:
                        used.add(a); used.add(b)
                        next_cubes.add(combined)
        for c in cubes:
            if c not in used:
                primes.add(c)
        cubes = next_cubes
    return list(primes)


def _qm_covers(prime: str, term: int, num_vars: int) -> bool:
    bin_t = format(term, f'0{num_vars}b')
    return all(p == '-' or p == m for p, m in zip(prime, bin_t))


def _qm_minimum_cover(primes, must_cover, num_vars: int):
    """Cobertura mínima de must_cover usando los primes disponibles.
    Algoritmo: implicantes esenciales primero, luego greedy para el resto."""
    if not must_cover:
        return []
    coverage = {p: {m for m in must_cover if _qm_covers(p, m, num_vars)}
                for p in primes}
    selected, remaining = set(), set(must_cover)
    while remaining:
        # Esenciales: mintérminos cubiertos por solo un implicante primo
        essential_added = False
        for m in list(remaining):
            covers_m = [p for p in primes if m in coverage[p]]
            if len(covers_m) == 1:
                p = covers_m[0]
                if p not in selected:
                    selected.add(p)
                    essential_added = True
                remaining -= coverage[p]
        if remaining and not essential_added:
            # Greedy: el prime que cubra más mintérminos restantes
            best, best_n = None, -1
            for p in primes:
                n = len(coverage[p] & remaining)
                if n > best_n:
                    best, best_n = p, n
            if best is None or best_n <= 0:
                break
            selected.add(best)
            remaining -= coverage[best]
    return list(selected)


def _qm_literal_count(prime: str) -> int:
    return sum(1 for bit in prime if bit != '-')


def _qm_minimum_cover_exact(primes, must_cover, num_vars: int):
    """Exact cover used by the digital analyzer for stable SOP/POS output."""
    if not must_cover:
        return []

    primes = sorted(set(primes), key=lambda p: (_qm_literal_count(p), p))
    coverage = {
        p: {m for m in must_cover if _qm_covers(p, m, num_vars)}
        for p in primes
    }
    selected, remaining = set(), set(must_cover)

    while remaining:
        essential_added = False
        for m in list(remaining):
            covers_m = [p for p in primes if m in coverage[p]]
            if len(covers_m) == 1:
                p = covers_m[0]
                selected.add(p)
                essential_added = True
                remaining -= coverage[p]
        if not essential_added:
            break

    if remaining:
        candidates = [p for p in primes if coverage[p] & remaining and p not in selected]
        best_combo = None
        best_score = None
        for r in range(1, len(candidates) + 1):
            found_at_size = False
            for combo in combinations(candidates, r):
                covered = set()
                for p in combo:
                    covered |= coverage[p]
                if not remaining <= covered:
                    continue
                found_at_size = True
                score = (r, sum(_qm_literal_count(p) for p in combo), tuple(combo))
                if best_score is None or score < best_score:
                    best_combo = combo
                    best_score = score
            if found_at_size:
                break
        if best_combo:
            selected.update(best_combo)

    return sorted(selected, key=lambda p: (_qm_literal_count(p), p))


def _prime_to_sop_term(prime: str, var_names) -> str:
    parts = []
    for bit, name in zip(prime, var_names):
        if bit == '0':
            parts.append(f"{name}'")
        elif bit == '1':
            parts.append(name)
    return ' · '.join(parts) if parts else '1'


def _prime_to_pos_term(prime: str, var_names) -> str:
    """Para POS, los implicantes representan los maxtérminos del original;
    la conversión invierte cada literal porque corresponde al complemento."""
    parts = []
    for bit, name in zip(prime, var_names):
        if bit == '0':
            parts.append(name)
        elif bit == '1':
            parts.append(f"{name}'")
    if not parts:
        return '0'
    return '(' + ' + '.join(parts) + ')'


def _sop_cover(minterms, dont_cares, num_vars: int):
    if not minterms:
        return []
    if len(set(minterms) | set(dont_cares)) >= 2 ** num_vars and len(minterms) == 2 ** num_vars:
        return ['-' * num_vars]
    primes = _qm_prime_implicants(list(set(minterms) | set(dont_cares)), num_vars)
    return _qm_minimum_cover_exact(primes, list(minterms), num_vars)


def _pos_cover(minterms, dont_cares, num_vars: int):
    all_idx = set(range(2 ** num_vars))
    maxterms = sorted(all_idx - set(minterms) - set(dont_cares))
    if not maxterms:
        return []
    if len(maxterms) == 2 ** num_vars:
        return ['-' * num_vars]
    primes = _qm_prime_implicants(list(set(maxterms) | set(dont_cares)), num_vars)
    return _qm_minimum_cover_exact(primes, list(maxterms), num_vars)


def simplify_sop(minterms, dont_cares, var_names):
    n = len(var_names)
    cover = _sop_cover(minterms, dont_cares, n)
    if not cover:
        return '0'
    if cover == ['-' * n]:
        return '1'
    return ' + '.join(_prime_to_sop_term(p, var_names) for p in cover)


def simplify_pos(minterms, dont_cares, var_names):
    n = len(var_names)
    cover = _pos_cover(minterms, dont_cares, n)
    if not cover:
        return '1'
    if cover == ['-' * n]:
        return '0'
    return ' * '.join(_prime_to_pos_term(p, var_names) for p in cover)


NOTATION_LABELS = {
    'math_bar': "Matematica (barrita)",
    'math_prime': "Matematica alternativa (')",
    'logic_words': "Logica",
    'logic_symbols': "Logica alternativa",
    'program_bool': "Programando con booleanos",
    'program_bits': "Programando con bits",
}


def _overline(text: str, rich: bool = False) -> str:
    if rich:
        return f'<span style="text-decoration: overline;">{html.escape(text)}</span>'
    return f"bar({text})"


def _notation_ops(notation: str, form: str, rich: bool = False):
    if notation == 'logic_words':
        return {
            'not': lambda name: f"¬{name}",
            'and': ' ∧ ',
            'or':  ' ∨ ',
            'sop_join': ' ∨ ',
            'pos_join': ' ∧ ',
        }
    if notation == 'logic_symbols':
        return {
            'not': lambda name: f"~{name}",
            'and': ' ∧ ',
            'or':  ' ∨ ',
            'sop_join': ' ∨ ',
            'pos_join': ' ∧ ',
        }
    if notation == 'program_bool':
        return {
            'not': lambda name: f"!{name}",
            'and': ' && ',
            'or':  ' | ',
            'sop_join': ' | ',
            'pos_join': ' && ',
        }
    if notation == 'program_bits':
        return {
            'not': lambda name: f"~{name}",
            'and': ' & ',
            'or':  ' | ',
            'sop_join': ' | ',
            'pos_join': ' & ',
        }
    if notation == 'math_bar':
        return {
            'not': lambda name: _overline(name, rich=rich),
            'and': ' · ',
            'or':  ' + ',
            'sop_join': ' + ',
            'pos_join': ' · ',
        }
    return {
        'not': lambda name: f"{name}'",
        'and': ' · ',
        'or':  ' + ',
        'sop_join': ' + ',
        'pos_join': ' · ',
    }


def _format_sop_cover(cover, var_names, notation: str, rich: bool = False) -> str:
    n = len(var_names)
    if not cover:
        return '0'
    if cover == ['-' * n]:
        return '1'
    ops = _notation_ops(notation, 'sop', rich=rich)
    terms = []
    for prime in cover:
        lits = []
        for bit, name in zip(prime, var_names):
            if bit == '0':
                lits.append(ops['not'](name))
            elif bit == '1':
                lits.append(html.escape(name) if rich else name)
        terms.append(ops['and'].join(lits) if lits else '1')
    return ops['sop_join'].join(terms)


def _format_pos_cover(cover, var_names, notation: str, rich: bool = False) -> str:
    n = len(var_names)
    if not cover:
        return '1'
    if cover == ['-' * n]:
        return '0'
    ops = _notation_ops(notation, 'pos', rich=rich)
    clauses = []
    for prime in cover:
        lits = []
        for bit, name in zip(prime, var_names):
            if bit == '0':
                lits.append(html.escape(name) if rich else name)
            elif bit == '1':
                lits.append(ops['not'](name))
        clauses.append('(' + ops['or'].join(lits) + ')' if lits else '0')
    return ops['pos_join'].join(clauses)


def _gray_codes(bits: int) -> list:
    if bits <= 0:
        return ['']
    codes = ['0', '1']
    for _ in range(1, bits):
        codes = ['0' + c for c in codes] + ['1' + c for c in reversed(codes)]
    return codes


KMAP_GROUPS_ROLE = int(Qt.ItemDataRole.UserRole) + 21


class TruthTableSeparatorDelegate(QStyledItemDelegate):
    def __init__(self, separator_column: int = -1, color: str = '#f5a623',
                 parent=None):
        super().__init__(parent)
        self.separator_column = separator_column
        self.color = color

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if index.column() != self.separator_column:
            return
        painter.save()
        pen = QPen(QColor(self.color), 3)
        painter.setPen(pen)
        x = option.rect.right()
        painter.drawLine(x, option.rect.top(), x, option.rect.bottom())
        painter.restore()


class KMapGroupDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        groups = index.data(KMAP_GROUPS_ROLE) or []
        if groups:
            painter.save()
            for i, group in enumerate(groups[:4]):
                color = QColor(group['color'])
                color.setAlpha(42)
                painter.fillRect(option.rect.adjusted(1, 1, -1, -1), color)
            painter.restore()

        super().paint(painter, option, index)

        if not groups:
            return
        painter.save()
        for i, group in enumerate(groups[:4]):
            color = QColor(group['color'])
            color.setAlpha(230)
            pen = QPen(color, 2)
            painter.setPen(pen)
            inset = 2 + i * 3
            painter.drawRoundedRect(option.rect.adjusted(inset, inset, -inset, -inset),
                                    4, 4)
        painter.restore()


class AutoBuildCircuitDialog(QDialog):
    def __init__(self, default_sheet_name: str = "Circuito simplificado",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Armar circuito automaticamente")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.sheet_name_edit = QLineEdit(default_sheet_name)
        form.addRow("Nombre de la hoja:", self.sheet_name_edit)
        layout.addLayout(form)

        self.two_input_only_check = QCheckBox("Usar compuertas unicamente de 2 entradas")
        self.nand_only_check = QCheckBox("Armar solo usando compuertas NAND")
        layout.addWidget(self.two_input_only_check)
        layout.addWidget(self.nand_only_check)

        hint = QLabel(
            "<small>Estas opciones definen la topologia que usara el generador "
            "al convertir las ecuaciones minimizadas en una hoja nueva.</small>")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_options(self) -> dict:
        return {
            'sheet_name': self.sheet_name_edit.text().strip(),
            'two_input_only': self.two_input_only_check.isChecked(),
            'nand_only': self.nand_only_check.isChecked(),
        }


class CircuitAnalyzerDialog(QDialog):
    """Analizador de circuitos digitales.

    Pestañas:
      1. Entradas / Salidas — definir variables del circuito.
      2. Tabla de verdad    — llenar con 0/1/X; reduce filas todo-X.
      3. Ecuaciones         — SOP / POS minimizados; botón futuro
                              "Armar circuito automáticamente".
      4. Mapa de Karnaugh   — placeholder para una futura implementación
                              que también permita ingresar K-maps y
                              obtener SOP/POS.
    """

    def __init__(self, parent=None, initial_state: dict = None):
        super().__init__(parent)
        self.setWindowTitle("Analizar Circuito Digital")
        self.resize(820, 580)

        # Estado del modelo
        self.var_inputs:  list = ['A', 'B']
        self.var_outputs: list = ['Y']
        self.truth_data: dict = {}
        self._last_simplification: dict = {}
        self._last_highlight: str = 'sop'
        self._all_outputs_label = "Todas las salidas"
        self._notation_id = 'math_prime'
        self._kmap_groups = []
        
        self._build_ui()
        
        # Restaurar estado si existe
        if initial_state:
            self._restore_state(initial_state)

    def _restore_state(self, state: dict):
        """Restaura variables, tabla, ecuaciones y configuración guardadas."""
        if not state:
            return

        # Bloquear señales para evitar reconstrucciones en cascada
        self.inputs_list.blockSignals(True)
        self.outputs_list.blockSignals(True)
        self.notation_selector.blockSignals(True)
        self.truth_table.blockSignals(True)

        # Recuperar datos
        self.var_inputs = list(state.get('var_inputs', ['A', 'B']))
        self.var_outputs = list(state.get('var_outputs', ['Y']))
        self.truth_data = dict(state.get('truth_data', {}))
        self._last_simplification = dict(state.get('_last_simplification', {}))
        self._last_highlight = state.get('_last_highlight', 'sop')
        self._notation_id = state.get('_notation_id', 'math_prime')

        # Actualizar listas de I/O
        self.inputs_list.clear()
        self.inputs_list.addItems(self.var_inputs)
        self.outputs_list.clear()
        self.outputs_list.addItems(self.var_outputs)

        # Restaurar notación
        idx = self.notation_selector.findData(self._notation_id)
        self.notation_selector.setCurrentIndex(idx if idx >= 0 else 1)

        # Desbloquear
        self.inputs_list.blockSignals(False)
        self.outputs_list.blockSignals(False)
        self.notation_selector.blockSignals(False)
        self.truth_table.blockSignals(False)

        # Reconstruir UI según datos recuperados
        if self.var_inputs and self.var_outputs:
            self._rebuild_truth_table()

        if self._last_simplification:
            self._populate_eqs_tab(highlight=self._last_highlight)
            self.tabs.setCurrentIndex(2)  # Saltar a Ecuaciones si hay análisis previo

        if self.tabs.currentIndex() == 3:
            self._refresh_kmap()

    def _persist_state_to_parent(self):
        """Vuelca el estado actual del analizador en MainWindow para que la
        próxima apertura lo restaure. Se llama desde todos los caminos de
        cierre (Cerrar, Esc, X de la ventana, accept) para no depender solo
        de closeEvent (que no se dispara en reject)."""
        if self.parent():
            self.parent()._analyzer_state = {
                'var_inputs': list(self.var_inputs),
                'var_outputs': list(self.var_outputs),
                'truth_data': dict(self.truth_data),
                '_last_simplification': dict(self._last_simplification),
                '_last_highlight': self._last_highlight,
                '_notation_id': self._notation_id,
            }

    def done(self, result):
        # done() es invocado por accept(), reject() y close(); cubre todos
        # los caminos de cierre del diálogo.
        self._persist_state_to_parent()
        super().done(result)

    def closeEvent(self, event):
        # Redundante con done() pero garantiza guardado si el sistema cierra
        # la ventana sin pasar por done() (raro, pero defensivo).
        self._persist_state_to_parent()
        event.accept()
        
    # ── UI principal ──────────────────────────────────────────────────────
    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_io_tab(),    "Entradas y Salidas")
        self.tabs.addTab(self._build_truth_tab(), "Tabla de Verdad")
        self.tabs.addTab(self._build_eqs_tab(),   "Ecuaciones simplificadas")
        self.tabs.addTab(self._build_kmap_tab(),  "Mapa de Karnaugh")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)

        # Botón cerrar
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ── Pestaña 1: I/O ────────────────────────────────────────────────────
    def _build_io_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        h = QHBoxLayout()

        def col(title, list_widget, edit_widget, btn_add, btn_rm):
            box = QGroupBox(title)
            v = QVBoxLayout(box)
            v.addWidget(list_widget)
            v.addWidget(edit_widget)
            row = QHBoxLayout()
            row.addWidget(btn_add); row.addWidget(btn_rm)
            v.addLayout(row)
            return box

        # Entradas
        self.inputs_list = QListWidget()
        self.inputs_list.addItems(self.var_inputs)
        self.in_edit  = QLineEdit(); self.in_edit.setPlaceholderText("Ej: A, B, EN")
        btn_add_in = QPushButton("Añadir entrada")
        btn_rm_in  = QPushButton("Quitar seleccionada")
        btn_add_in.clicked.connect(self._add_input)
        btn_rm_in.clicked.connect(self._remove_input)
        self.in_edit.returnPressed.connect(self._add_input)

        # Salidas
        self.outputs_list = QListWidget()
        self.outputs_list.addItems(self.var_outputs)
        self.out_edit = QLineEdit(); self.out_edit.setPlaceholderText("Ej: Y, S, COUT")
        btn_add_out = QPushButton("Añadir salida")
        btn_rm_out  = QPushButton("Quitar seleccionada")
        btn_add_out.clicked.connect(self._add_output)
        btn_rm_out.clicked.connect(self._remove_output)
        self.out_edit.returnPressed.connect(self._add_output)

        h.addWidget(col("Entradas",
                        self.inputs_list, self.in_edit, btn_add_in, btn_rm_in))
        h.addWidget(col("Salidas",
                        self.outputs_list, self.out_edit, btn_add_out, btn_rm_out))

        info = QLabel(
            "<small>Define los nombres de variables.  "
            "Al pasar a la pestaña <b>Tabla de Verdad</b> se generarán "
            "automáticamente las 2<sup>N</sup> filas correspondientes."
            "</small>")
        info.setWordWrap(True)
        outer.addLayout(h)
        outer.addWidget(info)
        return w

    def _parse_variable_names(self, text: str) -> list:
        names = [n for n in re.split(r'[\s,;]+', text.strip()) if n]
        valid = []
        invalid = []
        for name in names:
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
                valid.append(name)
            else:
                invalid.append(name)
        if invalid:
            QMessageBox.warning(
                self, "Nombre invalido",
                "Usa nombres como A, B, EN, S0 o COUT.")
        return valid

    def _add_variables(self, edit: QLineEdit, target: list, widget: QListWidget):
        added = False
        for name in self._parse_variable_names(edit.text()):
            if name in self.var_inputs or name in self.var_outputs:
                QMessageBox.warning(self, "Nombre duplicado",
                                    f"La variable '{name}' ya existe.")
                continue
            target.append(name)
            widget.addItem(name)
            added = True
        if added:
            edit.clear()
            self._last_simplification.clear()

    def _add_input(self):
        self._add_variables(self.in_edit, self.var_inputs, self.inputs_list)

    def _remove_input(self):
        row = self.inputs_list.currentRow()
        if row < 0:
            return
        name = self.var_inputs.pop(row)
        self.inputs_list.takeItem(row)
        # Limpiar truth_data inválido (cambia el número de filas)
        self.truth_data.clear()
        self._last_simplification.clear()

    def _add_output(self):
        self._add_variables(self.out_edit, self.var_outputs, self.outputs_list)

    def _remove_output(self):
        row = self.outputs_list.currentRow()
        if row < 0:
            return
        name = self.var_outputs.pop(row)
        self.outputs_list.takeItem(row)
        # Eliminar las celdas asociadas a esa salida
        for k in list(self.truth_data.keys()):
            if k[0] == name:
                del self.truth_data[k]
        self._last_simplification.clear()

    # ── Pestaña 2: Tabla de verdad ────────────────────────────────────────
    def _build_truth_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        info = QLabel(
            "<small>Llena cada celda de salida con <b>0</b>, <b>1</b> o "
            "<b>X</b> (don't care).  Las filas donde todas las salidas son "
            "<b>X</b> se ocultan automáticamente — no afectan a la "
            "minimización.</small>")
        info.setWordWrap(True)
        v.addWidget(info)

        self.truth_table = QTableWidget()
        self.truth_table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked |
            QTableWidget.EditTrigger.SelectedClicked)
        self._truth_delegate = TruthTableSeparatorDelegate(
            color=COLORS.get('comp_sel', '#f5a623'),
            parent=self.truth_table)
        self.truth_table.setItemDelegate(self._truth_delegate)
        self.truth_table.itemChanged.connect(self._on_truth_cell_changed)
        v.addWidget(self.truth_table)

        # Selector de salida a simplificar
        row = QHBoxLayout()
        self.truth_count_label = QLabel("")
        row.addWidget(self.truth_count_label)
        row.addStretch(1)
        row.addWidget(QLabel("Salida a simplificar:"))
        self.output_selector = QComboBox()
        row.addWidget(self.output_selector)
        v.addLayout(row)

        # Botones de minimización
        btn_row = QHBoxLayout()
        self.btn_sop = QPushButton("→ Mintérminos (SOP)")
        self.btn_pos = QPushButton("→ Maxtérminos (POS)")
        self.btn_sop.clicked.connect(lambda: self._simplify_and_show('sop'))
        self.btn_pos.clicked.connect(lambda: self._simplify_and_show('pos'))
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_sop)
        btn_row.addWidget(self.btn_pos)
        v.addLayout(btn_row)

        return w

    def _rebuild_truth_table(self):
        """Reconstruye la tabla de verdad según las variables definidas."""
        n_in  = len(self.var_inputs)
        n_out = len(self.var_outputs)
        if n_in == 0 or n_out == 0:
            self.truth_table.clear()
            self.truth_table.setRowCount(0)
            self.truth_table.setColumnCount(0)
            self.truth_count_label.setText("Filas activas: 0/0")
            self.output_selector.clear()
            return

        n_rows = 2 ** n_in
        # Bloquear señales mientras (re)llenamos
        self.truth_table.blockSignals(True)
        self.truth_table.clear()
        self.truth_table.setRowCount(n_rows)
        self.truth_table.setColumnCount(n_in + n_out + 1)   # +1 → columna #
        headers = ['#'] + list(self.var_inputs) + list(self.var_outputs)
        self.truth_table.setHorizontalHeaderLabels(headers)
        self._truth_delegate.separator_column = n_in
        input_header = QBrush(QColor(COLORS.get('grid', '#16213e')))
        output_header = QBrush(QColor(COLORS.get('toolbar', '#0f3460')))
        separator_brush = QBrush(QColor(COLORS.get('comp_sel', '#f5a623')))
        for c in range(self.truth_table.columnCount()):
            header_item = self.truth_table.horizontalHeaderItem(c)
            if header_item is None:
                continue
            if c == 0:
                header_item.setBackground(QBrush(QColor(COLORS.get('panel', '#16213e'))))
            elif c <= n_in:
                header_item.setBackground(input_header)
            else:
                header_item.setBackground(output_header)
            header_item.setForeground(QBrush(QColor(COLORS.get('text', '#e0e0e0'))))
        if n_in > 0:
            sep_header = self.truth_table.horizontalHeaderItem(n_in)
            if sep_header is not None:
                sep_header.setBackground(separator_brush)

        for r in range(n_rows):
            # Columna #: índice del mintérmino
            num_item = QTableWidgetItem(str(r))
            num_item.setFlags(num_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.truth_table.setItem(r, 0, num_item)
            # Columnas de entrada (binario, no editables)
            bin_str = format(r, f'0{n_in}b')
            for c, b in enumerate(bin_str):
                it = QTableWidgetItem(b)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.truth_table.setItem(r, c + 1, it)
            # Columnas de salida (editables, recuperar valor previo si existe)
            for j, oname in enumerate(self.var_outputs):
                key = (oname, r)
                val = self.truth_data.get(key, '0')
                it = QTableWidgetItem(val)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.truth_table.setItem(r, n_in + 1 + j, it)

        self.truth_table.resizeColumnsToContents()
        self.truth_table.blockSignals(False)
        self._refresh_hidden_rows()

        # Refrescar combo de salida a simplificar
        self.output_selector.blockSignals(True)
        self.output_selector.clear()
        self.output_selector.addItem(self._all_outputs_label)
        self.output_selector.addItems(self.var_outputs)
        self.output_selector.blockSignals(False)

    def _on_truth_cell_changed(self, item):
        """Normaliza el contenido a 0/1/X y oculta filas todo-X."""
        n_in = len(self.var_inputs)
        col = item.column()
        if col < n_in + 1:    # columna # o entrada
            return
        out_idx = col - (n_in + 1)
        if out_idx < 0 or out_idx >= len(self.var_outputs):
            return
        oname = self.var_outputs[out_idx]
        row = item.row()
        text = item.text().strip().lower()
        if text in ('1', 'true', 'high', 'h'):
            normalized = '1'
        elif text in ('x', 'd', 'dc', '-', '?'):
            normalized = 'X'
        else:
            normalized = '0'
        self.truth_data[(oname, row)] = normalized
        # Re-mostrar el valor normalizado (sin disparar el slot recursivamente)
        self.truth_table.blockSignals(True)
        item.setText(normalized)
        self.truth_table.blockSignals(False)
        self._refresh_hidden_rows()
        self._last_simplification.clear()

    def _refresh_hidden_rows(self):
        """Oculta filas donde TODAS las salidas son don't care (X)."""
        n_in = len(self.var_inputs)
        if n_in == 0 or not self.var_outputs:
            return
        n_rows = 2 ** n_in
        active = 0
        for r in range(n_rows):
            all_x = all(self.truth_data.get((o, r), '0') == 'X'
                        for o in self.var_outputs)
            self.truth_table.setRowHidden(r, all_x)
            if not all_x:
                active += 1
        self.truth_count_label.setText(f"Filas activas: {active}/{n_rows}")

    def _gather_terms(self, output_name: str):
        """Devuelve (minterms, dont_cares, maxterms) para la salida dada."""
        n_in = len(self.var_inputs)
        n_rows = 2 ** n_in
        minterms, dont_cares, maxterms = [], [], []
        for r in range(n_rows):
            v = self.truth_data.get((output_name, r), '0')
            if v == '1':
                minterms.append(r)
            elif v == 'X':
                dont_cares.append(r)
            else:
                maxterms.append(r)
        return minterms, dont_cares, maxterms

    def _simplify_and_show(self, mode: str):
        """mode: 'sop' o 'pos'.  Calcula la expresión y salta a Ecuaciones."""
        if not self.var_inputs or not self.var_outputs:
            QMessageBox.warning(self, "Faltan variables",
                                "Define al menos una entrada y una salida.")
            return
        selected = self.output_selector.currentText()
        outputs = (self.var_outputs if selected in ('', self._all_outputs_label)
                   else [selected])
        results = {}
        for o in outputs:
            mins, dcs, maxs = self._gather_terms(o)
            sop_cover = _sop_cover(mins, dcs, len(self.var_inputs))
            pos_cover = _pos_cover(mins, dcs, len(self.var_inputs))
            results[o] = {
                'sop_cover': sop_cover,
                'pos_cover': pos_cover,
                'minterms': mins,
                'maxterms': maxs,
                'dont_cares': dcs,
            }
        self._last_simplification = results
        self._last_highlight = mode
        self._populate_eqs_tab(highlight=mode)
        self.tabs.setCurrentIndex(2)   # Saltar a la pestaña Ecuaciones

    # ── Pestaña 3: Ecuaciones simplificadas ───────────────────────────────
    def _build_eqs_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        info = QLabel(
            "<small>Resultados de la minimización por Quine-McCluskey "
            "con soporte para don't cares.  Notación: <b>'</b> = NEGADO, "
            "<b>·</b> = AND, <b>+</b> = OR.</small>")
        info.setWordWrap(True)
        v.addWidget(info)

        notation_row = QHBoxLayout()
        notation_row.addWidget(QLabel("Notacion:"))
        self.notation_selector = QComboBox()
        for notation_id, label in NOTATION_LABELS.items():
            self.notation_selector.addItem(label, notation_id)
        self.notation_selector.setCurrentIndex(1)
        self.notation_selector.currentIndexChanged.connect(self._on_notation_changed)
        notation_row.addWidget(self.notation_selector)
        notation_row.addStretch(1)
        v.addLayout(notation_row)

        self.eqs_text = QTextEdit()
        self.eqs_text.setReadOnly(True)
        self.eqs_text.setFont(_qfont('Consolas', 11))
        v.addWidget(self.eqs_text)

        # Botón futuro: armar circuito automáticamente
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_build_circuit = QPushButton("⚙  Armar circuito automáticamente")
        self.btn_build_circuit.setToolTip(
            "Próximamente: genera y coloca las puertas lógicas necesarias "
            "en una hoja nueva, conectándolas según las ecuaciones SOP.")
        self.btn_build_circuit.clicked.connect(self._build_circuit_stub)
        btn_row.addWidget(self.btn_build_circuit)
        v.addLayout(btn_row)
        return w

    def _on_notation_changed(self, _index: int):
        self._notation_id = self.notation_selector.currentData() or 'math_prime'
        if self._last_simplification:
            self._populate_eqs_tab(highlight=self._last_highlight)

    def _populate_eqs_tab(self, highlight: str = 'sop'):
        if not self._last_simplification:
            self.eqs_text.setPlainText(
                "Aún no se ha minimizado ninguna salida.  "
                "Ve a la pestaña 'Tabla de Verdad', llena los valores "
                "y pulsa Mintérminos (SOP) o Maxtérminos (POS).")
            return
        lines = []
        notation = self._notation_id
        rich = notation == 'math_bar'
        for oname, info in self._last_simplification.items():
            mins = info['minterms']
            maxs = info.get('maxterms', [])
            dcs = info['dont_cares']
            sop = _format_sop_cover(info['sop_cover'], self.var_inputs, notation, rich=rich)
            pos = _format_pos_cover(info['pos_cover'], self.var_inputs, notation, rich=rich)
            lines.append(f"── {oname} ──")
            mins_str = ','.join(str(m) for m in mins) or '∅'
            maxs_str = ','.join(str(m) for m in maxs) or '∅'
            dcs_str  = ','.join(str(d) for d in dcs) if dcs else '∅'
            lines.append(f"  Σm = ({mins_str})    ΠM = ({maxs_str})    d = ({dcs_str})")
            mark_sop = '◀' if highlight == 'sop' else ' '
            mark_pos = '◀' if highlight == 'pos' else ' '
            lines.append(f"  SOP {mark_sop}   {oname} = {sop}")
            lines.append(f"  POS {mark_pos}   {oname} = {pos}")
            lines.append("")
        if rich:
            escaped = []
            for line in lines:
                escaped.append(line if '<span' in line else html.escape(line))
            body = '<br>'.join(escaped)
            self.eqs_text.setHtml(
                '<div style="font-family: Consolas, monospace; font-size: 11pt; white-space: pre;">'
                f'{body}</div>')
        else:
            self.eqs_text.setPlainText('\n'.join(lines))

    def _safe_generated_name(self, text: str) -> str:
        safe = re.sub(r'[^A-Za-z0-9_]+', '_', text.strip())
        safe = safe.strip('_')
        return safe or 'X'

    def _ensure_auto_build_results(self) -> dict:
        if self._last_simplification:
            return self._last_simplification
        results = {}
        for output in self.var_outputs:
            mins, dcs, maxs = self._gather_terms(output)
            results[output] = {
                'sop_cover': _sop_cover(mins, dcs, len(self.var_inputs)),
                'pos_cover': _pos_cover(mins, dcs, len(self.var_inputs)),
                'minterms': mins,
                'maxterms': maxs,
                'dont_cares': dcs,
            }
        self._last_simplification = results
        return results

    def _place_generated_gate(self, scene, gate_type: str, name: str,
                              x: float, y: float, input_nets: list,
                              output_net: str):
        node2 = input_nets[0] if input_nets else '0'
        node3 = input_nets[1] if len(input_nets) > 1 else ''
        item = scene.place_component(
            gate_type, QPointF(x, y), name=name, value=0.0,
            node1=output_net, node2=node2, node3=node3)
        item.dig_inputs = 1 if gate_type == 'NOT' else max(1, len(input_nets))
        item.dig_input_nodes = list(input_nets[2:])
        item.update()
        return item

    def _combine_generated_inputs(self, scene, gate_type: str, prefix: str,
                                  input_nets: list, output_net: str,
                                  x: float, y: float, two_input_only: bool) -> str:
        if not input_nets:
            return ''
        safe_prefix = self._safe_generated_name(prefix)
        if len(input_nets) == 1:
            self._place_generated_gate(
                scene, gate_type, f"{safe_prefix}_{gate_type}",
                x, y, input_nets, output_net)
            return output_net
        if (not two_input_only) or len(input_nets) <= 2:
            self._place_generated_gate(
                scene, gate_type, f"{safe_prefix}_{gate_type}",
                x, y, input_nets, output_net)
            return output_net

        current = input_nets[0]
        for i, next_net in enumerate(input_nets[1:], start=1):
            is_last = i == len(input_nets) - 1
            stage_out = output_net if is_last else f"{safe_prefix}_stage_{i}"
            self._place_generated_gate(
                scene, gate_type, f"{safe_prefix}_{gate_type}{i}",
                x + (i - 1) * 120, y, [current, next_net], stage_out)
            current = stage_out
        return current

    # ── Helper de layout para evitar solapamientos y choques de pines ───
    def _compute_auto_layout(self, results: dict, two_input_only: bool):
        """Calcula columnas, y_step y lanes de tronco para el armado
        automático (SOP y NAND).

        Garantiza:
          - y_step ≥ altura de la compuerta más alta + 1 grid (sin solapes)
          - lanes de variable y de inversor SIEMPRE caen entre la columna
            de origen y la columna de destino, sin tocar la x del pin del
            inversor / NOT (que rompía Union-Find creando cortocircuitos)
        """
        n_vars = max(2, len(self.var_inputs))
        max_lits = max(
            (sum(1 for b in p if b in ('0', '1'))
             for info in results.values()
             for p in info.get('sop_cover', [])),
            default=2)
        max_terms = max(
            (len(info.get('sop_cover', []))
             for info in results.values()),
            default=1)

        # Altura de gate por nº de entradas: (n-1)*GRID_SIZE + 40.
        # En modo two_input_only todos los gates colocados son de 2 entradas
        # (cascada), así que la altura máxima viene de los gates en el path
        # principal, que es 2.
        if two_input_only:
            max_n = 2
        else:
            max_n = max(2, max_lits, max_terms)
        gate_h = (max_n - 1) * GRID_SIZE + 40
        y_step = max(80, gate_h + GRID_SIZE)

        # Variables negadas distintas (cada una requiere su propio inversor
        # y, por tanto, una lane en el siguiente zone).
        used_negated_count = max(2, sum(
            1 for var in self.var_inputs
            if any(p[i] == '0' for info in results.values()
                   for p in info.get('sop_cover', [])
                   for i, _ in enumerate([var])
                   if i < len(self.var_inputs))))
        # Calcular número real de inversores por variable única usada
        neg_var_set = set()
        for info in results.values():
            for p in info.get('sop_cover', []):
                for bit, var in zip(p, self.var_inputs):
                    if bit == '0':
                        neg_var_set.add(var)
        n_neg = max(1, len(neg_var_set))

        input_x = -540
        src_snap = round((input_x + 15) / GRID_SIZE) * GRID_SIZE  # típicamente -520

        # Ancho mínimo del canal entre input y inversor: 1 lane por variable
        # (entre source pin y INV_input_pin, ambos exclusivos).
        # range(src+G, inv_in, G) debe contener al menos n_vars elementos
        # → inv_in - (src+G) ≥ n_vars*G → inv_in ≥ src + (n_vars+1)*G
        inv_x_min = src_snap + (n_vars + 2) * GRID_SIZE + 40
        inv_x = max(-360, inv_x_min)
        inv_x = round(inv_x / GRID_SIZE) * GRID_SIZE

        inv_out_x = inv_x + 40
        # Mismo razonamiento para canal inversor → término
        term_x_min = inv_out_x + (n_neg + 2) * GRID_SIZE + 40
        term_x = max(-100, term_x_min)
        term_x = round(term_x / GRID_SIZE) * GRID_SIZE

        # out_x y label_x se computan luego según modo (cascade añade más).
        return {
            'input_x': input_x, 'inv_x': inv_x, 'term_x': term_x,
            'y_step': y_step, 'src_snap': src_snap,
            'n_vars': n_vars, 'n_neg': n_neg,
            'max_lits': max_lits, 'max_terms': max_terms,
        }

    def _make_trunk_lanes(self, src_x: float, dest_in_x: float,
                          n_needed: int) -> List[float]:
        """Devuelve hasta `n_needed` valores x (alineados a GRID_SIZE) en el
        rango (src_x, dest_in_x), excluyendo los extremos para no chocar con
        pines en esas columnas."""
        start = int(src_x + GRID_SIZE)
        stop  = int(dest_in_x)
        lanes = list(range(start, stop, GRID_SIZE))
        return lanes[:n_needed] if len(lanes) >= n_needed else lanes

    def _auto_build_sop_circuit(self, opts: dict) -> bool:
        if opts.get('nand_only'):
            return self._auto_build_nand_circuit(opts)

        owner = self.parent()
        if owner is None or not hasattr(owner, '_add_sheet'):
            QMessageBox.warning(self, "Armado automatico",
                                "No se encontro la ventana principal para crear la hoja.")
            return False

        results = self._ensure_auto_build_results()
        if not self.var_inputs or not self.var_outputs:
            QMessageBox.warning(self, "Faltan variables",
                                "Define al menos una entrada y una salida.")
            return False

        owner._add_sheet(opts['sheet_name'])
        scene = owner.scene
        two_input_only = bool(opts.get('two_input_only'))

        # Layout adaptativo: y_step según altura máxima de gate; columnas
        # con suficiente canal para n lanes de tronco sin chocar con pines.
        L = self._compute_auto_layout(results, two_input_only)
        input_x   = L['input_x']
        not_x     = L['inv_x']
        term_x    = L['term_x']
        y_step    = L['y_step']
        src_snap  = L['src_snap']
        max_lits  = L['max_lits']
        max_terms = L['max_terms']

        # out_x debe quedar a la derecha del último gate de la cascada AND
        # (en two_input_only) para que su cuerpo no pise el AND final.
        # Cada etapa de cascada usa 120 px; la última etapa de un término
        # con n literales está en term_x + (n-2)*120.
        if two_input_only:
            and_w  = max(0, max_lits  - 2) * 120 + 60
            or_w   = max(0, max_terms - 2) * 120 + 60
            out_x   = term_x + and_w + 200
            label_x = out_x   + or_w  + 280
        else:
            out_x   = term_x + 200
            label_x = out_x  + 280

        used_negated = {
            var for info in results.values()
            for prime in info.get('sop_cover', [])
            for bit, var in zip(prime, self.var_inputs)
            if bit == '0'
        }
        # Orden estable de variables negadas para asignar lanes
        neg_var_order = [v for v in self.var_inputs if v in used_negated]
        neg_nets: Dict[str, str] = {}

        # Lanes (x del bus tronco) por zona, evitando columnas de pines.
        var_lanes = self._make_trunk_lanes(
            src_snap, not_x - 40, len(self.var_inputs))
        inv_out_x = not_x + 40
        neg_lanes = self._make_trunk_lanes(
            inv_out_x, term_x - 40, max(1, len(neg_var_order)))

        # Mapas para el cableado posterior:
        #   net_source[net]    = (pin_pos, comp, pin_idx)  → quien produce el net
        #   net_consumers[net] = [(pin_pos, comp, pin_idx), ...]  → quienes lo leen
        #   net_trunk_x[net]   = x del bus tronco (asignado para nets con varios consumidores)
        net_source: Dict[str, Tuple[QPointF, ComponentItem, int]] = {}
        net_consumers: Dict[str, List[Tuple[QPointF, ComponentItem, int]]] = {}
        net_trunk_x: Dict[str, float] = {}

        def _add_consumer(net: str, pin_pos: QPointF,
                          comp: ComponentItem, pin_idx: int):
            net_consumers.setdefault(net, []).append((pin_pos, comp, pin_idx))

        # ── Inputs como NET_LABEL_IN (sheet_label = nombre de la variable) ──
        for i, var in enumerate(self.var_inputs):
            safe_var = self._safe_generated_name(var)
            y = i * y_step
            in_label = scene.place_component(
                'NET_LABEL_IN', QPointF(input_x, y),
                name=f"IN_{safe_var}", value=0.0, node1=var)
            in_label.sheet_label = var
            in_label.update()
            pins = in_label.all_pin_positions_scene()
            net_source[var] = (pins[0], in_label, 0)
            # Bus tronco asignado desde la lista de lanes válidas (excluyen
            # columnas de pines para no provocar Union-Find espurio).
            if i < len(var_lanes):
                net_trunk_x[var] = var_lanes[i]

            if var in used_negated:
                neg_net = f"NOT_{safe_var}"
                neg_nets[var] = neg_net
                ng = self._place_generated_gate(
                    scene, 'NOT', f"NOT_{safe_var}", not_x, y, [var], neg_net)
                ng_pins = ng.all_pin_positions_scene()
                net_source[neg_net] = (ng_pins[0], ng, 0)        # salida
                _add_consumer(var, ng_pins[1], ng, 1)            # entrada
                neg_idx = neg_var_order.index(var)
                if neg_idx < len(neg_lanes):
                    net_trunk_x[neg_net] = neg_lanes[neg_idx]

        # ── Bloques de salida: AND de términos + OR + NET_LABEL_OUT ────────
        first_output_y = max(len(self.var_inputs) * y_step + 80, 120)
        for out_index, (output, info) in enumerate(results.items()):
            safe_out = self._safe_generated_name(output)
            cover = info.get('sop_cover', [])
            block_y = first_output_y + out_index * max(180, (len(cover) + 1) * y_step)

            # NET_LABEL_OUT: consumidor del net 'output'
            out_label = scene.place_component(
                'NET_LABEL_OUT', QPointF(label_x, block_y),
                name=f"OUT_{safe_out}", value=0.0, node1=output)
            out_label.sheet_label = output
            out_label.update()
            out_label_pin = out_label.all_pin_positions_scene()[0]
            _add_consumer(output, out_label_pin, out_label, 0)

            if not cover:
                const = scene.place_component(
                    'LOGIC_STATE', QPointF(out_x, block_y),
                    name=f"CONST0_{safe_out}", value=0.0, node1=output)
                const.update()
                net_source[output] = (const.all_pin_positions_scene()[0], const, 0)
            elif cover == ['-' * len(self.var_inputs)]:
                const = scene.place_component(
                    'LOGIC_STATE', QPointF(out_x, block_y),
                    name=f"CONST1_{safe_out}", value=1.0, node1=output)
                const.update()
                net_source[output] = (const.all_pin_positions_scene()[0], const, 0)
            else:
                term_nets: List[str] = []
                for term_index, prime in enumerate(cover):
                    lits: List[str] = []
                    for bit, var in zip(prime, self.var_inputs):
                        if bit == '1':
                            lits.append(var)
                        elif bit == '0':
                            lits.append(neg_nets.get(
                                var, f"NOT_{self._safe_generated_name(var)}"))
                    term_y = block_y + term_index * y_step
                    if not lits:
                        continue
                    if len(lits) == 1:
                        # Literal solo: pasa directo a la entrada del OR
                        term_nets.append(lits[0])
                    else:
                        term_net = f"{safe_out}_T{term_index + 1}"
                        self._place_combiner_gates(
                            scene, 'AND', f"{safe_out}_T{term_index + 1}",
                            lits, term_net, term_x, term_y, two_input_only,
                            net_source, net_consumers)
                        term_nets.append(term_net)

                if term_nets:
                    final_y = block_y + max(0, (len(term_nets) - 1) * y_step / 2)
                    self._place_combiner_gates(
                        scene, 'OR', f"{safe_out}_OUT",
                        term_nets, output, out_x, final_y, two_input_only,
                        net_source, net_consumers)
                else:
                    const = scene.place_component(
                        'LOGIC_STATE', QPointF(out_x, block_y),
                        name=f"CONST0_{safe_out}", value=0.0, node1=output)
                    const.update()
                    net_source[output] = (
                        const.all_pin_positions_scene()[0], const, 0)

        # ── Cableado de todos los nets ─────────────────────────────────────
        self._route_all_nets(scene, net_source, net_consumers, net_trunk_x)

        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-120, -120, 160, 120))
        if hasattr(owner, 'statusBar'):
            owner.statusBar().showMessage(
                f"Circuito generado en la hoja '{opts['sheet_name']}'")
        return True

    def _place_combiner_gates(self, scene, gate_type: str, prefix: str,
                              input_nets: list, output_net: str,
                              x: float, y: float, two_input_only: bool,
                              net_source: dict, net_consumers: dict):
        """Coloca uno o varios gates que combinan input_nets → output_net y
        registra en net_source / net_consumers los pines reales para que el
        cableado posterior pueda conectarlos.

        - Si two_input_only es False (o len(input_nets) ≤ 2): un único gate.
        - Si two_input_only es True con > 2 entradas: cascada de gates de 2.
        """
        if not input_nets:
            return
        safe_prefix = self._safe_generated_name(prefix)

        if (not two_input_only) or len(input_nets) <= 2:
            gate = self._place_generated_gate(
                scene, gate_type, f"{safe_prefix}_{gate_type}",
                x, y, input_nets, output_net)
            pins = gate.all_pin_positions_scene()
            net_source[output_net] = (pins[0], gate, 0)
            n_in = 1 if gate_type == 'NOT' else max(1, len(input_nets))
            for i in range(n_in):
                in_net = input_nets[i] if i < len(input_nets) else input_nets[0]
                net_consumers.setdefault(in_net, []).append(
                    (pins[i + 1], gate, i + 1))
            return

        # Cascada: cada etapa toma (acumulado, siguiente) y produce stage_i;
        # la última etapa produce output_net.
        current_net = input_nets[0]
        for i, next_net in enumerate(input_nets[1:], start=1):
            is_last = i == len(input_nets) - 1
            stage_out = output_net if is_last else f"{safe_prefix}_stage_{i}"
            gate = self._place_generated_gate(
                scene, gate_type, f"{safe_prefix}_{gate_type}{i}",
                x + (i - 1) * 120, y, [current_net, next_net], stage_out)
            pins = gate.all_pin_positions_scene()
            net_source[stage_out] = (pins[0], gate, 0)
            net_consumers.setdefault(current_net, []).append(
                (pins[1], gate, 1))
            net_consumers.setdefault(next_net, []).append(
                (pins[2], gate, 2))
            current_net = stage_out

    def _route_all_nets(self, scene,
                        net_source: dict, net_consumers: dict,
                        net_trunk_x: dict):
        """Coloca cables ortogonales para conectar cada net source con sus
        consumidores. Para nets con un único consumidor se usa una ruta L/Z
        directa; para nets con varios consumidores se usa un bus tronco
        vertical en x = net_trunk_x[net] (o un punto medio si no se asignó).
        """
        for net, source in net_source.items():
            consumers = net_consumers.get(net, [])
            if not consumers:
                continue
            trunk_x = net_trunk_x.get(net)
            if trunk_x is None:
                # Punto medio entre la fuente y el consumidor más cercano
                min_dest_x = min(c[0].x() for c in consumers)
                trunk_x = (source[0].x() + min_dest_x) / 2
            self._route_net(scene, source, consumers, trunk_x)

    def _route_net(self, scene, source, consumers, trunk_x: float):
        """Crea WireItems en el escenario para enlazar source→consumidores.

        Estrategia: tronco vertical en `trunk_x`. La fuente se conecta con un
        cable horizontal hasta el tronco; el tronco se descompone en
        segmentos verticales entre cada y de junction (fuente/destinos);
        cada destino recibe un cable horizontal corto desde el tronco. Todos
        los puntos de unión son extremos de cable, lo que garantiza que el
        Union-Find del netlist los detecte como conectados.
        """
        src_pin, src_comp, src_pin_idx = source
        trunk_x = round(trunk_x / GRID_SIZE) * GRID_SIZE
        src_y = src_pin.y()
        cons_ys = [c[0].y() for c in consumers]

        # Atajo: un único consumidor, mismo y, x distinto → un cable directo
        if (len(consumers) == 1
                and abs(consumers[0][0].y() - src_y) < 0.5
                and abs(consumers[0][0].x() - src_pin.x()) > 0.5):
            dp, dc, dpi = consumers[0]
            wire = WireItem(
                QPointF(src_pin), QPointF(dp),
                start_comp=src_comp, start_pin_idx=src_pin_idx,
                end_comp=dc, end_pin_idx=dpi)
            scene.addItem(wire)
            scene.wires.append(wire)
            return

        junction_ys = sorted(set([src_y] + cons_ys))

        # 1) Stub de la fuente al tronco
        if abs(src_pin.x() - trunk_x) > 0.5:
            wire = WireItem(
                QPointF(src_pin), QPointF(trunk_x, src_y),
                start_comp=src_comp, start_pin_idx=src_pin_idx)
            scene.addItem(wire)
            scene.wires.append(wire)

        # 2) Tronco vertical: segmentos consecutivos entre y's de junction
        for y_a, y_b in zip(junction_ys, junction_ys[1:]):
            if abs(y_a - y_b) > 0.5:
                wire = WireItem(QPointF(trunk_x, y_a),
                                QPointF(trunk_x, y_b))
                scene.addItem(wire)
                scene.wires.append(wire)

        # 3) Stubs del tronco a cada consumidor
        for (dp, dc, dpi) in consumers:
            if abs(dp.x() - trunk_x) > 0.5:
                wire = WireItem(
                    QPointF(trunk_x, dp.y()), QPointF(dp),
                    end_comp=dc, end_pin_idx=dpi)
                scene.addItem(wire)
                scene.wires.append(wire)

    # ── Armado SOP usando ÚNICAMENTE compuertas NAND ───────────────────
    def _auto_build_nand_circuit(self, opts: dict) -> bool:
        """Genera el mismo SOP pero con NAND como única compuerta.

        Identidades usadas:
          - NOT(x)  = NAND(x, x)
          - AND(...) → para cada término: T_i' = NAND(literales),
                       y luego Y = NAND(T1', T2', ..., Tn')
                       (De Morgan:  Σ T_i = NAND(T_i') )
          - Literal negado X' se obtiene con NAND(X, X)

        En modo two_input_only se cascadea sólo con NAND de 2 entradas:
          término  : NAND, [INV (NAND-NAND), NAND]+   →  T_i'
          salida   : NAND, [INV (NAND-NAND), NAND]+   →  Y
        """
        owner = self.parent()
        if owner is None or not hasattr(owner, '_add_sheet'):
            QMessageBox.warning(self, "Armado automatico",
                                "No se encontro la ventana principal para crear la hoja.")
            return False

        results = self._ensure_auto_build_results()
        if not self.var_inputs or not self.var_outputs:
            QMessageBox.warning(self, "Faltan variables",
                                "Define al menos una entrada y una salida.")
            return False

        owner._add_sheet(opts['sheet_name'])
        scene = owner.scene
        two_input_only = bool(opts.get('two_input_only'))

        # Layout adaptativo (mismo helper que la versión SOP).
        L = self._compute_auto_layout(results, two_input_only)
        input_x  = L['input_x']
        inv_x    = L['inv_x']
        term_x   = L['term_x']
        y_step   = L['y_step']
        src_snap = L['src_snap']
        max_lits = L['max_lits']
        max_terms = L['max_terms']

        # Reserva horizontal para cascadas de NAND (2-input only ensancha mucho).
        term_stages = max(1, 2 * max_lits - 3) if two_input_only else 1
        out_stages  = max(1, 2 * max_terms - 3) if two_input_only else 1
        stage_step  = 120
        out_x   = term_x + (term_stages * stage_step) + 80
        label_x = out_x   + (out_stages  * stage_step) + 80

        used_negated = {
            var for info in results.values()
            for prime in info.get('sop_cover', [])
            for bit, var in zip(prime, self.var_inputs)
            if bit == '0'
        }
        neg_var_order = [v for v in self.var_inputs if v in used_negated]
        inv_nets: Dict[str, str] = {}

        net_source: Dict[str, Tuple[QPointF, ComponentItem, int]] = {}
        net_consumers: Dict[str, List[Tuple[QPointF, ComponentItem, int]]] = {}
        net_trunk_x: Dict[str, float] = {}

        def _add_consumer(net: str, pin_pos: QPointF,
                          comp: ComponentItem, pin_idx: int):
            net_consumers.setdefault(net, []).append((pin_pos, comp, pin_idx))

        # Lanes de tronco por zona (excluyen columnas de pines)
        var_lanes = self._make_trunk_lanes(
            src_snap, inv_x - 40, len(self.var_inputs))
        inv_out_x = inv_x + 40
        neg_lanes = self._make_trunk_lanes(
            inv_out_x, term_x - 40, max(1, len(neg_var_order)))

        # ── Inputs como NET_LABEL_IN ──────────────────────────────────
        for i, var in enumerate(self.var_inputs):
            safe_var = self._safe_generated_name(var)
            y = i * y_step
            in_label = scene.place_component(
                'NET_LABEL_IN', QPointF(input_x, y),
                name=f"IN_{safe_var}", value=0.0, node1=var)
            in_label.sheet_label = var
            in_label.update()
            pins = in_label.all_pin_positions_scene()
            net_source[var] = (pins[0], in_label, 0)
            if i < len(var_lanes):
                net_trunk_x[var] = var_lanes[i]

            if var in used_negated:
                inv_net = f"NOT_{safe_var}"
                inv_nets[var] = inv_net
                ng = self._place_generated_gate(
                    scene, 'NAND', f"INV_{safe_var}",
                    inv_x, y, [var, var], inv_net)
                ng_pins = ng.all_pin_positions_scene()
                net_source[inv_net] = (ng_pins[0], ng, 0)
                _add_consumer(var, ng_pins[1], ng, 1)
                _add_consumer(var, ng_pins[2], ng, 2)
                neg_idx = neg_var_order.index(var)
                if neg_idx < len(neg_lanes):
                    net_trunk_x[inv_net] = neg_lanes[neg_idx]

        # ── Bloques por salida ────────────────────────────────────────
        first_output_y = max(len(self.var_inputs) * y_step + 80, 120)
        for out_index, (output, info) in enumerate(results.items()):
            safe_out = self._safe_generated_name(output)
            cover = info.get('sop_cover', [])
            block_y = first_output_y + out_index * max(180, (len(cover) + 1) * y_step)

            out_label = scene.place_component(
                'NET_LABEL_OUT', QPointF(label_x, block_y),
                name=f"OUT_{safe_out}", value=0.0, node1=output)
            out_label.sheet_label = output
            out_label.update()
            _add_consumer(output, out_label.all_pin_positions_scene()[0], out_label, 0)

            if not cover:
                const = scene.place_component(
                    'LOGIC_STATE', QPointF(out_x, block_y),
                    name=f"CONST0_{safe_out}", value=0.0, node1=output)
                const.update()
                net_source[output] = (const.all_pin_positions_scene()[0], const, 0)
                continue
            if cover == ['-' * len(self.var_inputs)]:
                const = scene.place_component(
                    'LOGIC_STATE', QPointF(out_x, block_y),
                    name=f"CONST1_{safe_out}", value=1.0, node1=output)
                const.update()
                net_source[output] = (const.all_pin_positions_scene()[0], const, 0)
                continue

            term_inv_nets: List[str] = []
            for term_index, prime in enumerate(cover):
                lits: List[str] = []
                for bit, var in zip(prime, self.var_inputs):
                    if bit == '1':
                        lits.append(var)
                    elif bit == '0':
                        lits.append(inv_nets.get(
                            var, f"NOT_{self._safe_generated_name(var)}"))
                if not lits:
                    continue
                term_y = block_y + term_index * y_step
                term_inv_net = f"{safe_out}_T{term_index + 1}_inv"
                self._place_nand_term(
                    scene, f"{safe_out}_T{term_index + 1}",
                    lits, term_inv_net, term_x, term_y, two_input_only,
                    net_source, net_consumers)
                term_inv_nets.append(term_inv_net)

            if term_inv_nets:
                final_y = block_y + max(0, (len(term_inv_nets) - 1) * y_step / 2)
                self._place_nand_or(
                    scene, f"{safe_out}_OUT",
                    term_inv_nets, output, out_x, final_y, two_input_only,
                    net_source, net_consumers)
            else:
                const = scene.place_component(
                    'LOGIC_STATE', QPointF(out_x, block_y),
                    name=f"CONST0_{safe_out}", value=0.0, node1=output)
                const.update()
                net_source[output] = (const.all_pin_positions_scene()[0], const, 0)

        self._route_all_nets(scene, net_source, net_consumers, net_trunk_x)
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-120, -120, 160, 120))
        if hasattr(owner, 'statusBar'):
            owner.statusBar().showMessage(
                f"Circuito NAND generado en la hoja '{opts['sheet_name']}'")
        return True

    def _place_nand_term(self, scene, prefix: str, lits: list,
                         term_inv_net: str, x: float, y: float,
                         two_input_only: bool,
                         net_source: dict, net_consumers: dict):
        """Coloca NAND(s) que producen term_inv_net = (∏ lits)'.

        - 1 literal:  NAND(l, l) actúa como inversor → l'.
        - 2 literales y/o no two_input_only: un único NAND multi-entrada.
        - 2 input only con n>=3 literales: cascada
            q1   = NAND(l1, l2)               → (l1·l2)'
            buf2 = NAND(q1, q1)               → l1·l2          (inversor)
            q3   = NAND(buf2, l3)             → (l1·l2·l3)'
            buf4 = NAND(q3, q3)               → l1·l2·l3       (inversor)
            ...
            qN   = NAND(buf_{N-1}, l_n)       → (Π lits)'  ≡ term_inv_net
        """
        if not lits:
            return
        safe_prefix = self._safe_generated_name(prefix)
        stage_step = 120

        if (not two_input_only) or len(lits) <= 2:
            actual_inputs = [lits[0], lits[0]] if len(lits) == 1 else lits
            gate = self._place_generated_gate(
                scene, 'NAND', f"{safe_prefix}_NAND",
                x, y, actual_inputs, term_inv_net)
            pins = gate.all_pin_positions_scene()
            net_source[term_inv_net] = (pins[0], gate, 0)
            for i, in_net in enumerate(actual_inputs):
                net_consumers.setdefault(in_net, []).append(
                    (pins[i + 1], gate, i + 1))
            return

        # Cascada 2-input
        q_net = f"{safe_prefix}_q1"
        gate = self._place_generated_gate(
            scene, 'NAND', f"{safe_prefix}_NAND1",
            x, y, [lits[0], lits[1]], q_net)
        pins = gate.all_pin_positions_scene()
        net_source[q_net] = (pins[0], gate, 0)
        net_consumers.setdefault(lits[0], []).append((pins[1], gate, 1))
        net_consumers.setdefault(lits[1], []).append((pins[2], gate, 2))

        stage = 1
        for k in range(2, len(lits)):
            # Inversor NAND-NAND para "deshacer" el NAND previo
            buf_net = f"{safe_prefix}_buf{stage + 1}"
            gate = self._place_generated_gate(
                scene, 'NAND', f"{safe_prefix}_INV{stage + 1}",
                x + stage * stage_step, y, [q_net, q_net], buf_net)
            pins = gate.all_pin_positions_scene()
            net_source[buf_net] = (pins[0], gate, 0)
            net_consumers.setdefault(q_net, []).append((pins[1], gate, 1))
            net_consumers.setdefault(q_net, []).append((pins[2], gate, 2))
            stage += 1

            is_last = (k == len(lits) - 1)
            next_q = term_inv_net if is_last else f"{safe_prefix}_q{stage + 1}"
            gate = self._place_generated_gate(
                scene, 'NAND', f"{safe_prefix}_NAND{stage + 1}",
                x + stage * stage_step, y, [buf_net, lits[k]], next_q)
            pins = gate.all_pin_positions_scene()
            net_source[next_q] = (pins[0], gate, 0)
            net_consumers.setdefault(buf_net, []).append((pins[1], gate, 1))
            net_consumers.setdefault(lits[k], []).append((pins[2], gate, 2))
            q_net = next_q
            stage += 1

    def _place_nand_or(self, scene, prefix: str, term_inv_nets: list,
                       output_net: str, x: float, y: float,
                       two_input_only: bool,
                       net_source: dict, net_consumers: dict):
        """OR final por De Morgan: output_net = NAND(T1', T2', ..., Tn').

        - 1 término: NAND(T', T') = T (inversor).
        - 2 términos y/o no two_input_only: un único NAND multi-entrada.
        - 2 input only con n>=3 términos: cascada
            acc1  = NAND(T1', T2')              → T1+T2
            inv2  = NAND(acc1, acc1)            → (T1+T2)'
            acc3  = NAND(inv2, T3')             → T1+T2+T3
            inv4  = NAND(acc3, acc3)            → ...'
            ...
            accN  = NAND(inv_{N-1}, Tn')        → Y
        """
        if not term_inv_nets:
            return
        safe_prefix = self._safe_generated_name(prefix)
        stage_step = 120

        if (not two_input_only) or len(term_inv_nets) <= 2:
            actual_inputs = (
                [term_inv_nets[0], term_inv_nets[0]]
                if len(term_inv_nets) == 1 else term_inv_nets)
            gate = self._place_generated_gate(
                scene, 'NAND', f"{safe_prefix}_NAND",
                x, y, actual_inputs, output_net)
            pins = gate.all_pin_positions_scene()
            net_source[output_net] = (pins[0], gate, 0)
            for i, in_net in enumerate(actual_inputs):
                net_consumers.setdefault(in_net, []).append(
                    (pins[i + 1], gate, i + 1))
            return

        acc_net = f"{safe_prefix}_acc1"
        gate = self._place_generated_gate(
            scene, 'NAND', f"{safe_prefix}_NAND1",
            x, y, [term_inv_nets[0], term_inv_nets[1]], acc_net)
        pins = gate.all_pin_positions_scene()
        net_source[acc_net] = (pins[0], gate, 0)
        net_consumers.setdefault(term_inv_nets[0], []).append((pins[1], gate, 1))
        net_consumers.setdefault(term_inv_nets[1], []).append((pins[2], gate, 2))

        stage = 1
        for k in range(2, len(term_inv_nets)):
            inv_net = f"{safe_prefix}_inv{stage + 1}"
            gate = self._place_generated_gate(
                scene, 'NAND', f"{safe_prefix}_INV{stage + 1}",
                x + stage * stage_step, y, [acc_net, acc_net], inv_net)
            pins = gate.all_pin_positions_scene()
            net_source[inv_net] = (pins[0], gate, 0)
            net_consumers.setdefault(acc_net, []).append((pins[1], gate, 1))
            net_consumers.setdefault(acc_net, []).append((pins[2], gate, 2))
            stage += 1

            is_last = (k == len(term_inv_nets) - 1)
            next_acc = output_net if is_last else f"{safe_prefix}_acc{stage + 1}"
            gate = self._place_generated_gate(
                scene, 'NAND', f"{safe_prefix}_NAND{stage + 1}",
                x + stage * stage_step, y, [inv_net, term_inv_nets[k]], next_acc)
            pins = gate.all_pin_positions_scene()
            net_source[next_acc] = (pins[0], gate, 0)
            net_consumers.setdefault(inv_net, []).append((pins[1], gate, 1))
            net_consumers.setdefault(term_inv_nets[k], []).append((pins[2], gate, 2))
            acc_net = next_acc
            stage += 1

    def _build_circuit_stub(self):
        dlg = AutoBuildCircuitDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        opts = dlg.get_options()
        if not opts['sheet_name']:
            QMessageBox.warning(self, "Nombre requerido",
                                "Indica un nombre para la hoja del circuito.")
            return
        self._last_auto_build_options = opts
        if self._auto_build_sop_circuit(opts):
            QMessageBox.information(
                self, "Armado automatico",
                f"Circuito generado en la hoja '{opts['sheet_name']}'.")

    # ── Pestaña 4: Mapa de Karnaugh (placeholder) ─────────────────────────
    def _build_kmap_tab(self) -> QWidget:
        w = QWidget()
        self._kmap_tab = w
        v = QVBoxLayout(w)
        title = QLabel("<h3>Mapa de Karnaugh</h3>")
        v.addWidget(title)

        top = QHBoxLayout()
        top.addWidget(QLabel("Salida:"))
        self.kmap_output_selector = QComboBox()
        self.kmap_output_selector.currentIndexChanged.connect(self._refresh_kmap)
        top.addWidget(self.kmap_output_selector)
        top.addWidget(QLabel("Grupos visibles:"))
        self.kmap_group_selector = QComboBox()
        self.kmap_group_selector.addItem("SOP", 'sop')
        self.kmap_group_selector.addItem("POS", 'pos')
        self.kmap_group_selector.currentIndexChanged.connect(self._on_kmap_group_mode_changed)
        top.addWidget(self.kmap_group_selector)
        btn_refresh = QPushButton("Actualizar")
        btn_refresh.clicked.connect(self._refresh_kmap)
        top.addWidget(btn_refresh)
        top.addStretch(1)
        v.addLayout(top)

        self.kmap_summary = QLabel("")
        self.kmap_summary.setWordWrap(True)
        v.addWidget(self.kmap_summary)

        self.kmap_table = QTableWidget()
        self.kmap_table.setItemDelegate(KMapGroupDelegate(self.kmap_table))
        self.kmap_table.setMouseTracking(True)
        self.kmap_table.viewport().setMouseTracking(True)
        self.kmap_table.cellEntered.connect(self._on_kmap_cell_entered)
        self.kmap_table.itemChanged.connect(self._on_kmap_cell_changed)
        v.addWidget(self.kmap_table)

        self.kmap_group_hint = QLabel("Pasa el cursor sobre una celda agrupada.")
        self.kmap_group_hint.setTextFormat(Qt.TextFormat.RichText)
        self.kmap_group_hint.setWordWrap(True)
        self.kmap_group_hint.setStyleSheet(
            f"color: {COLORS.get('text_dim', '#7f8c8d')};")
        v.addWidget(self.kmap_group_hint)

        btn_row = QHBoxLayout()
        self.btn_kmap_sop = QPushButton("K-map -> SOP")
        self.btn_kmap_pos = QPushButton("K-map -> POS")
        self.btn_kmap_export = QPushButton("Exportar PNG")
        self.btn_kmap_sop.clicked.connect(lambda: self._simplify_from_kmap('sop'))
        self.btn_kmap_pos.clicked.connect(lambda: self._simplify_from_kmap('pos'))
        self.btn_kmap_export.clicked.connect(self._export_kmap_png)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_kmap_sop)
        btn_row.addWidget(self.btn_kmap_pos)
        btn_row.addWidget(self.btn_kmap_export)
        v.addLayout(btn_row)

        msg = QLabel(
            "<p>Próximamente:</p>"
            "<ul>"
            "<li>Visualización del K-map para cada salida con sus "
            "agrupaciones óptimas.</li>"
            "<li>Edición directa del mapa para introducir 0/1/X.</li>"
            "<li>Generar SOP y POS desde el mapa "
            "y viceversa (ecuación → mapa).</li>"
            "<li>Exportación a imagen.</li>"
            "</ul>")
        msg.setWordWrap(True)
        msg.setVisible(False)
        v.addWidget(msg)
        future = QGroupBox("Opciones futuras")
        future.setVisible(False)
        future_row = QHBoxLayout(future)
        for text in (
                "Exportar K-map",
                "K-map -> SOP/POS",
                "Ecuaciones -> K-map"):
            btn = QPushButton(text)
            btn.setEnabled(False)
            future_row.addWidget(btn)
        v.addWidget(future)
        v.addStretch(1)
        return w

    # ── Cambio de pestaña ─────────────────────────────────────────────────
    def _sync_kmap_outputs(self):
        if not hasattr(self, 'kmap_output_selector'):
            return
        current = self.kmap_output_selector.currentText()
        self.kmap_output_selector.blockSignals(True)
        self.kmap_output_selector.clear()
        self.kmap_output_selector.addItems(self.var_outputs)
        if current in self.var_outputs:
            self.kmap_output_selector.setCurrentText(current)
        self.kmap_output_selector.blockSignals(False)

    def _kmap_axis_labels(self):
        n = len(self.var_inputs)
        row_bits = n // 2
        col_bits = n - row_bits
        row_vars = self.var_inputs[:row_bits]
        col_vars = self.var_inputs[row_bits:]
        row_codes = _gray_codes(row_bits)
        col_codes = _gray_codes(col_bits)

        def label(vars_, code):
            if not vars_:
                return '1'
            return ''.join(vars_) + '=' + (code or '0')

        return row_vars, col_vars, row_codes, col_codes, label

    def _kmap_group_mode(self) -> str:
        if not hasattr(self, 'kmap_group_selector'):
            return 'sop'
        return self.kmap_group_selector.currentData() or 'sop'

    def _on_kmap_group_mode_changed(self, _index: int):
        self._refresh_kmap()

    def _kmap_group_term(self, prime: str, mode: str) -> str:
        notation = getattr(self, '_notation_id', 'math_prime')
        rich = notation == 'math_bar'
        if mode == 'pos':
            return _format_pos_cover([prime], self.var_inputs, notation, rich=rich)
        return _format_sop_cover([prime], self.var_inputs, notation, rich=rich)

    def _kmap_group_hint_html(self, groups: list) -> str:
        parts = []
        for i, group in enumerate(groups):
            label = html.escape(f"{group['mode']} grupo {i + 1}: ")
            term = group['term']
            if '<span' not in term:
                term = html.escape(term)
            parts.append(f"{label}{term}")
        return '<br>'.join(parts)

    def _build_kmap_groups(self, output: str, mode: str) -> list:
        if not output or output not in self.var_outputs:
            return []
        n = len(self.var_inputs)
        if n == 0:
            return []

        mins, dcs, _maxs = self._gather_terms(output)
        cover = (_pos_cover(mins, dcs, n) if mode == 'pos'
                 else _sop_cover(mins, dcs, n))
        colors = [
            '#f5a623', '#4ecca3', '#e94560', '#6c8cff',
            '#ff6f91', '#2dd4bf', '#ffd166', '#a78bfa',
        ]
        groups = []
        for i, prime in enumerate(cover):
            cells = [idx for idx in range(2 ** n)
                     if _qm_covers(prime, idx, n)]
            groups.append({
                'prime': prime,
                'term': self._kmap_group_term(prime, mode),
                'cells': cells,
                'color': colors[i % len(colors)],
                'mode': mode.upper(),
            })
        return groups

    def _on_kmap_cell_entered(self, row: int, col: int):
        item = self.kmap_table.item(row, col)
        groups = item.data(KMAP_GROUPS_ROLE) if item else None
        if not groups:
            self.kmap_group_hint.setText("Esta celda no pertenece a ningun grupo visible.")
            return
        self.kmap_group_hint.setText(self._kmap_group_hint_html(groups))

    def _export_kmap_png(self):
        if not hasattr(self, 'kmap_table') or self.kmap_table.rowCount() == 0:
            QMessageBox.warning(self, "Exportar K-map",
                                "No hay un K-map listo para exportar.")
            return
        output = self.kmap_output_selector.currentText() or 'kmap'
        safe_output = re.sub(r'[^A-Za-z0-9_-]+', '_', output).strip('_') or 'kmap'
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar K-map a PNG", f"{safe_output}_kmap.png",
            "Imagen PNG (*.png)")
        if not path:
            return
        if not path.lower().endswith('.png'):
            path += '.png'
        widget = self.kmap_table
        ok = widget.grab().save(path, 'PNG')
        if ok:
            QMessageBox.information(self, "K-map exportado",
                                    f"Imagen guardada en:\n{path}")
        else:
            QMessageBox.warning(self, "Error",
                                f"No se pudo guardar la imagen:\n{path}")

    def _refresh_kmap(self):
        if not hasattr(self, 'kmap_table'):
            return
        self._sync_kmap_outputs()
        n = len(self.var_inputs)
        output = self.kmap_output_selector.currentText() if self.var_outputs else ''
        mode = self._kmap_group_mode()

        self.kmap_table.blockSignals(True)
        self.kmap_table.clear()
        self._kmap_groups = []
        if n == 0 or not output:
            self.kmap_table.setRowCount(0)
            self.kmap_table.setColumnCount(0)
            self.kmap_summary.setText("Define al menos una entrada y una salida.")
            self.kmap_group_hint.setText("Pasa el cursor sobre una celda agrupada.")
            self.kmap_table.blockSignals(False)
            return
        if n > 4:
            self.kmap_table.setRowCount(0)
            self.kmap_table.setColumnCount(0)
            self.kmap_summary.setText("La vista inicial de K-map soporta de 1 a 4 variables.")
            self.kmap_group_hint.setText("Las agrupaciones visibles estan disponibles hasta 4 variables.")
            self.kmap_table.blockSignals(False)
            return

        row_vars, col_vars, row_codes, col_codes, label = self._kmap_axis_labels()
        self._kmap_groups = self._build_kmap_groups(output, mode)
        self.kmap_table.setRowCount(len(row_codes))
        self.kmap_table.setColumnCount(len(col_codes))
        self.kmap_table.setVerticalHeaderLabels([label(row_vars, c) for c in row_codes])
        self.kmap_table.setHorizontalHeaderLabels([label(col_vars, c) for c in col_codes])

        for r, row_code in enumerate(row_codes):
            for c, col_code in enumerate(col_codes):
                bits = row_code + col_code
                idx = int(bits, 2) if bits else 0
                val = self.truth_data.get((output, idx), '0')
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setData(Qt.ItemDataRole.UserRole, idx)
                cell_groups = [g for g in self._kmap_groups if idx in g['cells']]
                item.setData(KMAP_GROUPS_ROLE, cell_groups)
                if cell_groups:
                    item.setToolTip(self._kmap_group_hint_html(cell_groups))
                self.kmap_table.setItem(r, c, item)

        self.kmap_table.resizeColumnsToContents()
        self.kmap_summary.setText(
            f"K-map de {output}: filas {', '.join(row_vars) or '1'}; "
            f"columnas {', '.join(col_vars) or '1'}. "
            f"Mostrando agrupaciones {mode.upper()} ({len(self._kmap_groups)} grupos).")
        self.kmap_group_hint.setText("Pasa el cursor sobre una celda agrupada.")
        self.kmap_table.blockSignals(False)

    def _on_kmap_cell_changed(self, item):
        output = self.kmap_output_selector.currentText()
        idx = item.data(Qt.ItemDataRole.UserRole)
        if output not in self.var_outputs or idx is None:
            return
        text = item.text().strip().lower()
        if text in ('1', 'true', 'high', 'h'):
            normalized = '1'
        elif text in ('x', 'd', 'dc', '-', '?'):
            normalized = 'X'
        else:
            normalized = '0'
        self.truth_data[(output, int(idx))] = normalized
        self.kmap_table.blockSignals(True)
        item.setText(normalized)
        self.kmap_table.blockSignals(False)
        self._last_simplification.clear()
        self._refresh_kmap()

    def _simplify_from_kmap(self, mode: str):
        output = self.kmap_output_selector.currentText()
        if hasattr(self, 'kmap_group_selector'):
            idx = self.kmap_group_selector.findData(mode)
            if idx >= 0:
                self.kmap_group_selector.setCurrentIndex(idx)
        self._rebuild_truth_table()
        if output in self.var_outputs and hasattr(self, 'output_selector'):
            self.output_selector.setCurrentText(output)
        self._simplify_and_show(mode)

    def _on_tab_changed(self, idx: int):
        if idx == 1:    # Tabla de verdad
            self._rebuild_truth_table()
        elif idx == 2:  # Ecuaciones (mostrar texto previo si existe)
            if self._last_simplification:
                self._populate_eqs_tab(highlight=self._last_highlight)
            else:
                self.eqs_text.setPlainText(
                    "Aún no se ha minimizado ninguna salida.  Ve a la "
                    "pestaña 'Tabla de Verdad', llena los valores y "
                    "pulsa Mintérminos (SOP) o Maxtérminos (POS).")
        elif idx == 3:  # Mapa de Karnaugh
            self._refresh_kmap()
