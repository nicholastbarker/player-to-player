from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import random
import time
from typing import Dict, Iterable

from football_data import CareerIndex


ROW_LENGTHS = [1, 2, 3, 4, 3, 2, 1]


def node_id(row: int, col: int) -> str:
    return f"r{row}c{col}"


NODES = [
    node_id(r, c)
    for r, length in enumerate(ROW_LENGTHS)
    for c in range(length)
]


def build_edges() -> list[tuple[str, str]]:
    """
    1-2-3-4-3-2-1 triangular/diamond topology.

    Expanding row:
      parent c -> child c and c+1

    Contracting row:
      child c <- parent c and c+1
    """
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


@dataclass
class Puzzle:
    players: Dict[str, int]
    edge_clubs: Dict[str, int]
    clue_nodes: list[str]
    blank_numbers: Dict[str, int]

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
            key = edge_key(a, b)
            cid = self.edge_clubs[key]
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
        }


def edge_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def _random_clue_path(rng: random.Random) -> list[str]:
    """
    Reveal one player per row, connected all the way from top to bottom.
    This mirrors the visual logic of the reference image and leaves 9 blanks.
    """
    path = [node_id(0, 0)]
    current = path[0]

    for r in range(1, len(ROW_LENGTHS)):
        down = [
            n
            for n in ADJ[current]
            if int(n.split("c")[0][1:]) == r
        ]
        if not down:
            raise RuntimeError("Topology error: clue path cannot continue")
        current = rng.choice(down)
        path.append(current)

    return path


def generate_puzzle(
    index: CareerIndex,
    seed: int | None = None,
    timeout_seconds: float = 20.0,
    candidate_cap: int = 220,
    restart_limit: int = 40,
) -> Puzzle:
    """
    Construct the COMPLETE valid 16-player solution first.

    Constraints:
      1. Every node is a different player.
      2. Every connected pair shares exactly ONE senior club.
      3. At a branching player, incident edges use different clubs.
         This makes the route matter: taking the left or right edge from
         a player gives a different club clue.
      4. The full solution is built before any blank/reveal UI is shown.
    """
    rng = random.Random(seed)
    start_time = time.monotonic()

    degrees = {n: len(ADJ[n]) for n in NODES}

    # A player must have at least as many distinct career clubs as the number
    # of differently-labelled incident edges we might need around that node.
    eligible_for_degree: dict[int, list[int]] = {}
    for d in sorted(set(degrees.values())):
        eligible_for_degree[d] = [
            pid
            for pid, clubs in index.clubs_by_player.items()
            if len(clubs) >= d
        ]

    # Start at a high-degree middle node: it creates useful constraints early.
    max_degree = max(degrees.values())
    seed_nodes = [n for n, d in degrees.items() if d == max_degree]

    def check_time() -> None:
        if time.monotonic() - start_time > timeout_seconds:
            raise GenerationTimeout(
                f"No pyramid found inside {timeout_seconds:.1f}s. "
                "Try a broader player pool or a larger timeout."
            )

    for _restart in range(restart_limit):
        check_time()

        assigned: dict[str, int] = {}
        used_players: set[int] = set()
        edge_clubs: dict[str, int] = {}
        incident_clubs: dict[str, set[int]] = defaultdict(set)

        start_node = rng.choice(seed_nodes)
        starters = eligible_for_degree[degrees[start_node]]
        if not starters:
            raise RuntimeError("Player pool is too small for the pyramid.")

        # Bias toward well-connected players, while keeping randomness.
        starter_sample = (
            rng.sample(starters, min(len(starters), 250))
            if len(starters) > 250
            else list(starters)
        )
        rng.shuffle(starter_sample)

        start_player = max(
            starter_sample,
            key=lambda pid: len(index.exact_neighbors(pid)),
        )
        assigned[start_node] = start_player
        used_players.add(start_player)

        def candidate_domain(node: str) -> list[int]:
            assigned_neighbors = [n for n in ADJ[node] if n in assigned]

            if not assigned_neighbors:
                # We normally won't use this because the graph is connected and
                # we always expand from an assigned component.
                pool = eligible_for_degree[degrees[node]]
                if len(pool) > candidate_cap:
                    pool = rng.sample(pool, candidate_cap)
                return [pid for pid in pool if pid not in used_players]

            maps = [index.exact_neighbors(assigned[n]) for n in assigned_neighbors]
            candidate_ids = set(maps[0])

            for m in maps[1:]:
                candidate_ids.intersection_update(m)
                if not candidate_ids:
                    return []

            candidate_ids.difference_update(used_players)

            valid = []
            for pid in candidate_ids:
                if len(index.clubs_by_player.get(pid, ())) < degrees[node]:
                    continue

                proposed_clubs = []
                ok = True

                for neighbor, m in zip(assigned_neighbors, maps):
                    cid = m.get(pid)
                    if cid is None:
                        ok = False
                        break

                    # The same player should not have two incident edges carrying
                    # the same club clue. This is what makes each branch distinct.
                    if cid in incident_clubs[neighbor]:
                        ok = False
                        break
                    proposed_clubs.append(cid)

                if not ok:
                    continue

                # If the new node touches multiple already-assigned neighbors,
                # its incoming edge clubs must also be different from one another.
                if len(proposed_clubs) != len(set(proposed_clubs)):
                    continue

                # Leave enough distinct career clubs for the node's future edges.
                future_edges = degrees[node] - len(assigned_neighbors)
                remaining_club_capacity = (
                    len(index.clubs_by_player[pid]) - len(set(proposed_clubs))
                )
                if remaining_club_capacity < future_edges:
                    continue

                valid.append(pid)

            if len(valid) > candidate_cap:
                valid = rng.sample(valid, candidate_cap)
            else:
                rng.shuffle(valid)

            # Mildly prefer players that themselves have many exact-one-club links.
            # We only score a small front slice to keep generation responsive.
            if len(valid) > 1:
                head = valid[: min(60, len(valid))]
                head.sort(
                    key=lambda pid: len(index.exact_neighbors(pid)),
                    reverse=True,
                )
                # Blend ranked and random order so puzzles vary.
                if rng.random() < 0.7:
                    valid[: len(head)] = head

            return valid

        def choose_next_node() -> tuple[str | None, list[int]]:
            unassigned = [n for n in NODES if n not in assigned]
            if not unassigned:
                return None, []

            connected = [
                n for n in unassigned if any(nb in assigned for nb in ADJ[n])
            ]
            if not connected:
                connected = unassigned

            best_node = None
            best_domain = None
            best_constraint_count = -1

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
                        and constraint_count > best_constraint_count
                    )
                ):
                    best_node = n
                    best_domain = domain
                    best_constraint_count = constraint_count

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
                valid = True

                for neighbor in assigned_neighbors:
                    cid = index.exact_neighbors(assigned[neighbor]).get(pid)
                    if cid is None:
                        valid = False
                        break

                    # Exact-one-club relation is guaranteed by exact_neighbors,
                    # but we keep an explicit invariant check for safety.
                    if len(index.shared_clubs(assigned[neighbor], pid)) != 1:
                        valid = False
                        break

                    if cid in incident_clubs[neighbor]:
                        valid = False
                        break
                    if cid in incident_clubs[node]:
                        valid = False
                        break

                    new_edges.append((node, neighbor, cid))

                if not valid:
                    continue

                # Multiple newly-created incident edges at this node must use
                # different clubs.
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
            # Final defensive validation.
            if len(set(assigned.values())) != len(NODES):
                raise AssertionError("Duplicate player in completed pyramid")

            for a, b in EDGES:
                pa = assigned[a]
                pb = assigned[b]
                shared = index.shared_clubs(pa, pb)
                if len(shared) != 1:
                    raise AssertionError(
                        f"Invalid edge {a}-{b}: expected 1 shared club, got {len(shared)}"
                    )
                cid = next(iter(shared))
                edge_clubs[edge_key(a, b)] = cid

            clues = _random_clue_path(rng)
            blanks = [n for n in NODES if n not in clues]

            # Number blanks top-to-bottom, left-to-right.
            blank_numbers = {node: i + 1 for i, node in enumerate(blanks)}

            return Puzzle(
                players=dict(assigned),
                edge_clubs=dict(edge_clubs),
                clue_nodes=clues,
                blank_numbers=blank_numbers,
            )

    raise GenerationTimeout(
        "Could not construct a valid 16-player pyramid. "
        "Try increasing timeout_seconds, reducing min_senior_appearances, "
        "or using a broader player pool."
    )
