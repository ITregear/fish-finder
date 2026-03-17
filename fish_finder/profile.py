from __future__ import annotations

from pathlib import Path

from .models import Location, Permit, Profile


def load_profile(path: str = "profile.md") -> Profile:
    """Parse a profile.md file into a Profile model."""
    text = Path(path).read_text()
    sections = _parse_sections(text)

    loc = sections.get("location", {})
    prefs = sections.get("preferences", {})
    sched = sections.get("schedule", {})

    location = Location(
        address=loc.get("address", "Unknown"),
        lat=float(loc.get("lat", 0)),
        lon=float(loc.get("lon", 0)),
    )

    permits = _parse_permits(text)

    return Profile(
        location=location,
        target_species=_parse_list(prefs.get("target_species", "")),
        methods=_parse_list(prefs.get("methods", "")),
        max_travel_minutes=int(prefs.get("max_travel_minutes", 60)),
        work_end=sched.get("work_end", "17:00"),
        permits=permits,
    )


def _parse_sections(text: str) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections[current] = {}
        elif current and line.strip().startswith("- "):
            content = line.strip()[2:]
            if ":" in content:
                key, value = content.split(":", 1)
                sections[current][key.strip().lower()] = value.strip()
    return sections


def _parse_permits(text: str) -> list[Permit]:
    """Parse the Licenses & Permits section, preserving original name casing."""
    permits: list[Permit] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line[3:].strip().lower() == "licenses & permits"
            continue
        if in_section and line.strip().startswith("- ") and ":" in line.strip()[2:]:
            content = line.strip()[2:]
            name, covers = content.split(":", 1)
            permits.append(Permit(name=name.strip(), covers=covers.strip()))
    return permits


def _parse_list(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]
