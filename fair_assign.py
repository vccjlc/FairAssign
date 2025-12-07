"""
fair_assign.py

Streamlit Secret Santa style app for a fixed group of 5 people.

New simplified behavior:
- Assignments are generated once locally with drawing.py into assignments_prod.json
- The deployed app only reads that fixed assignments file (no in-app drawing, no test mode)
- Each user has an external wishlist link (e.g. shared note or document)
- "Edit your wishlist" opens the external link
- "See who you got" shows your recipient(s) and links to their wishlists
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

PARTICIPANTS = [
    {"name": "Magda", "password": "dvorakus"},
    {"name": "Maria", "password": "kejton"},
    {"name": "Asia", "password": "zubrol"},
    {"name": "Zuza", "password": "alessi"},
    {"name": "Jan", "password": "bbbb"},
]

USER_PASSWORDS = {p["name"]: p["password"] for p in PARTICIPANTS}
USER_NAMES = [p["name"] for p in PARTICIPANTS]

# External wishlist links for each person – TODO: replace with your real URLs
WISHLIST_LINKS: Dict[str, str] = {
    "Magda": "https://www.notion.so/Magda-s-Wishlist-2c28d5786a5480c89072c154293a77f9?source=copy_link",
    "Maria": "https://www.notion.so/Maria-s-Wishlist-2c28d5786a5481788433d17776b3990e?source=copy_link",
    "Asia": "https://www.notion.so/Asia-s-Wishlist-2c28d5786a548143b8d0dfde76d1edbc?source=copy_link",
    "Zuza": "https://www.notion.so/Zuza-s-Wishlist-2c28d5786a548109a108c91227808e01?source=copy_link",
    "Jan": "https://www.notion.so/Jan-s-Wishlist-2c28d5786a548180be37e24c86d46b4c?source=copy_link",
}

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
    str(BASE_DIR / "assets" / "intro_jingle_new.mp3"),
)

# Number of recipients assigned to each giver (still used by drawing.py)
ASSIGNMENTS_PER_GIVER = int(os.getenv("ASSIGNMENTS_PER_GIVER", "1"))

# Fixed assignments file, generated once by drawing.py and committed to the repo
ASSIGNMENTS_FILE = BASE_DIR / "assignments_prod.json"


# ---------------------------------------------------------------------------
# Assignment helpers (used by drawing.py and this app)
# ---------------------------------------------------------------------------

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


def load_fixed_assignments() -> Dict[str, List[str]]:
    """
    Load assignments from assignments_prod.json in the repo.
    If the file is missing or invalid, return an empty dict.
    """
    if not ASSIGNMENTS_FILE.exists():
        return {}
    try:
        raw = ASSIGNMENTS_FILE.read_text(encoding="utf-8")
        mapping = json.loads(raw)
    except Exception:
        return {}
    # Accept legacy form giver -> single recipient string
    if isinstance(mapping, dict) and mapping and all(isinstance(v, str) for v in mapping.values()):
        mapping = {g: [r] for g, r in mapping.items()}
    if not is_valid_assignments(mapping, USER_NAMES):
        return {}
    return mapping


def get_recipients_for_giver(assignments: Dict[str, List[str]], giver: str) -> List[str]:
    """Return the list of recipients assigned to the given user from fixed assignments."""
    recipients = assignments.get(giver) or []
    if isinstance(recipients, str):
        return [recipients]
    if not isinstance(recipients, list):
        return []
    return recipients


def authenticate(name: str, password: str) -> bool:
    expected = USER_PASSWORDS.get(name)
    return expected is not None and password == expected


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _safe_rerun() -> None:
    """Call st.rerun when available, falling back to experimental API."""
    rerun_fn = getattr(st, "rerun", None)
    if callable(rerun_fn):
        rerun_fn()
    else:
        st.experimental_rerun()


def go(view: str, return_view: str | None = None, *, music_on: bool | None = None, clear_return: bool = False) -> None:
    """
    Minimal navigation helper:
    - sets main_view (and optionally return_view)
    - toggles music if requested
    - optionally clears any existing return_view
    - triggers an immediate clean rerun
    """
    if clear_return:
        st.session_state.pop("return_view", None)
    if return_view is not None:
        st.session_state["return_view"] = return_view
    if music_on is not None:
        st.session_state["bg_music_on"] = music_on
    st.session_state["main_view"] = view
    _safe_rerun()  # immediate clean rerun so previous containers disappear


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
/* Make disabled buttons clearly visible */
.stButton > button:disabled,
.stButton > button[disabled],
.stButton > button[aria-disabled="true"] {
    opacity: 1 !important;
    filter: none !important;
    background-color: #111827 !important;
    color: #ffffff !important;
    border-color: #111827 !important;
    cursor: not-allowed !important;
}
.buying-for {
    font-size: 1.25rem;
    font-weight: 700;
    margin: 0.2rem 0 0.6rem;
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
            pass
    return True


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
                _safe_rerun()  # clear the login card before rendering the app
                st.stop()
            else:
                st.error("Invalid password")
    return st.session_state.get("current_user")


def show_preferences_ui(user_name: str) -> None:
    """
    Wishlist screen.
    In the simplified app, this just points the user to their external wishlist link.
    """
    with st.container(border=True):
        st.markdown('<div class="caption-bg"><h3 style="margin:0;">Your wishlist</h3></div>', unsafe_allow_html=True)

        # --- Back button ---
        if st.button("⬅ Back", use_container_width=True):
            back_target = st.session_state.get("return_view", "home")
            st.session_state["bg_music_on"] = back_target in ("home", "wishlist")
            go(back_target, clear_return=True)

        url = WISHLIST_LINKS.get(user_name)

        if not url:
            st.warning("No wishlist link configured for you yet. Please contact the organizer.")
            return

        st.write("You can edit your wishlist here:")
        st.link_button("Open your wishlist", url, use_container_width=True)


def show_assignment_ui(user_name: str, assignments: Dict[str, List[str]]) -> None:
    """Page where a user sees their fixed assignment and links to recipients' wishlists."""
    with st.container(border=True):
        st.subheader("Your Secret Santa draw")

        # Pause background music while on assignment view
        st.session_state["bg_music_on"] = False

        # --- Back button ---
        if st.button("⬅ Back", use_container_width=True):
            back_target = st.session_state.get("return_view", "home")
            st.session_state["bg_music_on"] = back_target in ("home", "wishlist")
            go(back_target, clear_return=True)

        recipients = get_recipients_for_giver(assignments, user_name)
        if not recipients:
            st.info("Assignments are not ready yet. Please check back later.")
            return

        st.markdown('<div class="buying-for">You are buying for: ' + ", ".join(recipients) + "</div>", unsafe_allow_html=True)
        st.write("")

        for recipient in recipients:
            st.markdown(f"#### {recipient}'s wishlist")
            url = WISHLIST_LINKS.get(recipient)
            if url:
                st.link_button(f"Open {recipient}'s wishlist", url, use_container_width=True)
            else:
                st.caption("Wishlist link not configured yet.")


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
                st.video(video_src, format="video/mp4")
            else:
                vpath = Path(video_src)
                if not vpath.exists():
                    assets = BASE_DIR / "assets"
                    if assets.exists():
                        for f in assets.glob("*"):
                            if f.name.lower() == vpath.name.lower():
                                vpath = f
                                break
                st.video(str(vpath) if vpath.exists() else video_src, format="video/mp4")
        except Exception:
            st.video(video_src, format="video/mp4")

    if is_loading:
        with st.spinner("Loading video..."):
            render_video()
    else:
        render_video()

    # --- Back button ---
    if st.button("⬅ Back", use_container_width=True):
        back_target = st.session_state.get("return_view", "home")
        st.session_state["bg_music_on"] = back_target in ("home", "wishlist")
        go(back_target, clear_return=True)


def render_bottom_video_cta(current_user: str | None) -> None:
    """Render a bottom-of-page CTA to open the video subpage (only when logged in)."""
    if not current_user:
        return
    if not CHRISTMAS_CLIP_PATH:
        return
    with st.container(border=True):
        st.markdown('<div class="caption-bg small">See how the drawing was made</div>', unsafe_allow_html=True)
        if st.button("▶ Play", key=f"see_video_cta_{current_user}", use_container_width=True):
            st.session_state["video_loading"] = True
            # remember where we came from and navigate in a clean rerun
            current = st.session_state.get("main_view", "home")
            go("video", return_view=current, music_on=False)


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
        _safe_rerun()  # avoid showing the gate container alongside login
        st.stop()

    return False


def show_home_menu(current_user: str, assignments: Dict[str, List[str]]) -> None:
    """
    Show two main actions:
    - See assigned person
    - Open / edit wishlist (external link)
    """

    with st.container(border=True):
        st.subheader("What would you like to do?")

        col1, col2 = st.columns(2)

        with col1:
            if st.button("⭐ See who you got", use_container_width=True):
                go("assignment", return_view="home", music_on=False)

        with col2:
            if st.button("🎁 Edit your wishlist", use_container_width=True):
                go("wishlist", return_view="home", music_on=True)

        if assignments:
            st.success("The draw is ready. You can see who you got.")
        else:
            st.info("The draw has not been generated yet. Please check back later.")


def main() -> None:
    st.set_page_config(
        page_title="Fair Assign – Secret Santa",
        layout="centered",
    )
    add_christmas_style()

    # Show the heading only before entering the lodge
    if not st.session_state.get("entered_lodge"):
        st.markdown('<div class="christmas-title">Fair Assign – Secret Santa</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="christmas-subtitle">'
            'Shared Christmas wishlist with fixed random assignments'
            '</div>',
            unsafe_allow_html=True,
        )

    # Entry gate with big Play button and intro audio
    if not show_entry_gate():
        # User has not pressed Play yet; do not show login or anything else
        return

    # Render background music (looping) if enabled
    render_background_music()

    # Load fixed assignments
    assignments = load_fixed_assignments()

    # Login
    current_user = st.session_state.get("current_user")
    if not current_user:
        current_user = show_login()
        if not current_user:
            # No valid login yet, stop rendering further UI
            return

    # Sidebar user panel
    st.sidebar.write(f"Logged in as: **{current_user}**")
    if st.sidebar.button("Log out"):
        st.session_state.clear()
        st.rerun()

    # Default view
    if "main_view" not in st.session_state:
        st.session_state["main_view"] = "home"

    # Decide what to show based on selected view
    view = st.session_state.get("main_view", "home")

    if view == "home":
        show_home_menu(current_user, assignments)
    elif view == "wishlist":
        # Resume background music on non-video views
        st.session_state["bg_music_on"] = True
        show_preferences_ui(current_user)
    elif view == "assignment":
        # Show fixed assignment
        st.session_state["bg_music_on"] = False
        show_assignment_ui(current_user, assignments)
    elif view == "video":
        # Dedicated subpage with just the video
        st.session_state["bg_music_on"] = False
        show_video_page()

    # Bottom CTA only on home
    if view == "home":
        render_bottom_video_cta(current_user)


if __name__ == "__main__":
    main()
