"""Role-specific AI assistant for admins, vendors, and riders.
Each role gets analytics and insights relevant to their work, with authentication."""
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import api_error
from app.core.money import cedis
from app.core.timeutil import utcnow
from app.models.catalog import ApprovalStatus, MenuItem, Vendor
from app.models.orders import Order, OrderStatus
from app.models.riders import Delivery, DeliveryRider
from app.models.user import UserRole
from app.services import dispatch_service as dsp, forecast_service as fc
from app.services.analytics_service import COUNTED
from app.services.wallet_service import balances


# -------------------------------------------------------------------------
# ADMIN AI - Analytics and system insights
# -------------------------------------------------------------------------

ADMIN_GREETINGS = {
    "hi": "Hello Admin! I'm here to help you understand your platform. Ask me about orders, vendors, riders, AI performance, or any analytics you need.",
    "hello": "Hey! Ready to dive into the numbers? What would you like to know about the platform today?",
    "hey": "Hi! I can help you with vendor performance, rider dispatch, customer trends, or AI recommendations. What's on your mind?",
    "how are you": "I'm great! The platform is running smoothly. What analytics can I pull for you?",
}

ADMIN_QUERIES = {
    "orders": "orders",
    "vendors": "vendors",
    "riders": "riders",
    "customers": "customers",
    "revenue": "revenue",
    "analytics": "analytics",
    "performance": "performance",
    "ai": "ai performance",
    "dispatch": "dispatch",
    "wallets": "wallets",
    "payouts": "payouts",
}


def admin_chat(db: Session, admin_id: int, text: str) -> dict:
    """Admin-specific AI chat with analytics access using secure tools."""
    from app.models.user import User
    from app.services import ai_tools

    user = db.get(User, admin_id)
    t = text.lower().strip()

    # Greetings
    for greeting, response in ADMIN_GREETINGS.items():
        if greeting in t:
            return {"reply": response, "data": None, "query_type": "greeting"}

    # Use AI tools for database queries
    try:
        analytics = ai_tools.get_admin_analytics(db, user, 30)

        # Build contextual response based on query
        if "orders" in t or "how many" in t:
            return {"reply": f"**Order Statistics (Last 30 Days)**\n• Total orders: {analytics['total_orders']}\n• Status breakdown: " +
                       ", ".join([f"{k}: {v}" for k, v in analytics['order_status_breakdown'].items()]),
                   "data": analytics, "query_type": "orders"}
        elif "revenue" in t or "earnings" in t or "money" in t:
            return {"reply": f"**Platform Revenue (Last 30 Days)**\n• Total revenue: {cedis(analytics['total_revenue'])}\n• Total users: {analytics['total_users']}\n• Active vendors: {analytics['total_vendors']}\n• Active riders: {analytics['total_riders']}",
                   "data": analytics, "query_type": "revenue"}
        elif "vendors" in t:
            return {"reply": f"**Vendor Statistics**\n• Total vendors: {analytics['total_vendors']}\n• Total orders processed: {analytics['total_orders']}",
                   "data": analytics, "query_type": "vendors"}
        elif "riders" in t:
            return {"reply": f"**Rider Statistics**\n• Total riders: {analytics['total_riders']}\n• Orders delivered: {analytics['total_orders']}",
                   "data": analytics, "query_type": "riders"}
        else:
            return {"reply": f"**Platform Overview (Last 30 Days)**\n• Total orders: {analytics['total_orders']}\n• Total revenue: {cedis(analytics['total_revenue'])}\n• Users: {analytics['total_users']}\n• Vendors: {analytics['total_vendors']}\n• Riders: {analytics['total_riders']}\n\nI can provide more details on orders, revenue, vendors, or riders. What would you like to know?",
                   "data": analytics, "query_type": "overview"}
    except Exception as e:
        return {"reply": f"I encountered an error retrieving your analytics: {str(e)}. Please try again.", "data": None, "query_type": "error"}


# -------------------------------------------------------------------------
# VENDOR AI - Business intelligence
# -------------------------------------------------------------------------

VENDOR_GREETINGS = {
    "hi": "Hello! I'm here to help you grow your business. Ask me about your orders, sales, menu performance, or any analytics you need.",
    "hello": "Hey! Let's look at your restaurant's performance. What would you like to know?",
    "hey": "Hi! I can help you understand your sales, popular items, and customer feedback. What's on your mind?",
    "how are you": "I'm doing great! Ready to help you with your business analytics. What do you need?",
}

VENDOR_QUERIES = {
    "sales": "sales",
    "orders": "orders",
    "forecast": "forecast",
    "popular": "popular",
    "earnings": "earnings",
    "wallet": "wallet",
    "waste": "waste",
    "preparation": "preparation",
}


def vendor_chat(db: Session, vendor_id: int, text: str) -> dict:
    """Vendor-specific AI chat with business intelligence using secure tools."""
    from app.models.user import User
    from app.services import ai_tools

    vendor = db.get(Vendor, vendor_id)
    user = db.get(User, vendor.owner_user_id) if vendor else None
    t = text.lower().strip()

    # Greetings
    for greeting, response in VENDOR_GREETINGS.items():
        if greeting in t:
            return {"reply": response, "data": None, "query_type": "greeting"}

    # Use AI tools for database queries
    try:
        analytics = ai_tools.get_vendor_analytics(db, user, 30)

        if "earnings" in t or "money" in t or "revenue" in t:
            return {"reply": f"**Your Earnings (Last 30 Days)**\n• Total revenue: {cedis(analytics['total_revenue'])}\n• Available balance: {cedis(analytics['available_balance'])}\n• Total orders: {analytics['total_orders']}\n• Completed: {analytics['completed_orders']}",
                   "data": analytics, "query_type": "earnings"}
        elif "orders" in t:
            return {"reply": f"**Order Statistics (Last 30 Days)**\n• Total orders: {analytics['total_orders']}\n• Completed: {analytics['completed_orders']}\n• Pending: {analytics['pending_orders']}",
                   "data": analytics, "query_type": "orders"}
        elif "top" in t or "popular" in t or "best" in t:
            items = analytics['top_items'][:3]
            reply = f"**Top Performing Items (Last 30 Days)**\n"
            for item in items:
                reply += f"• {item['name']}: {item['orders']} orders, {cedis(item['revenue'])} revenue\n"
            return {"reply": reply, "data": analytics, "query_type": "popular"}
        else:
            return {"reply": f"**Business Overview (Last 30 Days)**\n• Total orders: {analytics['total_orders']}\n• Revenue: {cedis(analytics['total_revenue'])}\n• Available balance: {cedis(analytics['available_balance'])}\n\nI can provide details on earnings, orders, or popular items. What would you like to know?",
                   "data": analytics, "query_type": "overview"}
    except Exception as e:
        return {"reply": f"I encountered an error retrieving your business data: {str(e)}. Please try again.", "data": None, "query_type": "error"}


# -------------------------------------------------------------------------
# RIDER AI - Delivery intelligence
# -------------------------------------------------------------------------

RIDER_GREETINGS = {
    "hi": "Hello! I'm here to help you with your deliveries. Ask me about your orders, earnings, or delivery performance.",
    "hello": "Hey! Let's check your delivery stats. What would you like to know?",
    "hey": "Hi! I can help you track your deliveries and earnings. What's on your mind?",
    "how are you": "I'm doing great! Ready to help you with your delivery analytics. What do you need?",
}

RIDER_QUERIES = {
    "deliveries": "deliveries",
    "today": "today",
    "week": "week",
    "earnings": "earnings",
    "wallet": "wallet",
    "performance": "performance",
}


def rider_chat(db: Session, rider_id: int, text: str) -> dict:
    """Rider-specific AI chat with delivery insights using secure tools."""
    from app.models.user import User
    from app.services import ai_tools

    rider = db.get(DeliveryRider, rider_id)
    user = db.get(User, rider.user_id) if rider else None
    t = text.lower().strip()

    # Greetings
    for greeting, response in RIDER_GREETINGS.items():
        if greeting in t:
            return {"reply": response, "data": None, "query_type": "greeting"}

    # Use AI tools for database queries
    try:
        analytics = ai_tools.get_rider_analytics(db, user, 30)

        if "earnings" in t or "money" in t:
            return {"reply": f"**Your Earnings (Last 30 Days)**\n• Available balance: {cedis(analytics['available_balance'])}\n• Total deliveries: {analytics['total_deliveries']}\n• Completed: {analytics['completed_deliveries']}",
                   "data": analytics, "query_type": "earnings"}
        elif "deliveries" in t or "orders" in t:
            return {"reply": f"**Delivery Statistics (Last 30 Days)**\n• Total deliveries: {analytics['total_deliveries']}\n• Completed: {analytics['completed_deliveries']}\n• Pending: {analytics['pending_deliveries']}\n• Status: {'Online' if analytics['is_online'] else 'Offline'}",
                   "data": analytics, "query_type": "deliveries"}
        else:
            return {"reply": f"**Delivery Overview (Last 30 Days)**\n• Total deliveries: {analytics['total_deliveries']}\n• Available balance: {cedis(analytics['available_balance'])}\n• Status: {'Online' if analytics['is_online'] else 'Offline'}\n\nI can provide details on earnings or deliveries. What would you like to know?",
                   "data": analytics, "query_type": "overview"}
    except Exception as e:
        return {"reply": f"I encountered an error retrieving your delivery data: {str(e)}. Please try again.", "data": None, "query_type": "error"}
