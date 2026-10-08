# PICL Stats API (v1)

Read-only JSON for scripts and agents, such as a Team Director's weekly news. A key only ever sees the teams it was issued for.

## Access

- **Key:** created by an admin in **Admin → API keys** and shown once. Keep it in your agent's secrets.
- **Base URL:** `https://piclstats.fly.dev/api/v1`. Use this host, not piclstats.com: Cloudflare's bot protection on piclstats.com challenges automated clients.
- **Auth header:** `Authorization: Bearer pcls_…`
- **Rate limit:** 60 requests per minute per key (429 beyond that).

```sh
curl -s -H "Authorization: Bearer $PICLSTATS_KEY" https://piclstats.fly.dev/api/v1/me
```

| Status | Meaning |
|---|---|
| 401 | Missing, wrong or revoked key |
| 403 | The key doesn't cover that team |
| 429 | Slow down |

## Endpoints

### `GET /me`
The key's name and teams. Use it as a connection check.

### `GET /teams`
The teams this key can read, each with its digest URL.

### `GET /teams/{team}/digest?since=YYYY-MM-DD`
The team's races on or after `since` (default: the last 8 days) in the current season. Run it weekly after a race weekend. `{team}` is case-insensitive (`Lower%20Bucks%20Composite`).

```json
{
  "team": "Lower Bucks Composite",
  "since": "2026-10-01",
  "generated": "2026-10-08",
  "races": [
    {
      "event": {"id": 173, "name": "2026 Eastern Blue Conference #3 - …", "date": "2026-10-04",
                "course": "Blue Mountain", "results_url": "https://piclstats.com/results?event_id=173",
                "official_url": "https://my.raceresult.com/427237/"},
      "summary": {"riders": 11, "finishers": 10, "podiums": 5, "top10": 9, "points": 4765},
      "riders": [
        {"name": "TYLER MASSEY", "rider_url": "https://piclstats.com/rider/540",
         "category": "Varsity Male", "status": "OK", "place": 3, "field": 18,
         "field_beaten_pct": 83.3, "points": 556, "time": "1:03:34.3", "behind_winner": "+2:07.2",
         "previous": {"race": "…Battle at Belmont", "place": 9, "field": 19, "field_beaten_pct": 52.6},
         "standing": {"conference": "Eastern Blue", "rank": 5, "label": "5th", "of": 21},
         "highlights": [{"type": "podium", "text": "3rd of 18 in Varsity Male"}, …]}
      ],
      "highlights": [{"rider": "TYLER MASSEY", "type": "podium", "text": "3rd of 18 in Varsity Male"}, …]
    }
  ],
  "markdown": "## 2026 Eastern Blue Conference #3 …\n\nLower Bucks Composite: 11 riders, 5 podiums, …"
}
```

**Riders** are the team's riders at that race. `previous` is the rider's previous race this season (on any team); null for a first race. `standing` is the rider's **current** conference standing (total points, the leaderboard's ranking); null until they have two races. `field_beaten_pct` is the share of the category that finished behind them. `behind_winner` is set only for riders who rode the winner's number of laps. Names come as the league publishes them (upper case).

**Highlight types**, good news only, each a checkable fact:

| Type | When |
|---|---|
| `podium` | Top 3, always with the field size ("2nd of 2" is not "2nd of 30") |
| `top10` | 4th–10th |
| `season_best` | Better place than any earlier race this season in the same division |
| `big_move` | Beat at least 15 points more of the field than at their previous race |
| `moved_up` | First race in an older division |
| `first_race` | First race of the season |
| `standing` | Top 3 in their conference standings |

**`markdown`** is a plain summary built only from these facts: a team line, highlights, a results table and a link to the full results. Edit it freely, but keep the numbers as given. Many riders are minors; please don't add personal details beyond what the results show.
