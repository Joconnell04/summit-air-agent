"""Settings from the environment. Every value has a development default."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(os.environ.get("TOOL_DB", "data/tool_server.db"))
    )
    log_path: Path | None = None
    tenants_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("TENANTS_DIR", "tenants"))
    )
    # What a tenant's agent.yaml `extends` names (app/tenant.py).
    trades_dir: Path = field(default_factory=lambda: Path(os.environ.get("TRADES_DIR", "trades")))
    # Used when the dialed number matches no tenant. Empty is fine when there is only one tenant.
    default_tenant: str = field(default_factory=lambda: os.environ.get("DEFAULT_TENANT", ""))
    # Debug endpoints refuse every request when this is empty.
    debug_token: str = field(default_factory=lambda: os.environ.get("DEBUG_TOKEN", ""))
    # development keeps full logs and transcripts (spec S-45).
    environment: str = field(default_factory=lambda: os.environ.get("APP_ENV", "development"))
    fsm_base_url: str = field(default_factory=lambda: os.environ.get("FSM_BASE_URL", ""))
    fsm_timeout_seconds: float = 4.0
    # Background jobs (app/jobs.py). 0 turns them off.
    jobs_interval_seconds: float = field(
        default_factory=lambda: float(os.environ.get("JOBS_INTERVAL_SECONDS", "60"))
    )
    # 0 turns the slot cache off, as on the Retell eval instance, whose mock is reset between
    # scenarios and must never be answered from the previous world.
    slot_cache_seconds: float = field(
        default_factory=lambda: float(os.environ.get("SLOT_CACHE_SECONDS", "60"))
    )
    # Read the outside temperature from the National Weather Service (app/weather.py). Off in
    # tests and development so nothing calls out; on for the deployed server.
    weather: bool = field(default_factory=lambda: os.environ.get("WEATHER", "") == "1")
    # A side model for the vulnerable-person read (app/side_model.py): a profile name under `side`
    # in config/models.yaml. Empty is off, and the phrase rules decide alone.
    side_model: str = field(default_factory=lambda: os.environ.get("SIDE_MODEL", ""))
    # Find the ZIP for a street address the caller said (app/geocode.py): `geosearch` for NYC
    # Planning's GeoSearch with the Census geocoder behind it. Empty is off, as in tests and
    # development, so nothing calls out; the agent then asks for the ZIP as before.
    geocoder: str = field(default_factory=lambda: os.environ.get("GEOCODER", ""))
    # Write the technician's summary after each booked call (app/summary.py). Off in tests and
    # development so nothing calls out; it needs ANTHROPIC_API_KEY as well.
    summaries: bool = field(default_factory=lambda: os.environ.get("SUMMARIES", "") == "1")
    # The owner view's first admin, created once at startup when no user has this username
    # (app/accounts.py). More users are added on the Settings page or with make owner-user.
    owner_admin_username: str = field(
        default_factory=lambda: os.environ.get("OWNER_ADMIN_USERNAME", "")
    )
    owner_admin_name: str = field(default_factory=lambda: os.environ.get("OWNER_ADMIN_NAME", ""))
    owner_admin_password: str = field(
        default_factory=lambda: os.environ.get("OWNER_ADMIN_PASSWORD", "")
    )
    # Start the eval gate (evals/gate.py) in the background when an owner publishes. Off, a draft
    # waits as checking until someone runs make rules-gate. It needs ANTHROPIC_API_KEY.
    rules_gate: bool = field(default_factory=lambda: os.environ.get("RULES_GATE", "") == "1")
    # Where the owner view is served, for links in alert emails. Empty leaves the links out.
    owner_view_url: str = field(default_factory=lambda: os.environ.get("OWNER_VIEW_URL", ""))
    # The LangSmith page that holds the eval experiments (evals/README.md), linked from the debug
    # bench's eval pages. Empty leaves the links out.
    langsmith_url: str = field(default_factory=lambda: os.environ.get("LANGSMITH_URL", ""))
    # Alert email. With no SMTP_HOST, alerts are held, not sent (app/mail.py).
    smtp_host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.environ.get("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.environ.get("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.environ.get("SMTP_PASSWORD", ""))
    mail_from: str = field(default_factory=lambda: os.environ.get("MAIL_FROM", ""))
    # Owner reports (story O-8). With no token they are held, not filed (app/reports.py).
    reports_github_token: str = field(
        default_factory=lambda: os.environ.get("REPORTS_GITHUB_TOKEN", "")
    )
    reports_github_repo: str = field(
        default_factory=lambda: os.environ.get("REPORTS_GITHUB_REPO", "")
    )
    # Call recordings (story O-6). LiveKit uploads each call's audio to this S3-compatible bucket
    # (it can't write to our disk); the jobs loop copies it to recordings_dir and deletes it from the
    # bucket. No bucket turns recording off. The voice worker reads the same variables.
    recording_bucket: str = field(default_factory=lambda: os.environ.get("RECORDING_S3_BUCKET", ""))
    recording_endpoint: str = field(
        default_factory=lambda: os.environ.get("RECORDING_S3_ENDPOINT", "")
    )
    recording_region: str = field(default_factory=lambda: os.environ.get("RECORDING_S3_REGION", ""))
    recording_access_key: str = field(
        default_factory=lambda: os.environ.get("RECORDING_S3_ACCESS_KEY", "")
    )
    recording_secret: str = field(default_factory=lambda: os.environ.get("RECORDING_S3_SECRET", ""))
    recordings_dir: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["RECORDINGS_DIR"]) if os.environ.get("RECORDINGS_DIR") else None
        )
    )
    # Transfer numbers by environment variable name, used instead of the environment when set. Evals
    # set it so a run never reads a real number from the shell (app/transfer.py).
    transfer_numbers: dict[str, str] | None = None
    # Development only: serve every request as if it were this moment (ISO, with offset). The
    # owner demo sets it to the evening of the seeded day so "now" sits on the board.
    fixed_now: str = field(default_factory=lambda: os.environ.get("FIXED_NOW", ""))

    def __post_init__(self):
        self.db_path = Path(self.db_path)
        self.tenants_dir = Path(self.tenants_dir)
        self.trades_dir = Path(self.trades_dir)
        if self.log_path is None:
            self.log_path = self.db_path.with_suffix(".log")
        self.log_path = Path(self.log_path)
        # Beside the database by default: data/recordings on the server.
        if self.recordings_dir is None:
            self.recordings_dir = self.db_path.parent / "recordings"
        self.recordings_dir = Path(self.recordings_dir)


# Per-call limits. Hitting one flags a person instead of continuing (docs/architecture.md).
LIMITS = {
    # Two jobs on one call, each its own visit (DECISIONS.md 085, tools._second_job).
    "book": 2,
    "reschedule": 1,
    "cancel": 1,
    "find_customer": 5,
    "get_customer_history": 5,
    "find_address": 4,
}
NOTE_MAX_CHARS = 500
BOOKING_DAYS_AHEAD = 14
QUEUE_MAX_ATTEMPTS = 5
ALERT_MAX_ATTEMPTS = 3
REPORT_MAX_ATTEMPTS = 3
# A voice worker that hasn't sent a heartbeat for this long counts as down (healthz, owner view).
WORKER_STALE_SECONDS = 120
REPORT_MAX_CHARS = 2000
# A tag on a callback's call is tried again for this long, since a phone system may log a
# call some minutes late; after that a resolved callback is flagged for a person.
TAG_RETRY_MINUTES = 120
# A dialed call the voice layer never reported on, or an answered one that never ended, is settled
# after this, so it can't block a person.
STUCK_DIALING_MINUTES = 30
