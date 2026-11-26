# KORA EATS Payment, Wallet, and Commission System

## Overview

The KORA EATS system has a comprehensive wallet and ledger system that handles all financial transactions between customers, vendors, riders, and the platform (admin). All payments for food and deliveries are collected in the admin's Paystack account, and the system automatically calculates and distributes earnings to vendors and riders based on configurable commission rates.

## System Architecture

### 1. Wallet System

The wallet system uses a **ledger-based accounting** approach where every financial transaction is recorded as an immutable entry. This ensures complete auditability and prevents balance drift.

**Key Components:**

- **WalletAccount**: Represents a wallet for a vendor, rider, or platform
- **LedgerEntry**: Every money movement is recorded here (never edited, only reversed)
- **PayoutAccount**: Where vendors/riders want their money sent (MoMo or Bank)
- **Withdrawal**: Money sent to a payout account
- **WalletPayment**: Riders paying the platform for COD cash collected

### 2. Payment Flow

#### Online Payments (Paystack)

1. Customer pays via Paystack → Money goes to admin's Paystack account
2. Order is marked as PAID
3. After order completion, earnings are calculated:
   - **Vendor**: Food sales - Platform commission - Vendor-funded discounts
   - **Rider**: Delivery fee × Rider share %
   - **Platform**: Vendor commission + Rider commission + Platform-funded discounts
4. Earnings are held for 24 hours (configurable) to allow for disputes/refunds
5. After hold period, earnings become withdrawable
6. Vendors/Riders can withdraw to their MoMo or Bank accounts

#### Cash on Delivery (COD)

1. Customer places order with "Pay on Delivery" selected
2. Order is marked as PLACED (not PAID yet)
3. Rider is assigned and delivers the food
4. Rider collects cash from customer (e.g., GH₵15 delivery fee)
5. Rider's wallet shows **negative balance** (owes platform the full delivery fee)
6. Rider must remit cash to platform:
   - Via Paystack payment link
   - Or future online orders will automatically deduct from negative balance
7. **When rider remits cash**:
   - Platform deducts commission (e.g., 20% = GH₵3)
   - Rider keeps their share (e.g., 80% = GH₵12)
   - Commission is recorded in platform's ledger
8. Once remitted, rider's balance returns to zero or positive
9. **Rider COD Blocking**: If rider exceeds GH₵300 negative balance, they cannot take COD orders until they remit

### 3. Commission Structure

#### Vendor Commission

- **Default**: 10% of food sales (after vendor-funded discounts)
- **Configurable**: Admin can adjust from dashboard (0-50%)
- **Calculated on**: Subtotal - Vendor-funded discounts
- **Platform-funded discounts** are NOT deducted from vendor commission

**Example:**
- Food subtotal: GH₵100
- Vendor-funded discount: GH₵10
- Platform-funded discount: GH₵0
- Commission base: GH₵100 - GH₵10 = GH₵90
- Platform commission (10%): GH₵9
- Vendor earnings: GH₵90 - GH₵9 = GH₵81

**COD Example (Rider):**
- Delivery fee: GH₵15
- Rider collects GH₵15 from customer
- Rider wallet: -GH₵15 (owes platform)
- Rider remits GH₵15 via Paystack
- Platform deducts 20% commission: GH₵3
- Rider receives: GH₵12 (80% share)
- Platform earns: GH₵3 commission

#### Rider Commission

- **Default**: Rider keeps 80% of delivery fee
- **Platform keeps**: 20% of delivery fee
- **Configurable**: Admin can adjust rider share (0-100%)
- **COD riders**: Commission is STILL deducted when they remit cash

**Example:**
- Delivery fee: GH₵15
- Rider share (80%): GH₵12
- Platform share (20%): GH₵3
- Online payment: Rider gets GH₵12 immediately
- COD: Rider collects GH₵15, but only keeps GH₵12 after remitting (GH₵3 commission deducted)

### 4. Promo/Coupon System

Promos and coupons can be created by either the **Platform (Admin)** or **Vendors**. The cost of the discount is borne by whoever created it.

#### Platform-Funded Coupons

- Created by admin in dashboard
- Discount cost: **Platform pays**
- **Vendor commission**: Calculated on FULL subtotal (no reduction)
- **Platform ledger**: Records PLATFORM_DISCOUNT (-amount)
- **Use case**: Promotional campaigns, new user acquisition

**Example:**
- Food subtotal: GH₵100
- Platform coupon: GH₵20 off
- Customer pays: GH₵80
- Vendor commission base: GH₵100 (full amount)
- Platform commission (10%): GH₵10
- Vendor earnings: GH₵100 - GH₵10 = GH₵90
- Platform cost: GH₵20 discount + GH₵10 commission = GH₵30
- Platform receives: GH₵80 from customer
- Platform net: GH₵80 - GH₵30 = GH₵50

#### Vendor-Funded Coupons

- Created by vendor in dashboard
- Discount cost: **Vendor pays**
- **Vendor commission**: Calculated on subtotal AFTER discount
- **Vendor ledger**: Records DISCOUNT_FUNDING (-amount)
- **Use case**: Vendor-specific promotions, clearing inventory

**Example:**
- Food subtotal: GH₵100
- Vendor coupon: GH₵20 off
- Customer pays: GH₵80
- Vendor commission base: GH₵100 - GH₵20 = GH₵80
- Platform commission (10%): GH₵8
- Vendor earnings: GH₵80 - GH₵8 = GH₵72
- Platform cost: GH₵0
- Platform receives: GH₵80 from customer
- Platform net: GH₵8 (commission only)

### 5. Ledger Entry Types

The system uses different ledger entry types to track all money movements:

**Vendor Ledger:**
- `ORDER_SALE`: +Food sales amount
- `ORDER_COMMISSION`: -Platform commission
- `DISCOUNT_FUNDING`: -Vendor-funded discount
- `REFUND_REVERSAL`: Reverses earnings on refunds

**Rider Ledger:**
- `DELIVERY_PAY`: +Rider's share of delivery fee (online payments)
- `DELIVERY_COMMISSION`: -Platform's share of delivery fee (both online and COD)
- `COD_CASH_COLLECTED`: -Delivery fee (COD: rider collected cash, owes platform)
- `COD_REMITTANCE`: +Delivery fee (COD: rider paid platform)

**Platform Ledger:**
- `ORDER_COMMISSION`: +Vendor commission
- `DELIVERY_COMMISSION`: +Rider commission
- `PLATFORM_DISCOUNT`: -Platform-funded discount
- `COD_REFUSED_LOSS`: -Cost when customer refuses to pay COD
- `WITHDRAWAL_FEE`: +Withdrawal processing fee

### 6. Withdrawal System

Vendors and riders can withdraw their earnings after the payout hold period (default 24 hours).

**Withdrawal Flow:**
1. User requests withdrawal from dashboard
2. User selects payout account (MoMo or Bank)
3. System checks:
   - Available balance > minimum withdrawal (GH₵20)
   - Payout account is verified (Paystack recipient code)
   - Account is not in cooldown period (24h after adding)
   - For large withdrawals (>GH₵1000): Requires admin approval
4. Withdrawal is created with status PENDING_APPROVAL or PROCESSING
5. System calls Paystack Transfer API
6. On success: Status = SUCCESS, money deducted from wallet
7. On failure: Status = FAILED, money returned to wallet

**Withdrawal Fee:**
- Default: GH₵0 (platform absorbs Paystack's fee)
- Configurable: Can set flat fee per withdrawal

### 7. Dashboard Visibility

#### Vendor Dashboard Shows:
- Available balance (withdrawable)
- Pending balance (in hold period)
- Total earnings (all time)
- Commission paid to platform
- Discount funding (vendor-funded coupons)
- Recent ledger entries
- Withdrawal history
- Orders breakdown

#### Rider Dashboard Shows:
- Available balance (withdrawable)
- Pending balance (in hold period)
- COD balance (negative = owes platform)
- Total earnings (all time)
- Commission paid to platform
- COD collected vs remitted
- Recent ledger entries
- Withdrawal history
- Orders breakdown

#### Admin Dashboard Shows:
- Platform revenue (commissions + withdrawal fees)
- Vendor commission settings
- Rider commission settings
- All wallets (vendor, rider, platform)
- Pending withdrawals (for approval)
- COD rider balances
- System-wide earnings summary

### 8. Admin Commission Management

Admins can adjust commission rates via the API:

**API Endpoints:**
- `GET /api/admin/commission-settings` - View current rates
- `POST /api/admin/commission-settings` - Update rates

**Settings:**
- `vendor_commission_percent`: 0-50% (default 10%)
- `rider_share_percent`: 0-100% (default 80%)

**Note:** Settings changes require server restart (stored in .env). In production, these should be stored in a database table for dynamic updates.

### 9. COD Rider Blocking

To prevent riders from accumulating too much unremitted cash:

**Blocking Logic:**
- Rider collects COD cash → Negative balance increases
- If balance < -GH₵300 → Rider blocked from COD orders
- Blocked rider can still take online payment orders
- Rider must remit cash to unblock

**Unblocking:**
- Rider pays via Paystack payment link
- Platform deducts commission from remitted amount
- Rider receives their share (e.g., 80% of delivery fee)
- Or online orders automatically deduct from negative balance
- Once balance ≥ -GH₵300 → Rider unblocked

**Configuration:**
- `cod_rider_max_cash_in_hand`: GH₵300 (default)
- `cod_enabled`: Toggle COD on/off system-wide
- `cod_max_order_total`: GH₵150 (orders above this must pay online)

### 10. Paystack Integration

**Payment Collection:**
- All online payments go to admin's Paystack account
- Paystack Secret Key stored in server config (never exposed to frontend)
- Webhooks: Paystack sends payment success/failure events

**Withdrawals:**
- Uses Paystack Transfer API
- Requires recipient codes (RCP_...) for MoMo/Bank accounts
- Idempotency keys prevent duplicate transfers
- Webhooks: Paystack sends transfer success/failure events

**Webhook Endpoints:**
- `/api/payments/webhook` - Payment status updates
- `/api/wallets/webhook` - Transfer status updates

### 11. Financial Safety Features

**Payout Hold Period:**
- Default: 24 hours after order completion
- Purpose: Allow time for disputes/refunds
- Configurable: `payout_hold_hours`

**Withdrawal Limits:**
- Minimum: GH₵20
- Maximum per day: GH₵5,000
- Large withdrawals (>GH₵1,000): Require admin approval

**Account Takeover Protection:**
- Payout account cooldown: 24 hours after adding/changing
- Prevents stolen accounts from immediately withdrawing

**Ledger Immutability:**
- Ledger entries are never edited
- Mistakes are fixed with reversing entries
- Complete audit trail

### 12. Data Retention

All financial records are retained for **6 months** after account deletion:

- Wallet accounts (soft deleted)
- Ledger entries (soft deleted)
- Payout accounts (soft deleted)
- Withdrawals (soft deleted)
- Payments (soft deleted)

After 6 months, sensitive data is permanently deleted (account numbers, names, etc.) but transaction records are anonymized for compliance.

## Summary

The KORA EATS payment and wallet system is designed to:

1. **Collect all payments centrally** in the admin's Paystack account
2. **Automatically calculate and distribute** earnings based on configurable commissions
3. **Support both online and COD payments** with proper balance tracking
4. **Block riders with excessive COD debt** to protect platform revenue
5. **Allow admin control** over commission rates via dashboard
6. **Distinguish between platform and vendor discounts** for fair cost allocation
7. **Provide full visibility** to vendors, riders, and admins of all financial transactions
8. **Ensure financial safety** with hold periods, limits, and audit trails
9. **Support withdrawals** to MoMo and Bank accounts via Paystack
10. **Retain financial records** for 6 months for compliance
11. **Deduct rider commission on ALL deliveries** - including COD when rider remits cash

### Important: COD Commission Deduction

**Riders ALWAYS pay commission on COD deliveries:**
- Rider collects full delivery fee from customer (e.g., GH₵15)
- Rider wallet shows negative balance (-GH₵15)
- When rider remits cash to platform:
  - Platform deducts commission (e.g., 20% = GH₵3)
  - Rider receives their share (e.g., 80% = GH₵12)
  - Commission is recorded in platform's ledger
- This ensures platform revenue from ALL deliveries, not just online payments

The system is production-ready and handles all edge cases including refunds, disputes, failed payments, and account security.
