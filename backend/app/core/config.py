"""Central configuration. All settings come from environment variables / .env.

Phase 2 adds auth/email/Google settings. Every new setting has a safe
development default, so the Phase 1 .env keeps working unchanged.
"""
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEV_SECRET = "dev-only-insecure-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Phase 1 ---
    app_name: str = "KORA EATS"
    environment: str = "development"  # development | production
    debug: bool = True
    database_url: str
    cors_origins: Annotated[list[str], NoDecode] = []

    # --- Phase 2: auth ---
    secret_key: str = DEV_SECRET
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    otp_expire_minutes: int = 10
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 60
    otp_max_per_hour: int = 5
    reset_token_minutes: int = 30
    frontend_base_url: str = "http://localhost:8000"

    # --- Phase 2: email (Resend) and Google ---
    resend_api_key: str = ""  # empty = development mode, emails are logged
    email_from: str = "KORA EATS <onboarding@resend.dev>"
    google_client_id: str = ""  # empty = Google sign-in disabled

    # --- Phase 3-14 ---
    upload_dir: str = "uploads"
    max_upload_mb: int = 3
    delivery_base_fee: float = 5.0        # GHS
    delivery_per_km: float = 1.5          # GHS per km beyond the free distance
    delivery_free_km: float = 2.0
    default_delivery_radius_km: float = 15.0
    rider_share_percent: int = 80         # share of the delivery fee the RIDER keeps (the platform deducts 20%)
    rider_max_pickup_minutes: int = 20    # maximum time rider should take to reach vendor (in minutes)
    rider_allow_far_pickup: int = 0      # allow riders beyond max pickup time when nearby riders are scarce (1 = yes, 0 = no)
    vendor_commission_percent: float = 10.0   # the platform's cut of a vendor's food sales (after any discount the vendor funded)
    payout_hold_hours: int = 24           # earnings become withdrawable this long after the order is completed (time for disputes/refunds)
    min_withdrawal: float = 20.0          # GH₵
    max_withdrawal_per_day: float = 5000.0
    withdrawal_approval_threshold: float = 1000.0   # withdrawals above this wait for an admin to approve (0 = never wait)
    withdrawal_fee: float = 0.0           # flat fee charged to the user per withdrawal (0 = the platform absorbs Paystack's transfer fee)
    payout_account_cooldown_hours: int = 24   # after adding/changing a payout account, wait this long before withdrawing (account-takeover protection)
    cod_enabled: int = 1                  # pay on delivery on/off (1 or 0)
    cod_max_order_total: float = 150.0    # cash orders above this must be paid online
    cod_rider_max_cash_in_hand: float = 300.0   # a rider holding more unremitted cash than this can't take pay-on-delivery orders
    cod_max_failures: int = 2             # customers who refuse to pay this many times lose pay-on-delivery for a while
    cod_block_days: int = 30
    cod_rider_block_on_debt: int = 1     # block riders from orders if they owe more than cod_rider_max_cash_in_hand (1 = yes, 0 = no)
    cod_penalty_percent: float = 20.0     # penalty charged to customer when they refuse to pay COD (percentage of order total)
    cod_penalty_fixed: float = 10.0       # fixed penalty charged to customer when they refuse to pay COD (GHS)
    # Recommendation engine weights (relative importance, 0 = ignore). Editable by admins under AI settings.
    rec_w_value: float = 1.0              # does the price/total fit the budget and the customer's usual spend
    rec_w_distance: float = 0.8           # how close the vendor is
    rec_w_time: float = 0.8               # how soon the food is ready / arrives
    rec_w_fee: float = 0.5                # how much of the cost is delivery fee
    rec_w_rating: float = 0.9             # vendor rating (smoothed for new vendors)
    rec_w_popularity: float = 0.4         # how often the dish is ordered
    rec_w_memory: float = 1.2             # match with what this customer ordered / saved before
    rec_w_time_of_day: float = 0.7        # right meal for the time of day
    rec_w_craving: float = 1.5            # match with stated cravings
    rec_retrieval_limit: int = 500        # max dishes scored per request
    rec_event_retention_days: int = 90
    avg_speed_kmh: float = 20.0           # city riding speed used for ETAs (estimates, always labelled as such)
    rider_assign_buffer_minutes: int = 8  # typical wait for a rider to arrive at the vendor
    pickup_min_saving_minutes: int = 5    # only call pickup 'faster' when it saves at least this long
    payment_provider: str = "dev"         # dev | paystack   (dev is a simulator: blocked in production)
    paystack_secret_key: str = ""         # sk_... from the Paystack dashboard. SERVER ONLY, never sent to the browser
    paystack_base_url: str = "https://api.paystack.co"
    enabled_payment_methods: Annotated[list[str], NoDecode] = ["MOBILE_MONEY"]   # add CARD / BANK_TRANSFER when enabled on your Paystack account
    background_jobs: bool = True          # scheduled-order release, payment timeouts, reminders
    order_payment_timeout_minutes: int = 30
    review_reminder_hours: int = 1
    scheduled_min_lead_minutes: int = 45
    scheduled_max_days: int = 7
    vapid_public_key: str = ""            # web push (python -m scripts.generate_vapid)
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"
    csp_mode: str = "report-only"         # off | report-only | enforce  (applies in production)
    api_rate_limit_per_minute: int = 300  # global per-IP limit for /api
    trusted_proxy_hops: int = 0           # 1 when behind ONE reverse proxy/load balancer (Render, Fly, Nginx...)
    db_pool_size: int = 5
    db_max_overflow: int = 10

    @field_validator("enabled_payment_methods", mode="before")
    @classmethod
    def parse_methods(cls, v):
        items = [m.strip().upper() for m in v.split(",")] if isinstance(v, str) else [str(m).upper() for m in v]
        items = [m for m in items if m]
        bad = set(items) - {"MOBILE_MONEY", "CARD", "BANK_TRANSFER"}
        if bad or not items:
            raise ValueError("ENABLED_PAYMENT_METHODS must list MOBILE_MONEY, CARD and/or BANK_TRANSFER")
        return items

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @model_validator(mode="after")
    def check_payment_config(self):
        if self.payment_provider not in ("dev", "paystack"):
            raise ValueError("PAYMENT_PROVIDER must be dev or paystack")
        if self.payment_provider == "paystack" and self.environment == "production" and not self.paystack_secret_key.startswith("sk_"):
            raise ValueError("PAYSTACK_SECRET_KEY (sk_...) is required when PAYMENT_PROVIDER=paystack in production")
        return self

    @model_validator(mode="after")
    def block_insecure_production(self):
        if self.environment == "production":
            if self.secret_key == DEV_SECRET or len(self.secret_key) < 32:
                raise ValueError("SECRET_KEY must be a strong random value (32+ chars) in production")
            if self.payment_provider == "dev":
                raise ValueError("PAYMENT_PROVIDER=dev is a simulator and cannot be used in production")
            if self.debug:
                raise ValueError("DEBUG must be false in production")
            if not self.frontend_base_url.startswith("https://"):
                raise ValueError("FRONTEND_BASE_URL must be an https:// URL in production")
            if "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS must list your real origins, not *")
        return self

    @property
    def cookie_secure(self) -> bool:
        """Secure cookies are HTTPS-only, so enable them in production."""
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
