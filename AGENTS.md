# ShowerHacks

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
uv run showerhacks --port /dev/ttyACM0
```

For a sensor-free preview, run `uv run showerhacks --dev --windowed --scale 4`.
Dev mode supplies changing TVOC readings and does not read or write room history
or write sensor baselines.

The app opens full screen. It draws to a 480×270 pixel canvas and scales that
canvas with sharp edges: 4× on the 1080p target display and 8× on a 4K display.
Use `--windowed` to preview it at native 480×270 pixels. For a larger sharp
window on a 4K monitor, run
`uv run showerhacks --port /dev/ttyACM0 --windowed --scale 4` for a 1920×1080
window. Scaling always uses whole pixels and nearest
neighbor filtering.

The START button becomes active once a valid TVOC reading arrives, including
0 ppb during startup. Tap it to
open a 5-second **GET READY!** screen with the underarm animation. The
20-second smell round and peak recording begin when that countdown ends. The
round countdown shows tenths of a second and flashes
orange during the last five seconds. The nose animation grows stronger at 15
seconds remaining and reaches its largest sniff cycle at five seconds. The
round ends automatically at zero. When START is tapped, the game takes the
average of the preceding five minutes of room readings after rejecting outliers
more than three scaled median absolute deviations from the median (with a
minimum tolerance of 10 ppb). Zero readings are excluded while positive readings
exist. The reference can fall to the most recent 15-second median when at least
three room readings are available in that interval. This lets it recover after
a spike without following one low reading. It
excludes readings taken during GET READY and the active round from future room
references, then resumes room sampling on the title screen. A round's start line
is the room reference plus 10 ppb. For example, a room at 230 ppb sets the
start line at 240 ppb. The increase above that line becomes a score from 1 to
99 using a fixed curve that gives common readings more separation and caps at
99 for large increases. An increase of 75 ppb above the start line scores about
51, giving smaller responses more visible movement. The 20-segment bar tracks the round's best score, so a
500 ppb increase and a 1,000 ppb increase produce different fills. Its bar
rises from empty to the displayed score over the first five seconds of each
round. Only the large on-screen number wiggles by up to three points; the bar,
saved score, and leaderboard use the stable score.
Readings above 5,000 ppb are ignored for gameplay as implausible spikes, while
each rejected reading is written to the app log. The once-per-minute baseline
log still records the raw TVOC value if a spike coincides with its sample.

Every successful one-second sensor poll also saves the room buffer to
`showerhacks_room_history.jsonl`, replacing it atomically. The file contains up
to 300 room readings with timestamps, TVOC, and eCO₂; gameplay readings are
excluded. On startup, only valid records less than five minutes old are restored.
The game still requires a fresh sensor reading before START is enabled. It uses
whatever recent history is available, with no five-minute startup wait. Use
`--room-history-file` to change the path.

After the timer ends, the split screen shows your score and projected rank.
Enter three initials using the on-screen gradient letters and tap
**SAVE SCORE**. The leaderboard shows the five highest scores and a QR code
for <https://mkly.github.io/showerhacks>. Completed rounds are saved in `showerhacks_scores.csv` in the current
directory. You can tune the deadband with `--threshold`, or change the CSV path
with `--scores-file`. The CSV's
`score` column contains the normalized value; raw TVOC measurements remain in
the other columns. `web/index.html` reads the public CSV and refreshes every
15 seconds.

To publish the scores for the website, set `SHOWERHACKS_S3_BUCKET` to an S3
bucket name and provide AWS credentials through the normal AWS credential chain.
After each local score save, a background worker uploads the complete CSV as
`public/showerhacks_scores.csv` with a CSV content type. Upload failures are
written to stderr; the local CSV remains the source of truth. Dev mode never
uploads, even when the environment variable is set. The production bucket is
`showerhacks-public-795385723395`, and the browser URL is
<https://showerhacks-public-795385723395.s3.us-west-2.amazonaws.com/public/showerhacks_scores.csv>.

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
