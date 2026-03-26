import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import typer

from src.gpscleaner.gpscleaner import GPSCleaner, GPSSampleRateReducer

app = typer.Typer(add_completion=False)

GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"


def _parse_datetime(value: str, option_name: str) -> datetime:
    """
    Parse a datetime string in ISO 8601 format (e.g. 2026-03-22T13:10:00Z).
    Raises a Typer error with a clear message if the format is invalid.
    """
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise typer.BadParameter(
            f"'{value}' is not a valid date/time. Expected format: 2026-03-22T13:10:00Z",
            param_hint=f"'--{option_name}'",
        )


def _extract_coords(gpx_file: Path) -> tuple[list[float], list[float]]:
    """Return two lists (latitudes, longitudes) for all track points in a GPX file."""
    root = ET.parse(gpx_file).getroot()
    lats, lons = [], []
    for trkpt in root.iter(f"{{{GPX_NAMESPACE}}}trkpt"):
        lats.append(float(trkpt.attrib["lat"]))
        lons.append(float(trkpt.attrib["lon"]))
    return lats, lons


def _plot_tracks(
    recording: Path,
    target: Path | None,
    cleaned: Path,
    output_png: Path,
) -> None:
    """
    Plot the original recording, optionally a target route, and the cleaned
    recording on a single map and save the result as a PNG file.
    """
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 10))

    tracks = [
        (recording, "steelblue", "Recording (original)", 0.4, 1.0),
        (target,    "green",     "Target route",         2.0, 8.0),
        (cleaned,   "tomato",    "Cleaned recording",    1.2, 4.0),
    ]

    for gpx_file, color, label, linewidth, markersize in tracks:
        if gpx_file is None or not gpx_file.exists():
            continue
        lats, lons = _extract_coords(gpx_file)
        ax.plot(lons, lats, color=color, linewidth=linewidth, label=label)
        ax.plot(lons[0], lats[0], "o", color=color, markersize=markersize)

    ax.set_aspect("equal")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("GPS Tracks")
    ax.legend()
    ax.grid(True, linestyle="--", alpha=0.4)

    plt.tight_layout()
    plt.savefig(output_png, dpi=150)
    plt.close(fig)
    print(f"Plot saved to: {output_png}")


@app.command()
def main(
    orig: Path = typer.Option(
        ...,
        exists=True,
        help="Path to the GPX recording",
    ),
    start: str | None = typer.Option(
        None,
        help="Start of the deviation window (UTC), e.g. 2026-03-22T13:10:00Z",
    ),
    end: str | None = typer.Option(
        None,
        help="End of the deviation window (UTC), e.g. 2026-03-22T13:20:00Z",
    ),
    reference: Path | None = typer.Option(
        None,
        exists=True,
        help="Path to the GPX file with the actual route",
    ),
    sample_rate: float | None = typer.Option(
        None,
        "--sample-rate",
        help="Reduce track to this many positions per second (e.g. 0.2 for one every 5 s)",
    ),
    plot: bool = typer.Option(
        False,
        "--plot",
        help="Save a PNG plot of the tracks",
    ),
) -> None:
    """
    Clean a GPS recording by replacing deviating track points with positions
    from a reference route, or reduce the recording's sample sample_rate.
    """
    if sample_rate is not None:
        if start is not None or end is not None or reference is not None:
            typer.echo(
                "Error: --start, --end, and --reference cannot be used with --sample-rate.\n"
                "       When using --sample-rate, only --orig and --plot are allowed.",
                err=True,
            )
            raise typer.Exit(code=1)

        GPSSampleRateReducer(orig, sample_rate).start()

        if plot:
            cleaned = orig.parent / (orig.stem + f"_sample-rate={sample_rate}" + orig.suffix)
            output_png = orig.parent / (orig.stem + f"_sample-rate={sample_rate}.png")
            _plot_tracks(orig, None, cleaned, output_png)

    else:
        missing = [
            name for name, val in [("--start", start), ("--end", end), ("--reference", reference)]
            if val is None
        ]
        if missing:
            typer.echo(
                f"Error: {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} required "
                f"when --sample-rate is not used.",
                err=True,
            )
            raise typer.Exit(code=1)

        start_time = _parse_datetime(start, "start")
        end_time = _parse_datetime(end, "end")

        GPSCleaner(
            start_time=start_time,
            end_time=end_time,
            recording=orig,
            target=reference,
        ).start()

        if plot:
            cleaned = orig.parent / (orig.stem + "_cleaned" + orig.suffix)
            output_png = orig.parent / (orig.stem + "_cleaned.png")
            _plot_tracks(orig, reference, cleaned, output_png)


if __name__ == "__main__":
    app()
