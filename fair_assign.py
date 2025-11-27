"""
fair_assign.py

Streamlit Secret Santa style app for a fixed group of 5 people.

Features now
- Login with name + password
- Each user can enter up to 7 wishlist items
- When everyone has confirmed, a random assignment (derangement) is created
- On next login, each user sees a Christmas clip and then only their own assignment
- In TEST mode, there is a developer panel with full visibility and reset tools
- In PROD mode, assignments remain secret except for each user's own result

To run locally:
    streamlit run fair_assign.py

To switch modes:
    export FAIR_ASSIGN_MODE=test   # or prod
"""

import json
import base64
import os
import random
import time
from pathlib import Path
from typing import Dict, List

import streamlit as st


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_MODE = (os.getenv("FAIR_ASSIGN_MODE") or "test").strip().lower()
if APP_MODE not in ("test", "prod"):
    APP_MODE = "test"

PARTICIPANTS = [
    {"name": "Magda", "password": "Magda1"},
    {"name": "Maria", "password": "Maria2"},
    {"name": "Asia", "password": "Asia3"},
    {"name": "Zuza", "password": "Zuza4"},
    {"name": "Jan", "password": "Jan5"},
]

USER_PASSWORDS = {p["name"]: p["password"] for p in PARTICIPANTS}
USER_NAMES = [p["name"] for p in PARTICIPANTS]

# Base directory and media paths
BASE_DIR = Path(__file__).parent
BACKGROUND_PATH = BASE_DIR / "assets" / "background.png"

CHRISTMAS_CLIP_PATH = os.getenv(
    "CHRISTMAS_CLIP_PATH",
    str(BASE_DIR / "assets" / "christmas_clip.mp4"),
)

SANTA_AUDIO_PATH = os.getenv(
    "SANTA_AUDIO_PATH",
    str(BASE_DIR / "assets" / "intro_jingle.mp3"),
)

STATE_FILE = Path(f"fair_assign_state_{APP_MODE}.json")


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _safe_rerun() -> None:
    """Call st.rerun when available, falling back to experimental API."""
    rerun_fn = getattr(st, "rerun", None)
    if callable(rerun_fn):
        rerun_fn()
    else:
        st.experimental_rerun()


def init_state() -> Dict:
    """Create a fresh state structure for the current mode."""
    return {
        "users": {
            name: {
                "preferences": [],
                "confirmed": False,
                "result_revealed": False,
            }
            for name in USER_NAMES
        },
        # Pre-generate a derangement early; it will be finalized later
        "assignments": generate_derangement(USER_NAMES),
        "assignments_generated": False,
    }


def save_state(state: Dict) -> None:
    """Safely save state to disk as JSON."""
    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp.replace(STATE_FILE)


def load_state() -> Dict:
    """Load state from disk, or create a fresh one if needed."""
    if not STATE_FILE.exists():
        state = init_state()
        save_state(state)
        return state

    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        # Corrupted file or JSON error; start over
        state = init_state()
        save_state(state)
        return state

    changed = False

    # Ensure basic keys
    if "users" not in state or not isinstance(state["users"], dict):
        state["users"] = {}
        changed = True
    if "assignments" not in state or not isinstance(state["assignments"], dict):
        state["assignments"] = {}
        changed = True
    if "assignments_generated" not in state:
        state["assignments_generated"] = False
        changed = True

    # Ensure all participants exist and have expected fields
    for name in USER_NAMES:
        if name not in state["users"]:
            state["users"][name] = {
                "preferences": [],
                "confirmed": False,
                "result_revealed": False,
            }
            changed = True
        else:
            u = state["users"][name]
            if "preferences" not in u:
                u["preferences"] = []
                changed = True
            if "confirmed" not in u:
                u["confirmed"] = False
                changed = True
            if "result_revealed" not in u:
                u["result_revealed"] = False
                changed = True

    # Remove any users that are no longer in the participant list
    for name in list(state["users"].keys()):
        if name not in USER_NAMES:
            del state["users"][name]
            changed = True

    # Validate or (re)create a pre-generated derangement if needed
    if not is_valid_derangement(state.get("assignments", {}), USER_NAMES):
        state["assignments"] = generate_derangement(USER_NAMES)
        changed = True

    if changed:
        save_state(state)

    return state


def all_users_confirmed(state: Dict) -> bool:
    users = state.get("users", {})
    return bool(users) and all(u.get("confirmed") for u in users.values())


def generate_derangement(names: List[str]) -> Dict[str, str]:
    """Return a random mapping where no one gets themselves."""
    if len(names) < 2:
        raise ValueError("Need at least two participants")

    while True:
        shuffled = names[:]
        random.shuffle(shuffled)
        if all(a != b for a, b in zip(names, shuffled)):
            return dict(zip(names, shuffled))


def is_valid_derangement(mapping: Dict[str, str], names: List[str]) -> bool:
    """Check if mapping is a valid derangement over the given names."""
    if not isinstance(mapping, dict):
        return False
    if set(mapping.keys()) != set(names):
        return False
    if set(mapping.values()) != set(names):
        return False
    for giver, receiver in mapping.items():
        if giver == receiver:
            return False
    return True


def ensure_assignments(state: Dict) -> None:
    """Generate assignments once all users have confirmed."""
    if not state["assignments_generated"] and all_users_confirmed(state):
        # Keep the pre-generated mapping if valid; otherwise create one now
        if not is_valid_derangement(state.get("assignments", {}), USER_NAMES):
            state["assignments"] = generate_derangement(USER_NAMES)
        state["assignments_generated"] = True
        save_state(state)


def authenticate(name: str, password: str) -> bool:
    expected = USER_PASSWORDS.get(name)
    return expected is not None and password == expected


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def add_christmas_style() -> None:
    """Simple Christmas themed background and card styling."""
    # Try to load background image
    bg_css = ""
    try:
        if BACKGROUND_PATH.exists():
            with BACKGROUND_PATH.open("rb") as f:
                data = f.read()
            encoded = base64.b64encode(data).decode()
            bg_css = f"""
        [data-testid="stAppViewContainer"] {{
            background: url("data:image/png;base64,{encoded}") no-repeat center center fixed;
            background-size: cover;
        }}
        """
        else:
            # Darker fallback gradient for better contrast with white headings
            bg_css = """
        [data-testid="stAppViewContainer"] {
            background: radial-gradient(circle at top, #2a2a2a, #000000);
        }
        """
    except Exception:
        # If any error, keep a safe fallback
        bg_css = """
        [data-testid="stAppViewContainer"] {
            background: radial-gradient(circle at top, #2a2a2a, #000000);
        }
        """

    st.markdown(
        f"""
        <style>
        {bg_css}
        [data-testid="stHeader"] {
            background-color: rgba(0, 0, 0, 0);
        }
        .christmas-card {
            background-color: #ffffffee;
            padding: 1.5rem 2rem;
            border-radius: 1rem;
            border: 1px solid #eed6d6;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.06);
            max-width: 640px;
            margin: 1.5rem auto;
            color: #111111;
        }
        .christmas-title {
            text-align: center;
            font-size: 2.1rem;
            font-weight: 700;
            color: #ffffff;
            margin-top: 0.5rem;
            margin-bottom: 0.2rem;
        }
        .christmas-subtitle {
            text-align: center;
            font-size: 1rem;
            color: #ffffff;
            margin-bottom: 0.8rem;
        }
        [data-testid="stCaptionContainer"] p {{
            color: #ffffff !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def show_login() -> str | None:
    """Show login form and return the authenticated user name, or None."""
    st.markdown('<div class="christmas-card">', unsafe_allow_html=True)
    st.subheader("Log in")

    with st.form("login_form"):
        name = st.selectbox("Who are you", USER_NAMES)
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Enter the Christmas lodge")

    if submitted:
        if authenticate(name, password):
            st.session_state["current_user"] = name
            st.success(f"Welcome, {name}")
            # Trigger a rerun so we skip the login form
            _safe_rerun()
        else:
            st.error("Invalid password")

    st.markdown("</div>", unsafe_allow_html=True)
    return st.session_state.get("current_user")


def show_preferences_ui(user_name: str, state: Dict) -> None:
    """Page where a user defines their wishlist."""
    user_state = state["users"][user_name]
    existing_prefs = user_state.get("preferences") or []

    st.markdown('<div class="christmas-card">', unsafe_allow_html=True)
    st.subheader("Your Christmas wishlist")

    st.write(
        "You can list up to seven ideas. "
        "Leave fields empty if you prefer to be surprised."
    )

    with st.form("wishlist_form"):
        new_prefs: List[str] = []
        for i in range(7):
            default_value = existing_prefs[i] if i < len(existing_prefs) else ""
            value = st.text_input(
                f"Gift idea {i + 1}",
                value=default_value,
                key=f"pref_{user_name}_{i}",
            )
            value = value.strip()
            if value:
                new_prefs.append(value)

        submitted = st.form_submit_button("Save my wishlist")

    if submitted:
        user_state["preferences"] = new_prefs
        user_state["confirmed"] = True
        save_state(state)
        st.success(
            "Wishlist saved. You can log in again later to update it until everyone is done."
        )

    completed = sum(1 for u in state["users"].values() if u.get("confirmed"))
    total = len(state["users"])
    st.info(f"{completed} of {total} participants have saved their wishlist.")

    # Status overview for fun; full details in test mode developer panel
    with st.expander("Progress of everyone", expanded=False):
        for name, u in state["users"].items():
            mark = "✓" if u.get("confirmed") else "•"
            st.write(f"{mark} {name}")

    st.markdown("</div>", unsafe_allow_html=True)


def show_assignment_ui(user_name: str, state: Dict) -> None:
    """Page where a user sees their final assignment."""
    user_state = state["users"][user_name]
    assignments = state.get("assignments", {})
    recipient = assignments.get(user_name)

    st.markdown('<div class="christmas-card">', unsafe_allow_html=True)
    st.subheader("Your Secret Santa draw")

    if not recipient:
        st.error("Assignments are not ready yet. Please check back later.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    st.write(
        "Enjoy a short Christmas clip while the magic randomizer works in the background."
    )

    if CHRISTMAS_CLIP_PATH:
        try:
            st.video(CHRISTMAS_CLIP_PATH)
        except Exception:
            st.warning(
                "Christmas clip is not configured correctly. "
                "Place a video file next to this script and set CHRISTMAS_CLIP_PATH."
            )
    else:
        st.warning("No Christmas clip configured yet.")

    st.caption("Randomly selecting your Christmas giftee")

    already_revealed = bool(user_state.get("result_revealed"))
    clicked = False

    if not already_revealed:
        clicked = st.button("Reveal who I am buying a gift for")

        if clicked:
            with st.spinner("Shuffling names"):
                time.sleep(1.5)
            user_state["result_revealed"] = True
            save_state(state)
            already_revealed = True

    if already_revealed:
        st.success(f"You are buying a gift for: {recipient}")
        prefs = state["users"].get(recipient, {}).get("preferences") or []
        if prefs:
            st.write("")
            st.write("Their wishlist:")
            for idx, item in enumerate(prefs, 1):
                st.write(f"{idx}. {item}")
        else:
            st.write("")
            st.write(
                "They did not provide any wishlist items. "
                "Total creative freedom for you."
            )
    else:
        st.info("Press the button when you are ready to reveal your assignment.")

    st.markdown("</div>", unsafe_allow_html=True)


def show_test_panel(state: Dict) -> None:
    """Developer tools only in TEST mode."""
    st.sidebar.markdown("---")
    st.sidebar.subheader("Test mode panel")

    st.sidebar.write(f"Assignments finalized: {state.get('assignments_generated')}")

    # Show current (draft or final) assignments in test mode
    if state.get("assignments"):
        header = (
            "**Assignments (final)**"
            if state.get("assignments_generated")
            else "**Assignments (current draft)**"
        )
        st.sidebar.markdown(header)
        for giver, receiver in state["assignments"].items():
            st.sidebar.write(f"{giver} → {receiver}")

    # Allow regenerating draft assignments before finalization
    if not state.get("assignments_generated"):
        if st.sidebar.button("Regenerate draft assignments"):
            state["assignments"] = generate_derangement(USER_NAMES)
            save_state(state)
            st.sidebar.success("Draft assignments regenerated.")

    with st.sidebar.expander("User details and wishlists", expanded=False):
        for name, u in state["users"].items():
            st.write(f"**{name}**")
            st.write(
                f"confirmed: {u.get('confirmed')}, "
                f"revealed: {u.get('result_revealed')}"
            )
            prefs = u.get("preferences") or []
            if prefs:
                for idx, item in enumerate(prefs, 1):
                    st.write(f"{idx}. {item}")
            else:
                st.write("no wishlist items")
            st.write("")

    if st.sidebar.button("Reset all data for this mode"):
        new_state = init_state()
        save_state(new_state)
        st.sidebar.success("State reset. The page will reload.")
        _safe_rerun()


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def show_entry_gate() -> bool:
    """
    Show a big 'Play & enter' button the first time.
    After the user clicks it, play intro audio once and let the rest of the app render.
    Returns True if the main UI should continue, False if we should stop after the gate.
    """

    # If the user has already entered, play audio once (if available) and continue
    if st.session_state.get("entered_lodge"):
        if SANTA_AUDIO_PATH and not st.session_state.get("intro_audio_played"):
            try:
                # Prefer hidden HTML audio so no bar is visible
                if str(SANTA_AUDIO_PATH).startswith(("http://", "https://", "data:")):
                    st.markdown(
                        f'<audio src="{SANTA_AUDIO_PATH}" autoplay style="display:none"></audio>',
                        unsafe_allow_html=True,
                    )
                else:
                    audio_path = Path(SANTA_AUDIO_PATH)
                    if audio_path.exists():
                        data = audio_path.read_bytes()
                        b64 = base64.b64encode(data).decode()
                        st.markdown(
                            f'<audio autoplay style="display:none" src="data:audio/mpeg;base64,{b64}"></audio>',
                            unsafe_allow_html=True,
                        )
                    else:
                        # Fall back to visible player if path missing
                        st.audio(SANTA_AUDIO_PATH, format="audio/mp3", start_time=0, autoplay=True)
            except Exception:
                # Final fallback
                try:
                    st.audio(SANTA_AUDIO_PATH, format="audio/mp3", start_time=0, autoplay=True)
                except Exception:
                    st.warning("Intro music not available; check SANTA_AUDIO_PATH.")
            st.session_state["intro_audio_played"] = True
        return True

    # First visit; show gate with big button
    st.markdown(
        """
        <div class="christmas-card" style="text-align:center;">
            <h2 style="margin-bottom: 0.5rem;">Welcome to the Christmas lodge</h2>
            <p style="margin-bottom: 1rem;">Press play to enter</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    entered = st.button(
        "▶ Play & enter",
        key="enter_lodge_button",
        use_container_width=True,
    )

    if entered:
        st.session_state["entered_lodge"] = True
        # Play immediately, without visible controls
        try:
            if str(SANTA_AUDIO_PATH).startswith(("http://", "https://", "data:")):
                st.markdown(
                    f'<audio src="{SANTA_AUDIO_PATH}" autoplay style="display:none"></audio>',
                    unsafe_allow_html=True,
                )
            else:
                audio_path = Path(SANTA_AUDIO_PATH)
                if audio_path.exists():
                    data = audio_path.read_bytes()
                    b64 = base64.b64encode(data).decode()
                    st.markdown(
                        f'<audio autoplay style="display:none" src="data:audio/mpeg;base64,{b64}"></audio>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.audio(SANTA_AUDIO_PATH, format="audio/mp3", start_time=0, autoplay=True)
        except Exception:
            try:
                st.audio(SANTA_AUDIO_PATH, format="audio/mp3", start_time=0, autoplay=True)
            except Exception:
                st.warning("Intro music not available; check SANTA_AUDIO_PATH.")
        st.session_state["intro_audio_played"] = True
        # Continue without forcing a rerun
        return True

    return False

def main() -> None:
    st.set_page_config(
        page_title="Fair Assign – Secret Santa",
        layout="centered",
    )
    add_christmas_style()

    st.markdown('<div class="christmas-title">Fair Assign – Secret Santa</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="christmas-subtitle">'
        'Shared Christmas wishlist and fair random assignments for our group'
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        f"<p style='text-align:center; color:#ffffff; font-size:0.9rem;'>"
        f"Current mode: <strong>{APP_MODE.upper()}</strong>"
        "</p>",
        unsafe_allow_html=True,
    )

    # NEW: entry gate with big Play button and intro audio
    if not show_entry_gate():
        # User has not pressed Play yet; do not show login or anything else
        return

    state = load_state()

    # Login
    current_user = st.session_state.get("current_user")
    if not current_user:
        current_user = show_login()
        if not current_user:
            # No valid login yet, stop rendering further UI
            if APP_MODE == "test":
                # Still show test panel if you want to inspect state without logging in
                show_test_panel(state)
            return

    # Sidebar user panel
    st.sidebar.write(f"Logged in as: **{current_user}**")
    if st.sidebar.button("Log out"):
        st.session_state.clear()
        st.rerun()

    # Generate assignments once possible
    ensure_assignments(state)

    if not state["assignments_generated"]:
        show_preferences_ui(current_user, state)
    else:
        show_assignment_ui(current_user, state)

    if APP_MODE == "test":
        show_test_panel(state)


if __name__ == "__main__":
    main()
