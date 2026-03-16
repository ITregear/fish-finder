from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt

from . import __version__
from . import log as logcfg
from .models import FishingIntent, SessionRecommendation
from .planner import Planner
from .profile import load_profile

log = logging.getLogger(__name__)

app = typer.Typer(add_completion=False, rich_markup_mode="rich")
console = Console()

# ── Palette ───────────────────────────────────────────

TEAL = "#5DE4C7"
MID = "#2CAAB6"
DEEP = "#007C9C"

# ── Logo ──────────────────────────────────────────────

def _load_pike_art() -> list[str]:
    art_path = Path(__file__).parent / "pike.txt"
    if art_path.exists():
        lines = art_path.read_text().splitlines()
        return [l for l in lines if l.strip()] or []
    return []


_TEXT_FISH = [
    "   ███████╗██╗███████╗██╗  ██╗",
    "   ██╔════╝██║██╔════╝██║  ██║",
    "   █████╗  ██║███████╗███████║",
    "   ██╔══╝  ██║╚════██║██╔══██║",
    "   ██║     ██║███████║██║  ██║",
    "   ╚═╝     ╚═╝╚══════╝╚═╝  ╚═╝",
]

_TEXT_FINDER = [
    "   ███████╗██╗███╗   ██╗██████╗ ███████╗██████╗ ",
    "   ██╔════╝██║████╗  ██║██╔══██╗██╔════╝██╔══██╗",
    "   █████╗  ██║██╔██╗ ██║██║  ██║█████╗  ██████╔╝",
    "   ██╔══╝  ██║██║╚██╗██║██║  ██║██╔══╝  ██╔══██╗",
    "   ██║     ██║██║ ╚████║██████╔╝███████╗██║  ██║",
    "   ╚═╝     ╚═╝╚═╝  ╚═══╝╚═════╝ ╚══════╝╚═╝  ╚═╝",
]

_GRAD_START = (130, 240, 208)
_GRAD_END = (0, 124, 130)


def _build_gradient(n: int) -> list[str | None]:
    if n <= 1:
        return ["#82F0D0"]
    return [
        f"#{int(_GRAD_START[0] + (_GRAD_END[0] - _GRAD_START[0]) * i / (n - 1)):02X}"
        f"{int(_GRAD_START[1] + (_GRAD_END[1] - _GRAD_START[1]) * i / (n - 1)):02X}"
        f"{int(_GRAD_START[2] + (_GRAD_END[2] - _GRAD_START[2]) * i / (n - 1)):02X}"
        for i in range(n)
    ]


def _header() -> None:
    if console.width < 52:
        console.print()
        console.print(f"  [{TEAL}]><(((°>  fish-finder[/{TEAL}]  [dim]v{__version__}[/dim]")
        console.print()
        return

    pike = _load_pike_art()
    text_lines = _TEXT_FISH + _TEXT_FINDER
    text_width = max(len(l.rstrip()) for l in text_lines)

    if pike:
        pike_right = max((len(l.rstrip()) for l in pike), default=0)
        delta = text_width - pike_right
        if delta > 0:
            pike = [" " * delta + l.rstrip() for l in pike]
        elif delta < 0:
            min_lead = min(
                (len(l) - len(l.lstrip()) for l in pike if l.strip()),
                default=0,
            )
            trim = min(abs(delta), min_lead)
            pike = [l[trim:] for l in pike]

    logo_lines = (pike + [""]) if pike else []
    logo_lines += text_lines

    colored = [l for l in logo_lines if l]
    gradient = _build_gradient(len(colored))
    gi = 0

    console.print()
    for line in logo_lines:
        if not line:
            console.print()
        else:
            console.print(Text(line, style=gradient[gi]))
            gi += 1
    console.print()
    tag = Text()
    tag.append("   plan your next session", style=f"italic {MID}")
    tag.append(f"   v{__version__}", style="dim")
    console.print(tag)
    console.print()


def _query_display(query: str) -> None:
    console.print(f"  [{TEAL}]❯[/{TEAL}] [italic]{query}[/italic]")
    console.print()


def _step(label: str, fn, *args, **kwargs):
    with console.status(
        f"  [dim]◌[/dim]  [dim]{label}[/dim]",
        spinner="dots",
        spinner_style=TEAL,
    ):
        result = fn(*args, **kwargs)
    console.print(f"  [{MID}]●[/{MID}]  {label}")
    return result


def _step_warn(label: str, note: str) -> None:
    console.print(f"  [yellow]●[/yellow]  {label}  [dim]({note})[/dim]")


def _show_intent(intent: FishingIntent) -> None:
    console.print()
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(style=f"bold {TEAL}", justify="right")
    tbl.add_column()
    tbl.add_row("Date", intent.date)
    tbl.add_row("Start", intent.start_time)
    tbl.add_row("Duration", f"{intent.duration_minutes} min")
    tbl.add_row("Species", ", ".join(intent.species_preference) or "any")
    tbl.add_row("Type", intent.session_type)
    tbl.add_row("Travel", intent.travel_mode)
    if intent.notes:
        tbl.add_row("Notes", intent.notes)
    console.print(Panel(
        tbl,
        title=f"[bold {TEAL}]Parsed Intent[/bold {TEAL}]",
        border_style="dim",
        padding=(1, 3),
    ))
    console.print()


def _show_plan(rec: SessionRecommendation) -> None:
    console.print()

    details = Table.grid(padding=(0, 2))
    details.add_column(style=f"bold {TEAL}", justify="right", min_width=10)
    details.add_column()
    details.add_row("Location", f"{rec.location_name}  [dim]({rec.location_type})[/dim]")
    details.add_row("Species", ", ".join(rec.target_species))
    details.add_row("Travel", f"{rec.travel_minutes:.0f} min drive" if not rec.transit_summary else f"{rec.travel_minutes:.0f} min")
    if rec.parking:
        details.add_row("Parking", rec.parking)
    if rec.transit_summary:
        details.add_row("Route", rec.transit_summary)
    details.add_row("Weather", rec.weather_summary)

    sections: list[str] = []
    sections.append(f"{rec.reasoning}")
    sections.append(f"[bold {TEAL}]Approach[/bold {TEAL}]\n{rec.approach}")

    if rec.tackle:
        tackle_items = "  ".join(f"[dim]•[/dim] {t}" for t in rec.tackle)
        sections.append(f"[bold {TEAL}]Tackle[/bold {TEAL}]\n{tackle_items}")

    if rec.timeline:
        tl = "\n".join(f"  [{TEAL}]{e.time}[/{TEAL}]  {e.activity}" for e in rec.timeline)
        sections.append(f"[bold {TEAL}]Timeline[/bold {TEAL}]\n{tl}")

    if rec.reminders:
        rems = "\n".join(f"  [dim]•[/dim] {r}" for r in rec.reminders)
        sections.append(f"[bold {TEAL}]Reminders[/bold {TEAL}]\n{rems}")

    body = Text.from_markup("\n\n".join(sections))

    content = Table.grid(padding=0)
    content.add_row(details)
    content.add_row(Text(""))
    content.add_row(body)

    console.print(Panel(
        content,
        title=f"[bold {TEAL}]Session Plan[/bold {TEAL}]",
        border_style=MID,
        padding=(1, 3),
    ))
    console.print()


def _error(msg: str) -> None:
    console.print(f"\n  [bold red]error[/bold red]  {msg}")
    log_file = logcfg.get_log_file()
    if log_file:
        console.print(f"  [dim]log: {log_file}[/dim]\n")
    else:
        console.print()


@app.command()
def plan(
    query: Optional[str] = typer.Argument(None, help="Natural language fishing query"),
    profile_path: str = typer.Option("profile.md", "--profile", "-p"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Plan a fishing session from a natural language query."""
    t0 = time.monotonic()

    log_file = logcfg.setup(verbose=verbose)
    log.info("Session started (verbose=%s)", verbose)

    _header()

    if query is None:
        query = Prompt.ask(f"  [{TEAL}]❯[/{TEAL}] [bold]What are you looking for?[/bold]")
        console.print()

    _query_display(query)
    log.info("Query: %s", query)

    try:
        profile = _step("Loading profile", load_profile, profile_path)
    except FileNotFoundError:
        _error(f"Profile not found at [bold]{profile_path}[/bold]")
        raise typer.Exit(1)

    planner = Planner(profile)

    try:
        intent = _step("Parsing request", planner.parse_query, query)
    except Exception as e:
        log.exception("Failed to parse query")
        _error(f"Failed to parse query: {e}")
        raise typer.Exit(1)

    if verbose:
        _show_intent(intent)

    try:
        weather = _step("Checking weather", planner.get_weather, intent)
    except Exception:
        log.exception("Weather fetch failed")
        weather = None
        _step_warn("Checking weather", "failed, continuing")

    try:
        waters = _step("Finding nearby waters", planner.find_waters)
    except Exception:
        log.exception("Water body search failed")
        _error("Failed to find water bodies. Check your connection.")
        raise typer.Exit(1)

    if not waters:
        _error("No water bodies found. Try increasing max_travel_minutes.")
        raise typer.Exit(1)

    # Travel — mode-dependent
    if intent.travel_mode == "train":
        try:
            travel_data = _step(
                "Finding transit routes",
                planner.get_transit_routes, waters, intent,
            )
        except Exception:
            log.exception("Transit route search failed")
            _error("Failed to find transit routes.")
            raise typer.Exit(1)
    else:
        travel_data = _step("Calculating drive times", planner.get_drive_times, waters)

    if not travel_data:
        _error("No reachable waters within your max travel time.")
        raise typer.Exit(1)

    if verbose:
        console.print(f"\n  [dim]{len(travel_data)} reachable location(s)[/dim]\n")

    # Parking — car mode only, non-blocking
    parking = None
    if intent.travel_mode == "car":
        try:
            parking = _step("Checking parking", planner.find_parking, travel_data)
        except Exception:
            log.exception("Parking search failed")
            _step_warn("Checking parking", "failed, continuing")

    try:
        rec = _step(
            "Planning session",
            planner.recommend, intent, weather, travel_data, parking,
        )
    except Exception as e:
        log.exception("Recommendation failed")
        _error(f"Failed to generate plan: {e}")
        raise typer.Exit(1)

    _show_plan(rec)

    elapsed = time.monotonic() - t0
    log.info("Session complete in %.1fs", elapsed)
    console.print(f"  [dim]done in {elapsed:.1f}s[/dim]")
    console.print()
