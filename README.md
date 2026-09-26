# Smell City

An Arcade touchscreen game for an SGP30 connected to a Raspberry Pi Pico. The
desktop requests one measurement per second over USB serial. A background thread
keeps the most recent 50 readings in a bounded `deque`.

## Hardware

Connect SGP30 SDA to Pico GP0, SCL to GP1, VCC to 3V3, and GND to GND. The
SGP30 uses I²C address `0x58`. Connect the Pico to the desktop with a data USB
cable.

## Pico setup

Install MicroPython on the Pico, then copy [pico/main.py](pico/main.py) to its
root filesystem as `main.py`. Reset the Pico. The script waits for `READ` lines
on the USB serial port and returns `OK,eCO2,TVOC`. It also answers `BASELINE`
with the SGP30's eCO₂ and TVOC baseline words in hex. It checks the response
CRC before returning values.

The MicroPython REPL shares that USB serial port; close Thonny or another serial
monitor before starting the desktop app.

## Desktop setup

Install dependencies with `uv sync`. Find the Pico serial port (for example,
`/dev/ttyACM0` on Linux or a `COM` port on Windows), then run:

```sh
uv run smellcity --port /dev/ttyACM0
```

For a sensor-free preview, run `uv run smellcity --dev --windowed --scale 4`.
Dev mode supplies changing TVOC readings and does not write sensor baselines.

The app opens full screen. It draws to a 480×270 pixel canvas and scales that
canvas with sharp edges: 4× on the 1080p target display and 8× on a 4K display.
Use `--windowed` to preview it at native 480×270 pixels. For a larger sharp
window on a 4K monitor, run
`uv run smellcity --port /dev/ttyACM0 --windowed --scale 4` for a 1920×1080
window. Scaling always uses whole pixels and nearest
neighbor filtering.

The START button becomes active once a valid TVOC reading arrives, including
0 ppb during startup. Tap it to
open a 7-second **GET READY!** screen with the underarm animation. The
24-second smell round and peak recording begin when that countdown ends. The
round countdown shows tenths of a second and flashes
orange during the last five seconds. The nose animation grows stronger at 15
seconds remaining and reaches its largest sniff cycle at five seconds. The
round ends automatically at zero. When START is tapped, the game takes the
median of the preceding minute's room readings as a fixed reference. It
excludes readings taken during GET READY and the active round from future room
references, then resumes room sampling on the title screen. The score is the
round's peak TVOC above that reference
plus 10 ppb. For example, a room at 230 ppb starts scoring above 240 ppb. The
meter uses a logarithmic scale, so a 500 ppb increase and a 1,000 ppb increase
produce different fills. The default ceiling is 60,000 ppb, the SGP30's maximum
TVOC output.

After the timer ends, the split screen shows your score and projected rank.
Enter three initials using the on-screen gradient letters and tap
**SAVE SCORE**. The leaderboard shows the five highest scores and a QR code
for <https://showerhacks.mkly.workers.dev>. Completed rounds are saved in `smellcity_scores.csv` in the current
directory. You can tune the deadband and log scale ceiling with `--threshold`
and `--meter-ceiling`, or change the CSV path with `--scores-file`.

The game text uses [He's On Fire](https://fontstruct.com/fontstructions/show/748820)
by Jamie, licensed under [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/).
The font is rasterized with hard pixel edges before the canvas is scaled. The
font's license and readme are included alongside the bundled font.

Every minute, the desktop app also requests the sensor's two IAQ baseline words
and appends a JSON object to `sgp30_baselines.jsonl` with the timestamp, latest
TVOC, and both hex values. Use `--baseline-file` to change the path. These words
are the SGP30's internal compensation state; they are separate from the game's
room reference. [Sensirion recommends](https://files.seeedstudio.com/wiki/Grove-VOC_and_eCO2_Gas_Sensor-SGP30/res/Sensirion_Gas_Sensors_SGP30_Driver-Integration-Guide_HW_I2C.pdf)
waiting 12 hours before using a newly learned pair for restoration, then saving
roughly hourly. A saved pair is valid for at most seven days while the sensor is
off. This prototype records every minute as requested and does not restore the
words automatically.

If the Pico or sensor is unavailable, the window shows the connection or read
error. The SGP30 normally reports 0 ppb TVOC for roughly its first 15 seconds
after initialization.
