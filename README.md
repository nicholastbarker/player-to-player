# Player to Player — 16-player pyramid

A Dash/Cytoscape implementation of a football "player to player" pyramid.

## Rules implemented

- Shape is exactly **1-2-3-4-3-2-1** = 16 footballers.
- The **entire valid solution is generated before the UI is shown**.
- Every connected pair of players has **exactly one recorded senior club in common**.
- Club links are **edge-specific**, not row-specific.
- At any branching player, incident edges must use **different clubs**, so choosing a different route really does change the club clue.
- Players are unique within a puzzle.
- Loans are included automatically when the player made a recorded senior appearance for the loan club.
- A player is eligible if their most recent recorded senior appearance is within the last 20 years (configurable).
- One connected top-to-bottom route is revealed at the start: 7 clue players, leaving 9 numbered blanks.
- Clicking a numbered blank reveals that player.

## Data

The first run downloads these Transfermarkt-derived public dataset files:

- `players.csv.gz`
- `clubs.csv.gz`
- `appearances.csv.gz`

from the `dcaribou/transfermarkt-datasets` published data host.

Using senior appearances rather than raw transfer rows has a useful property for this game:
a club only counts if the player is actually recorded as appearing for it. That naturally includes loan spells with appearances and avoids most youth-team-only history.

The source dataset is currently a snapshot to July 2026, so it does not yet include 2026/27 updates.

## Install

Python 3.11+ recommended.

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Then:

```bash
pip install -r requirements.txt
python app.py
```

Open the local Dash URL printed in the terminal, normally:

```text
http://127.0.0.1:8050
```

The first run can take a while because it downloads and preprocesses the football data. Later runs use cached local files.

## Optional environment settings

Defaults:

```text
PTP_ACTIVE_WITHIN_YEARS=20
PTP_MIN_APPEARANCES=20
PTP_MIN_PEAK_VALUE_EUR=0
PTP_GENERATION_TIMEOUT=25
```

Examples:

### Favour more recognisable players

Require a historical peak Transfermarkt value of at least €5m:

Windows PowerShell:

```powershell
$env:PTP_MIN_PEAK_VALUE_EUR="5000000"
python app.py
```

macOS/Linux:

```bash
PTP_MIN_PEAK_VALUE_EUR=5000000 python app.py
```

### Broaden the pool if generation is difficult

```text
PTP_MIN_APPEARANCES=10
PTP_GENERATION_TIMEOUT=45
```

## Files

- `app.py` — Dash user interface and reveal interactions
- `football_data.py` — download, preprocess, and career-club index
- `pyramid.py` — 16-node topology and constraint/backtracking generator
- `requirements.txt` — Python dependencies

## Important data caveat

"Exactly one club in common" means exactly one club in common **within the dataset's recorded senior appearances**. This is much more reliable than guessing from transfer rows, but no third-party football dataset can guarantee perfect historical completeness for every league and every player.
