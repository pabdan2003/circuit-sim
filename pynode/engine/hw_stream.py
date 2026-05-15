"""
Hilo lector de muestras del osciloscopio físico.

Lee un flujo de bytes (serial real o mock interno) y decodifica frames
binarios que el firmware del micro envía. Cada frame trae un bloque de
N pares (ch_a, ch_b) en mV, junto con el timestamp del primer sample
y el intervalo de muestreo. Los frames se sincronizan por dos bytes
mágicos [0xAA 0x55]; si se pierde alineación el lector escanea byte a
byte hasta reencontrar el patrón.

Layout binario:
    [0xAA][0x55]               # 2 bytes sync
    [ts_us  : u32 LE]          # timestamp del primer sample (microseg)
    [dt_us  : u16 LE]          # intervalo de muestreo (microseg)
    [N      : u16 LE]          # número de pares (ch_a, ch_b)
    [ ch_a_0 : i16 LE ]
    [ ch_b_0 : i16 LE ]
    ...                        # ×N pares en mV
"""
from __future__ import annotations

import math
import struct
import time
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal


SYNC0 = 0xAA
SYNC1 = 0x55
_HEADER_FMT = '<IHH'   # ts_us, dt_us, N (sin contar los 2 bytes de sync)
_HEADER_LEN = struct.calcsize(_HEADER_FMT)   # 8 bytes


class HardwareStreamThread(QThread):
    """Hilo que produce muestras (timestamps + ch_a + ch_b) a partir de un
    puerto serial o de un mock interno.

    Señales:
        samples_received(t_list, va_list, vb_list)  ← cada frame leído,
            ya convertidas con la ganancia/offset por canal.
        error_occurred(str)                          ← fallo al abrir/leer.
        connection_state(bool)                       ← True conectado.
    """
    samples_received = pyqtSignal(list, list, list)
    error_occurred   = pyqtSignal(str)
    connection_state = pyqtSignal(bool)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.cfg = dict(config)
        self._stop = False
        # Buffer interno para reensamblar frames cuando los reads del puerto
        # llegan partidos.
        self._buf = bytearray()
        # Tiempo de referencia: nos llega ts_us absoluto del micro. Para que
        # el osciloscopio del programa muestre tiempo desde "ahora", llevamos
        # un offset que se calibra con el primer frame.
        self._t0_micro: Optional[int] = None
        self._t0_local: float = 0.0

    # ── Loop principal ───────────────────────────────────────────────────
    def run(self):
        try:
            if self.cfg.get('is_mock'):
                self._run_mock()
            else:
                self._run_serial()
        except Exception as e:
            self.error_occurred.emit(f'Hilo HW falló: {e}')
        finally:
            self.connection_state.emit(False)

    def stop(self):
        """Solicita la parada del hilo. Bloquea hasta que termine."""
        self._stop = True
        self.wait(2000)

    # ── Backend: serial real ────────────────────────────────────────────
    def _run_serial(self):
        try:
            import serial   # type: ignore
        except Exception:
            self.error_occurred.emit(
                'pyserial no está instalado. Usa Mock device o instala con '
                '`pip install pyserial`.')
            return
        port = self.cfg.get('port', '')
        baud = int(self.cfg.get('baud', 921600))
        try:
            ser = serial.Serial(port, baud, timeout=0.1)
        except Exception as e:
            self.error_occurred.emit(f'No pude abrir {port}: {e}')
            return
        self.connection_state.emit(True)
        try:
            while not self._stop:
                chunk = ser.read(4096)
                if not chunk:
                    continue
                self._buf.extend(chunk)
                self._drain_frames()
        finally:
            try:
                ser.close()
            except Exception:
                pass

    # ── Backend: mock device ────────────────────────────────────────────
    def _run_mock(self):
        """Genera una onda configurable y la entrega como si viniera de un
        puerto real. Útil para desarrollar/depurar sin hardware. La forma
        de onda se selecciona en `cfg['mock_wave']`."""
        wave = self.cfg.get('mock_wave', 'Senoidal')
        f = float(self.cfg.get('mock_freq', 1000.0))
        amp = float(self.cfg.get('mock_amp', 1.0))
        rate = float(self.cfg.get('mock_rate', 50_000.0))
        dt_s = 1.0 / max(rate, 1.0)
        # Para evitar saturar la UI, agrupamos muestras en bloques de
        # ~20 ms reales antes de emitir. A 50 kSps son 1000 muestras/bloque.
        block_seconds = 0.02
        block_n = max(8, int(rate * block_seconds))
        period = 1.0 / max(f, 1e-9)
        t_sim = 0.0
        self.connection_state.emit(True)
        t_real_start = time.monotonic()
        next_block_end = t_real_start + block_seconds
        while not self._stop:
            # Generar bloque
            ts = [t_sim + i * dt_s for i in range(block_n)]
            va = []
            vb = []
            for t in ts:
                phase = (t / period) % 1.0
                if wave.startswith('Cuadr'):
                    y = amp if phase < 0.5 else -amp
                elif wave.startswith('Triang'):
                    y = (4.0 * amp) * (phase if phase < 0.5 else (1.0 - phase)) - amp
                else:
                    y = amp * math.sin(2.0 * math.pi * phase)
                va.append(y)
                # Canal B: misma onda con desfase de 90° (cuadratura)
                phase_b = (t / period + 0.25) % 1.0
                if wave.startswith('Cuadr'):
                    yb = amp if phase_b < 0.5 else -amp
                elif wave.startswith('Triang'):
                    yb = (4.0 * amp) * (phase_b if phase_b < 0.5 else (1.0 - phase_b)) - amp
                else:
                    yb = amp * math.sin(2.0 * math.pi * phase_b)
                vb.append(yb)
            # Aplicar calibración inversa: el "HW" enviaría mV, así que
            # aquí ya entregamos voltios calibrados. Mantenemos la
            # ganancia/offset por consistencia con el path serial.
            ga = float(self.cfg.get('gain_a', 1.0))
            oa = float(self.cfg.get('offset_a', 0.0))
            gb = float(self.cfg.get('gain_b', 1.0))
            ob = float(self.cfg.get('offset_b', 0.0))
            va = [ga * v + oa for v in va]
            vb = [gb * v + ob for v in vb]
            self.samples_received.emit(ts, va, vb)
            t_sim += block_n * dt_s
            # Esperar a que pase el tiempo real correspondiente para no
            # llenar la UI más rápido de lo que puede dibujar.
            now = time.monotonic()
            if next_block_end > now:
                time.sleep(next_block_end - now)
            next_block_end += block_seconds

    # ── Decoder de frames ───────────────────────────────────────────────
    def _drain_frames(self):
        """Drena cuantos frames completos haya en el buffer interno."""
        while True:
            if not self._find_sync():
                return   # no hay sync — espera más datos
            if len(self._buf) < 2 + _HEADER_LEN:
                return
            ts_us, dt_us, n_pairs = struct.unpack(
                _HEADER_FMT, bytes(self._buf[2:2 + _HEADER_LEN]))
            payload_len = n_pairs * 4   # 2 bytes ch_a + 2 bytes ch_b
            total_len = 2 + _HEADER_LEN + payload_len
            if len(self._buf) < total_len:
                return   # frame incompleto — espera más datos
            payload = bytes(self._buf[2 + _HEADER_LEN:total_len])
            # Avanzar buffer
            del self._buf[:total_len]
            # Decodificar payload: int16 LE
            try:
                samples = struct.unpack(f'<{n_pairs * 2}h', payload)
            except struct.error:
                continue
            self._emit_samples(ts_us, dt_us, samples)

    def _find_sync(self) -> bool:
        """Avanza self._buf hasta dejar [0xAA 0x55] al inicio. Devuelve
        True si quedó sincronizado; False si el buffer se agotó."""
        n = len(self._buf)
        for i in range(n - 1):
            if self._buf[i] == SYNC0 and self._buf[i + 1] == SYNC1:
                if i > 0:
                    del self._buf[:i]
                return True
        # Sin patrón: descartar todo menos el último byte (podría ser SYNC0)
        if n >= 1:
            del self._buf[:n - 1]
        return False

    def _emit_samples(self, ts_us: int, dt_us: int, samples_int16: tuple):
        """Convierte el bloque a (timestamps, va, vb) en V y emite la señal."""
        if not samples_int16:
            return
        n_pairs = len(samples_int16) // 2
        # Calibrar
        ga = float(self.cfg.get('gain_a', 1.0))
        oa = float(self.cfg.get('offset_a', 0.0))
        gb = float(self.cfg.get('gain_b', 1.0))
        ob = float(self.cfg.get('offset_b', 0.0))
        ts = []
        va = []
        vb = []
        # El micro envía mV. ganancia · mV + offset_V = V mostrado.
        if self._t0_micro is None:
            self._t0_micro = ts_us
            self._t0_local = time.monotonic()
        for i in range(n_pairs):
            t_micro = ts_us + i * dt_us
            t_s = (t_micro - self._t0_micro) / 1_000_000.0
            ts.append(t_s)
            a_mv = samples_int16[2 * i]
            b_mv = samples_int16[2 * i + 1]
            va.append(ga * (a_mv / 1000.0) + oa)
            vb.append(gb * (b_mv / 1000.0) + ob)
        self.samples_received.emit(ts, va, vb)
