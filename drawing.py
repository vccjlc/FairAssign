# drawing.py
# Helper script to generate a fixed Secret Santa draw once, locally.

import json
from pathlib import Path

from fair_assign import USER_NAMES, ASSIGNMENTS_PER_GIVER, generate_assignments

BASE_DIR = Path(__file__).parent
ASSIGNMENTS_FILE = BASE_DIR / "assignments_prod.json"

if __name__ == "__main__":
    assignments = generate_assignments(USER_NAMES, ASSIGNMENTS_PER_GIVER)
    ASSIGNMENTS_FILE.write_text(
        json.dumps(assignments, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print("Written fixed assignments to", ASSIGNMENTS_FILE.resolve())
