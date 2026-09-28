from __future__ import annotations

from bisect import bisect_right
from datetime import date
import math
import os
import re
import unicodedata

from dash import Dash, Input, Output, State, callback, ctx, dcc, html, no_update
import dash_cytoscape as cyto

from football_data import CareerIndex, load_index
from pyramid import NODES, POSITIONS, GenerationTimeout, generate_puzzle


# ----------------------------
# Settings
# ----------------------------
ACTIVE_WITHIN_YEARS = int(os.getenv("PTP_ACTIVE_WITHIN_YEARS", "20"))
MIN_SENIOR_APPEARANCES = int(os.getenv("PTP_MIN_APPEARANCES", "20"))
MIN_PEAK_VALUE_EUR = int(os.getenv("PTP_MIN_PEAK_VALUE_EUR", "0"))
GENERATION_TIMEOUT = float(os.getenv("PTP_GENERATION_TIMEOUT", "25"))
START_NODE = "r6c0"

DIFFICULTY_LABELS = {
    "random": "Random",
    "easy": "Easy",
    "medium": "Medium",
    "difficult": "Difficult",
}


# ----------------------------
# Load data
# ----------------------------
print("\nLoading football career data...")
INDEX = load_index(
    data_dir="data",
    active_within_years=ACTIVE_WITHIN_YEARS,
    min_senior_appearances=MIN_SENIOR_APPEARANCES,
    min_peak_market_value_eur=MIN_PEAK_VALUE_EUR,
)
print(f"Loaded {len(INDEX.player_info):,} eligible players.")
print(f"Loaded {len(INDEX.club_names):,} clubs.")


# ----------------------------
# Difficulty model
# ----------------------------
# This version is intentionally stricter than the earlier one.
# Easy is a HARD restricted player pool, not just a soft average score.
# Peak market value dominates; appearances only have a small influence.

BIG_CLUB_KEYWORDS = (
    "arsenal",
    "chelsea",
    "liverpool",
    "manchester united",
    "manchester city",
    "tottenham",
    "real madrid",
    "barcelona",
    "bayern munich",
    "bayern münchen",
    "borussia dortmund",
    "paris saint-germain",
    "paris saint germain",
    "juventus",
    "inter milan",
    "internazionale",
    "ac milan",
    "atlético madrid",
    "atletico madrid",
    "ajax",
)


def _percentile_map(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    ordered = sorted(values.values())
    if len(ordered) == 1:
        return {next(iter(values)): 100.0}

    result = {}
    for key, value in values.items():
        rank = bisect_right(ordered, value)
        result[key] = (rank - 1) / (len(ordered) - 1) * 100.0
    return result


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _has_big_club(player_id: int) -> bool:
    for club_id in INDEX.clubs_by_player.get(player_id, ()):
        club_name = INDEX.club_names.get(club_id, "").lower()
        if any(keyword in club_name for keyword in BIG_CLUB_KEYWORDS):
            return True
    return False


def _build_relevance_scores():
    market_raw = {}
    appearances_raw = {}
    recency = {}
    today = date.today()

    for player_id, info in INDEX.player_info.items():
        market = float(info.get("highest_market_value_in_eur", 0) or 0)
        appearances = float(info.get("senior_appearances", 0) or 0)

        market_raw[player_id] = math.log1p(max(0.0, market))
        appearances_raw[player_id] = math.log1p(max(0.0, appearances))

        last = _parse_date(info.get("last_appearance"))
        if last is None:
            recency[player_id] = 0.0
        else:
            years_ago = max(0.0, (today - last).days / 365.2425)
            recency[player_id] = max(0.0, 100.0 * (1.0 - min(years_ago, 20.0) / 20.0))

    market_pct = _percentile_map(market_raw)
    appearances_pct = _percentile_map(appearances_raw)

    scores = {}
    for player_id in INDEX.player_info:
        big_club = 100.0 if _has_big_club(player_id) else 0.0

        # Market value is deliberately dominant.
        scores[player_id] = (
            0.68 * market_pct.get(player_id, 0.0)
            + 0.12 * appearances_pct.get(player_id, 0.0)
            + 0.10 * recency.get(player_id, 0.0)
            + 0.10 * big_club
        )

    return scores, market_pct


PLAYER_RELEVANCE, MARKET_PERCENTILE = _build_relevance_scores()


def _allowed(player_id: int, difficulty: str) -> bool:
    if difficulty == "random":
        return True

    info = INDEX.player_info[player_id]
    relevance = PLAYER_RELEVANCE.get(player_id, 0.0)
    market_pct = MARKET_PERCENTILE.get(player_id, 0.0)
    market = int(info.get("highest_market_value_in_eur", 0) or 0)
    appearances = int(info.get("senior_appearances", 0) or 0)
    big_club = _has_big_club(player_id)

    if difficulty == "easy":
        # Long career alone can never make a player Easy.
        # This is the key fix for cases like Silvio Proto.
        return (
            market >= 10_000_000
            and market_pct >= 72
            and relevance >= 72
            and appearances >= 40
            and (market >= 20_000_000 or big_club)
        )

    if difficulty == "medium":
        return (
            market >= 2_000_000
            and market_pct >= 38
            and relevance >= 45
            and appearances >= 30
        )

    if difficulty == "difficult":
        # Keep a broad hard pool, but remove the superstar tier.
        return relevance <= 72

    raise ValueError(f"Unknown difficulty: {difficulty}")


def _filtered_index(difficulty: str) -> CareerIndex:
    if difficulty == "random":
        return INDEX

    keep = {
        player_id
        for player_id in INDEX.player_info
        if _allowed(player_id, difficulty)
    }

    player_info = {
        player_id: info
        for player_id, info in INDEX.player_info.items()
        if player_id in keep
    }

    clubs_by_player = {
        player_id: clubs
        for player_id, clubs in INDEX.clubs_by_player.items()
        if player_id in keep
    }

    players_by_club = {}
    for club_id, player_ids in INDEX.players_by_club.items():
        kept = set(player_ids) & keep
        if kept:
            players_by_club[club_id] = kept

    return CareerIndex(
        player_info=player_info,
        club_names=INDEX.club_names,
        clubs_by_player=clubs_by_player,
        players_by_club=players_by_club,
    )


DIFFICULTY_INDEXES = {
    difficulty: _filtered_index(difficulty)
    for difficulty in ("random", "easy", "medium", "difficult")
}

print("\nDifficulty pools:")
for difficulty, idx in DIFFICULTY_INDEXES.items():
    print(f"  {difficulty.title():10s}: {len(idx.player_info):,} players")


# ----------------------------
# Graph helpers
# ----------------------------
def neighbours(game: dict, node: str) -> list[str]:
    result = []
    for edge in game["edges"]:
        if edge["source"] == node:
            result.append(edge["target"])
        elif edge["target"] == node:
            result.append(edge["source"])
    return result


def edge_between(game: dict, a: str, b: str) -> dict | None:
    for edge in game["edges"]:
        if {edge["source"], edge["target"]} == {a, b}:
            return edge
    return None


# ----------------------------
# Club search
# ----------------------------
def normalise_club_text(text) -> str:
    text = text or ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", "and")
    text = re.sub(r"\bfootball club\b", "", text)
    text = re.sub(r"[^a-z0-9]", "", text)

    aliases = {
        "psg": "parissaintgermain",
        "manutd": "manchesterunited",
        "manu": "manchesterunited",
        "mancity": "manchestercity",
        "spurs": "tottenhamhotspur",
        "barca": "barcelona",
        "bayern": "bayernmunchen",
        "intermilan": "internazionale",
        "atletico": "atleticomadrid",
        "dortmund": "borussiadortmund",
    }
    return aliases.get(text, text)


def search_clubs(search_text, limit=20):
    query = normalise_club_text(search_text)
    if len(query) < 2:
        return []

    matches = []
    for club_id, club_name in INDEX.club_names.items():
        normalised = normalise_club_text(club_name)
        if query not in normalised:
            continue

        if normalised == query:
            quality = 0
        elif normalised.startswith(query):
            quality = 1
        else:
            quality = 2

        matches.append((quality, len(club_name), club_name.lower(), club_id, club_name))

    matches.sort()
    return [
        {"label": club_name, "value": club_id}
        for _q, _l, _s, club_id, club_name in matches[:limit]
    ]


# ----------------------------
# Game state / generation
# ----------------------------
def reset_game_state(game: dict) -> dict:
    game = dict(game)
    blank_nodes = [node for node in NODES if node != START_NODE]

    game["blank_numbers"] = {
        node: number
        for number, node in enumerate(blank_nodes, start=1)
    }
    game["unlocked"] = [START_NODE]
    game["active_node"] = START_NODE
    game["pending"] = None
    game["solved_edges"] = []
    return game


def make_new_game(difficulty: str = "random") -> dict:
    difficulty = difficulty or "random"
    game_index = DIFFICULTY_INDEXES[difficulty]

    if len(game_index.player_info) < 100:
        raise RuntimeError(
            f"The {difficulty} player pool is too small to construct a pyramid."
        )

    timeout = GENERATION_TIMEOUT
    if difficulty == "easy":
        timeout = max(timeout, 45.0)
    elif difficulty in {"medium", "difficult"}:
        timeout = max(timeout, 35.0)

    # Important: use random generation WITHIN the restricted difficulty pool.
    # This bypasses the older soft difficulty scoring in pyramid.py.
    puzzle = generate_puzzle(
        game_index,
        difficulty="random",
        timeout_seconds=timeout,
    )

    game = puzzle.serialise(game_index)
    game["difficulty"] = difficulty
    game["difficulty_score"] = None
    return reset_game_state(game)


# ----------------------------
# Cytoscape elements
# ----------------------------
def build_elements(game: dict) -> list[dict]:
    if not game.get("players"):
        return []

    unlocked = set(game.get("unlocked", []))
    active = game.get("active_node")
    pending = game.get("pending")
    solved_edges = set(game.get("solved_edges", []))
    blank_numbers = game.get("blank_numbers", {})

    pending_target = pending["target"] if pending else None
    pending_edge = pending["edge_id"] if pending else None
    active_neighbours = set(neighbours(game, active)) if active else set()

    elements = []

    for node in NODES:
        player = game["players"][node]
        classes = ["player"]

        if node in unlocked:
            classes += ["revealed", "unlocked"]
            label = player["name"]
        elif node == pending_target:
            classes += ["revealed", "pending-player"]
            label = player["name"]
        else:
            classes.append("hidden")
            label = str(blank_numbers.get(node, "?"))
            if pending is None and node in active_neighbours:
                classes.append("available")
            else:
                classes.append("locked")

        if node == active:
            classes.append("active-player")

        elements.append({
            "data": {
                "id": node,
                "label": label,
                "name": player["name"],
                "image": player.get("image_url", ""),
            },
            "position": POSITIONS[node],
            "classes": " ".join(classes),
            "locked": True,
        })

    for edge in game["edges"]:
        classes = ["club-edge"]
        edge_id = edge["id"]
        source = edge["source"]
        target = edge["target"]
        label = ""

        if edge_id in solved_edges:
            classes.append("solved")
            label = edge["club"]
        elif edge_id == pending_edge:
            classes.append("pending-edge")
            label = "?"
        elif (
            pending is None
            and active in {source, target}
            and ((source == active and target not in unlocked)
                 or (target == active and source not in unlocked))
        ):
            classes.append("available-edge")
        else:
            classes.append("locked-edge")

        elements.append({
            "data": {
                "id": edge_id,
                "source": source,
                "target": target,
                "label": label,
            },
            "classes": " ".join(classes),
        })

    return elements


# ----------------------------
# Cytoscape graph styling
# ----------------------------
STYLESHEET = [
    {
        "selector": "node.player",
        "style": {
            "width": 94,
            "height": 94,
            "shape": "hexagon",
            "border-width": 3,
            "border-color": "#ffffff",
            "text-wrap": "wrap",
            "text-max-width": 135,
            "font-family": "Arial, sans-serif",
            "font-weight": 700,
            "font-size": 13,
            "text-valign": "bottom",
            "text-margin-y": 18,
            "color": "#0d2438",
            "overlay-opacity": 0,
        },
    },
    {
        "selector": "node.hidden",
        "style": {
            "background-color": "#dff8fb",
            "color": "#07365b",
            "font-size": 28,
            "font-weight": 900,
            "text-valign": "center",
            "text-margin-y": 0,
        },
    },
    {
        "selector": "node.locked",
        "style": {"opacity": 0.35, "border-color": "#c7dadd"},
    },
    {
        "selector": "node.available",
        "style": {
            "opacity": 1,
            "border-color": "#0a7ea4",
            "border-width": 5,
            "background-color": "#ffffff",
        },
    },
    {
        "selector": "node.revealed",
        "style": {
            "opacity": 1,
            "background-color": "#dff8fb",
            "background-image": "data(image)",
            "background-fit": "contain",
            "background-clip": "node",
        },
    },
    {
        "selector": "node.pending-player",
        "style": {"border-color": "#d7a100", "border-width": 5},
    },
    {
        "selector": "node.active-player",
        "style": {"border-color": "#0a7ea4", "border-width": 6},
    },
    {
        "selector": "edge.club-edge",
        "style": {
            "width": 3,
            "curve-style": "straight",
            "line-color": "#b8dfe4",
            "label": "data(label)",
            "font-size": 11,
            "font-weight": 800,
            "text-rotation": "autorotate",
            "text-background-color": "#f7ffff",
            "text-background-opacity": 0.96,
            "text-background-padding": 4,
            "overlay-opacity": 0,
        },
    },
    {"selector": "edge.locked-edge", "style": {"opacity": 0.2}},
    {
        "selector": "edge.available-edge",
        "style": {"line-color": "#0a7ea4", "width": 4, "opacity": 0.9},
    },
    {
        "selector": "edge.pending-edge",
        "style": {"line-color": "#d7a100", "width": 5},
    },
    {
        "selector": "edge.solved",
        "style": {
            "line-color": "#18794e",
            "width": 5,
            "color": "#145c3c",
            "text-background-color": "#e8f7ef",
        },
    },
]


# ----------------------------
# Initial puzzle
# ----------------------------
try:
    print("\nGenerating first random pyramid...")
    INITIAL_GAME = make_new_game("random")
    starter = INITIAL_GAME["players"][START_NODE]["name"]
    INITIAL_MESSAGE = (
        f"Start with {starter}. Click one of the highlighted connected hexes."
    )
    print("Pyramid generated successfully.")
except Exception as exc:
    print("\nERROR GENERATING FIRST PUZZLE:")
    print(exc)
    INITIAL_GAME = {
        "players": {},
        "edges": [],
        "blank_numbers": {},
        "unlocked": [],
        "active_node": None,
        "pending": None,
        "solved_edges": [],
        "difficulty": "random",
    }
    INITIAL_MESSAGE = f"Could not generate a puzzle: {exc}"


# ----------------------------
# Dash layout
# ----------------------------
app = Dash(__name__)
app.title = "Player to Player"

app.layout = html.Div(
    className="page",
    children=[
        dcc.Store(id="game-store", data=INITIAL_GAME),

        html.Div(
            className="topbar",
            children=[
                html.Div(children=[
                    html.H1("PLAYER TO PLAYER", className="title"),
                    html.Div(
                        "Reveal player → guess shared club → unlock next route",
                        className="subtitle",
                    ),
                ]),
                html.Div(
                    className="controls",
                    children=[
                        html.Div(
                            className="difficulty-control",
                            children=[
                                html.Div("DIFFICULTY", className="difficulty-label"),
                                dcc.Dropdown(
                                    id="difficulty-select",
                                    options=[
                                        {"label": "Random", "value": "random"},
                                        {"label": "Easy", "value": "easy"},
                                        {"label": "Medium", "value": "medium"},
                                        {"label": "Difficult", "value": "difficult"},
                                    ],
                                    value="random",
                                    clearable=False,
                                    searchable=False,
                                    className="difficulty-dropdown",
                                ),
                            ],
                        ),
                        html.Button("New puzzle", id="new-puzzle", n_clicks=0),
                        html.Button("Reset game", id="reset-game", n_clicks=0),
                    ],
                ),
            ],
        ),

        html.Div(id="message", className="message", children=INITIAL_MESSAGE),

        html.Div(
            className="main-grid",
            children=[
                html.Div(
                    className="graph-card",
                    children=[
                        cyto.Cytoscape(
                            id="pyramid",
                            elements=build_elements(INITIAL_GAME) if INITIAL_GAME.get("players") else [],
                            layout={"name": "preset", "fit": True, "padding": 45},
                            stylesheet=STYLESHEET,
                            style={"width": "100%", "height": "900px"},
                            minZoom=0.45,
                            maxZoom=1.7,
                            userZoomingEnabled=True,
                            userPanningEnabled=True,
                            autoungrabify=True,
                        )
                    ],
                ),

                html.Div(
                    className="side",
                    children=[
                        html.Div(
                            className="stat-card",
                            children=[
                                html.Div("PLAYERS UNLOCKED", className="eyebrow"),
                                html.Div(id="unlocked-count", className="big-stat"),
                                html.Div(id="difficulty-readout", className="muted"),
                                html.Div(
                                    "Click any unlocked player to explore from them again.",
                                    className="muted",
                                    style={"marginTop": "8px"},
                                ),
                            ],
                        ),

                        html.Div(
                            className="guess-card",
                            children=[
                                html.Div("GUESS THE LINK", className="eyebrow"),
                                html.Div(id="guess-question", className="guess-question"),
                                html.Div(
                                    className="guess-row",
                                    children=[
                                        dcc.Dropdown(
                                            id="club-guess",
                                            options=[],
                                            value=None,
                                            placeholder="Start typing a club...",
                                            searchable=True,
                                            clearable=True,
                                            disabled=True,
                                            className="guess-dropdown",
                                        ),
                                        html.Button(
                                            "Submit",
                                            id="submit-guess",
                                            className="primary-button",
                                            n_clicks=0,
                                            disabled=True,
                                        ),
                                    ],
                                ),
                                html.Div(
                                    "Type at least 2 letters and select the club from the suggestions.",
                                    className="small-note",
                                ),
                                html.Div(
                                    style={"marginTop": "8px"},
                                    children=[
                                        html.Button(
                                            "Choose another hex",
                                            id="cancel-pending",
                                            className="secondary-button",
                                            n_clicks=0,
                                            disabled=True,
                                        )
                                    ],
                                ),
                            ],
                        ),

                        html.Div(id="player-detail", className="detail-card"),

                        html.Div(
                            className="rule-card",
                            children=[
                                html.Div("HOW TO PLAY", className="eyebrow"),
                                html.P("1. Click a highlighted numbered player."),
                                html.P("2. Their identity is revealed immediately."),
                                html.P("3. Find the one senior club they share with your current player."),
                                html.P("4. Select the club and submit your answer."),
                                html.P("5. If correct, the player is unlocked and you can continue from them."),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ],
)


# ----------------------------
# Club autocomplete
# ----------------------------
@callback(
    Output("club-guess", "options"),
    Input("club-guess", "search_value"),
    State("club-guess", "value"),
    State("game-store", "data"),
)
def update_club_suggestions(search_value, selected_value, game):
    if not game or not game.get("pending"):
        return []

    options = []

    if selected_value is not None:
        try:
            selected_id = int(selected_value)
            selected_name = INDEX.club_names.get(selected_id)
            if selected_name:
                options.append({"label": selected_name, "value": selected_id})
        except (TypeError, ValueError):
            pass

    if not search_value or len(search_value.strip()) < 2:
        return options

    existing = {option["value"] for option in options}
    for option in search_clubs(search_value, limit=20):
        if option["value"] not in existing:
            options.append(option)

    return options


# ----------------------------
# Main interaction callback
# ----------------------------
@callback(
    Output("game-store", "data"),
    Output("message", "children"),
    Output("club-guess", "value"),
    Input("pyramid", "tapNodeData"),
    Input("submit-guess", "n_clicks"),
    Input("cancel-pending", "n_clicks"),
    Input("new-puzzle", "n_clicks"),
    Input("reset-game", "n_clicks"),
    State("club-guess", "value"),
    State("game-store", "data"),
    State("difficulty-select", "value"),
    prevent_initial_call=True,
)
def interact(
    tap_node,
    submit_clicks,
    cancel_clicks,
    new_clicks,
    reset_clicks,
    selected_club_id,
    game,
    selected_difficulty,
):
    trigger = ctx.triggered_id

    if trigger == "new-puzzle":
        difficulty = selected_difficulty or "random"
        try:
            new_game = make_new_game(difficulty)
            starter = new_game["players"][START_NODE]["name"]
            label = DIFFICULTY_LABELS[difficulty]
            return new_game, f"{label} puzzle generated. Start with {starter}.", None
        except GenerationTimeout as exc:
            return game, f"{exc} Try pressing New puzzle again.", None
        except Exception as exc:
            return game, f"Could not generate a new puzzle: {exc}", None

    if not game or not game.get("players"):
        return no_update, "No puzzle is loaded.", no_update

    if trigger == "reset-game":
        game = reset_game_state(game)
        starter = game["players"][START_NODE]["name"]
        return game, f"Game reset. Start with {starter}.", None

    if trigger == "cancel-pending":
        if not game.get("pending"):
            return game, "Nothing to cancel.", None

        game = dict(game)
        game["pending"] = None
        active = game["active_node"]
        active_name = game["players"][active]["name"]
        return game, f"Choose another player connected to {active_name}.", None

    if trigger == "submit-guess":
        pending = game.get("pending")

        if not pending:
            return game, "Choose a player first.", None

        if selected_club_id is None:
            return game, "Start typing a club and select it from the suggestions.", None

        edge = next(
            (edge for edge in game["edges"] if edge["id"] == pending["edge_id"]),
            None,
        )

        if edge is None:
            return game, "Could not find this connection.", None

        try:
            guessed_club_id = int(selected_club_id)
        except (TypeError, ValueError):
            return game, "Select a club from the suggestions.", None

        source = pending["source"]
        target = pending["target"]
        source_name = game["players"][source]["name"]
        target_name = game["players"][target]["name"]

        if guessed_club_id == int(edge["club_id"]):
            game = dict(game)
            unlocked = set(game.get("unlocked", []))
            solved_edges = set(game.get("solved_edges", []))

            unlocked.add(target)
            solved_edges.add(edge["id"])

            game["unlocked"] = list(unlocked)
            game["solved_edges"] = list(solved_edges)
            game["active_node"] = target
            game["pending"] = None

            return (
                game,
                f"Correct — {edge['club']}. {target_name} is now unlocked. "
                f"Choose any highlighted player connected to {target_name}.",
                None,
            )

        guessed_name = INDEX.club_names.get(guessed_club_id, "that club")
        return (
            game,
            f"No — {guessed_name} is not the link between {source_name} and {target_name}. Try again.",
            None,
        )

    if trigger == "pyramid" and tap_node:
        node = tap_node.get("id")

        if node not in game["players"]:
            return no_update, no_update, no_update

        if game.get("pending"):
            return game, "Guess the shared club first, or choose another hex.", no_update

        unlocked = set(game.get("unlocked", []))

        if node in unlocked:
            game = dict(game)
            game["active_node"] = node
            player_name = game["players"][node]["name"]
            return (
                game,
                f"{player_name} selected. Choose one of the highlighted players connected to them.",
                None,
            )

        active = game.get("active_node")
        if active is None:
            return game, "No active player selected.", None

        if node not in neighbours(game, active):
            active_name = game["players"][active]["name"]
            return (
                game,
                f"That player is not connected to {active_name}. Choose a highlighted hex.",
                None,
            )

        edge = edge_between(game, active, node)
        if edge is None:
            return game, "Could not find that connection.", None

        game = dict(game)
        game["pending"] = {
            "source": active,
            "target": node,
            "edge_id": edge["id"],
        }

        source_name = game["players"][active]["name"]
        target_name = game["players"][node]["name"]

        return (
            game,
            f"{target_name} revealed. What club did {source_name} and {target_name} both play for?",
            None,
        )

    return no_update, no_update, no_update


# ----------------------------
# Render current game state
# ----------------------------
@callback(
    Output("pyramid", "elements"),
    Output("unlocked-count", "children"),
    Output("difficulty-readout", "children"),
    Output("guess-question", "children"),
    Output("club-guess", "disabled"),
    Output("submit-guess", "disabled"),
    Output("cancel-pending", "disabled"),
    Output("player-detail", "children"),
    Input("game-store", "data"),
)
def render_game(game):
    if not game or not game.get("players"):
        return (
            [],
            "0 / 16",
            "No puzzle loaded.",
            "No puzzle loaded.",
            True,
            True,
            True,
            html.Div("No player selected.", className="muted"),
        )

    unlocked = set(game.get("unlocked", []))
    pending = game.get("pending")
    active = game.get("active_node")

    difficulty = game.get("difficulty", "random")
    difficulty_text = f"Current puzzle: {DIFFICULTY_LABELS.get(difficulty, difficulty.title())}"

    if pending:
        source = game["players"][pending["source"]]["name"]
        target = game["players"][pending["target"]]["name"]
        question = f"What club did {source} and {target} both play for?"
        dropdown_disabled = False
        submit_disabled = False
        cancel_disabled = False
    else:
        active_name = game["players"][active]["name"]
        question = f"Choose a numbered player connected to {active_name}."
        dropdown_disabled = True
        submit_disabled = True
        cancel_disabled = True

    active_player = game["players"][active]
    player_detail = [
        html.Div("CURRENT PLAYER", className="eyebrow"),
        html.Div(active_player["name"], className="player-name"),
        html.Div(active_player.get("position", ""), className="muted"),
    ]

    return (
        build_elements(game),
        f"{len(unlocked)} / 16",
        difficulty_text,
        question,
        dropdown_disabled,
        submit_disabled,
        cancel_disabled,
        player_detail,
    )


if __name__ == "__main__":
    print("\nStarting Player to Player...")
    print("http://127.0.0.1:8050\n")
    app.run(debug=False)
