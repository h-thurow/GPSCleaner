# GPSCleaner

Corrects GPS recordings where track points deviate from the actual route during a given time window. The affected points are replaced by positions evenly distributed along a reference route. The original file is left unchanged; the result is written to a new file.

## Example

The highlighted section of the GPS track is intended to follow the actual route, which can be seen in magenta in the background: 

![](readme_resources/recording.png)

In Garmin BaseCamp, for example, set the start and end times for the deviations:

![](readme_resources/start_time.png)
![](readme_resources/end_time.png)

Create and export a GPS track of the actual route:

![](readme_resources/reference_path.png)

The GPS track must correspond exactly to the section that is to serve as a reference for all original positions from the start to the end time. The original GPS points are evenly distributed along this track.

And here is the corrected section:

![](readme_resources/cleaned.png)

Of course, the result is only an estimate, but the more evenly the route was covered, the more accurate it will be. It should not include interruptions such as breaks or red lights. Nor should it include U-turns, where the route suddenly heads back in the opposite direction.

## Requirements

Both GPS files must be in **GPX 1.1** format (`xmlns="http://www.topografix.com/GPX/1/1"`). This format is exported by Garmin devices and Garmin BaseCamp, among others.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```
python -m gpscleaner --start TIME --end TIME --orig FILE --reference FILE [--plot]
```

| Option        | Description |
|---------------|-------------|
| `--start`     | Time when the recording begins to deviate from the actual route (UTC, ISO 8601) |
| `--end`       | Time when the recording returns to the actual route (UTC, ISO 8601) |
| `--orig`      | GPX file containing the recording with deviations |
| `--reference` | GPX file containing the actual route for the given time window |
| `--plot`      | Also save a PNG visualisation of the tracks (optional) |

Times must be given in UTC. Garmin BaseCamp displays times in local time — please convert accordingly.

### Example

```bash
python -m gpscleaner \
  --start 2026-03-22T15:06:08Z \
  --end   2026-03-22T15:30:50Z \
  --orig  /path/to/recording.gpx \
  --reference /path/to/reference.gpx
```

The cleaned file is saved in the same directory as `--orig`, with the suffix `_cleaned`:

```
recording.gpx  →  recording_cleaned.gpx
```

## Plot

With `--plot`, an additional PNG file is created showing the original recording (blue), reference route (green), and cleaned track (red) overlaid:

```bash
python -m gpscleaner \
  --start 2026-03-22T15:06:08Z \
  --end   2026-03-22T15:30:50Z \
  --orig  /path/to/recording.gpx \
  --reference /path/to/reference.gpx \
  --plot
```

```
recording.gpx  →  recording_cleaned.gpx
               →  recording_cleaned.png
```

![GPS Tracks](tests/fixtures/260322-recording_cleaned.png)
