
"""Collect NBA projections from PropSims for PropEdge.

Use only with permission from PropSims.
Reads the public NBA projections HTML table.
Does not bypass authentication or access restrictions.
"""

import json
import re
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

URL = "https://propsims.com/nba/projections/"
OUTPUT = Path(__file__).resolve().parent / "data" / "propsims.json"
USER_AGENT = "PropEdgeResearchBot/0.1"

# Fields confirmed from the PropSims HTML.
STAT_FIELDS = {
    "pts_p50": "Points",
    "pts_if_play": "Points If Playing",
    "reb_p50": "Rebounds",
    "ast_p50": "Assists",
    "fg3m_p50": "3-PT Made",
    "stl_p50": "Steals",
    "blk_p50": "Blocks",
    "tov_p50": "Turnovers",
}

class PropSimsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside_table = False
        self.inside_row = False
        self.inside_cell = False
        self.row_attrs = {}
        self.cell_attrs = {}
        self.cell_parts = []
        self.cells = []
        self.players = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "table" and attrs.get("id") == "nbaroi":
            self.inside_table = True
            return

        if not self.inside_table:
            return

        if tag == "tr":
            self.inside_row = True
            self.row_attrs = attrs
            self.cells = []

        elif tag in ("td", "th") and self.inside_row:
            self.inside_cell = True
            self.cell_attrs = attrs
            self.cell_parts = []

    def handle_data(self, data):
        if self.inside_cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag):
        if not self.inside_table:
            return

        if tag in ("td", "th") and self.inside_cell:
            text = " ".join("".join(self.cell_parts).split())

            self.cells.append({
                "attrs": self.cell_attrs,
                "text": text,
            })
            self.inside_cell = False

        elif tag == "tr" and self.inside_row:
            self.process_row()
            self.inside_row = False

        elif tag == "table":
            self.inside_table = False

    def process_row(self):
        if not self.row_attrs.get("data-pid"):
            return

        player = ""
        team = self.row_attrs.get("data-team", "")
        position = ""
        status = ""
        fields = {}

        for cell in self.cells:
            attrs = cell["attrs"]
            label = attrs.get("data-l", "").lower()
            field = attrs.get("data-f", "").lower()
            value = attrs.get("data-v")

            if label == "player":
                player = cell["text"]
            elif label == "team" and not team:
                team = cell["text"]
            elif label == "pos":
                position = cell["text"]
            elif label == "status":
                status = cell["text"]

            if field and value is not None:
                fields[field] = value

        if not player:
            return

        projections = {}

        for field, stat in STAT_FIELDS.items():
            raw = fields.get(field)
            if raw is None:
                continue

            try:
                projections[stat] = float(raw)
            except (TypeError, ValueError):
                continue

        if not projections:
            return

        # Store every numeric projection field, including
        # future stats that aren't in STAT_FIELDS yet.
        all_fields = {}

        for field, raw in fields.items():
            try:
                all_fields[field] = float(raw)
            except (TypeError, ValueError):
                pass

        self.players.append({
            "player": player,
            "team": team,
            "position": position,
            "status": status,
            "player_id": self.row_attrs["data-pid"],
            "league": "NBA",
            "projections": projections,
            "raw_projection_fields": all_fields,
        })


def check_robots():
    robots = urllib.robotparser.RobotFileParser()
    robots.set_url("https://propsims.com/robots.txt")
    robots.read()

    if not robots.can_fetch(USER_AGENT, URL):
        raise RuntimeError(
            "PropSims robots.txt does not permit this request."
        )


def collect():
    check_robots()

    request = urllib.request.Request(
        URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html",
        },
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        html = response.read().decode(
            "utf-8", errors="replace"
        )

    parser = PropSimsParser()
    parser.feed(html)

    players = parser.players

    if len(players) < 100:
        raise RuntimeError(
            f"Only {len(players)} players extracted. "
            "Expected at least 100. "
            "Existing data will not be overwritten."
        )

    # Try to extract the model-run timestamp from the page.
    timestamp_match = re.search(
        r"Model run\s+(\d{4}-\d{2}-\d{2}\s+"
        r"\d{1,2}:\d{2}\s+[A-Z]{2,4})",
        html,
        re.IGNORECASE,
    )

    model_run = (
        timestamp_match.group(1)
        if timestamp_match
        else None
    )

    payload = {
        "source": "PropSims",
        "source_url": URL,
        "league": "NBA",
        "retrieved_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "model_run": model_run,
        "player_count": len(players),
        "players": players,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    temporary.replace(OUTPUT)

    print(
        f"PropSims: saved {len(players)} NBA players "
        f"to {OUTPUT}"
    )

    if players:
        print(
            "Example:",
            players[0]["player"],
            players[0]["projections"],
        )


if __name__ == "__main__":
    collect()
