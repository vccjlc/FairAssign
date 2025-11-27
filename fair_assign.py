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
    {"name": "Magda", "password": "dvorakus"},
    {"name": "Maria", "password": "kejton"},
    {"name": "Asia", "password": "zubrol"},
    {"name": "Zuza", "password": "alessi"},
    {"name": "Jan", "password": "bbbb"},
]

USER_PASSWORDS = {p["name"]: p["password"] for p in PARTICIPANTS}
USER_NAMES = [p["name"] for p in PARTICIPANTS]

# Base directory and media paths
BASE_DIR = Path(__file__).parent
BACKGROUND_PATH = BASE_DIR / "assets" / "background.png"

CHRISTMAS_CLIP_PATH = os.getenv(
    "CHRISTMAS_CLIP_PATH",
    # Default to local MP4; override via env to use a different file or URL
    str(BASE_DIR / "christmas_clip.mp4"),
)

SANTA_AUDIO_PATH = os.getenv(
    "SANTA_AUDIO_PATH",
    str(BASE_DIR / "assets" / "intro_jingle.mp3"),
)


# Number of recipients assigned to each giver (1 by default; future configurable)
ASSIGNMENTS_PER_GIVER = int(os.getenv("ASSIGNMENTS_PER_GIVER", "1"))

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
                "preferences": [],  # list[{"text": str, "status": "open"|"reserved"|"bought", "marked_by": Optional[str]}]
                "confirmed": False,
                "result_revealed": False,
            }
            for name in USER_NAMES
        },
        # Generate assignments once at app start
        # Mapping: giver -> list of recipients
        "assignments": {giver: recips for giver, recips in generate_assignments(USER_NAMES, ASSIGNMENTS_PER_GIVER).items()},
        "assignments_generated": True,
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
        state["assignments_generated"] = True
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
            # Normalize preferences into structured list
            if "preferences" not in u or not isinstance(u["preferences"], list):
                u["preferences"] = []
                changed = True
            # Migrate legacy string lists to structured dict entries
            migrated_prefs = []
            for item in u.get("preferences", []):
                if isinstance(item, str):
                    text = item.strip()
                    if text:
                        migrated_prefs.append({"text": text, "status": "open", "marked_by": None})
                elif isinstance(item, dict):
                    text = (item.get("text") or "").strip()
                    status = item.get("status") or "open"
                    marked_by = item.get("marked_by")
                    if text:
                        if status not in ("open", "reserved", "bought"):
                            status = "open"
                        migrated_prefs.append({"text": text, "status": status, "marked_by": marked_by})
            if migrated_prefs != u.get("preferences"):
                u["preferences"] = migrated_prefs
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

    # Validate or (re)create assignments if needed
    assignments = state.get("assignments", {})
    # Convert legacy mapping of giver -> single recipient into list form
    legacy_detected = False
    if isinstance(assignments, dict) and assignments and all(isinstance(v, str) for v in assignments.values()):
        assignments = {giver: [recipient] for giver, recipient in assignments.items()}
        legacy_detected = True
    if legacy_detected or not is_valid_assignments(assignments, USER_NAMES):
        state["assignments"] = generate_assignments(USER_NAMES, ASSIGNMENTS_PER_GIVER)
        changed = True
    else:
        state["assignments"] = assignments

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

def is_valid_assignments(mapping: Dict[str, List[str]], names: List[str]) -> bool:
    """Validate mapping giver -> list of recipients (no self-assignments)."""
    if not isinstance(mapping, dict):
        return False
    if set(mapping.keys()) != set(names):
        return False
    for giver, recipients in mapping.items():
        if not isinstance(recipients, list):
            return False
        # Allow duplicates across givers, but not within the same giver and not self
        seen = set()
        for r in recipients:
            if r == giver or r not in names:
                return False
            if r in seen:
                return False
            seen.add(r)
    return True

def generate_assignments(names: List[str], recipients_per_giver: int) -> Dict[str, List[str]]:
    """
    Generate mapping giver -> list of recipients.
    - For 1 recipient per giver, produce a derangement.
    - For k>1, generate k independent derangements and combine per giver (deduped).
    """
    if recipients_per_giver <= 1:
        single = generate_derangement(names)
        return {giver: [recipient] for giver, recipient in single.items()}

    # For k>1, stack k derangements and combine
    combined: Dict[str, List[str]] = {n: [] for n in names}
    attempts = 0
    while any(len(v) < recipients_per_giver for v in combined.values()):
        attempts += 1
        if attempts > 5000:
            # Fallback: reset and try again (very unlikely for small N)
            combined = {n: [] for n in names}
            attempts = 0
        d = generate_derangement(names)
        for giver, recipient in d.items():
            lst = combined[giver]
            if recipient not in lst and len(lst) < recipients_per_giver:
                lst.append(recipient)
    return combined

def get_recipients_for_giver(state: Dict, giver: str) -> List[str]:
    """Return the list of recipients assigned to the given user."""
    assignments = state.get("assignments", {}) or {}
    recipients = assignments.get(giver) or []
    # Normalize string -> list if any legacy remains
    if isinstance(recipients, str):
        return [recipients]
    if not isinstance(recipients, list):
        return []
    return recipients


def ensure_assignments(state: Dict) -> None:
    """Ensure assignments exist and are valid. In this simplified model, they are generated at startup."""
    assignments = state.get("assignments", {})
    # Normalize legacy form if necessary
    if isinstance(assignments, dict) and assignments and all(isinstance(v, str) for v in assignments.values()):
        assignments = {g: [r] for g, r in assignments.items()}
    if not is_valid_assignments(assignments, USER_NAMES):
        state["assignments"] = generate_assignments(USER_NAMES, ASSIGNMENTS_PER_GIVER)
        state["assignments_generated"] = True
        save_state(state)
    else:
        # Valid assignments exist; ensure the flag is set
        if not state.get("assignments_generated"):
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

    css_core = """
[data-testid="stHeader"] {
    background-color: rgba(0, 0, 0, 0);
}
[data-testid="stDecoration"] {
    display: none !important;
}
.caption-bg, .christmas-card .caption-bg {
    background: #e9edf5;
    padding: 0.5rem 0.75rem;
    border-radius: 0.5rem;
    margin-bottom: 0.6rem;
    border: 1px solid #d6dbea;
    box-shadow: 0 1px 2px rgba(0,0,0,0.03);
    color: #111111;
}
.caption-bg.small, .christmas-card .caption-bg.small {
    font-size: 0.95rem;
}

/* Card look for Streamlit containers created with border=True */
[data-testid="stVerticalBlockBorderWrapper"] {
    background-color: #ffffffee;
    padding: 1.5rem 2rem;
    border-radius: 1rem;
    border: 1px solid #eed6d6;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.06);
    max-width: 640px;
    margin: 1.5rem auto;
    color: #111111;
}
[data-testid="stVerticalBlockBorderWrapper"] input[type="text"] {
    background-color: #f7f7fb;
    color: #222222;
    border: 1px solid #e7e7ef;
}
[data-testid="stVerticalBlockBorderWrapper"] input[type="text"]::placeholder {
    color: #9aa1b0;
}
[data-testid="stVerticalBlockBorderWrapper"] input[type="text"]:focus {
    border-color: #d0d6f0;
    box-shadow: 0 0 0 3px rgba(105,145,255,0.15);
    outline: none;
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
.christmas-card input[type="text"] {
    background-color: #f7f7fb;
    color: #222222;
    border: 1px solid #e7e7ef;
}
.christmas-card input[type="text"]::placeholder {
    color: #9aa1b0;
}
.christmas-card input[type="text"]:focus {
    border-color: #d0d6f0;
    box-shadow: 0 0 0 3px rgba(105,145,255,0.15);
    outline: none;
}
/* Make disabled buttons (Reserved / Unmark) clearly visible */
.stButton > button:disabled,
.stButton > button[disabled],
.stButton > button[aria-disabled="true"] {
    opacity: 1 !important;              /* no fading */
    filter: none !important;
    background-color: #111827 !important;  /* dark navy (same as normal button) */
    color: #ffffff !important;             /* white text */
    border-color: #111827 !important;
    cursor: not-allowed !important;        /* still looks disabled */
}
.buying-for {
    font-size: 1.25rem;
    font-weight: 700;
    margin: 0.2rem 0 0.6rem;
}
.reserved-badge {
    display: inline-block;
    background: #2ea043;
    color: #ffffff;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 0.75rem;
    margin-left: 6px;
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
[data-testid="stCaptionContainer"] p {
    color: #ffffff !important;
}
"""
    st.markdown("<style>" + bg_css + css_core + "</style>", unsafe_allow_html=True)


def render_background_music() -> None:
    """Render looping background music if enabled."""
    # Honor temporary pause (e.g., during ho-ho-ho)
    pause_until = st.session_state.get("bg_music_paused_until")
    if pause_until is not None:
        now = time.time()
        if now < pause_until:
            return
        # Pause expired; resume if requested
        st.session_state.pop("bg_music_paused_until", None)
        if st.session_state.pop("resume_bg_after_ho", False):
            st.session_state["bg_music_on"] = True

    if not st.session_state.get("bg_music_on"):
        return
    if not SANTA_AUDIO_PATH:
        return
    try:
        if str(SANTA_AUDIO_PATH).startswith(("http://", "https://", "data:")):
            st.markdown(
                f'<audio src="{SANTA_AUDIO_PATH}" autoplay loop style="display:none"></audio>',
                unsafe_allow_html=True,
            )
        else:
            audio_path = Path(SANTA_AUDIO_PATH)
            if audio_path.exists():
                # Use cached b64 if available to avoid disk IO on reruns
                b64 = _read_file_b64(str(audio_path)) or ""
                if not b64:
                    return
                st.markdown(
                    f'<audio autoplay loop style="display:none" src="data:audio/mpeg;base64,{b64}"></audio>',
                    unsafe_allow_html=True,
                )
            else:
                # Do not show a visible player; silently skip if file missing
                return
    except Exception:
        # Avoid rendering a visible player; skip on error
        return


def play_hidden_audio(audio_src: str) -> None:
    """Best-effort hidden audio playback from local file or URL."""
    if not audio_src:
        return
    try:
        src_str = str(audio_src)
        if src_str.startswith(("http://", "https://", "data:")):
            st.markdown(
                f'<audio src="{src_str}" autoplay style="display:none"></audio>',
                unsafe_allow_html=True,
            )
            return
        audio_path = Path(src_str)
        # Case-insensitive fallback within assets
        if not audio_path.exists():
            assets = BASE_DIR / "assets"
            if assets.exists():
                for f in assets.glob("*"):
                    if f.name.lower() == audio_path.name.lower():
                        audio_path = f
                        break
        if audio_path.exists():
            data = audio_path.read_bytes()
            b64 = base64.b64encode(data).decode()
            st.markdown(
                f'<audio autoplay style="display:none" src="data:audio/mpeg;base64,{b64}"></audio>',
                unsafe_allow_html=True,
            )
        else:
            st.audio(src_str, format="audio/mp3", start_time=0)
    except Exception:
        try:
            st.audio(audio_src, format="audio/mp3", start_time=0)
        except Exception:
            # Swallow error to avoid noisy UI
            pass


@st.cache_data(show_spinner=False)
def _read_file_b64(path_str: str) -> str | None:
    """Read a local file and return base64 string; cached across reruns."""
    try:
        p = Path(path_str)
        if p.exists():
            data = p.read_bytes()
            return base64.b64encode(data).decode()
    except Exception:
        return None
    return None


def preload_christmas_clip() -> None:
    """Hint the browser to preload the video once per session to speed up first play."""
    if st.session_state.get("video_preloaded"):
        return
    src = str(CHRISTMAS_CLIP_PATH or "").strip()
    if not src:
        return
    try:
        # If local file, embed a hidden preloading video via data URL
        vpath = Path(src)
        if vpath.exists():
            b64 = _read_file_b64(src)
            if b64:
                st.markdown(
                    f'<video preload="auto" muted playsinline style="display:none" src="data:video/mp4;base64,{b64}"></video>',
                    unsafe_allow_html=True,
                )
        else:
            # For URL sources, use preload link and hidden video
            st.markdown(f'<link rel="preload" as="video" href="{src}">', unsafe_allow_html=True)
            st.markdown(
                f'<video preload="auto" muted playsinline style="display:none" src="{src}"></video>',
                unsafe_allow_html=True,
            )
        st.session_state["video_preloaded"] = True
    except Exception:
        # Ignore preloading errors
        pass

def show_fullscreen_clip_once(video_src: str) -> bool:
    """
    If the 'show_assignment_clip' flag is set, render a near-fullscreen video
    and a continue button. Returns True if the function handled the entire
    screen (caller should return early), False otherwise.
    """
    if not st.session_state.pop("show_assignment_clip", False):
        return False

    if not video_src:
        return False

    # Hide header and sidebar; expand the video to viewport
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] { display: none !important; }
        [data-testid="stHeader"] { display: none !important; }
        .block-container { padding: 0 !important; margin: 0 !important; }
        .fs-video { width: 100vw; height: 100vh; object-fit: cover; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    try:
        if str(video_src).startswith(("http://", "https://", "data:")):
            st.markdown(
                f'<video src="{video_src}" class="fs-video" autoplay controls playsinline></video>',
                unsafe_allow_html=True,
            )
        else:
            vpath = Path(video_src)
            if not vpath.exists():
                assets = BASE_DIR / "assets"
                if assets.exists():
                    for f in assets.glob("*"):
                        if f.name.lower() == vpath.name.lower():
                            vpath = f
                            break
            if vpath.exists():
                data = vpath.read_bytes()
                b64 = base64.b64encode(data).decode()
                st.markdown(
                    f'<video class="fs-video" autoplay controls playsinline src="data:video/mp4;base64,{b64}"></video>',
                    unsafe_allow_html=True,
                )
            else:
                st.video(video_src, format="video/mp4")
    except Exception:
        st.video(video_src, format="video/mp4")

    # Show a continue button to proceed to assignment details
    col = st.container()
    with col:
        if st.button("Continue to your draw", use_container_width=True):
            _safe_rerun()
    return True


def on_wishlist_change(user_name: str, state: Dict) -> None:
    """Autosave wishlist when any input changes."""
    user_state = state["users"][user_name]
    existing_items = user_state.get("preferences") or []

    # Build new list from inputs, preserving status for unchanged texts
    existing_by_text = {item["text"]: item for item in existing_items if isinstance(item, dict) and "text" in item}

    new_items: List[Dict] = []
    for i in range(7):
        key = f"wishlist_input_{user_name}_{i}"
        raw = st.session_state.get(key, "")
        value = (raw or "").strip()
        if value:
            prev = existing_by_text.get(value)
            if prev:
                new_items.append({"text": value, "status": prev.get("status", "open"), "marked_by": prev.get("marked_by")})
            else:
                new_items.append({"text": value, "status": "open", "marked_by": None})

    user_state["preferences"] = new_items
    user_state["confirmed"] = bool(new_items)
    save_state(state)
    st.session_state["wishlist_saved_ts"] = time.time()
    # Ephemeral toast if available
    toast_fn = getattr(st, "toast", None)
    if callable(toast_fn):
        try:
            toast_fn("Wishlist saved", icon="✅")
        except Exception:
            pass


def show_login() -> str | None:
    """Show login form and return the authenticated user name, or None."""
    with st.container(border=True):
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
    return st.session_state.get("current_user")


def show_preferences_ui(user_name: str, state: Dict) -> None:
    """Page where a user defines their wishlist."""
    user_state = state["users"][user_name]
    existing_prefs = user_state.get("preferences") or []

    with st.container(border=True):
        st.markdown('<div class="caption-bg"><h3 style="margin:0;">Edit your wishlist</h3></div>', unsafe_allow_html=True)

        # --- Back button ---
        back_target = st.session_state.get("return_view", "home")
        if st.button("⬅ Back", use_container_width=True):
            st.session_state["main_view"] = back_target
            if back_target in ("home", "wishlist"):
                st.session_state["bg_music_on"] = True
            else:
                st.session_state["bg_music_on"] = False
            st.session_state.pop("return_view", None)
            _safe_rerun()

        # Caution caption if others have reserved/bought items
        any_marked = any(
            isinstance(item, dict)
            and item.get("status") in ("reserved",)
            and item.get("marked_by") != user_name
            for item in existing_prefs
        )
        if any_marked:
            st.markdown(
                '<div class="caption-bg small">Some of your gifts might have been reserved! '
                "Be careful with editing.</div>",
                unsafe_allow_html=True,
            )

        # Show ephemeral saved caption at the top (visible for ~5s after save)
        ts = st.session_state.get("wishlist_saved_ts")
        if ts is not None:
            try:
                if (time.time() - ts) <= 5:
                    st.caption("✓ Wishlist saved")
            except Exception:
                pass


        # Initialize inputs and autosave on change
        for i in range(7):
            default_value = ""
            if i < len(existing_prefs) and isinstance(existing_prefs[i], dict):
                default_value = existing_prefs[i].get("text", "")
            st.text_input(
                f"Gift idea {i + 1}",
                value=default_value,
                key=f"wishlist_input_{user_name}_{i}",
                on_change=on_wishlist_change,
                args=(user_name, state),
            )

        # Repeat the ephemeral saved caption at the bottom
        ts = st.session_state.get("wishlist_saved_ts")
        if ts is not None:
            try:
                if (time.time() - ts) <= 5:
                    st.caption("✓ Wishlist saved")
            except Exception:
                pass

        completed = sum(1 for u in state["users"].values() if u.get("confirmed"))
        total = len(state["users"])
        st.info(f"{completed} of {total} participants have saved their wishlist.")

        # Status overview for fun; full details in test mode developer panel
        with st.expander("Progress of everyone", expanded=False):
            for name, u in state["users"].items():
                mark = "✓" if u.get("confirmed") else "•"
                st.write(f"{mark} {name}")


def show_assignment_ui(user_name: str, state: Dict) -> None:
    """Page where a user sees their final assignment and can mark items reserved/bought."""
    with st.container(border=True):
        st.subheader("Your Secret Santa draw")

        # Pause background music while on assignment view
        st.session_state["bg_music_on"] = False

        # --- Back button ---
        back_target = st.session_state.get("return_view", "home")
        if st.button("⬅ Back", use_container_width=True):
            st.session_state["main_view"] = back_target
            if back_target in ("home", "wishlist"):
                st.session_state["bg_music_on"] = True
            else:
                st.session_state["bg_music_on"] = False
            st.session_state.pop("return_view", None)
            _safe_rerun()

        recipients = get_recipients_for_giver(state, user_name)
        if not recipients:
            st.info("Assignments are not ready yet. Please check back later.")
            return

        st.markdown('<div class="buying-for">You are buying for: ' + ", ".join(recipients) + "</div>", unsafe_allow_html=True)
        st.write("")

        for recipient in recipients:
            header_cols = st.columns([4, 2])
            with header_cols[0]:
                st.markdown(f"**{recipient}'s wishlist**")
            prefs = state["users"].get(recipient, {}).get("preferences") or []
            if not prefs:
                st.markdown(
                    f'<div class="caption-bg small">{recipient} didn\'t add any wishlist item yet. '
                    f'Use your imagination or contact {recipient}!</div>',
                    unsafe_allow_html=True,
                )
                st.write("")
                continue
            with header_cols[1]:
                try:
                    lines = []
                    for i, it in enumerate(prefs):
                        if not isinstance(it, dict):
                            continue
                        txt = (it.get("text") or "").strip()
                        if not txt:
                            continue
                        status = it.get("status", "open")
                        marked_by = it.get("marked_by")
                        suffix = ""
                        if status == "reserved":
                            suffix = " - Reserved by you" if marked_by == user_name else " - Reserved by someone"
                        lines.append(f"{i + 1}. {txt}{suffix}")
                    wishlist_text = "\n".join(lines) or "No wishlist items"
                    st.download_button(
                        label="Download wishlist (.txt)",
                        data=wishlist_text,
                        file_name=f"wishlist_{recipient}.txt",
                        mime="text/plain",
                        key=f"download_wishlist_{user_name}_{recipient}",
                    )
                except Exception:
                    pass

            for idx, item in enumerate(prefs):
                text = item.get("text", "")
                status = item.get("status", "open")
                marked_by = item.get("marked_by")

                cols = st.columns([6, 2, 2])
                with cols[0]:
                    label_html = f"{idx + 1}. {text}"
                    if status == "reserved":
                        badge = "Reserved by you" if marked_by == user_name else "Reserved by someone"
                        label_html += f' <span class="reserved-badge">{badge}</span>'
                    st.markdown(label_html, unsafe_allow_html=True)

                # Actions
                # Disable reserve if already reserved by someone else
                disable_reserve = status == "reserved" and marked_by != user_name
                disable_unmark = not (status == "reserved" and marked_by == user_name)

                reserve_key = f"reserve_{user_name}_{recipient}_{idx}"
                clear_key = f"clear_{user_name}_{recipient}_{idx}"

                with cols[1]:
                    # If already reserved by current user, show disabled "Reserved" button
                    if status == "reserved" and marked_by == user_name:
                        st.button("Reserved", key=reserve_key, disabled=True)
                    elif st.button("Reserve", key=reserve_key, disabled=disable_reserve):
                        item["status"] = "reserved"
                        item["marked_by"] = user_name
                        save_state(state)
                        _safe_rerun()
                with cols[2]:
                    if st.button("Unmark", key=clear_key, disabled=disable_unmark):
                        item["status"] = "open"
                        item["marked_by"] = None
                        save_state(state)
                        _safe_rerun()

            st.write("")

        # Reservation info moved below and hidden behind an expander
        with st.expander("Info"):
            # Build a readable label for one vs many giftees
            if len(recipients) == 1:
                giftee_label = recipients[0]
                does_verb = "does"
                sees_verb = "sees"
            else:
                giftee_label = "your recipients"
                does_verb = "do"
                sees_verb = "see"

            info_html = f"""
            <div class="caption-bg">
              <ul style="margin: 0; padding-left: 1.1rem;">
                <li>You can mark gifts as reserved and unmark as many times as you want.</li>
                <li>Changes are visible live.</li>
                <li>{giftee_label} {does_verb} not see which specific item was reserved.</li>
                <li>Other buyers (if any) can see reserved marks to avoid duplicate purchases.</li>
                <li>{giftee_label} only {sees_verb} that some items are reserved, without details.</li>
              </ul>
            </div>
            """
            st.markdown(info_html, unsafe_allow_html=True)

        # Video moved to a dedicated subpage to avoid impacting core page load
        if CHRISTMAS_CLIP_PATH:
            st.markdown('<div class="caption-bg"><strong>See how the drawing was made</strong></div>', unsafe_allow_html=True)
            if st.button("▶ Play", key=f"see_video_{user_name}", use_container_width=True):
                # remember where we came from
                st.session_state["return_view"] = st.session_state.get("main_view", "home")
                st.session_state["video_loading"] = True
                st.session_state["main_view"] = "video"
                st.session_state["bg_music_on"] = False
                _safe_rerun()


def show_video_page() -> None:
    """Dedicated subpage that only displays the drawing video in a large frame."""
    st.session_state["bg_music_on"] = False

    video_src = str(CHRISTMAS_CLIP_PATH or "").strip()
    if not video_src:
        st.info("No video configured.")
        return

    # Simple heading without a card wrapper
    st.markdown('<div class="caption-bg"><strong>See how the drawing was made</strong></div>', unsafe_allow_html=True)

    is_loading = bool(st.session_state.pop("video_loading", False))

    def render_video() -> None:
        try:
            if video_src.startswith(("http://", "https://", "data:")):
                st.markdown(
                    f'<video preload="metadata" controls playsinline '
                    f'style="width:100%; border-radius:12px; opacity:1; box-shadow: 0 8px 24px rgba(0,0,0,0.08);" '
                    f'src="{video_src}"></video>',
                    unsafe_allow_html=True,
                )
            else:
                vpath = Path(video_src)
                if not vpath.exists():
                    assets = BASE_DIR / "assets"
                    if assets.exists():
                        for f in assets.glob("*"):
                            if f.name.lower() == vpath.name.lower():
                                vpath = f
                                break
                if vpath.exists():
                    b64 = _read_file_b64(str(vpath)) or ""
                    if b64:
                        st.markdown(
                            f'<video preload="metadata" controls playsinline '
                            f'style="width:100%; border-radius:12px; opacity:1; box-shadow: 0 8px 24px rgba(0,0,0,0.08);" '
                            f'src="data:video/mp4;base64,{b64}"></video>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.video(str(vpath), format="video/mp4")
                else:
                    st.video(video_src, format="video/mp4")
        except Exception:
            st.video(video_src, format="video/mp4")

    if is_loading:
        with st.spinner("Loading video..."):
            render_video()
    else:
        render_video()

    # --- Back button ---
    back_target = st.session_state.get("return_view", "home")
    if st.button("⬅ Back", use_container_width=True):
        st.session_state["main_view"] = back_target
        # resume music only on home / wishlist
        if back_target in ("home", "wishlist"):
            st.session_state["bg_music_on"] = True
        else:
            st.session_state["bg_music_on"] = False
        # clean up so the next jump to video can store a fresh origin
        st.session_state.pop("return_view", None)
        _safe_rerun()

def render_bottom_video_cta(current_user: str | None) -> None:
    """Render a bottom-of-page CTA to open the video subpage (only when logged in)."""
    if not current_user:
        return
    if not CHRISTMAS_CLIP_PATH:
        return
    with st.container(border=True):
        st.markdown('<div class="caption-bg small">See how the drawing was made</div>', unsafe_allow_html=True)
        if st.button("▶ Play", key=f"see_video_cta_{current_user}", use_container_width=True):
            # remember where we came from
            st.session_state["return_view"] = st.session_state.get("main_view", "home")
            st.session_state["video_loading"] = True
            st.session_state["main_view"] = "video"
            st.session_state["bg_music_on"] = False
            _safe_rerun()

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
        for giver, recipients in state["assignments"].items():
            if isinstance(recipients, list):
                st.sidebar.write(f"{giver} → {', '.join(recipients)}")
            else:
                st.sidebar.write(f"{giver} → {recipients}")

    # Allow regenerating assignments anytime in test mode
    if APP_MODE == "test":
        if st.sidebar.button("Regenerate assignments"):
            state["assignments"] = generate_assignments(USER_NAMES, ASSIGNMENTS_PER_GIVER)
            state["assignments_generated"] = True
            save_state(state)
            st.sidebar.success("Assignments regenerated.")

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
                    if isinstance(item, dict):
                        suffix = ""
                        if item.get("status") == "reserved":
                            suffix = f" (reserved by {item.get('marked_by')})"
                        elif item.get("status") == "bought":
                            suffix = f" (bought by {item.get('marked_by')})"
                        st.write(f"{idx}. {item.get('text', '')}{suffix}")
                    else:
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
    Always show a big Play button. When clicked, start background audio for this session and continue.
    Returns True if the main UI should continue, False otherwise.
    """

    # If already entered during this session, skip the gate
    if st.session_state.get("entered_lodge"):
        return True

    entered = False
    with st.container(border=True):
        st.markdown("<h2 style='margin-bottom: 0.5rem; text-align:center;'>Welcome to the Christmas lodge</h2>", unsafe_allow_html=True)
        entered = st.button(
            "▶ Enter",
            key="enter_lodge_button",
            use_container_width=True,
        )

    if entered:
        # Persist that user has entered and enable background music
        st.session_state["entered_lodge"] = True
        st.session_state["bg_music_on"] = True
        _safe_rerun()
        return True

    return False


def show_home_menu(state: Dict, current_user: str) -> None:
    """
    Show two main actions:
    - Enter / edit gifts
    - See assigned person (once assignments are generated)

    Sets st.session_state["main_view"] accordingly.
    """

    # Default to home (no view selected) until user clicks a button
    if "main_view" not in st.session_state:
        st.session_state["main_view"] = "home"

    with st.container(border=True):
        st.subheader("What would you like to do?")

        col1, col2 = st.columns(2)

        with col1:
            if st.button("⭐ See who you got", use_container_width=True):
                st.session_state["main_view"] = "assignment"
                st.session_state["bg_music_on"] = False

        with col2:
            if st.button("🎁 Edit your wishlist", use_container_width=True):
                st.session_state["main_view"] = "wishlist"
                st.session_state["bg_music_on"] = True

        # Status hint (show once for up to 30 seconds, then never again this session)
        if state.get("assignments_generated"):
            dismissed = st.session_state.get("draw_ready_dismissed", False)
            ts = st.session_state.get("draw_ready_ts")
            now = time.time()
            if not dismissed:
                if ts is None:
                    st.session_state["draw_ready_ts"] = now
                    st.success("The draw is ready. You can see who you got.")
                else:
                    if (now - ts) <= 30:
                        st.success("The draw is ready. You can see who you got.")
                    else:
                        st.session_state["draw_ready_dismissed"] = True

def main() -> None:
    st.set_page_config(
        page_title="Fair Assign – Secret Santa",
        layout="centered",
    )
    add_christmas_style()

    # Show the heading and mode only before entering the lodge
    if not st.session_state.get("entered_lodge"):
        st.markdown('<div class="christmas-title">Fair Assign – Secret Santa</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="christmas-subtitle">'
            'Shared Christmas wishlist with random assignments'
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

    # Render background music (looping) if enabled
    render_background_music()

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

    # Skip preloading the video to keep the page snappy; it will load on demand

    # Sidebar user panel
    st.sidebar.write(f"Logged in as: **{current_user}**")
    if st.sidebar.button("Log out"):
        st.session_state.clear()
        st.rerun()

    # Generate assignments once possible
    ensure_assignments(state)

    # Default view
    if "main_view" not in st.session_state:
        st.session_state["main_view"] = "home"

    # Decide what to show based on selected view
    view = st.session_state.get("main_view", "home")

    if view == "home":
        show_home_menu(state, current_user)
    elif view == "wishlist":
        # Resume background music on non-video views
        st.session_state["bg_music_on"] = True
        show_preferences_ui(current_user, state)
    elif view == "assignment":
        # Always allow viewing assignment immediately
        st.session_state["bg_music_on"] = False
        show_assignment_ui(current_user, state)
    elif view == "video":
        # Dedicated subpage with just the video
        st.session_state["bg_music_on"] = False
        show_video_page()

    # Bottom CTA only on home and wishlist
    if view in ("home", "wishlist"):
        render_bottom_video_cta(current_user)

    if APP_MODE == "test":
        show_test_panel(state)


if __name__ == "__main__":
    main()
