from __future__ import annotations

import os
import re
import unicodedata

from dash import (
    Dash,
    Input,
    Output,
    State,
    callback,
    ctx,
    dcc,
    html,
    no_update,
)

import dash_cytoscape as cyto

from football_data import load_index
from pyramid import (
    NODES,
    POSITIONS,
    GenerationTimeout,
    generate_puzzle,
)


# ============================================================
# SETTINGS
# ============================================================

ACTIVE_WITHIN_YEARS = int(
    os.getenv("PTP_ACTIVE_WITHIN_YEARS", "20")
)

MIN_SENIOR_APPEARANCES = int(
    os.getenv("PTP_MIN_APPEARANCES", "20")
)

MIN_PEAK_VALUE_EUR = int(
    os.getenv("PTP_MIN_PEAK_VALUE_EUR", "0")
)

GENERATION_TIMEOUT = float(
    os.getenv("PTP_GENERATION_TIMEOUT", "25")
)

START_NODE = "r6c0"


# ============================================================
# LOAD DATA
# ============================================================

print()
print("Loading football career data...")

INDEX = load_index(
    data_dir="data",
    active_within_years=ACTIVE_WITHIN_YEARS,
    min_senior_appearances=MIN_SENIOR_APPEARANCES,
    min_peak_market_value_eur=MIN_PEAK_VALUE_EUR,
)

print(
    f"Loaded {len(INDEX.player_info):,} eligible players."
)

print(
    f"Loaded {len(INDEX.club_names):,} clubs."
)


# ============================================================
# HELPERS
# ============================================================


def neighbours(game, node):

    result = []

    for edge in game["edges"]:

        if edge["source"] == node:
            result.append(edge["target"])

        elif edge["target"] == node:
            result.append(edge["source"])

    return result


def edge_between(game, a, b):

    for edge in game["edges"]:

        if {
            edge["source"],
            edge["target"],
        } == {a, b}:

            return edge

    return None


# ============================================================
# CLUB SEARCH
# ============================================================


def normalise_club_text(text):
    """
    Used ONLY to make club searching more forgiving.

    For example:

        Benfica
        SL Benfica

    both reduce to strings that can be matched by substring.
    """

    text = text or ""

    text = unicodedata.normalize(
        "NFKD",
        str(text),
    )

    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )

    text = text.lower()

    text = text.replace(
        "&",
        "and",
    )

    text = re.sub(
        r"\bfootball club\b",
        "",
        text,
    )

    text = re.sub(
        r"[^a-z0-9]",
        "",
        text,
    )

    aliases = {

        "psg":
            "parissaintgermain",

        "manutd":
            "manchesterunited",

        "manu":
            "manchesterunited",

        "mancity":
            "manchestercity",

        "spurs":
            "tottenhamhotspur",

        "barca":
            "barcelona",

        "bayern":
            "bayernmunchen",

        "intermilan":
            "internazionale",

        "atletico":
            "atleticomadrid",

        "dortmund":
            "borussiadortmund",
    }

    return aliases.get(
        text,
        text,
    )


def search_clubs(search_text, limit=20):

    query = normalise_club_text(
        search_text
    )

    if len(query) < 2:
        return []

    matches = []

    for club_id, club_name in INDEX.club_names.items():

        normalised_name = normalise_club_text(
            club_name
        )

        if query not in normalised_name:
            continue

        # Better matches appear first.
        if normalised_name == query:
            match_quality = 0

        elif normalised_name.startswith(query):
            match_quality = 1

        else:
            match_quality = 2

        matches.append(
            (
                match_quality,
                len(club_name),
                club_name.lower(),
                club_id,
                club_name,
            )
        )

    matches.sort()

    return [
        {
            "label": club_name,
            "value": club_id,
        }
        for (
            _quality,
            _length,
            _sort_name,
            club_id,
            club_name,
        )
        in matches[:limit]
    ]


# ============================================================
# GAME STATE
# ============================================================


def reset_game_state(game):

    game = dict(game)

    blank_nodes = [
        node
        for node in NODES
        if node != START_NODE
    ]

    game["blank_numbers"] = {
        node: number
        for number, node in enumerate(
            blank_nodes,
            start=1,
        )
    }

    # Only the bottom player is unlocked initially.
    game["unlocked"] = [
        START_NODE
    ]

    game["active_node"] = (
        START_NODE
    )

    # When a player has been revealed but the club has not
    # yet been correctly guessed.
    game["pending"] = None

    game["solved_edges"] = []

    return game


def make_new_game():

    # Generate all 16 valid players first.
    puzzle = generate_puzzle(
        INDEX,
        timeout_seconds=GENERATION_TIMEOUT,
    )

    game = puzzle.serialise(
        INDEX
    )

    return reset_game_state(
        game
    )


# ============================================================
# GRAPH ELEMENTS
# ============================================================


def build_elements(game):

    if not game.get("players"):
        return []

    unlocked = set(
        game.get(
            "unlocked",
            [],
        )
    )

    active = game.get(
        "active_node"
    )

    pending = game.get(
        "pending"
    )

    solved_edges = set(
        game.get(
            "solved_edges",
            [],
        )
    )

    blank_numbers = game.get(
        "blank_numbers",
        {},
    )

    pending_target = None
    pending_edge = None

    if pending:

        pending_target = (
            pending["target"]
        )

        pending_edge = (
            pending["edge_id"]
        )

    if active:

        active_neighbours = set(
            neighbours(
                game,
                active,
            )
        )

    else:

        active_neighbours = set()

    elements = []

    # ========================================================
    # PLAYERS
    # ========================================================

    for node in NODES:

        player = (
            game["players"][node]
        )

        classes = [
            "player"
        ]

        is_unlocked = (
            node in unlocked
        )

        is_pending = (
            node == pending_target
        )

        # ----------------------------------------------------
        # UNLOCKED PLAYER
        # ----------------------------------------------------

        if is_unlocked:

            classes.append(
                "revealed"
            )

            classes.append(
                "unlocked"
            )

            label = (
                player["name"]
            )

        # ----------------------------------------------------
        # PLAYER REVEALED, BUT LINK NOT SOLVED YET
        # ----------------------------------------------------

        elif is_pending:

            classes.append(
                "revealed"
            )

            classes.append(
                "pending-player"
            )

            label = (
                player["name"]
            )

        # ----------------------------------------------------
        # NUMBERED BLANK
        # ----------------------------------------------------

        else:

            classes.append(
                "hidden"
            )

            label = str(
                blank_numbers.get(
                    node,
                    "?",
                )
            )

            if (
                pending is None
                and node in active_neighbours
            ):

                classes.append(
                    "available"
                )

            else:

                classes.append(
                    "locked"
                )

        # ----------------------------------------------------
        # CURRENT PLAYER
        # ----------------------------------------------------

        if node == active:

            classes.append(
                "active-player"
            )

        elements.append(
            {
                "data": {

                    "id":
                        node,

                    "label":
                        label,

                    "name":
                        player["name"],

                    "image":
                        player.get(
                            "image_url",
                            "",
                        ),
                },

                "position":
                    POSITIONS[node],

                "classes":
                    " ".join(
                        classes
                    ),

                "locked":
                    True,
            }
        )

    # ========================================================
    # EDGES
    # ========================================================

    for edge in game["edges"]:

        classes = [
            "club-edge"
        ]

        edge_id = (
            edge["id"]
        )

        source = (
            edge["source"]
        )

        target = (
            edge["target"]
        )

        # Club name stays hidden unless solved.
        label = ""

        # ----------------------------------------------------
        # SOLVED LINK
        # ----------------------------------------------------

        if edge_id in solved_edges:

            classes.append(
                "solved"
            )

            label = (
                edge["club"]
            )

        # ----------------------------------------------------
        # LINK CURRENTLY BEING GUESSED
        # ----------------------------------------------------

        elif edge_id == pending_edge:

            classes.append(
                "pending-edge"
            )

            label = "?"

        # ----------------------------------------------------
        # AVAILABLE LINK
        # ----------------------------------------------------

        elif (
            pending is None
            and active in {
                source,
                target,
            }
            and (
                (
                    source == active
                    and target not in unlocked
                )
                or
                (
                    target == active
                    and source not in unlocked
                )
            )
        ):

            classes.append(
                "available-edge"
            )

        # ----------------------------------------------------
        # LOCKED LINK
        # ----------------------------------------------------

        else:

            classes.append(
                "locked-edge"
            )

        elements.append(
            {
                "data": {

                    "id":
                        edge_id,

                    "source":
                        source,

                    "target":
                        target,

                    "label":
                        label,
                },

                "classes":
                    " ".join(
                        classes
                    ),
            }
        )

    return elements


# ============================================================
# CYTOSCAPE STYLES
# ============================================================

STYLESHEET = [

    {
        "selector":
            "node.player",

        "style": {

            "width":
                94,

            "height":
                94,

            "shape":
                "hexagon",

            "border-width":
                3,

            "border-color":
                "#ffffff",

            "text-wrap":
                "wrap",

            "text-max-width":
                135,

            "font-family":
                "Arial, sans-serif",

            "font-weight":
                700,

            "font-size":
                13,

            "text-valign":
                "bottom",

            "text-margin-y":
                18,

            "color":
                "#0d2438",

            "overlay-opacity":
                0,
        },
    },

    {
        "selector":
            "node.hidden",

        "style": {

            "background-color":
                "#dff8fb",

            "color":
                "#07365b",

            "font-size":
                28,

            "font-weight":
                900,

            "text-valign":
                "center",

            "text-margin-y":
                0,
        },
    },

    {
        "selector":
            "node.locked",

        "style": {

            "opacity":
                0.35,

            "border-color":
                "#c7dadd",
        },
    },

    {
        "selector":
            "node.available",

        "style": {

            "opacity":
                1,

            "border-color":
                "#0a7ea4",

            "border-width":
                5,

            "background-color":
                "#ffffff",
        },
    },

    {
        "selector":
            "node.revealed",

        "style": {

            "opacity":
                1,

            "background-color":
                "#dff8fb",

            "background-image":
                "data(image)",

            "background-fit":
                "contain",

            "background-clip":
                "node",
        },
    },

    {
        "selector":
            "node.pending-player",

        "style": {

            "border-color":
                "#d7a100",

            "border-width":
                5,
        },
    },

    {
        "selector":
            "node.active-player",

        "style": {

            "border-color":
                "#0a7ea4",

            "border-width":
                6,
        },
    },

    {
        "selector":
            "edge.club-edge",

        "style": {

            "width":
                3,

            "curve-style":
                "straight",

            "line-color":
                "#b8dfe4",

            "label":
                "data(label)",

            "font-size":
                11,

            "font-weight":
                800,

            "text-rotation":
                "autorotate",

            "text-background-color":
                "#f7ffff",

            "text-background-opacity":
                0.96,

            "text-background-padding":
                4,

            "overlay-opacity":
                0,
        },
    },

    {
        "selector":
            "edge.locked-edge",

        "style": {

            "opacity":
                0.2,
        },
    },

    {
        "selector":
            "edge.available-edge",

        "style": {

            "line-color":
                "#0a7ea4",

            "width":
                4,

            "opacity":
                0.9,
        },
    },

    {
        "selector":
            "edge.pending-edge",

        "style": {

            "line-color":
                "#d7a100",

            "width":
                5,
        },
    },

    {
        "selector":
            "edge.solved",

        "style": {

            "line-color":
                "#18794e",

            "width":
                5,

            "color":
                "#145c3c",

            "text-background-color":
                "#e8f7ef",
        },
    },
]


# ============================================================
# INITIAL PUZZLE
# ============================================================

try:

    print()
    print(
        "Generating first pyramid..."
    )

    INITIAL_GAME = (
        make_new_game()
    )

    starter = (
        INITIAL_GAME
        ["players"]
        [START_NODE]
        ["name"]
    )

    INITIAL_MESSAGE = (
        f"Start with {starter}. "
        "Click one of the highlighted connected hexes."
    )

    print(
        "Pyramid generated successfully."
    )

except Exception as exc:

    print()
    print(
        "ERROR GENERATING FIRST PUZZLE:"
    )

    print(
        exc
    )

    INITIAL_GAME = {

        "players":
            {},

        "edges":
            [],

        "blank_numbers":
            {},

        "unlocked":
            [],

        "active_node":
            None,

        "pending":
            None,

        "solved_edges":
            [],
    }

    INITIAL_MESSAGE = (
        f"Could not generate a puzzle: {exc}"
    )


# ============================================================
# DASH
# ============================================================

app = Dash(
    __name__
)

app.title = (
    "Player to Player"
)


# ============================================================
# PAGE
# ============================================================

app.layout = html.Div(

    className="page",

    children=[

        dcc.Store(
            id="game-store",
            data=INITIAL_GAME,
        ),

        # ====================================================
        # HEADER
        # ====================================================

        html.Div(

            className="topbar",

            children=[

                html.Div(

                    children=[

                        html.H1(
                            "PLAYER TO PLAYER",
                            className="title",
                        ),

                        html.Div(
                            (
                                "Reveal player → "
                                "guess shared club → "
                                "unlock next route"
                            ),
                            className="subtitle",
                        ),
                    ],
                ),

                html.Div(

                    className="controls",

                    children=[

                        html.Button(
                            "New puzzle",
                            id="new-puzzle",
                            n_clicks=0,
                        ),

                        html.Button(
                            "Reset game",
                            id="reset-game",
                            n_clicks=0,
                        ),
                    ],
                ),
            ],
        ),

        html.Div(
            id="message",
            className="message",
            children=INITIAL_MESSAGE,
        ),

        # ====================================================
        # CONTENT
        # ====================================================

        html.Div(

            className="main-grid",

            children=[

                # ============================================
                # PYRAMID
                # ============================================

                html.Div(

                    className="graph-card",

                    children=[

                        cyto.Cytoscape(

                            id="pyramid",

                            elements=(
                                build_elements(
                                    INITIAL_GAME
                                )
                                if INITIAL_GAME.get(
                                    "players"
                                )
                                else []
                            ),

                            layout={
                                "name":
                                    "preset",

                                "fit":
                                    True,

                                "padding":
                                    45,
                            },

                            stylesheet=
                                STYLESHEET,

                            style={
                                "width":
                                    "100%",

                                "height":
                                    "900px",
                            },

                            minZoom=
                                0.45,

                            maxZoom=
                                1.7,

                            userZoomingEnabled=
                                True,

                            userPanningEnabled=
                                True,

                            autoungrabify=
                                True,
                        )
                    ],
                ),

                # ============================================
                # SIDEBAR
                # ============================================

                html.Div(

                    className="side",

                    children=[

                        html.Div(

                            className="stat-card",

                            children=[

                                html.Div(
                                    "PLAYERS UNLOCKED",
                                    className="eyebrow",
                                ),

                                html.Div(
                                    id="unlocked-count",
                                    className="big-stat",
                                ),

                                html.Div(
                                    (
                                        "Click any unlocked player "
                                        "to explore from them again."
                                    ),
                                    className="muted",
                                ),
                            ],
                        ),

                        # ====================================
                        # CLUB GUESS
                        # ====================================

                        html.Div(

                            className="guess-card",

                            children=[

                                html.Div(
                                    "GUESS THE LINK",
                                    className="eyebrow",
                                ),

                                html.Div(
                                    id="guess-question",
                                    className="guess-question",
                                ),

                                html.Div(

                                    className="guess-row",

                                    children=[

                                        dcc.Dropdown(

                                            id="club-guess",

                                            options=[],

                                            value=None,

                                            placeholder=(
                                                "Start typing a club..."
                                            ),

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
                                    (
                                        "Type at least 2 letters. "
                                        "For example, typing "
                                        "'Benf' will find clubs "
                                        "containing Benfica."
                                    ),
                                    className="small-note",
                                ),

                                html.Div(

                                    style={
                                        "marginTop":
                                            "8px"
                                    },

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

                        # ====================================
                        # ACTIVE PLAYER
                        # ====================================

                        html.Div(
                            id="player-detail",
                            className="detail-card",
                        ),

                        # ====================================
                        # RULES
                        # ====================================

                        html.Div(

                            className="rule-card",

                            children=[

                                html.Div(
                                    "HOW TO PLAY",
                                    className="eyebrow",
                                ),

                                html.P(
                                    (
                                        "1. Click a highlighted "
                                        "numbered player."
                                    )
                                ),

                                html.P(
                                    (
                                        "2. Their identity is "
                                        "revealed immediately."
                                    )
                                ),

                                html.P(
                                    (
                                        "3. Search for the club "
                                        "they share with your "
                                        "current player."
                                    )
                                ),

                                html.P(
                                    (
                                        "4. Select the club and "
                                        "submit your answer."
                                    )
                                ),

                                html.P(
                                    (
                                        "5. If correct, the player "
                                        "is unlocked and you can "
                                        "continue from them."
                                    )
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ],
)


# ============================================================
# AUTOCOMPLETE CLUB SEARCH
# ============================================================


@callback(

    Output(
        "club-guess",
        "options",
    ),

    Input(
        "club-guess",
        "search_value",
    ),

    State(
        "club-guess",
        "value",
    ),

    State(
        "game-store",
        "data",
    ),
)
def update_club_suggestions(
    search_value,
    selected_value,
    game,
):

    # No player is waiting for a club guess.
    if (
        not game
        or not game.get(
            "pending"
        )
    ):

        return []

    options = []

    # Keep an already-selected option visible.
    if selected_value is not None:

        try:

            selected_id = int(
                selected_value
            )

            selected_name = (
                INDEX.club_names.get(
                    selected_id
                )
            )

            if selected_name:

                options.append(
                    {
                        "label":
                            selected_name,

                        "value":
                            selected_id,
                    }
                )

        except (
            TypeError,
            ValueError,
        ):

            pass

    if not search_value:

        return options

    if len(
        search_value.strip()
    ) < 2:

        return options

    matches = search_clubs(
        search_value,
        limit=20,
    )

    existing_ids = {
        option["value"]
        for option in options
    }

    for option in matches:

        if (
            option["value"]
            not in existing_ids
        ):

            options.append(
                option
            )

    return options


# ============================================================
# MAIN GAME CALLBACK
# ============================================================


@callback(

    Output(
        "game-store",
        "data",
    ),

    Output(
        "message",
        "children",
    ),

    Output(
        "club-guess",
        "value",
    ),

    Input(
        "pyramid",
        "tapNodeData",
    ),

    Input(
        "submit-guess",
        "n_clicks",
    ),

    Input(
        "cancel-pending",
        "n_clicks",
    ),

    Input(
        "new-puzzle",
        "n_clicks",
    ),

    Input(
        "reset-game",
        "n_clicks",
    ),

    State(
        "club-guess",
        "value",
    ),

    State(
        "game-store",
        "data",
    ),

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
):

    trigger = (
        ctx.triggered_id
    )

    # ========================================================
    # NEW PUZZLE
    # ========================================================

    if trigger == "new-puzzle":

        try:

            new_game = (
                make_new_game()
            )

            starter = (
                new_game
                ["players"]
                [START_NODE]
                ["name"]
            )

            return (
                new_game,
                (
                    f"Start with {starter}. "
                    "Click a highlighted connected hex."
                ),
                None,
            )

        except GenerationTimeout as exc:

            return (
                game,
                str(exc),
                None,
            )

        except Exception as exc:

            return (
                game,
                (
                    "Could not generate a new puzzle: "
                    f"{exc}"
                ),
                None,
            )

    # ========================================================
    # NO GAME
    # ========================================================

    if (
        not game
        or not game.get(
            "players"
        )
    ):

        return (
            no_update,
            "No puzzle is loaded.",
            no_update,
        )

    # ========================================================
    # RESET
    # ========================================================

    if trigger == "reset-game":

        game = (
            reset_game_state(
                game
            )
        )

        starter = (
            game
            ["players"]
            [START_NODE]
            ["name"]
        )

        return (
            game,
            (
                f"Game reset. "
                f"Start with {starter}."
            ),
            None,
        )

    # ========================================================
    # CANCEL CURRENT PLAYER
    # ========================================================

    if trigger == "cancel-pending":

        if not game.get(
            "pending"
        ):

            return (
                game,
                "Nothing to cancel.",
                None,
            )

        game = dict(
            game
        )

        game[
            "pending"
        ] = None

        active = (
            game[
                "active_node"
            ]
        )

        active_name = (
            game
            ["players"]
            [active]
            ["name"]
        )

        return (
            game,
            (
                f"Choose another player "
                f"connected to {active_name}."
            ),
            None,
        )

    # ========================================================
    # SUBMIT CLUB
    # ========================================================

    if trigger == "submit-guess":

        pending = (
            game.get(
                "pending"
            )
        )

        if not pending:

            return (
                game,
                "Choose a player first.",
                None,
            )

        if selected_club_id is None:

            return (
                game,
                (
                    "Start typing a club and "
                    "select it from the suggestions."
                ),
                None,
            )

        edge = next(
            (
                edge
                for edge in game["edges"]
                if edge["id"]
                == pending["edge_id"]
            ),
            None,
        )

        if edge is None:

            return (
                game,
                "Could not find this connection.",
                None,
            )

        actual_club_id = int(
            edge["club_id"]
        )

        try:

            guessed_club_id = int(
                selected_club_id
            )

        except (
            TypeError,
            ValueError,
        ):

            return (
                game,
                "Select a club from the suggestions.",
                None,
            )

        source = (
            pending["source"]
        )

        target = (
            pending["target"]
        )

        source_name = (
            game
            ["players"]
            [source]
            ["name"]
        )

        target_name = (
            game
            ["players"]
            [target]
            ["name"]
        )

        # ====================================================
        # CORRECT
        # ====================================================

        if (
            guessed_club_id
            == actual_club_id
        ):

            actual_club_name = (
                edge["club"]
            )

            game = dict(
                game
            )

            unlocked = set(
                game.get(
                    "unlocked",
                    [],
                )
            )

            unlocked.add(
                target
            )

            solved_edges = set(
                game.get(
                    "solved_edges",
                    [],
                )
            )

            solved_edges.add(
                edge["id"]
            )

            game[
                "unlocked"
            ] = list(
                unlocked
            )

            game[
                "solved_edges"
            ] = list(
                solved_edges
            )

            # Move onto the newly unlocked player.
            game[
                "active_node"
            ] = target

            game[
                "pending"
            ] = None

            return (
                game,
                (
                    f"Correct — {actual_club_name}. "
                    f"{target_name} is now unlocked. "
                    f"Choose any highlighted player "
                    f"connected to {target_name}."
                ),
                None,
            )

        # ====================================================
        # INCORRECT
        # ====================================================

        guessed_name = (
            INDEX.club_names.get(
                guessed_club_id,
                "that club",
            )
        )

        return (
            game,
            (
                f"No — {guessed_name} is not "
                f"the link between {source_name} "
                f"and {target_name}. Try again."
            ),
            None,
        )

    # ========================================================
    # CLICK PLAYER
    # ========================================================

    if (
        trigger == "pyramid"
        and tap_node
    ):

        node = (
            tap_node.get(
                "id"
            )
        )

        if node not in game[
            "players"
        ]:

            return (
                no_update,
                no_update,
                no_update,
            )

        # A revealed player is already waiting for a club guess.
        if game.get(
            "pending"
        ):

            return (
                game,
                (
                    "Guess the shared club first, "
                    "or choose another hex."
                ),
                no_update,
            )

        unlocked = set(
            game.get(
                "unlocked",
                [],
            )
        )

        # ====================================================
        # SELECT AN EXISTING UNLOCKED PLAYER
        # ====================================================

        if node in unlocked:

            game = dict(
                game
            )

            game[
                "active_node"
            ] = node

            player_name = (
                game
                ["players"]
                [node]
                ["name"]
            )

            return (
                game,
                (
                    f"{player_name} selected. "
                    "Choose one of the highlighted "
                    "players connected to them."
                ),
                None,
            )

        # ====================================================
        # NUMBERED PLAYER
        # ====================================================

        active = (
            game.get(
                "active_node"
            )
        )

        if active is None:

            return (
                game,
                "No active player selected.",
                None,
            )

        if node not in neighbours(
            game,
            active,
        ):

            active_name = (
                game
                ["players"]
                [active]
                ["name"]
            )

            return (
                game,
                (
                    f"That player is not connected "
                    f"to {active_name}. "
                    "Choose a highlighted hex."
                ),
                None,
            )

        edge = edge_between(
            game,
            active,
            node,
        )

        if edge is None:

            return (
                game,
                "Could not find that connection.",
                None,
            )

        game = dict(
            game
        )

        # Reveal the footballer immediately.
        # They do not become unlocked until the club is correct.
        game[
            "pending"
        ] = {

            "source":
                active,

            "target":
                node,

            "edge_id":
                edge["id"],
        }

        source_name = (
            game
            ["players"]
            [active]
            ["name"]
        )

        target_name = (
            game
            ["players"]
            [node]
            ["name"]
        )

        return (
            game,
            (
                f"{target_name} revealed. "
                f"What club did {source_name} "
                f"and {target_name} both play for?"
            ),
            None,
        )

    return (
        no_update,
        no_update,
        no_update,
    )


# ============================================================
# RENDER
# ============================================================


@callback(

    Output(
        "pyramid",
        "elements",
    ),

    Output(
        "unlocked-count",
        "children",
    ),

    Output(
        "guess-question",
        "children",
    ),

    Output(
        "club-guess",
        "disabled",
    ),

    Output(
        "submit-guess",
        "disabled",
    ),

    Output(
        "cancel-pending",
        "disabled",
    ),

    Output(
        "player-detail",
        "children",
    ),

    Input(
        "game-store",
        "data",
    ),
)
def render_game(
    game,
):

    if (
        not game
        or not game.get(
            "players"
        )
    ):

        return (
            [],
            "0 / 16",
            "No puzzle loaded.",
            True,
            True,
            True,
            html.Div(
                "No player selected.",
                className="muted",
            ),
        )

    unlocked = set(
        game.get(
            "unlocked",
            [],
        )
    )

    pending = (
        game.get(
            "pending"
        )
    )

    active = (
        game.get(
            "active_node"
        )
    )

    # ========================================================
    # GUESSING
    # ========================================================

    if pending:

        source = (
            game
            ["players"]
            [pending["source"]]
            ["name"]
        )

        target = (
            game
            ["players"]
            [pending["target"]]
            ["name"]
        )

        question = (
            f"What club did {source} "
            f"and {target} both play for?"
        )

        dropdown_disabled = False
        submit_disabled = False
        cancel_disabled = False

    else:

        active_name = (
            game
            ["players"]
            [active]
            ["name"]
        )

        question = (
            f"Choose a numbered player "
            f"connected to {active_name}."
        )

        dropdown_disabled = True
        submit_disabled = True
        cancel_disabled = True

    # ========================================================
    # CURRENT PLAYER
    # ========================================================

    active_player = (
        game
        ["players"]
        [active]
    )

    player_detail = [

        html.Div(
            "CURRENT PLAYER",
            className="eyebrow",
        ),

        html.Div(
            active_player["name"],
            className="player-name",
        ),

        html.Div(
            active_player.get(
                "position",
                "",
            ),
            className="muted",
        ),
    ]

    return (

        build_elements(
            game
        ),

        f"{len(unlocked)} / 16",

        question,

        dropdown_disabled,

        submit_disabled,

        cancel_disabled,

        player_detail,
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    print()
    print(
        "Starting Player to Player..."
    )

    print(
        "http://127.0.0.1:8050"
    )

    print()

    app.run(
        debug=False
    )