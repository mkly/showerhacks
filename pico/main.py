"""Copy to main.py on a Raspberry Pi Pico running MicroPython.

Wiring: GP0=SDA, GP1=SCL, 3V3=VCC, GND=GND.
Host protocol: READ -> OK,eCO2_ppm,TVOC_ppb; BASELINE -> BASELINE,eCO2_hex,TVOC_hex.
"""

import sys
import time
from machine import I2C, Pin

ADDRESS = 0x58


def crc8(data):
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def word(data):
    if crc8(data[:2]) != data[2]:
        raise OSError("SGP30 CRC mismatch")
    return (data[0] << 8) | data[1]


i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=100_000)
if ADDRESS not in i2c.scan():
    raise OSError("SGP30 not found at 0x58 on GP0/GP1")

initialized = False

while True:
    command = sys.stdin.readline().strip()
    if command not in ("READ", "BASELINE"):
        continue
    try:
        if command == "READ":
            if not initialized:
                i2c.writeto(ADDRESS, b"\x20\x03")  # sgp30_iaq_init
                time.sleep_ms(10)
                initialized = True
            i2c.writeto(ADDRESS, b"\x20\x08")  # sgp30_measure_iaq
            time.sleep_ms(12)
            response = i2c.readfrom(ADDRESS, 6)
            eco2 = word(response[:3])
            tvoc = word(response[3:])
            print("OK,{},{}".format(eco2, tvoc))
        elif not initialized:
            print("ERR,Sensor not initialized")
        else:
            i2c.writeto(ADDRESS, b"\x20\x15")  # sgp30_get_iaq_baseline
            time.sleep_ms(10)
            response = i2c.readfrom(ADDRESS, 6)
            eco2_baseline = word(response[:3])
            tvoc_baseline = word(response[3:])
            print("BASELINE,0x{:04X},0x{:04X}".format(eco2_baseline, tvoc_baseline))
    except Exception as exc:
        print("ERR,{}".format(str(exc).replace(",", ";")))
