## Fair Assign – Secret Santa

Minimal Streamlit app for a 5-person Secret Santa with:
- Login per user (name + password)
- Up to 7 wishlist items per user
- Random derangement (no one gets themselves)
- Test and Prod modes
- Simple Christmas-themed UI and optional intro clip

### Files
- `fair_assign.py`: the Streamlit app
- `requirements.txt`: Python dependencies
- (optional) `christmas_clip.mp4` in the repo, or set a URL via env var

### Run locally
1. Install dependencies:
   - `pip install -r requirements.txt`
2. (Optional) Set app mode:
   - `export FAIR_ASSIGN_MODE=test` or `prod` (Windows PowerShell: `$env:FAIR_ASSIGN_MODE='test'`)
3. (Optional) Provide a clip:
   - Put `christmas_clip.mp4` next to `fair_assign.py`, or
   - Set `CHRISTMAS_CLIP_PATH` to a local filename or URL.
4. Start the app:
   - `streamlit run fair_assign.py`

### Deploy on Streamlit Community Cloud
1. Push this folder to a new GitHub repo (e.g., `fair-assign`).
2. Sign in to Streamlit Community Cloud with GitHub.
3. Deploy:
   - Repository: your `fair-assign` repo
   - Branch: `main` (or your default)
   - Main file: `fair_assign.py`
   - Optional environment variables:
     - `FAIR_ASSIGN_MODE=test` for test app
     - `FAIR_ASSIGN_MODE=prod` for prod app
     - `CHRISTMAS_CLIP_PATH` set to a URL or filename
4. After deploy you’ll get a public URL like `https://fair-assign-yourname.streamlit.app`.

You can deploy two apps from the same repo: one with `FAIR_ASSIGN_MODE=test` and one with `FAIR_ASSIGN_MODE=prod`.

### Notes
- State is stored in a JSON file next to the script: `fair_assign_state_<mode>.json`. On Streamlit Cloud, this persists while the app instance is running, but may reset after a redeploy or idle shutdown.
- To reset state in TEST mode, use the sidebar “Reset all data for this mode” button.

### Future development
- Roster management: multiple groups, invitations, custom users, simple roles.
- Auto assignments: constraints such as no self, no recent repeats, avoid pairs, weighted preferences, fairness metrics, reproducible seeds.
- Scheduling: setup wizard, deadlines, reminders, calendar export, email reminders.
- Election modeling: choose and run voting systems such as ranked-choice, approval, STV; configurable tie-breakers; audit logs.
- General fair random assignment: multiple algorithms, constraint validators, simulations to assess fairness, auditability with seed control.
- Authentication and access: custom login beyond the fixed list, optional OAuth, admin dashboard for organizers.
- Persistence and multi-tenant: external database option such as SQLite or Postgres, namespaced state per group, backups.
- UI and UX: theme switcher, responsive layout, optional multi-language content.
- Integrations: optional Google Sheets import or export, Slack notifications, email sending.
- DevOps: tests, pre-commit hooks, CI for lint and type checks, versioned migrations if a database is added.
- Data privacy: secrets via environment variables, minimal PII storage, configurable data retention.

### Users and passwords (fixed list for now)
- Magda / Magda1
- Maria / Maria2
- Asia / Asia3
- Zuza / Zuza4
- Jan / Jan5

