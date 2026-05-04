from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QStackedWidget, QWidget,
)

from ui.component_metadata import (
    COMPONENT_NODE_LABELS,
    DEFAULT_NODE_LABELS,
    DIGITAL_BRIDGE_TYPES,
    DIGITAL_COUNT_TYPES,
    DIGITAL_FLIPFLOP_TYPES,
    DIGITAL_GATE_TYPES,
    FOUR_PIN_NODE_LABELS,
    VALUE_LABELS,
)


class ComponentDialog(QDialog):
    def __init__(self, item, colors, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.setWindowTitle(f"Propiedades — {item.comp_type}")
        self.setStyleSheet(f"""
            QDialog {{ background: {colors['panel']}; color: {colors['text']}; }}
            QLabel  {{ color: {colors['text']}; }}
            QLineEdit, QDoubleSpinBox {{
                background: {colors['bg']}; color: {colors['text']};
                border: 1px solid {colors['panel_brd']}; border-radius: 4px;
                padding: 4px;
            }}
            QPushButton {{
                background: {colors['component']}; color: white;
                border-radius: 4px; padding: 6px 16px;
            }}
            QPushButton:hover {{ background: {colors['comp_sel']}; }}
        """)
        self.item = item
        self._build_ui()

    def _build_ui(self):
        layout = QFormLayout(self)
        layout.setSpacing(10)

        is_netlabel = self.item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT')

        self.name_edit = QLineEdit(self.item.name)
        self.value_spin = QDoubleSpinBox()
        self.value_spin.setValue(self.item.value)
        self.node1_edit = QLineEdit(self.item.node1)
        self.node2_edit = QLineEdit(self.item.node2)
        self.node3_edit = None
        self._extra_node_edits = []
        self._sheet_label_edit = None
        self._freq_spin = self._phase_spin = self._mode_combo = None
        self._led_color_combo = None
        self._z_mode_combo = self._z_real = self._z_imag = None
        self._z_mag = self._z_phase = None
        self._pot_wiper_spin = None
        self._xfmr_ratio_spin = self._xfmr_imax_spin = None
        self._node4_edit = None
        self._dig_inputs_spin = self._dig_bits_spin = self._dig_vref_spin = None
        self._dig_tpd_spin = self._dig_clk_edit = self._dig_anode_edit = None

        if is_netlabel:
            kind = 'Entrada' if self.item.comp_type == 'NET_LABEL_IN' else 'Salida'
            layout.addRow(QLabel(f'<b>Net Label — {kind}</b>'))
            self._sheet_label_edit = QLineEdit(self.item.sheet_label)
            self._sheet_label_edit.setPlaceholderText('ej: VCC, CLK, RESET…')
            layout.addRow('Nombre de red:', self._sheet_label_edit)
            layout.addRow(QLabel(
                '<small>Todos los net labels con el mismo nombre<br>'
                'quedan eléctricamente conectados,<br>'
                'en la misma hoja o en hojas distintas.</small>'
            ))
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addRow(buttons)
            return

        layout.addRow("Nombre:", self.name_edit)

        self.value_spin = QDoubleSpinBox()
        self.value_spin.setRange(-1e12, 1e12)
        self.value_spin.setDecimals(6)
        self.value_spin.setValue(self.item.value)
        layout.addRow(VALUE_LABELS.get(self.item.comp_type, 'Valor:'), self.value_spin)

        if self.item.comp_type in DIGITAL_GATE_TYPES:
            n_in = self.item.dig_inputs if self.item.comp_type != 'NOT' else 1
            self.node1_edit = QLineEdit(self.item.node1)
            layout.addRow('Salida (Y):', self.node1_edit)
            self.node2_edit = QLineEdit(self.item.node2)
            layout.addRow('Entrada 1 (A):', self.node2_edit)
            self.node3_edit = None
            self._extra_node_edits = []
            if n_in >= 2:
                n3_val = self.item.node3 if hasattr(self.item, 'node3') else ''
                self.node3_edit = QLineEdit(n3_val)
                layout.addRow('Entrada 2 (B):', self.node3_edit)
            for i in range(2, n_in):
                extra_nodes = getattr(self.item, 'dig_input_nodes', [])
                val = extra_nodes[i - 2] if len(extra_nodes) > i - 2 else ''
                edit = QLineEdit(val)
                layout.addRow(f'Entrada {i+1}:', edit)
                self._extra_node_edits.append(edit)
        elif self.item.comp_type in DIGITAL_FLIPFLOP_TYPES:
            self.node1_edit = QLineEdit(self.item.node1)
            self.node2_edit = QLineEdit(self.item.node2)
            layout.addRow('Salida Q:', self.node1_edit)
            layout.addRow('Dato D / J:', self.node2_edit)
            self.node3_edit = QLineEdit(self.item.node3 if hasattr(self.item, 'node3') else '')
            layout.addRow('CLK:', self.node3_edit)
            self._extra_node_edits = []
        elif self.item.comp_type in FOUR_PIN_NODE_LABELS:
            lbls = FOUR_PIN_NODE_LABELS[self.item.comp_type]
            self.node1_edit = QLineEdit(self.item.node1)
            self.node2_edit = QLineEdit(self.item.node2)
            self.node3_edit = QLineEdit(self.item.node3)
            self._node4_edit = QLineEdit(self.item.node4)
            layout.addRow(lbls[0] + ':', self.node1_edit)
            layout.addRow(lbls[1] + ':', self.node2_edit)
            layout.addRow(lbls[2] + ':', self.node3_edit)
            layout.addRow(lbls[3] + ':', self._node4_edit)
            self._extra_node_edits = []
        else:
            self.node1_edit = QLineEdit(self.item.node1)
            self.node2_edit = QLineEdit(self.item.node2)
            self.node3_edit = None
            self._extra_node_edits = []
            lbl1, lbl2, lbl3 = COMPONENT_NODE_LABELS.get(
                self.item.comp_type, DEFAULT_NODE_LABELS)
    
            # Para fuentes, invertir el orden visual porque:
            # node1 = pin izquierdo = negativo (−)
            # node2 = pin derecho = positivo (+)
            if self.item.comp_type in ('V', 'VAC', 'I'):
                layout.addRow("Nodo +" + ':', self.node2_edit)  # Nodo +
                layout.addRow("Nodo -" + ':', self.node1_edit)  # Nodo −
            else:
                layout.addRow(lbl1 + ':', self.node1_edit)
                layout.addRow(lbl2 + ':', self.node2_edit)
    
            if lbl3 is not None:
                self.node3_edit = QLineEdit(self.item.node3)
                layout.addRow(lbl3 + ':', self.node3_edit)

        self._led_color_combo = None
        if self.item.comp_type == 'LED':
            self._led_color_combo = QComboBox()
            self._led_color_combo.addItems(['red', 'green', 'blue', 'yellow', 'white', 'orange'])
            self._led_color_combo.setCurrentText(getattr(self.item, 'led_color', 'red'))
            layout.addRow('Color del LED:', self._led_color_combo)

        self._freq_spin = None
        self._phase_spin = None
        self._mode_combo = None
        if self.item.comp_type == 'VAC':
            self._mode_combo = QComboBox()
            self._mode_combo.addItems(['rms', 'peak'])
            self._mode_combo.setCurrentText(self.item.ac_mode)
            layout.addRow('Modo amplitud:', self._mode_combo)

            self._freq_spin = QDoubleSpinBox()
            self._freq_spin.setRange(0.001, 1e9)
            self._freq_spin.setDecimals(3)
            self._freq_spin.setSuffix(' Hz')
            self._freq_spin.setValue(self.item.frequency)
            layout.addRow('Frecuencia:', self._freq_spin)

            self._phase_spin = QDoubleSpinBox()
            self._phase_spin.setRange(-360.0, 360.0)
            self._phase_spin.setDecimals(2)
            self._phase_spin.setSuffix(' °')
            self._phase_spin.setValue(self.item.phase_deg)
            layout.addRow('Fase:', self._phase_spin)

        self._z_mode_combo = None
        self._z_real = self._z_imag = self._z_mag = self._z_phase = None
        if self.item.comp_type == 'Z':
            self._z_mode_combo = QComboBox()
            self._z_mode_combo.addItems(['Rectangular (R + jX)', 'Fasorial |Z|∠θ'])
            self._z_mode_combo.setCurrentIndex(0 if self.item.z_mode == 'rect' else 1)
            layout.addRow("Modo entrada:", self._z_mode_combo)

            w_rect = QWidget()
            l_rect = QHBoxLayout(w_rect)
            self._z_real = QDoubleSpinBox()
            self._z_real.setRange(-1e12, 1e12)
            self._z_real.setDecimals(6)
            self._z_real.setSuffix(" Ω")
            self._z_real.setValue(self.item.z_real)
            self._z_imag = QDoubleSpinBox()
            self._z_imag.setRange(-1e12, 1e12)
            self._z_imag.setDecimals(6)
            self._z_imag.setSuffix(" jΩ")
            self._z_imag.setValue(self.item.z_imag)
            l_rect.addWidget(QLabel("Real:"))
            l_rect.addWidget(self._z_real)
            l_rect.addWidget(QLabel("Imag:"))
            l_rect.addWidget(self._z_imag)

            w_phas = QWidget()
            l_phas = QHBoxLayout(w_phas)
            self._z_mag = QDoubleSpinBox()
            self._z_mag.setRange(0, 1e12)
            self._z_mag.setDecimals(6)
            self._z_mag.setSuffix(" Ω")
            self._z_mag.setValue(self.item.z_mag)
            self._z_phase = QDoubleSpinBox()
            self._z_phase.setRange(-360, 360)
            self._z_phase.setDecimals(2)
            self._z_phase.setSuffix(" °")
            self._z_phase.setValue(self.item.z_phase)
            l_phas.addWidget(QLabel("|Z|:"))
            l_phas.addWidget(self._z_mag)
            l_phas.addWidget(QLabel("∠:"))
            l_phas.addWidget(self._z_phase)

            self._z_stack = QStackedWidget()
            self._z_stack.addWidget(w_rect)
            self._z_stack.addWidget(w_phas)
            layout.addRow(self._z_stack)
            self._z_mode_combo.currentIndexChanged.connect(self._z_stack.setCurrentIndex)

        self._pot_wiper_spin = None
        self._xfmr_ratio_spin = None
        self._xfmr_imax_spin = None

        if self.item.comp_type == 'POT':
            self._pot_wiper_spin = QDoubleSpinBox()
            self._pot_wiper_spin.setRange(0.0, 1.0)
            self._pot_wiper_spin.setDecimals(3)
            self._pot_wiper_spin.setSingleStep(0.05)
            self._pot_wiper_spin.setValue(self.item.pot_wiper)
            layout.addRow("Cursor (0–1):", self._pot_wiper_spin)

        if self.item.comp_type == 'XFMR':
            self._xfmr_ratio_spin = QDoubleSpinBox()
            self._xfmr_ratio_spin.setRange(0.001, 1000.0)
            self._xfmr_ratio_spin.setDecimals(4)
            self._xfmr_ratio_spin.setValue(self.item.xfmr_ratio)
            layout.addRow("Relación n = N1/N2:", self._xfmr_ratio_spin)

            self._xfmr_imax_spin = QDoubleSpinBox()
            self._xfmr_imax_spin.setRange(0.001, 1e6)
            self._xfmr_imax_spin.setDecimals(3)
            self._xfmr_imax_spin.setSuffix(' A')
            self._xfmr_imax_spin.setValue(self.item.xfmr_imax)
            layout.addRow("Corriente máx primaria:", self._xfmr_imax_spin)

        self._dig_inputs_spin = None
        self._dig_bits_spin = None
        self._dig_vref_spin = None
        self._dig_tpd_spin = None
        self._dig_clk_edit = None
        self._dig_anode_edit = None

        if self.item.comp_type in DIGITAL_GATE_TYPES:
            if self.item.comp_type != 'NOT':
                self._dig_inputs_spin = QDoubleSpinBox()
                self._dig_inputs_spin.setRange(2, 8)
                self._dig_inputs_spin.setDecimals(0)
                self._dig_inputs_spin.setValue(self.item.dig_inputs)
                layout.addRow('Nº entradas:', self._dig_inputs_spin)

            self._dig_tpd_spin = QDoubleSpinBox()
            self._dig_tpd_spin.setRange(0.001, 1000)
            self._dig_tpd_spin.setDecimals(3)
            self._dig_tpd_spin.setSuffix(' ns')
            self._dig_tpd_spin.setValue(self.item.dig_tpd_ns)
            layout.addRow('Retardo tpd:', self._dig_tpd_spin)

        elif self.item.comp_type in DIGITAL_FLIPFLOP_TYPES:
            self._dig_clk_edit = QLineEdit(self.item.dig_clk)
            layout.addRow('Net CLK:', self._dig_clk_edit)

            self._dig_tpd_spin = QDoubleSpinBox()
            self._dig_tpd_spin.setRange(0.001, 1000)
            self._dig_tpd_spin.setDecimals(3)
            self._dig_tpd_spin.setSuffix(' ns')
            self._dig_tpd_spin.setValue(self.item.dig_tpd_ns)
            layout.addRow('Retardo tpd:', self._dig_tpd_spin)

        elif self.item.comp_type in DIGITAL_BRIDGE_TYPES:
            self._dig_bits_spin = QDoubleSpinBox()
            self._dig_bits_spin.setRange(1, 24)
            self._dig_bits_spin.setDecimals(0)
            self._dig_bits_spin.setValue(self.item.dig_bits_adc)
            layout.addRow('Resolución (bits):', self._dig_bits_spin)

            self._dig_vref_spin = QDoubleSpinBox()
            self._dig_vref_spin.setRange(0.1, 100.0)
            self._dig_vref_spin.setDecimals(3)
            self._dig_vref_spin.setSuffix(' V')
            self._dig_vref_spin.setValue(self.item.dig_vref)
            layout.addRow('Vref:', self._dig_vref_spin)

            self._dig_anode_edit = QLineEdit(self.item.dig_analog_node)
            layout.addRow('Nodo analógico MNA:', self._dig_anode_edit)

            self._dig_clk_edit = QLineEdit(self.item.dig_clk)
            layout.addRow('Net CLK (opcional):', self._dig_clk_edit)

        elif self.item.comp_type in DIGITAL_COUNT_TYPES:
            self._dig_bits_spin = QDoubleSpinBox()
            self._dig_bits_spin.setRange(1, 32)
            self._dig_bits_spin.setDecimals(0)
            self._dig_bits_spin.setValue(self.item.dig_bits)
            layout.addRow('Bits:', self._dig_bits_spin)

            self._dig_clk_edit = QLineEdit(self.item.dig_clk)
            layout.addRow('Net CLK:', self._dig_clk_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self):
        data = {
            'name': self.name_edit.text(),
            'value': self.value_spin.value(),
            'node1': self.node1_edit.text(),
            'node2': self.node2_edit.text(),
            'node3': self.node3_edit.text() if self.node3_edit else '',
            'frequency': self._freq_spin.value() if self._freq_spin else 60.0,
            'phase_deg': self._phase_spin.value() if self._phase_spin else 0.0,
            'ac_mode': self._mode_combo.currentText() if self._mode_combo else 'rms',
            'led_color': self._led_color_combo.currentText() if self._led_color_combo else 'red',
        }
        if self.item.comp_type == 'Z' and self._z_mode_combo is not None:
            data['z_mode'] = 'rect' if self._z_mode_combo.currentIndex() == 0 else 'phasor'
            data['z_real'] = self._z_real.value()
            data['z_imag'] = self._z_imag.value()
            data['z_mag'] = self._z_mag.value()
            data['z_phase'] = self._z_phase.value()
        if self._dig_inputs_spin is not None:
            data['dig_inputs'] = int(self._dig_inputs_spin.value())
        if self._dig_bits_spin is not None:
            data['dig_bits'] = int(self._dig_bits_spin.value())
            data['dig_bits_adc'] = int(self._dig_bits_spin.value())
        if self._dig_vref_spin is not None:
            data['dig_vref'] = self._dig_vref_spin.value()
        if self._dig_tpd_spin is not None:
            data['dig_tpd_ns'] = self._dig_tpd_spin.value()
        if self._dig_clk_edit is not None:
            data['dig_clk'] = self._dig_clk_edit.text()
        if self._dig_anode_edit is not None:
            data['dig_analog_node'] = self._dig_anode_edit.text()
        if hasattr(self, '_extra_node_edits') and self._extra_node_edits:
            data['dig_input_nodes'] = [e.text() for e in self._extra_node_edits]
        if hasattr(self, '_node4_edit') and self._node4_edit is not None:
            data['node4'] = self._node4_edit.text()
        if self._pot_wiper_spin is not None:
            data['pot_wiper'] = self._pot_wiper_spin.value()
        if self._xfmr_ratio_spin is not None:
            data['xfmr_ratio'] = self._xfmr_ratio_spin.value()
        if self._xfmr_imax_spin is not None:
            data['xfmr_imax'] = self._xfmr_imax_spin.value()
        if self._sheet_label_edit is not None:
            data['sheet_label'] = self._sheet_label_edit.text()
        return data
