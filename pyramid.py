from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
import math
import random
import time
from typing import Dict

from football_data import CareerIndex


ROW_LENGTHS = [1, 2, 3, 4, 3, 2, 1]


def node_id(row: int, col: int) -> str:
    return f"r{row}c{col}"


NODES = [node_id(r, c) for r, length in enumerate(ROW_LENGTHS) for c in range(length)]


def build_edges() -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for r in range(len(ROW_LENGTHS) - 1):
        a = ROW_LENGTHS[r]
        b = ROW_LENGTHS[r + 1]
        if b == a + 1:
            for c in range(a):
                edges.append((node_id(r, c), node_id(r + 1, c)))
                edges.append((node_id(r, c), node_id(r + 1, c + 1)))
        elif b == a - 1:
            for c in range(b):
                edges.append((node_id(r, c), node_id(r + 1, c)))
                edges.append((node_id(r, c + 1), node_id(r + 1, c)))
        else:
            raise ValueError("Unexpected row shape")
    return edges


EDGES = build_edges()

ADJ: dict[str, set[str]] = {n: set() for n in NODES}
for a, b in EDGES:
    ADJ[a].add(b)
    ADJ[b].add(a)


def positions(x_gap: int = 190, y_gap: int = 135) -> dict[str, dict]:
    max_len = max(ROW_LENGTHS)
    pos = {}
    for r, length in enumerate(ROW_LENGTHS):
        for c in range(length):
            x = (c - (length - 1) / 2) * x_gap + (max_len - 1) / 2 * x_gap
            y = r * y_gap
            pos[node_id(r, c)] = {"x": x, "y": y}
    return pos


POSITIONS = positions()


class GenerationTimeout(RuntimeError):
    pass


def edge_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


VALID_DIFFICULTIES = {"random", "easy", "medium", "difficult"}


@dataclass
class DifficultyProfile:
    player_relevance: Dict[int, float]  # 0 obscure -> 100 famous/relevant
    club_prestige: Dict[int, float]     # 0 obscure -> 100 prestigious

    def edge_difficulty(
        self,
        index: CareerIndex,
        player_a: int,
        player_b: int,
        club_id: int,
    ) -> float:
        club_obscurity = 100.0 - self.club_prestige.get(club_id, 0.0)
        pair_relevance = (
            self.player_relevance.get(player_a, 0.0)
            + self.player_relevance.get(player_b, 0.0)
        ) / 2.0
        pair_obscurity = 100.0 - pair_relevance

        # More career clubs = more plausible answers = harder link.
        career_breadth = (
            len(index.clubs_by_player.get(player_a, ()))
            + len(index.clubs_by_player.get(player_b, ()))
        )
        breadth_score = min(100.0, max(0.0, (career_breadth - 4) / 14 * 100.0))

        return (
            0.50 * club_obscurity
            + 0.25 * pair_obscurity
            + 0.25 * breadth_score
        )


def _percentiles(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    ordered = sorted(values.values())
    if len(ordered) == 1:
        return {next(iter(values)): 100.0}
    n = len(ordered)
    return {
        key: (bisect_right(ordered, value) - 1) / (n - 1) * 100.0
        for key, value in values.items()
    }


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def build_difficulty_profile(index: CareerIndex) -> DifficultyProfile:
    """
    Offline relevance model using data already in the project:
      50% peak market-value percentile
      30% senior-appearance percentile
      20% recency
    Then a 20% career-club prestige boost is blended into the final score.
    """
    cached = getattr(index, "_ptp_difficulty_profile", None)
    if cached is not None:
        return cached

    market_raw = {}
    appearances_raw = {}
    recency = {}
    today = date.today()

    for pid, info in index.player_info.items():
        market = float(info.get("highest_market_value_in_eur", 0) or 0)
        apps = float(info.get("senior_appearances", 0) or 0)
        market_raw[pid] = math.log1p(max(0.0, market))
        appearances_raw[pid] = math.log1p(max(0.0, apps))

        last = _parse_date(info.get("last_appearance"))
        if last is None:
            recency[pid] = 0.0
        else:
            age_years = max(0.0, (today - last).days / 365.2425)
            recency[pid] = max(0.0, 100.0 * (1.0 - min(age_years, 20.0) / 20.0))

    market_pct = _percentiles(market_raw)
    apps_pct = _percentiles(appearances_raw)

    base = {
        pid: (
            0.50 * market_pct.get(pid, 0.0)
            + 0.30 * apps_pct.get(pid, 0.0)
            + 0.20 * recency.get(pid, 0.0)
        )
        for pid in index.player_info
    }

    club_prestige = {}
    for cid, player_ids in index.players_by_club.items():
        scores = sorted(
            (base.get(pid, 0.0) for pid in player_ids),
            reverse=True,
        )
        top = scores[:8]
        club_prestige[cid] = sum(top) / len(top) if top else 0.0

    relevance = {}
    for pid in index.player_info:
        best_club = max(
            (club_prestige.get(cid, 0.0) for cid in index.clubs_by_player.get(pid, ())),
            default=0.0,
        )
        relevance[pid] = 0.80 * base.get(pid, 0.0) + 0.20 * best_club

    profile = DifficultyProfile(relevance, club_prestige)
    setattr(index, "_ptp_difficulty_profile", profile)
    return profile


def _target(difficulty: str) -> float | None:
    # Challenge score: 0 = very easy, 100 = very hard
    return {
        "random": None,
        "easy": 22.0,
        "medium": 50.0,
        "difficult": 78.0,
    }[difficulty]


def _candidate_challenge(
    index: CareerIndex,
    profile: DifficultyProfile,
    player_id: int,
    assigned_neighbors: list[str],
    assigned: dict[str, int],
    shared_clubs: list[int],
) -> float:
    obscurity = 100.0 - profile.player_relevance.get(player_id, 0.0)
    if not assigned_neighbors:
        return obscurity

    edge_scores = []
    for neighbour, cid in zip(assigned_neighbors, shared_clubs):
        edge_scores.append(
            profile.edge_difficulty(
                index,
                player_id,
                assigned[neighbour],
                cid,
            )
        )
    mean_edge = sum(edge_scores) / len(edge_scores)
    return 0.65 * obscurity + 0.35 * mean_edge


def score_completed_puzzle(
    index: CareerIndex,
    profile: DifficultyProfile,
    players: dict[str, int],
    edge_clubs: dict[str, int],
) -> float:
    player_scores = [
        100.0 - profile.player_relevance.get(pid, 0.0)
        for pid in players.values()
    ]
    edge_scores = []
    for a, b in EDGES:
        cid = edge_clubs[edge_key(a, b)]
        edge_scores.append(
            profile.edge_difficulty(index, players[a], players[b], cid)
        )
    return (
        0.60 * (sum(player_scores) / len(player_scores))
        + 0.40 * (sum(edge_scores) / len(edge_scores))
    )


@dataclass
class Puzzle:
    players: Dict[str, int]
    edge_clubs: Dict[str, int]
    clue_nodes: list[str]
    blank_numbers: Dict[str, int]
    difficulty: str = "random"
    difficulty_score: float | None = None

    def serialise(self, index: CareerIndex) -> dict:
        player_payload = {}
        for node, pid in self.players.items():
            info = dict(index.player_info[pid])
            info["career_clubs"] = sorted(
                index.club_names.get(cid, str(cid))
                for cid in index.clubs_by_player[pid]
            )
            player_payload[node] = info

        edges_payload = []
        for i, (a, b) in enumerate(EDGES):
            cid = self.edge_clubs[edge_key(a, b)]
            edges_payload.append(
                {
                    "id": f"e{i}",
                    "source": a,
                    "target": b,
                    "club_id": cid,
                    "club": index.club_names[cid],
                }
            )

        return {
            "players": player_payload,
            "edges": edges_payload,
            "clues": self.clue_nodes,
            "blank_numbers": self.blank_numbers,
            "revealed": list(self.clue_nodes),
            "difficulty": self.difficulty,
            "difficulty_score": (
                None
                if self.difficulty_score is None
                else round(self.difficulty_score, 1)
            ),
        }


def _random_clue_path(rng: random.Random) -> list[str]:
    # Kept for backwards compatibility; current UI starts at bottom.
    path = [node_id(0, 0)]
    current = path[0]
    for r in range(1, len(ROW_LENGTHS)):
        down = [
            n for n in ADJ[current]
            if int(n.split("c")[0][1:]) == r
        ]
        if not down:
            raise RuntimeError("Topology error")
        current = rng.choice(down)
        path.append(current)
    return path


def generate_puzzle(
    index: CareerIndex,
    difficulty: str = "random",
    seed: int | None = None,
    timeout_seconds: float = 25.0,
    candidate_cap: int = 240,
    restart_limit: int = 50,
) -> Puzzle:
    """
    Complete 16-player CSP generator.

    Difficulty:
      random    -> original unrestricted behaviour
      easy      -> favours famous/recent players and obvious/prestigious links
      medium    -> favours mid-range relevance and links
      difficult -> favours less obvious players and links

    Difficulty is a soft bias, not a brittle hard cutoff, so generation still
    has enough freedom to complete a valid pyramid.
    """
    difficulty = (difficulty or "random").lower()
    if difficulty not in VALID_DIFFICULTIES:
        raise ValueError(f"Unknown difficulty: {difficulty}")

    rng = random.Random(seed)
    started = time.monotonic()
    profile = build_difficulty_profile(index)
    target = _target(difficulty)

    degrees = {n: len(ADJ[n]) for n in NODES}
    eligible_for_degree = {
        d: [
            pid for pid, clubs in index.clubs_by_player.items()
            if len(clubs) >= d
        ]
        for d in sorted(set(degrees.values()))
    }

    max_degree = max(degrees.values())
    seed_nodes = [n for n, d in degrees.items() if d == max_degree]

    def check_time():
        if time.monotonic() - started > timeout_seconds:
            raise GenerationTimeout(
                f"No {difficulty} pyramid found inside {timeout_seconds:.1f}s. "
                "Try again or increase PTP_GENERATION_TIMEOUT."
            )

    def order_candidates(
        player_ids: list[int],
        assigned_neighbors: list[str],
        assigned: dict[str, int],
        shared_by_player: dict[int, list[int]],
    ) -> list[int]:
        result = list(player_ids)
        if difficulty == "random":
            rng.shuffle(result)
            return result

        ranked = []
        for pid in result:
            challenge = _candidate_challenge(
                index,
                profile,
                pid,
                assigned_neighbors,
                assigned,
                shared_by_player.get(pid, []),
            )
            distance = abs(challenge - target) + rng.random() * 4.0
            ranked.append((distance, pid))
        ranked.sort(key=lambda x: x[0])
        return [pid for _, pid in ranked]

    for _restart in range(restart_limit):
        check_time()

        assigned: dict[str, int] = {}
        used_players: set[int] = set()
        edge_clubs: dict[str, int] = {}
        incident_clubs: dict[str, set[int]] = defaultdict(set)

        start_node = rng.choice(seed_nodes)
        starters = list(eligible_for_degree[degrees[start_node]])
        if not starters:
            raise RuntimeError("Player pool is too small")

        sample = rng.sample(starters, min(len(starters), 450))

        if difficulty == "random":
            start_player = max(
                sample,
                key=lambda pid: len(index.exact_neighbors(pid)),
            )
        else:
            ranked = []
            for pid in sample:
                challenge = 100.0 - profile.player_relevance.get(pid, 0.0)
                connectivity = min(250, len(index.exact_neighbors(pid)))
                score = (
                    abs(challenge - target)
                    - connectivity * 0.01
                    + rng.random() * 3.0
                )
                ranked.append((score, pid))
            ranked.sort(key=lambda x: x[0])
            start_player = ranked[0][1]

        assigned[start_node] = start_player
        used_players.add(start_player)

        def candidate_domain(node: str) -> list[int]:
            assigned_neighbors = [n for n in ADJ[node] if n in assigned]

            if not assigned_neighbors:
                pool = list(eligible_for_degree[degrees[node]])
                if len(pool) > candidate_cap:
                    pool = rng.sample(pool, candidate_cap)
                pool = [pid for pid in pool if pid not in used_players]
                return order_candidates(
                    pool,
                    assigned_neighbors,
                    assigned,
                    {pid: [] for pid in pool},
                )

            maps = [index.exact_neighbors(assigned[n]) for n in assigned_neighbors]
            candidate_ids = set(maps[0])

            for m in maps[1:]:
                candidate_ids.intersection_update(m)
                if not candidate_ids:
                    return []

            candidate_ids.difference_update(used_players)

            valid = []
            shared_by_player: dict[int, list[int]] = {}

            for pid in candidate_ids:
                if len(index.clubs_by_player.get(pid, ())) < degrees[node]:
                    continue

                proposed = []
                ok = True

                for neighbour, neighbour_map in zip(assigned_neighbors, maps):
                    cid = neighbour_map.get(pid)
                    if cid is None or cid in incident_clubs[neighbour]:
                        ok = False
                        break
                    proposed.append(cid)

                if not ok:
                    continue

                if len(proposed) != len(set(proposed)):
                    continue

                future_edges = degrees[node] - len(assigned_neighbors)
                remaining_capacity = (
                    len(index.clubs_by_player[pid]) - len(set(proposed))
                )
                if remaining_capacity < future_edges:
                    continue

                valid.append(pid)
                shared_by_player[pid] = proposed

            ordered = order_candidates(
                valid,
                assigned_neighbors,
                assigned,
                shared_by_player,
            )
            return ordered[:candidate_cap]

        def choose_next_node():
            unassigned = [n for n in NODES if n not in assigned]
            if not unassigned:
                return None, []

            connected = [
                n for n in unassigned
                if any(nb in assigned for nb in ADJ[n])
            ]
            if not connected:
                connected = unassigned

            best_node = None
            best_domain = None
            best_constraints = -1

            for n in connected:
                domain = candidate_domain(n)
                constraint_count = sum(nb in assigned for nb in ADJ[n])

                if not domain:
                    return n, []

                if (
                    best_domain is None
                    or len(domain) < len(best_domain)
                    or (
                        len(domain) == len(best_domain)
                        and constraint_count > best_constraints
                    )
                ):
                    best_node = n
                    best_domain = domain
                    best_constraints = constraint_count

            return best_node, best_domain or []

        def backtrack() -> bool:
            check_time()

            if len(assigned) == len(NODES):
                return True

            node, domain = choose_next_node()
            if node is None:
                return True
            if not domain:
                return False

            assigned_neighbors = [n for n in ADJ[node] if n in assigned]

            for pid in domain:
                check_time()

                new_edges: list[tuple[str, str, int]] = []
                ok = True

                for neighbour in assigned_neighbors:
                    cid = index.exact_neighbors(assigned[neighbour]).get(pid)
                    if cid is None:
                        ok = False
                        break

                    if len(index.shared_clubs(assigned[neighbour], pid)) != 1:
                        ok = False
                        break

                    if cid in incident_clubs[neighbour] or cid in incident_clubs[node]:
                        ok = False
                        break

                    new_edges.append((node, neighbour, cid))

                if not ok:
                    continue

                cids = [cid for _, _, cid in new_edges]
                if len(cids) != len(set(cids)):
                    continue

                assigned[node] = pid
                used_players.add(pid)

                for a, b, cid in new_edges:
                    edge_clubs[edge_key(a, b)] = cid
                    incident_clubs[a].add(cid)
                    incident_clubs[b].add(cid)

                if backtrack():
                    return True

                for a, b, cid in new_edges:
                    edge_clubs.pop(edge_key(a, b), None)
                    incident_clubs[a].discard(cid)
                    incident_clubs[b].discard(cid)

                used_players.remove(pid)
                del assigned[node]

            return False

        if backtrack():
            if len(set(assigned.values())) != len(NODES):
                raise AssertionError("Duplicate player in completed pyramid")

            for a, b in EDGES:
                shared = index.shared_clubs(assigned[a], assigned[b])
                if len(shared) != 1:
                    raise AssertionError(
                        f"Invalid edge {a}-{b}: expected 1 shared club, got {len(shared)}"
                    )
                edge_clubs[edge_key(a, b)] = next(iter(shared))

            clues = _random_clue_path(rng)
            blanks = [n for n in NODES if n not in clues]
            blank_numbers = {node: i + 1 for i, node in enumerate(blanks)}

            final_score = (
                None
                if difficulty == "random"
                else score_completed_puzzle(
                    index,
                    profile,
                    assigned,
                    edge_clubs,
                )
            )

            return Puzzle(
                players=dict(assigned),
                edge_clubs=dict(edge_clubs),
                clue_nodes=clues,
                blank_numbers=blank_numbers,
                difficulty=difficulty,
                difficulty_score=final_score,
            )

    raise GenerationTimeout(
        f"Could not construct a valid {difficulty} 16-player pyramid. "
        "Try again or increase PTP_GENERATION_TIMEOUT."
    )
