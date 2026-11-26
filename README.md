# KORA EATS

An AI-powered food marketplace for Ghana. Customers discover, compare and order meals; vendors (restaurants, chop bars, home cooks,
bakers, street-food sellers, caterers) manage menus and orders; riders deliver; admins run the platform.
FastAPI + PostgreSQL backend, plain HTML/CSS/JS + Bootstrap frontend, installable PWA.

## Run it (VS Code)
1. Install **Python 3.12+** and **PostgreSQL 14+**.
2. Create the databases (once), in `psql` or pgAdmin:
   ```sql
   CREATE USER foodapp WITH PASSWORD 'foodapp_dev_pw';
   CREATE DATABASE foodapp_dev OWNER foodapp;
   CREATE DATABASE foodapp_test OWNER foodapp;   -- used only by the automated tests
   ```
3. In a terminal opened in the `backend/` folder:
   ```bash
   python -m venv venv
   source venv/bin/activate            # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   alembic upgrade head                # creates every table (this is required after each update too)
   python -m scripts.seed_dev_data     # optional: fictional sample vendors + dishes across Ghana
   uvicorn app.main:app --reload
   ```
   `backend/.env` is included and works as-is for local development.
4. Open http://localhost:8000. Run the tests with `pytest` (from `backend/`).

The database and folder names still say `foodapp`; they are internal and don't affect the product name.

## Try every role (demo logins)
After `python -m scripts.seed_dev_data --demo-users` you can sign in (password `Demo!2345`) as `customer@demo.kora`, `vendor@demo.kora`,
`rider@demo.kora` or `admin@demo.kora`. These exist only on your machine; the command refuses to run in production.

## Checks you can run
- `pytest` (in `backend/`): the full test suite, including a complete customer-to-rider journey and query-count guards.
- `ruff check .` (in `backend/`): catches real bugs (undefined names, unused variables).
- Frontend smoke test: with the server running and demo users seeded, `cd frontend-tests && npm install && npm run smoke` loads every
  page and dashboard section in jsdom against the live API and fails on any JavaScript error, server error or page that shows "undefined".
- VS Code: `.vscode/launch.json` has "run server" and "run tests" entries.

## Going live
See `DEPLOYMENT.md` (Docker, Neon, Paystack webhook, security checklist, data-protection notes).

## Payments (Paystack)
- Development: `PAYMENT_PROVIDER=dev` uses a built-in simulator page. No real money moves.
- Real payments: create a Paystack account for Ghana, then in `backend/.env` set `PAYMENT_PROVIDER=paystack` and
  `PAYSTACK_SECRET_KEY=sk_test_...` (use the TEST key first). In the Paystack dashboard set the webhook URL to
  `https://YOUR-DOMAIN/api/payments/webhook/paystack` (Paystack must be able to reach it; use a tunnel such as ngrok while developing).
- Mobile Money is enabled first. When Card / Bank Transfer are enabled on your Paystack account:
  `ENABLED_PAYMENT_METHODS=MOBILE_MONEY,CARD,BANK_TRANSFER`.
- An order becomes PAID only after the SERVER asks Paystack (`/transaction/verify/:reference`) and the amount (in pesewas) and currency
  (GHS) match. A browser redirect or a webhook body is never trusted on its own. The secret key is only read by the server.

## Other services (all optional in development)
- Email: set `RESEND_API_KEY` (otherwise emails are printed in the terminal, including OTP codes).
- Google sign-in: set `GOOGLE_CLIENT_ID`.
- Web push: `python -m scripts.generate_vapid`, then put the keys in `.env`.

## Create an admin
`python -m scripts.create_admin you@yourdomain.com "Your Name"` (it asks for the password, minimum 12 characters; admins can never be created through the website). Step-by-step for production: `DEPLOYMENT.md`, section 4.

## Status
**All planned steps are built.** 485 automated tests pass on a fresh install, and every page and dashboard section loads without a JavaScript
error against the live API (the jsdom smoke test, 61 screens).

What's in it: customer, vendor, rider and admin systems; orders; Paystack payments; notifications; reviews; coupons, scheduled and group
orders; analytics; the installable PWA; pickup vs delivery with one shared price/time quote; structured food data (dietary, allergens,
traits, meal times); business types for every kind of food seller; Ghana-specific phone/location rules; and the KORA EATS AI:
- **Decision engine** (`/decide.html`): ranked "For you now" feed with a plain-language "Why this?", **What can I afford?** (food + delivery
  or pickup, plus combos), **Craving Diagnosis**, **Personal Food Memory** you can see, switch off or erase (`/food-profile.html`)
- **Group Food AI** and **Split the Budget** (`/split.html`): one dish per person, everyone's dietary rules respected, shared delivery fee,
  an exact budget optimiser, compatibility %
- **Vendor Intelligence** (vendor dashboard, "AI insights"): demand forecast, peak periods, popular meals, preparation and food-waste advice;
  says plainly when there isn't enough history
- **Smart Rider Dispatch**: best rider per order (ETAs, capacity, batching of same-direction orders, urgency), recommended-rider alerts,
  admin dispatch board with manual assignment
- **QR codes, sharing and camera**: printable menu QR, pickup QR the vendor can scan, shareable vendor/dish pages with link previews, in-app
  camera with photo compression, and uploaded photos are re-encoded so hidden GPS/camera data is stripped
- **Admin "AI performance"** screen: how recommendations convert, privacy-control usage, forecast coverage, live dispatch; ranking weights are
  editable under Settings

What is NOT proven yet (please read): the app has not been opened in a real browser or on a real phone; Paystack and Resend have only been
tested against fakes of their documented APIs; the Dockerfile/compose files are written but were not built here (no Docker in the build
environment), although the exact start script, production configuration guards and gunicorn boot were verified; the AI quality (rankings,
forecasts) is only as good as the real data it will see once customers and vendors use it.

Known limits: uploads are stored on local disk (use persistent storage or an object store in production); the rate limiter is in-memory
(single server process); Paystack has only been tested against a fake of its documented API; the frontend has been syntax-checked and
exercised through its API, but has not yet been run in a real browser. QR scanning needs a browser with the BarcodeDetector feature (Chrome/Edge);
on others the vendor types the code. Install the new dependencies with `pip install -r requirements.txt` (adds segno and Pillow).
