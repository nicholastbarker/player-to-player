from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, FrozenSet, Set

import duckdb
import pandas as pd
import requests


DATA_BASE = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data"
FILES = {
    "players": f"{DATA_BASE}/players.csv.gz",
    "clubs": f"{DATA_BASE}/clubs.csv.gz",
    "appearances": f"{DATA_BASE}/appearances.csv.gz",
}


def _download(url: str, dest: Path) -> None:
    """Download once and cache locally."""
    if dest.exists() and dest.stat().st_size > 0:
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    with requests.get(url, stream=True, timeout=90) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        downloaded = 0

        with tmp.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    print(f"\rDownloading {dest.name}: {pct:5.1f}%", end="", flush=True)

    tmp.replace(dest)
    print(f"\nSaved {dest}")


def prepare_data(
    data_dir: str | Path = "data",
    active_within_years: int = 20,
    min_senior_appearances: int = 20,
    force: bool = False,
) -> tuple[Path, Path, Path]:
    """
    Build compact parquet files for the game.

    Eligibility:
      - player has at least `min_senior_appearances`
      - player's most recent recorded senior appearance is within the last
        `active_within_years` years

    Career clubs:
      - all recorded senior clubs for that eligible player are retained,
        not only clubs from the last 20 years
      - loan clubs are naturally included when the player made senior
        appearances for the loan club
    """
    data_dir = Path(data_dir)
    raw_dir = data_dir / "raw"
    cache_dir = data_dir / "cache"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    raw_paths = {}
    for key, url in FILES.items():
        path = raw_dir / f"{key}.csv.gz"
        _download(url, path)
        raw_paths[key] = path

    player_out = cache_dir / "eligible_players.parquet"
    club_out = cache_dir / "clubs.parquet"
    player_club_out = cache_dir / "player_clubs.parquet"

    if (
        not force
        and player_out.exists()
        and club_out.exists()
        and player_club_out.exists()
    ):
        return player_out, club_out, player_club_out

    cutoff = date.today() - timedelta(days=int(365.2425 * active_within_years))

    con = duckdb.connect()

    players_path = str(raw_paths["players"]).replace("'", "''")
    clubs_path = str(raw_paths["clubs"]).replace("'", "''")
    appearances_path = str(raw_paths["appearances"]).replace("'", "''")

    # Read only the columns we need from the large appearances file.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE app AS
        SELECT
            CAST(player_id AS BIGINT) AS player_id,
            CAST(player_club_id AS BIGINT) AS club_id,
            CAST(date AS DATE) AS appearance_date
        FROM read_csv_auto('{appearances_path}', header=true)
        WHERE player_id IS NOT NULL
          AND player_club_id IS NOT NULL
        """
    )

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE eligible AS
        SELECT
            player_id,
            COUNT(*) AS senior_appearances,
            MAX(appearance_date) AS last_appearance
        FROM app
        GROUP BY player_id
        HAVING COUNT(*) >= {int(min_senior_appearances)}
           AND MAX(appearance_date) >= DATE '{cutoff.isoformat()}'
        """
    )

    # IMPORTANT: We retain every recorded club from the eligible player's career,
    # even if that club spell predates the cutoff.
    con.execute(
        f"""
        COPY (
            SELECT DISTINCT
                a.player_id,
                a.club_id
            FROM app a
            INNER JOIN eligible e USING (player_id)
        )
        TO '{str(player_club_out).replace("'", "''")}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    con.execute(
        f"""
        COPY (
            SELECT
                CAST(p.player_id AS BIGINT) AS player_id,
                p.name,
                p.position,
                p.image_url,
                p.highest_market_value_in_eur,
                e.senior_appearances,
                e.last_appearance
            FROM read_csv_auto('{players_path}', header=true) p
            INNER JOIN eligible e
                ON CAST(p.player_id AS BIGINT) = e.player_id
        )
        TO '{str(player_out).replace("'", "''")}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    con.execute(
        f"""
        COPY (
            SELECT
                CAST(club_id AS BIGINT) AS club_id,
                name
            FROM read_csv_auto('{clubs_path}', header=true)
            WHERE club_id IS NOT NULL
        )
        TO '{str(club_out).replace("'", "''")}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    con.close()
    return player_out, club_out, player_club_out


@dataclass
class CareerIndex:
    player_info: Dict[int, dict]
    club_names: Dict[int, str]
    clubs_by_player: Dict[int, FrozenSet[int]]
    players_by_club: Dict[int, Set[int]]
    _neighbor_cache: Dict[int, Dict[int, int]] = field(default_factory=dict)

    def exact_neighbors(self, player_id: int) -> Dict[int, int]:
        """
        Return {other_player_id: unique_shared_club_id} for players who share
        EXACTLY ONE recorded senior club with `player_id`.

        This is calculated lazily, so we never need to construct the complete
        all-vs-all footballer graph in memory.
        """
        if player_id in self._neighbor_cache:
            return self._neighbor_cache[player_id]

        counts: Counter[int] = Counter()
        first_shared_club: dict[int, int] = {}

        for club_id in self.clubs_by_player.get(player_id, frozenset()):
            for other_id in self.players_by_club.get(club_id, set()):
                if other_id == player_id:
                    continue
                counts[other_id] += 1
                first_shared_club.setdefault(other_id, club_id)

        exact = {
            other_id: first_shared_club[other_id]
            for other_id, count in counts.items()
            if count == 1
        }

        self._neighbor_cache[player_id] = exact
        return exact

    def shared_clubs(self, a: int, b: int) -> FrozenSet[int]:
        return self.clubs_by_player.get(a, frozenset()) & self.clubs_by_player.get(
            b, frozenset()
        )


def load_index(
    data_dir: str | Path = "data",
    active_within_years: int = 20,
    min_senior_appearances: int = 20,
    min_peak_market_value_eur: int = 0,
) -> CareerIndex:
    player_path, club_path, player_club_path = prepare_data(
        data_dir=data_dir,
        active_within_years=active_within_years,
        min_senior_appearances=min_senior_appearances,
    )

    players = pd.read_parquet(player_path)
    clubs = pd.read_parquet(club_path)
    player_clubs = pd.read_parquet(player_club_path)

    if min_peak_market_value_eur > 0:
        peak = pd.to_numeric(
            players["highest_market_value_in_eur"], errors="coerce"
        ).fillna(0)
        keep = set(
            players.loc[peak >= min_peak_market_value_eur, "player_id"]
            .astype(int)
            .tolist()
        )
        players = players[players["player_id"].astype(int).isin(keep)]
        player_clubs = player_clubs[
            player_clubs["player_id"].astype(int).isin(keep)
        ]

    player_info: dict[int, dict] = {}
    for row in players.itertuples(index=False):
        pid = int(row.player_id)
        image = "" if pd.isna(row.image_url) else str(row.image_url)
        player_info[pid] = {
            "player_id": pid,
            "name": str(row.name),
            "position": "" if pd.isna(row.position) else str(row.position),
            "image_url": image,
            "highest_market_value_in_eur": (
                0
                if pd.isna(row.highest_market_value_in_eur)
                else int(row.highest_market_value_in_eur)
            ),
            "senior_appearances": int(row.senior_appearances),
            "last_appearance": str(row.last_appearance),
        }

    club_names = {
        int(row.club_id): str(row.name)
        for row in clubs.itertuples(index=False)
        if int(row.club_id) >= 0
    }

    clubs_by_player_mut: dict[int, set[int]] = {
        pid: set() for pid in player_info
    }
    players_by_club: dict[int, set[int]] = {}

    for row in player_clubs.itertuples(index=False):
        pid = int(row.player_id)
        cid = int(row.club_id)
        if pid not in player_info or cid not in club_names:
            continue
        clubs_by_player_mut.setdefault(pid, set()).add(cid)
        players_by_club.setdefault(cid, set()).add(pid)

    clubs_by_player = {
        pid: frozenset(cids) for pid, cids in clubs_by_player_mut.items()
    }

    # Drop any player who somehow has no usable club after joins.
    valid_ids = {pid for pid, cids in clubs_by_player.items() if cids}
    player_info = {pid: info for pid, info in player_info.items() if pid in valid_ids}
    clubs_by_player = {
        pid: cids for pid, cids in clubs_by_player.items() if pid in valid_ids
    }
    for cid in list(players_by_club):
        players_by_club[cid] &= valid_ids
        if not players_by_club[cid]:
            del players_by_club[cid]

    return CareerIndex(
        player_info=player_info,
        club_names=club_names,
        clubs_by_player=clubs_by_player,
        players_by_club=players_by_club,
    )
