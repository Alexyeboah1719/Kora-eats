# Importing every model here registers it on Base.metadata (needed by Alembic).
from app.models.audit import AuditLog  # noqa: F401
from app.models.auth import EmailVerification, PasswordResetToken, RefreshToken  # noqa: F401
from app.models.user import User, UserProfile, UserRole  # noqa: F401
from app.models.catalog import (  # noqa: F401,E402
    ApprovalStatus, BUSINESS_LABELS, BusinessType, Category, MenuItem, Promotion, Vendor, VendorDocument, VendorHour, WasteLog)
from app.models.customer import Address, Favorite  # noqa: F401,E402
from app.models.orders import (  # noqa: F401,E402
    Cart, CartItem, FulfillmentType, Order, OrderItem, OrderStatus, OrderStatusHistory)
from app.models.payments import DevProviderSession, Payment, PaymentStatus  # noqa: F401,E402
from app.models.riders import Delivery, DeliveryRider, DeliveryTracking, VehicleType  # noqa: F401,E402
from app.models.notifications import Notification, PushSubscription  # noqa: F401,E402
from app.models.reviews import Review  # noqa: F401,E402
from app.models.ai import AiConversation, AiMessage, CustomerPreference, RecommendationEvent  # noqa: F401,E402
from app.models.promos import Coupon, CouponType, CouponUsage  # noqa: F401,E402
from app.models.groups import GroupOrder, GroupOrderItem, GroupOrderMember, GroupOrderPreference, GroupStatus  # noqa: F401,E402
from app.models.admin import SupportTicket, SystemSetting, TicketStatus  # noqa: F401,E402

from app.models.wallet import (LedgerEntry, LedgerKind, PayoutAccount, PayoutAccountType, WalletAccount, WalletOwner, WalletPayment, Withdrawal,  # noqa: E402,F401
                               WithdrawalStatus)
