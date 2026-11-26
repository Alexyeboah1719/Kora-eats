"""Subscribers to order events. Imported once at startup (see main.py)."""
from sqlalchemy import select, update

from app.models.catalog import ApprovalStatus, MenuItem
from app.models.orders import FulfillmentType, OrderStatus as S
from app.models.riders import Delivery, DeliveryRider
from app.models.user import User
from app.core.money import cedis
from app.services import email_templates as et
from app.services import notification_service as ns
from app.services import order_events


@order_events.subscribe
def bump_popularity(db, order, old, new, actor_role, background):
    if new in (S.DELIVERED, S.COLLECTED) and old not in (S.DELIVERED, S.COLLECTED):
        for i in order.items:
            if i.menu_item_id:
                db.execute(update(MenuItem).where(MenuItem.id == i.menu_item_id)
                           .values(order_count=MenuItem.order_count + i.quantity))


def _items(order):
    return [(i.name, i.quantity, float(i.line_total)) for i in order.items]


def _rider_user_id(db, order):
    d = db.scalar(select(Delivery).where(Delivery.order_id == order.id))
    return d.rider.user_id if d is not None else None


@order_events.subscribe
def notify_on_order_events(db, order, old, new, actor_role, background):
    cust = db.get(User, order.customer_id)
    url = f"/order.html?id={order.id}"
    vendor_owner = order.vendor.owner_user_id
    to_customer = lambda type_, title, body, email=None: ns.notify(
        db, cust.id, type_, title, body, url, email=email, email_to=cust.email, background=background)

    if actor_role == "rider:nearby":
        return to_customer("rider_nearby", "Your rider is nearby", "Get ready, your food is almost there.")
    if old is None and new == S.PENDING_PAYMENT:
        return to_customer("order_created", "Order placed", f"Complete payment to send order {order.code} to {order.vendor.name}.")
    if old is None and new == S.PLACED:
        to_customer("order_created", "Order placed", f"Order {order.code} is with {order.vendor.name}. Pay {cedis(order.total)} in cash on delivery.")
        if vendor_owner:
            ns.notify(db, vendor_owner, "new_order", "New pay-on-delivery order!",
                      f"Order {order.code}: {sum(i.quantity for i in order.items)} item(s), {cedis(order.total)} — the customer pays cash on delivery.",
                      "/vendor/dashboard.html#orders", background=background)
        return
    if new == S.PAID:
        email = et.order_summary_email(cust.profile.full_name, order.code, _items(order), float(order.total),
                                       "Payment received", "we've received your payment. The vendor will confirm shortly.", url)
        to_customer("payment_success", "Payment successful", f"{cedis(order.total)} received for order {order.code}.", email)
        if vendor_owner:
            ns.notify(db, vendor_owner, "new_order", "New order!", f"Order {order.code}: {sum(i.quantity for i in order.items)} item(s), {cedis(order.total)}.",
                      "/vendor/dashboard.html#orders", background=background)
    elif new == S.PAYMENT_FAILED:
        to_customer("payment_failed", "Payment failed", f"We couldn't confirm payment for order {order.code}. You can try again.")
    elif new == S.CONFIRMED:
        email = et.order_summary_email(cust.profile.full_name, order.code, _items(order), float(order.total),
                                       "Order confirmed", f"{order.vendor.name} accepted your order and will start preparing it.", url)
        to_customer("order_accepted", "Order accepted", f"{order.vendor.name} accepted your order.", email)
    elif new == S.PREPARING:
        to_customer("food_preparing", "Your food is being prepared", f"{order.vendor.name} is cooking order {order.code}.")
    elif new == S.READY_FOR_PICKUP:
        if order.fulfillment_type == FulfillmentType.PICKUP:       # no riders: the customer comes to the vendor
            return to_customer("food_ready", "Ready for pickup", f"Your order is ready at {order.vendor.name}. Show your pickup code when you arrive.")
        to_customer("food_ready", "Your food is ready", "A rider will pick it up shortly.")
        from app.services import dispatch_service, rider_service
        best = dispatch_service.recommended(db, order)
        for p in best:                                            # smart dispatch: tell the riders who suit this order best
            ns.notify(db, p.rider.user_id, "delivery_recommended", "Recommended for you",
                      f"Pickup at {order.vendor.name} is {p.pickup_km:.1f} km away (about {max(p.pickup_eta, 1)} min). You'd earn {cedis(rider_service.earning_for(order))}.",
                      "/rider/dashboard.html", background=background)
        for r in ([] if best else db.scalars(select(DeliveryRider).where(DeliveryRider.is_online.is_(True),
                                                        DeliveryRider.approval_status == ApprovalStatus.APPROVED).limit(100))):   # nobody has a location: alert everyone online
            ns.notify(db, r.user_id, "delivery_available", "New delivery available", f"Pickup at {order.vendor.name}.",
                      "/rider/dashboard.html", background=background)
    elif new == S.RIDER_ASSIGNED:
        to_customer("rider_assigned", "Rider assigned", "A rider is on the way to pick up your order.")
        if vendor_owner:
            ns.notify(db, vendor_owner, "rider_assigned", "Rider assigned", f"A rider is coming for order {order.code}.",
                      "/vendor/dashboard.html#orders", background=background)
    elif new == S.PICKED_UP:
        to_customer("order_picked_up", "Order picked up", "Your rider has your food.")
    elif new == S.OUT_FOR_DELIVERY:
        to_customer("out_for_delivery", "Out for delivery", "Your rider is heading to you. Track them live.")
    elif new == S.RIDER_ARRIVED:
        to_customer("rider_arrived", "Your rider has arrived", "Please meet your rider at your delivery address.")
    elif new == S.ACCEPTED_RETURN:
        rider_user = _rider_user_id(db, order)
        if rider_user:
            ns.notify(db, rider_user, "return_accepted", "Return accepted",
                      f"{order.vendor.name} accepted the return for order {order.code}. Bring the food back to the vendor.",
                      "/rider/dashboard.html", background=background)
    elif new == S.RETURN_DECLINED:
        rider_user = _rider_user_id(db, order)
        if rider_user:
            ns.notify(db, rider_user, "return_declined", "Vendor declined the return",
                      f"{order.vendor.name} declined the return for order {order.code}. Contact support if you need help.",
                      "/rider/dashboard.html", background=background)
        to_customer("order_return_failed", "Order couldn't be completed",
                    f"We couldn't complete order {order.code}, and the vendor couldn't accept the returned food. Our team will follow up.", url)
    elif new == S.RETURNED_TO_VENDOR:
        rider_user = _rider_user_id(db, order)
        if rider_user:
            ns.notify(db, rider_user, "return_confirmed", "Return confirmed",
                      f"{order.vendor.name} confirmed receipt of order {order.code}.", "/rider/dashboard.html", background=background)
        to_customer("order_returned", "Order returned",
                    f"Order {order.code} was returned to {order.vendor.name} because it couldn't be delivered. It will not be offered for delivery again.", url)
    elif new == S.DELIVERED:
        email = et.notification_email("Your order was delivered", f"Order {order.code} from {order.vendor.name} has been delivered. Enjoy!", url, "Rate your order")
        to_customer("order_delivered", "Order delivered", "Enjoy your meal! Tap to rate your order.", email)
    elif new == S.COLLECTED:
        email = et.notification_email("Enjoy your order", f"Order {order.code} from {order.vendor.name} was collected. Enjoy!", url, "Rate your order")
        to_customer("order_collected", "Order collected", "Enjoy your meal! Tap to rate your order.", email)
    elif new == S.CANCELLED:
        reason = f" Reason: {order.cancelled_reason}" if order.cancelled_reason else ""
        refund = " You'll be refunded." if order.paid_at else ""
        to_customer("order_cancelled", "Order cancelled", f"Order {order.code} was cancelled.{reason}{refund}")
    elif new == S.REFUNDED:
        to_customer("order_refunded", "Refund issued",
                    f"Your refund for order {order.code} has been issued to your original payment method. "
                    "It can take 1–5 business days to reflect on your statement.")

from app.services import wallet_service  # noqa: E402,F401  (importing registers the wallet handler on the order event bus)
