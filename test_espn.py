
import json
import urllib.request

url = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/3975/gamelog"

request = urllib.request.Request(
    url,
    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
)

try:
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)

    print("ESPN connection successful")
    print("Statistics:", data.get("labels"))
    print("Games found:", len(data.get("events", {})))

except Exception as error:
    print("ESPN connection failed:", error)
    raise
