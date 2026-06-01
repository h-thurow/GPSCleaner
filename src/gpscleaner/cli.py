import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import typer

from src.gpscleaner.gpscleaner import GPSCleaner, GPSDistanceReducer, GPSRetimer, GPSSampleRateResampler, GPSSampleRateUpsampler, _get_timestamp_by_coord, _get_timestamp_by_index, compare_tracks

app = typer.Typer(add_completion=False)

GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"


def _parse_coord(value: str, option_name: str) -> tuple[float, float]:
    """
    Parse a coordinate string in the format 'LAT,LON' (no spaces).
    Raises a Typer error with a clear message if the format is invalid.
    """
    try:
        lat_str, lon_str = value.split(",")
        return float(lat_str.strip()), float(lon_str.strip())
    except ValueError:
        raise typer.BadParameter(
            f"'{value}' is not a valid coordinate. Expected format: 47.7936893,13.0076771",
            param_hint=f"'--{option_name}'",
        )


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


@app.command("compare")
def compare(
    recording: Path = typer.Option(
        ...,
        "--recording",
        exists=True,
        help="Path to the GPX recording",
    ),
    reference: Path = typer.Option(
        ...,
        "--reference",
        exists=True,
        help="Path to the reference GPX track",
    ),
    max_time_diff: float = typer.Option(
        ...,
        "--max-time-diff",
        help="Maximum time difference in seconds between matched points (interpreted as ±)",
    ),
    interval: float | None = typer.Option(
        None,
        "--interval",
        help="Check interval in seconds (optional; all reference points are compared if omitted)",
    ),
) -> None:
    """
    Compare two GPS tracks by matching points with similar timestamps and
    printing their distances as an aligned table.
    """
    compare_tracks(recording, reference, max_time_diff, interval)


@app.command("clean")
def clean(
    recording: Path = typer.Option(
        ...,
        "--recording",
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
    start_point: int | None = typer.Option(
        None,
        "--start-point",
        help="Index of the first deviating track point (1 = first point in the recording)",
    ),
    end_point: int | None = typer.Option(
        None,
        "--end-point",
        help="Index of the last deviating track point (1 = first point in the recording)",
    ),
    start_coord: str | None = typer.Option(
        None,
        "--start-coord",
        help="Coordinate of the first deviating point, e.g. 47.7936893,13.0076771",
    ),
    end_coord: str | None = typer.Option(
        None,
        "--end-coord",
        help="Coordinate of the last deviating point, e.g. 47.5432100,13.1234567",
    ),
    reference: Path | None = typer.Option(
        None,
        exists=True,
        help="Path to the GPX file with the actual route",
    ),
    sample_rate: float | None = typer.Option(
        None,
        "--sample-rate",
        help="Resample track to this many positions per second (e.g. 0.2 for one every 5 s)",
    ),
    upsample_only: bool = typer.Option(
        False,
        "--upsample-only",
        help="With --sample-rate: only insert points in sparse gaps, never remove dense points. "
             "Useful for geotagging photos from a track with adaptive sampling.",
    ),
    distance: float | None = typer.Option(
        None,
        "--distance",
        help="Reduce track so consecutive points are at least this many metres apart",
    ),
    plot: bool = typer.Option(
        False,
        "--plot",
        help="Save a PNG plot of the tracks",
    ),
) -> None:
    """
    Clean a GPS recording by replacing deviating track points with positions
    from a reference route, or resample the recording's sample rate or point density.
    """
    using_coord = start_coord is not None or end_coord is not None
    using_index = start_point is not None or end_point is not None
    using_time  = start       is not None or end       is not None

    if upsample_only and sample_rate is None:
        typer.echo(
            "Error: --upsample-only requires --sample-rate.",
            err=True,
        )
        raise typer.Exit(code=1)

    if distance is not None:
        if using_time or using_index or using_coord or reference is not None or sample_rate is not None or upsample_only:
            typer.echo(
                "Error: --start, --end, --start-point, --end-point, --start-coord, --end-coord, "
                "--reference, --sample-rate, and --upsample-only cannot be used with --distance.\n"
                "       When using --distance, only --recording and --plot are allowed.",
                err=True,
            )
            raise typer.Exit(code=1)

        GPSDistanceReducer(recording, distance).start()

        if plot:
            cleaned = recording.parent / (recording.stem + f"_distance={distance}" + recording.suffix)
            if cleaned.exists():
                output_png = recording.parent / (recording.stem + f"_distance={distance}.png")
                _plot_tracks(recording, None, cleaned, output_png)

    elif sample_rate is not None:
        if using_time or using_index or using_coord or reference is not None:
            typer.echo(
                "Error: --start, --end, --start-point, --end-point, and --reference "
                "cannot be used with --sample-rate.\n"
                "       When using --sample-rate, only --recording, --upsample-only, and --plot are allowed.",
                err=True,
            )
            raise typer.Exit(code=1)

        if upsample_only:
            GPSSampleRateUpsampler(recording, sample_rate).start()
        else:
            GPSSampleRateResampler(recording, sample_rate).start()

        if plot:
            cleaned = recording.parent / (recording.stem + f"_sample-rate={sample_rate}" + recording.suffix)
            output_png = recording.parent / (recording.stem + f"_sample-rate={sample_rate}.png")
            _plot_tracks(recording, None, cleaned, output_png)

    elif using_coord:
        if using_time or using_index:
            typer.echo(
                "Error: --start and --end and --start-point and --end-point cannot be used "
                "together with --start-coord and --end-coord.\n"
                "       Use only one mode: time-based, index-based, or coordinate-based.",
                err=True,
            )
            raise typer.Exit(code=1)

        if start_coord is None or end_coord is None:
            missing = []
            if start_coord is None:
                missing.append("--start-coord")
            if end_coord is None:
                missing.append("--end-coord")
            typer.echo(
                f"Error: {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} required "
                f"when using coordinate-based mode.",
                err=True,
            )
            raise typer.Exit(code=1)

        if reference is None:
            typer.echo("Error: --reference is required.", err=True)
            raise typer.Exit(code=1)

        sc_lat, sc_lon = _parse_coord(start_coord, "start-coord")
        ec_lat, ec_lon = _parse_coord(end_coord, "end-coord")

        try:
            start_time = _get_timestamp_by_coord(recording, sc_lat, sc_lon)
            end_time   = _get_timestamp_by_coord(recording, ec_lat, ec_lon)
        except ValueError as error:
            typer.echo(f"Error: {error}", err=True)
            raise typer.Exit(code=1)

        GPSCleaner(
            start_time=start_time,
            end_time=end_time,
            recording=recording,
            target=reference,
        ).start()

        if plot:
            cleaned = recording.parent / (recording.stem + "_cleaned" + recording.suffix)
            output_png = recording.parent / (recording.stem + "_cleaned.png")
            _plot_tracks(recording, reference, cleaned, output_png)

    elif using_index:
        if using_time:
            typer.echo(
                "Error: --start and --end cannot be used together with --start-point and --end-point.\n"
                "       Use either time-based (--start/--end) or index-based (--start-point/--end-point).",
                err=True,
            )
            raise typer.Exit(code=1)

        if start_point is None or end_point is None:
            missing = []
            if start_point is None:
                missing.append("--start-point")
            if end_point is None:
                missing.append("--end-point")
            typer.echo(
                f"Error: {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} required "
                f"when using index-based mode.",
                err=True,
            )
            raise typer.Exit(code=1)

        if reference is None:
            typer.echo("Error: --reference is required.", err=True)
            raise typer.Exit(code=1)

        try:
            start_time = _get_timestamp_by_index(recording, start_point)
            end_time   = _get_timestamp_by_index(recording, end_point)
        except ValueError as error:
            typer.echo(f"Error: {error}", err=True)
            raise typer.Exit(code=1)

        GPSCleaner(
            start_time=start_time,
            end_time=end_time,
            recording=recording,
            target=reference,
        ).start()

        if plot:
            cleaned = recording.parent / (recording.stem + "_cleaned" + recording.suffix)
            output_png = recording.parent / (recording.stem + "_cleaned.png")
            _plot_tracks(recording, reference, cleaned, output_png)

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
            recording=recording,
            target=reference,
        ).start()

        if plot:
            cleaned = recording.parent / (recording.stem + "_cleaned" + recording.suffix)
            output_png = recording.parent / (recording.stem + "_cleaned.png")
            _plot_tracks(recording, reference, cleaned, output_png)


@app.command("retime")
def retime(
    recording: Path = typer.Option(
        ...,
        "--recording",
        exists=True,
        help="Path to the GPX recording",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Overwrite the recording in place instead of creating a new file",
    ),
    sample_rate: float | None = typer.Option(
        None,
        "--sample-rate",
        help="Insert additional points in gap sections to reach this sample rate (positions/second)",
    ),
    plot: bool = typer.Option(
        False,
        "--plot",
        help="(not supported for retime)",
    ),
) -> None:
    """
    Assign timestamps to track points that have none, based on their
    distance from the surrounding timestamped points (constant-speed assumption).
    Optionally insert additional interpolated points to reach a target sample rate.
    """
    if plot:
        typer.echo(
            "Error: --plot is not supported for retime (coordinates are unchanged).",
            err=True,
        )
        raise typer.Exit(code=1)
    GPSRetimer(recording, overwrite, sample_rate).start()


if __name__ == "__main__":
    app()
