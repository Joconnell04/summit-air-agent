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
    slot_cache_seconds: float = 60.0
    # The owner view refuses every request when this is empty.
    owner_token: str = field(default_factory=lambda: os.environ.get("OWNER_TOKEN", ""))
    # Where the owner view is served, for links in alert emails. Empty leaves the links out.
    owner_view_url: str = field(default_factory=lambda: os.environ.get("OWNER_VIEW_URL", ""))
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
    # Development only: serve every request as if it were this moment (ISO, with offset). The
    # owner demo sets it to the evening of the seeded day so "now" sits on the board.
    fixed_now: str = field(default_factory=lambda: os.environ.get("FIXED_NOW", ""))

    def __post_init__(self):
        self.db_path = Path(self.db_path)
        self.tenants_dir = Path(self.tenants_dir)
        if self.log_path is None:
            self.log_path = self.db_path.with_suffix(".log")
        self.log_path = Path(self.log_path)


# Per-call limits. Hitting one flags a person instead of continuing (docs/architecture.md).
LIMITS = {
    "book": 1,
    "reschedule": 1,
    "cancel": 1,
    "find_customer": 5,
    "get_customer_history": 5,
}
NOTE_MAX_CHARS = 500
BOOKING_DAYS_AHEAD = 14
QUEUE_MAX_ATTEMPTS = 5
ALERT_MAX_ATTEMPTS = 3
REPORT_MAX_ATTEMPTS = 3
# A voice worker that hasn't sent a heartbeat for this long counts as down (healthz, owner view).
WORKER_STALE_SECONDS = 120
REPORT_MAX_CHARS = 2000
