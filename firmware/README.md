# Firmware de referencia — Osciloscopio físico

El osciloscopio del simulador (`OSC`) puede mostrar muestras de un
microcontrolador conectado por **USB-CDC serial**. Este directorio
contiene el protocolo binario que tu firmware debe implementar y
ejemplos de referencia para los micros soportados.

## Hardware probado / recomendado

| MCU                  | ADC   | Sample rate / canal | Coste aprox. |
|----------------------|-------|---------------------|--------------|
| Raspberry Pi Pico (RP2040) | 12-bit | ~250 kSps (2 ch) | ~$4 |
| Raspberry Pi Pico 2 (RP2350) | 12-bit | ~250 kSps (2 ch) | ~$5 |
| STM32 Black Pill (F411) | 12-bit | ~2 Msps por ADC | ~$10 |

Para señales de audio / op-amps (≤100 kHz) cualquiera de los tres
sirve sobrado. Para >500 kHz usar STM32 con ADCs interleaved.

## Frontend analógico (importante)

El ADC del Pico es **0 V – 3.3 V**, así que necesitas acondicionar la
señal del DUT para que entre en ese rango. Circuito mínimo:

```
      probe          1 kΩ
   ────●─────────[==========]────●────── ADC pin (e.g. GP26)
                                  │
                              ━━━━━━━━━  C 1 nF (anti-aliasing)
                                  │
                                  ┴ GND
```

Para señales de **±5 V** suma un divisor de tensión 1:3 (gain `3.0` en
la calibración del osciloscopio) y opcionalmente un offset DC de +1.65 V
con dos resistores para centrar AC.

Para señales más grandes (±15 V o más) usa un op-amp inversor /
seguidor que escale + desplace dentro de 0–3.3 V.

## Protocolo binario

El programa escucha frames de tamaño variable. Cada frame es:

```
Offset  Bytes  Campo
─────────────────────────────────────────────────────────────
0       2      Magic header  = 0xAA 0x55
2       4      ts_us  : uint32 LE — timestamp del primer sample (µs)
6       2      dt_us  : uint16 LE — intervalo entre muestras (µs)
8       2      N      : uint16 LE — número de pares (ch_a, ch_b)
10      4·N    samples: int16 LE [N pares] — ch_a_0, ch_b_0, …, en mV
```

- **`ts_us`** debe ser monótono entre frames. El programa lo usa como
  base de tiempo. La primera vez se toma como `t=0`.
- **`dt_us` = `1e6 / sample_rate_per_channel`**. Si muestreas a 100 kSps
  por canal, `dt_us = 10`.
- **`N`**: tamaño de bloque. Recomendado **32–256**. Bloques muy
  pequeños suben overhead USB; muy grandes aumentan latencia.
- **Resync**: si se pierde alineación (cable desconectado, etc.) el PC
  busca byte a byte el patrón `0xAA 0x55` y reanuda.

Si solo usas un canal manda el otro en `0` — el PC siempre espera
pares. Mantener bloques pareados simplifica el lector.

## Firmware mínimo — Raspberry Pi Pico (C, Pico SDK)

`pico_scope.c` — proyecto autocontenido que muestrea GP26 (CH A) y
GP27 (CH B) a 100 kSps cada uno y los envía por USB CDC en bloques de
128 pares.

```c
#include <stdio.h>
#include <string.h>
#include "pico/stdlib.h"
#include "hardware/adc.h"
#include "tusb.h"

#define BLOCK_N        128
#define SAMPLE_DT_US   10           // 100 kSps por canal

static uint8_t  buf[10 + BLOCK_N * 4];
static int16_t  *samples = (int16_t *)(buf + 10);

int main(void) {
    stdio_init_all();              // habilita CDC sobre USB
    adc_init();
    adc_gpio_init(26);             // CH A
    adc_gpio_init(27);             // CH B

    uint32_t ts_us = 0;
    // Sync + dt + N fijos en el header (se mantienen entre frames)
    buf[0] = 0xAA; buf[1] = 0x55;
    *(uint16_t *)(buf + 6) = SAMPLE_DT_US;
    *(uint16_t *)(buf + 8) = BLOCK_N;

    while (true) {
        absolute_time_t t0 = get_absolute_time();
        for (int i = 0; i < BLOCK_N; i++) {
            adc_select_input(0);       // GP26
            uint16_t raw_a = adc_read();
            adc_select_input(1);       // GP27
            uint16_t raw_b = adc_read();
            // raw 0-4095 → mV (Vref=3.3V). Sample en mV con signo:
            int16_t mv_a = (int16_t)((raw_a * 3300) / 4095);
            int16_t mv_b = (int16_t)((raw_b * 3300) / 4095);
            samples[2*i]     = mv_a;
            samples[2*i + 1] = mv_b;
            sleep_until(delayed_by_us(t0, (i + 1) * SAMPLE_DT_US));
        }
        // Escribir timestamp del primer sample
        *(uint32_t *)(buf + 2) = ts_us;
        // Enviar frame por USB CDC
        if (tud_cdc_available() || tud_cdc_connected()) {
            tud_cdc_write(buf, sizeof(buf));
            tud_cdc_write_flush();
        }
        ts_us += BLOCK_N * SAMPLE_DT_US;
    }
}
```

Compilar con CMake estándar del Pico SDK:

```bash
mkdir build && cd build
cmake -DPICO_BOARD=pico ..      # o "pico2" para Pico 2 (RP2350)
make
# Arrastrar el .uf2 al USB del Pico (en modo BOOTSEL).
```

## Firmware mínimo — MicroPython (más simple, ligeramente más lento)

`pico_scope.py` — para empezar rápido sin SDK, aunque limitado a
~30 kSps porque cada `read_u16()` lleva varios µs.

```python
import struct, sys, time
from machine import ADC, Pin

adc_a = ADC(Pin(26))
adc_b = ADC(Pin(27))

BLOCK_N = 64
DT_US   = 50                       # 20 kSps por canal

buf = bytearray(10 + BLOCK_N * 4)
buf[0] = 0xAA; buf[1] = 0x55
struct.pack_into('<H', buf, 6, DT_US)
struct.pack_into('<H', buf, 8, BLOCK_N)

ts_us = 0
while True:
    t0 = time.ticks_us()
    for i in range(BLOCK_N):
        ra = adc_a.read_u16()       # 0..65535
        rb = adc_b.read_u16()
        mv_a = (ra * 3300) // 65535
        mv_b = (rb * 3300) // 65535
        struct.pack_into('<hh', buf, 10 + 4 * i, mv_a, mv_b)
        # Espera activa hasta el siguiente sample
        while time.ticks_diff(time.ticks_us(), t0) < (i + 1) * DT_US:
            pass
    struct.pack_into('<I', buf, 2, ts_us)
    sys.stdout.buffer.write(buf)
    ts_us += BLOCK_N * DT_US
```

Guarda como `main.py` en el Pico con `mpremote cp main.py :main.py` y
resetea.

## Cómo conectarlo desde el simulador

1. Coloca un osciloscopio (`OSC`) en el canvas.
2. Doble-click → se abre el panel del osciloscopio.
3. Botón **`Hardware…`** → elige tu puerto (`COM3`, `/dev/ttyACM0`, …).
4. Calibración: si tu frontend es 1:1 (señal cae directo al ADC),
   `ganancia = 1.0` para ambos canales. Si usaste divisor 1:3, pon `3.0`.
5. Aceptar → el panel cambia a **`Desconectar HW`** y empiezas a ver
   las muestras del micro en tiempo real.

Sin hardware puedes elegir **`⟨Mock device⟩`** como puerto para que el
programa genere una senoidal/cuadrada interna y validar la integración.

## Troubleshooting

- **No aparece el puerto**: refresca con el botón ↻; en Linux comprueba
  permisos (`sudo usermod -aG dialout $USER` y vuelve a entrar).
- **Trazas planas**: revisa la calibración. Si el frontend te entrega
  0 V cuando la sonda está libre, deberías ver ruido alrededor de 0.
- **Trazas saturadas**: tu señal supera el rango del ADC. Ajusta el
  divisor o el offset para que la señal viva en 0–3.3 V.
- **Pérdida de sync** (gráfico se ve "roto"): bloques muy grandes
  desbordan los buffers USB. Reduce `BLOCK_N` a 32–64.
