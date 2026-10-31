# Deploying KORA EATS

This guide takes you from "works on my laptop" to a live site. Read it top to bottom once before you start.

## 1. What you need
|| Thing | Why | Where |
||---|---|---|
|| A domain name | https is required (browsers only allow camera, location and install-as-app on https) | any registrar |
|| A server or app host | runs the Docker image | a VPS (Hetzner, DigitalOcean), or Render / Fly.io |
|| PostgreSQL 14+ | the database | managed: Neon, Supabase, Render Postgres (or the one in `docker-compose.yml`) |
|| Paystack account (Ghana) | real payments | paystack.com, start with TEST keys |
|| Resend account + verified domain | emails (order receipts, OTP codes) | resend.com |
|| A persistent disk | uploaded photos live on disk | the host's "volume" feature |

## 2. Configure
Copy `backend/.env.production.example` and fill every blank. The important ones:

- `SECRET_KEY`: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Keep it secret and never change it casually (it signs logins).
- `DATABASE_URL`: for **Neon** use `postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require` (the `sslmode=require` part is mandatory).
- `FRONTEND_BASE_URL` and `CORS_ORIGINS`: your real `https://` address. The app refuses to start in production with `http://`, `*` or a missing secret. That is deliberate.
- `PAYMENT_PROVIDER=paystack` and `PAYSTACK_SECRET_KEY` (the app refuses to start in production with the simulator).
- `TRUSTED_PROXY_HOPS`: how many proxies sit in front of the app (1 on most hosts). Without it every visitor looks like the same IP to the rate limiter.

## 3. Run it
**Option A: Docker on a VPS**
```bash
cp backend/.env.production.example .env.production      # then edit it
docker compose up --build -d
```
Put Caddy or nginx in front for https (Caddy: `your-domain.com { reverse_proxy localhost:8000 }` gets a certificate automatically).

**Option B: a managed host (Render, Fly.io...)**: point it at this repo's `Dockerfile`, add the environment variables from step 2, attach a persistent disk mounted at `/data`, and set the health check path to `/api/health`. The container runs the database migrations on every start (`alembic upgrade head`), so deploying a new version updates the database automatically.

## 4. First-time setup: create your real admin account
There is **no "sign up as admin" on the website, by design**: anyone who tries to register with an admin role is simply made a normal customer.
The only way to create an admin is this command, run on the server, which asks for the password at the keyboard (it is never typed on the
command line, so it doesn't end up in your shell history or logs):

```bash
python -m scripts.create_admin admin@yourdomain.com "Your Full Name"
Password:            (typing is hidden)
Confirm password:
ADMIN ready: admin@yourdomain.com
```
The password must be **at least 12 characters** with a letter and a number. A short sentence is easy to remember and hard to guess, for example
`Correct horse battery 7`, but use your own, and keep it in a password manager.

**Where to run it** (the app must already be deployed once, so the database tables exist; the start script does that automatically):
- **Docker on a VPS:** `docker compose exec app python -m scripts.create_admin admin@yourdomain.com "Your Full Name"`
- **Render / Fly.io:** open the service's **Shell** (Render: the Shell tab; Fly: `fly ssh console`) and run the same
  `python -m scripts.create_admin ...` inside it. The environment variables (including `DATABASE_URL`) are already there.
- **From your own laptop, pointed at the production database:** in the `backend/` folder, in a terminal you will close afterwards:
  `export DATABASE_URL="postgresql+psycopg://...your Neon/production URL...?sslmode=require"` then run the command, then close the terminal.
  Be careful: this writes to the REAL database, so double-check the URL.

**Then:** go to `https://YOUR-DOMAIN/auth/login.html`, sign in, and you land on the admin dashboard.

**Good practice**
- Use a dedicated address such as `admin@yourdomain.com`, not your personal one, and one with email you can actually receive.
- Create a **second admin** for a trusted colleague so you are never locked out. Keep the number of admins small.
- Lost the password? Run the same command again with the same email: it sets a new password (and makes sure the account is an admin).
- Check there are no demo accounts, which must never exist in production (`seed_dev_data` refuses to run there, but verify):
  `SELECT email FROM users WHERE email LIKE '%@demo.kora';` should return no rows. Also confirm the admin list:
  `SELECT email FROM users WHERE role = 'ADMIN';` should show only people you know.
- There is **no two-factor login yet**. Until it is added, the protections are the long password, HTTPS only, login rate-limiting (10 attempts per
  5 minutes) and a short list of admins. Treat the admin password like the keys to a till.

## 5. Connect Paystack
1. Dashboard -> Settings -> API Keys: copy the **test** secret key into `PAYSTACK_SECRET_KEY`.
2. Same page -> Webhook URL: `https://YOUR-DOMAIN/api/payments/webhook/paystack`.
3. Place a test order with a Paystack test number, pay, and confirm the order becomes PAID. The server verifies every payment with Paystack itself; a redirect alone never marks an order paid.
4. Only then switch to the live key. Mobile Money is enabled first; add `CARD` / `BANK_TRANSFER` to `ENABLED_PAYMENT_METHODS` once Paystack has enabled them for you.

## 6. Before you tell anyone
- [ ] Site loads on https and the browser offers "Install app".
- [ ] `https://YOUR-DOMAIN/api/health` says ok.
- [ ] Sign-up email arrives (check spam; verify your Resend domain).
- [ ] A test order works end to end: customer, vendor, rider, payment.
- [ ] `CSP_MODE=report-only` for the first week: watch the logs for `/api/csp-report` entries, fix anything legitimate, then set `CSP_MODE=enforce`.
- [ ] Database backups are on (Neon and most hosts do this; check the retention period).
- [ ] You can restore: try restoring a backup into a scratch database once.
- [ ] The persistent disk is mounted at `/data` (upload a photo, redeploy, check the photo is still there).

## 7. Data protection (Ghana Data Protection Act, 2012)
KORA EATS collects names, phone numbers, delivery addresses and order history. Out of the box it already:
- lets customers see what the AI learned about them (`/food-profile.html`), switch personalisation off, and erase their history;
- keeps recommendation tracking for `REC_EVENT_RETENTION_DAYS` (90) and then deletes it automatically;
- strips GPS/camera data from uploaded photos, and shows verification documents only to admins;
- never logs email bodies, OTP codes or secrets in production.

You still need to: publish a privacy policy, register with the Data Protection Commission as the law requires, and decide how long you keep order history.

## 8. Known limits (be aware, not afraid)
- **Uploads are on local disk.** One server only, with a persistent volume. For several servers, move uploads to object storage (S3-compatible) first.
- **The rate limiter is in memory.** With `WEB_CONCURRENCY=2` each worker counts separately, so limits are roughly doubled. Fine for launch; move to Redis when you scale out.
- **Background jobs** (expiring unpaid orders, review reminders, scheduled orders) run inside the web process and use a database lock so only one worker does each job.
- **Paystack and Resend are unproven until you test them with real keys.** The code was tested against fakes of their documented APIs.
- **QR scanning** works in Chrome/Edge; other browsers fall back to typing the code.

## 9. Updating
Push the new code, rebuild, restart. Migrations run on start. If something goes wrong, roll back the image; migrations are written to be additive, but check `alembic downgrade` notes in each migration before downgrading a live database.
