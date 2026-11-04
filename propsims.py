
"""PropEdge NBA PropSims projection collector.

Reads publicly accessible HTML table data only.
Checks robots.txt before requesting projections.
Does not use credentials or bypass access restrictions.
"""

import json
import re
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

URL = "https://propsims.com/nba/projections/"
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "propsims.json"

USER_AGENT = "PropEdgeResearchBot/0.1"

STAT_NAMES = {
    "pts": "Points",
    "reb": "Rebounds",
    "ast": "Assists",
    "fg3m": "3-PT Made",
    "stl": "Steals",
    "blk": "Blocks",
    "tov": "Turnovers",
}

class ProjectionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.table_depth = 0
        self.in_row = False
        self.in_cell = False
        self.cell_attrs = {}
        self.cell_text = []
        self.row = []
        self.players = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "table":
            if attrs.get("id") == "nbaroi":
                self.in_table = True
                self.table_depth = 1
            elif self.in_table:
                self.table_depth += 1

        if not self.in_table:
            return

        if tag == "tr":
            self.in_row = True
            self.row = []

        elif tag in ("td", "th") and self.in_row:
            self.in_cell = True
            self.cell_attrs = attrs
            self.cell_text = []

    def handle_data(self, data):
        if self.in_cell:
            self.cell_text.append(data)

    def handle_endtag(self, tag):
        if not self.in_table:
            return

        if tag in ("td", "th") and self.in_cell:
            self.row.append({
                "attrs": self.cell_attrs,
                "text": " ".join(
                    "".join(self.cell_text).split()
                ),
            })
            self.in_cell = False

        elif tag == "tr" and self.in_row:
            self.process_row()
            self.in_row = False

        elif tag == "table":
            self.table_depth -= 1
            if self.table_depth == 0:
                self.in_table = False

    def process_row(self):
        values = {}
        for cell in self.row:
            attrs = cell["attrs"]
            field = attrs.get("data-f", "").lower()
            if field:
                values[field] = (
                    attrs.get("data-v") or cell["text"]
                )

        player = (
            values.get("player")
            or next(
                (
                    c["text"]
                    for c in self.row
                    if c["attrs"].get("data-l") == "Player"
                ),
                ""
            )
        )

        if not player:
            return

        team = values.get("team", "")
        projections = {}

        for source_field, stat_name in STAT_NAMES.items():
            raw = values.get(source_field)
            if raw is None:
                continue
            try:
                projections[stat_name] = float(raw)
            except (ValueError, TypeError):
                pass

        if not projections:
            return

        self.players.append({
            "player": player,
            "team": team,
            "league": "NBA",
            "projections": projections,
        })


def main():
    parsed = urlparse(URL)
    robots_url = (
        f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    )

    robots = urllib.robotparser.RobotFileParser()
    robots.set_url(robots_url)

    try:
        robots.read()
    except Exception as exc:
        raise RuntimeError(
            f"Could not verify robots.txt: {exc}"
        )

    if not robots.can_fetch(USER_AGENT, URL):
        raise RuntimeError(
            "Automated access not confirmed by robots.txt."
        )

    request = urllib.request.Request(
        URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html",
        },
    )

    with urllib.request.urlopen(
        request, timeout=45
    ) as response:
        html = response.read().decode(
            "utf-8", errors="replace"
        )

    parser = ProjectionParser()
    parser.feed(html)

    if len(parser.players) < 100:
        raise RuntimeError(
            "Only "
            f"{len(parser.players)} player records found. "
            "Check the table structure or access rules. "
            "Previous data will not be overwritten."
        )

    payload = {
        "retrieved_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": URL,
        "league": "NBA",
        "player_count": len(parser.players),
        "players": parser.players,
        "note": (
            "Third-party projections from publicly "
            "accessible PropSims HTML. "
            "Verify freshness before comparison."
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_suffix(".json.tmp")

    temporary.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    temporary.replace(OUT)

    print(
        f"Saved {len(parser.players)} PropSims players "
        f"to {OUT}",
        flush=True,
    )


if __name__ == "__main__":
    main()
