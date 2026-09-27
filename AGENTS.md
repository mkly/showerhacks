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

The START button becomes active after three fresh, consecutive room readings,
including 0 ppb during startup. Tap it to
open a 5-second **GET READY!** screen with the underarm animation. The
20-second smell round and peak recording begin when that countdown ends. The
round countdown shows tenths of a second and flashes
orange during the last five seconds. The nose animation grows stronger at 15
seconds remaining and reaches its largest sniff cycle at five seconds. The
round ends automatically at zero. When START is tapped, the game takes the
median of up to the most recent 15 seconds of title-screen readings. At least
three readings are required, with no gap over 2.5 seconds and the latest no more
than 2.5 seconds old. Startup and returning to the title screen require new
samples; persisted five-minute history cannot substitute for a fresh start.
Zero readings participate in the median. The reference is frozen before GET READY.
Readings during GET READY, the round, and score entry are excluded from room
sampling, which resumes on the title screen.

The noise allowance is `ceil(1.4826 * MAD)`, where MAD is the median absolute
deviation from that same recent median. The scoring start line is the reference
plus `max(--threshold, noise_allowance)`; the default threshold remains 10 ppb.
This uses one robust standard deviation as a modest gameplay noise allowance,
not a calibrated probability or a guarantee against room drift. A steady room
at 230 ppb still starts scoring above 240 ppb. Each round scores its highest
rolling three-reading median, requiring a full window and never bridging gaps
longer than 2.5 seconds. Isolated one-second spikes cannot set the peak; sustained
blowing can still resemble a strong response. The increase above that line becomes a score from 1 to
99 using a fixed exponential curve. A 43 ppb increase scores 40, 100 ppb scores
69, and 200 ppb scores 90. The curve caps at 99 after rounding. The 20-segment
bar tracks the round's best score. Its bar
rises from empty to the displayed score over the first five seconds of each
round. Only the large on-screen number wiggles by up to three points; the bar,
saved score, and leaderboard use the stable score.

The score formula is `min(99, 1 + round(98 * (1 - exp(-increase / scale))))`,
where `increase = max(0, peak - start_line)` and
`scale = 200 / ln(98 / 9)`, approximately 83.761 ppb. Solving the scale from
the anchor makes +200 ppb score exactly 90 before rounding. The continuous
curve is increasing, concave, and bounded: small changes stay responsive and
larger increases have diminishing gains. It uses the
[exponential CDF shape](https://www.itl.nist.gov/div898/handbook/eda/section3/eda3667.htm)
as a game mapping, not an assumed statistical distribution or a validated
measure of perceived odor. Python's `expm1` evaluates the expression accurately
near zero. The scale stays fixed across rounds; the leaderboard never sets it.

The existing exponential curve is retained as a gameplay choice. Historical
runs were one person's desk trials in different positions, not a representative
population for calibration or an outlier cutoff. Saved scores are recalculated
from each row's recorded peak and starting line. New rows store the filtered
peak in `peak_tvoc_ppb` and the effective noise allowance/deadband in
`threshold_ppb`; old rows retain their original individual peaks because the
full round samples needed to filter them were not recorded.

Readings above 5,000 ppb are ignored for gameplay as implausible spikes, while
each rejected reading is written to the app log. The once-per-minute baseline
log still records the raw TVOC value if a spike coincides with its sample.

Every successful one-second sensor poll also saves the room buffer to
`showerhacks_room_history.jsonl`, replacing it atomically. The file contains up
to 300 room readings with timestamps, TVOC, and eCO₂; gameplay readings are
excluded. On startup, only valid records less than five minutes old are restored.
The game requires three fresh room readings before START is enabled, with no
five-minute startup wait. Use
`--room-history-file` to change the path.

After the timer ends, the split screen shows your score and projected rank.
Enter three initials using the on-screen gradient letters and tap
**SAVE SCORE**. The leaderboard shows the five highest scores and a QR code
for <https://mkly.github.io/showerhacks>. Completed rounds are saved in `showerhacks_scores.csv` in the current
directory. You can tune the deadband with `--threshold`, or change the CSV path
with `--scores-file`. The CSV's
`score` column contains the normalized value; the starting reference and filtered TVOC peak remain in
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
