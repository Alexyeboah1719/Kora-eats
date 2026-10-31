# KORA EATS - Food Delivery Platform

## Architecture
- **Backend**: FastAPI (hosted on Render)
- **Frontend**: Static HTML/JS (hosted on Vercel)
- **Database**: PostgreSQL (hosted on Render)
- **CDN**: Cloudflare (for performance and security)

## Deployment Guide

### Step 1: Deploy Backend on Render

1. **Push code to GitHub** (already done at: https://github.com/Alexyeboah1719/Kora-eats)

2. **Deploy to Render**:
   - Go to [render.com](https://render.com)
   - Sign up/login with GitHub
   - Click **New** → **Web Service**
   - Connect GitHub and select `Kora-eats` repository
   - **Build Command**: `cd backend && pip install -r requirements.txt`
   - **Start Command**: `cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Click **Create Web Service**

3. **Add PostgreSQL Database**:
   - In Render dashboard: **New** → **PostgreSQL**
   - Name: `kora-eats-db`
   - Click **Create Database**

4. **Configure Environment Variables** (in web service):
   ```
   DATABASE_URL = [from PostgreSQL database → Connect → Internal Database URL]
   SECRET_KEY = kora_eats_secret_2024
   CORS_ORIGINS = https://your-vercel-app.vercel.app
   ENVIRONMENT = production
   DEBUG = false
   RUN_MIGRATIONS = true
   PAYSTACK_SECRET_KEY = [your Paystack secret key]
   PAYSTACK_PUBLIC_KEY = [your Paystack public key]
   ```

5. **Run Migrations**:
   - After adding DATABASE_URL, Render will auto-redeploy
   - Or manually trigger redeploy
   - Migrations will run automatically via RUN_MIGRATIONS=true

6. **Copy Render Backend URL** (looks like: `https://kora-eats.onrender.com`)

### Step 2: Deploy Frontend on Vercel

1. **Deploy to Vercel**:
   - Go to [vercel.com](https://vercel.com)
   - Sign up/login with GitHub
   - Click **Add New Project**
   - Import the `Kora-eats` repository
   - Set root to `frontend` folder
   - Click **Deploy**

2. **Add Environment Variable** (in Vercel project settings):
   - Variable name: `RAILWAY_BACKEND_URL` → Change to `RENDER_BACKEND_URL`
   - Value: Your Render backend URL (e.g., `https://kora-eats.onrender.com`)

3. **Redeploy** after adding the variable

4. **Copy Vercel Frontend URL** (looks like: `https://kora-eats.vercel.app`)

### Step 3: Update API Client for Render

I'll update the frontend to use the Render backend URL.

### Step 4: Enable Cloudflare

1. **Create Cloudflare Account**:
   - Go to [cloudflare.com](https://cloudflare.com)
   - Sign up for free account

2. **Add Your Site**:
   - Click **Add a Site**
   - Enter your Vercel domain (e.g., `kora-eats.vercel.app`)
   - Cloudflare will scan your DNS records

3. **Update Nameservers**:
   - Cloudflare will give you nameservers (e.g., `ns1.cloudflare.com`)
   - Go to your domain registrar (where you bought your domain)
   - Update nameservers to Cloudflare's nameservers
   - Wait 24-48 hours for propagation

4. **Configure SSL**:
   - Cloudflare will auto-configure SSL for free
   - Set SSL mode to **Full** (not Flexible) in Cloudflare SSL/TLS settings

5. **Update CORS on Render**:
   - Back to Render → Web service → **Environment**
   - Update `CORS_ORIGINS` to include your Cloudflare domain
   - Redeploy Render

### Step 5: Test the Setup

1. Access your site via Cloudflare URL
2. Test all features:
   - User registration/login
   - Food ordering
   - Payment processing
   - Rider operations
   - Vendor dashboard

## Demo Accounts
- Customer: `customer@demo.kora` / `Demo!2345`
- Vendor: `vendor@demo.kora` / `Demo!2345`
- Rider: `rider@demo.kora` / `Demo!2345`
- Admin: `admin@demo.kora` / `Demo!2345`

## Features
- AI-powered food recommendations
- Cash on Delivery (COD)
- Vendor and rider dashboards
- Wallet and payout system
- Real-time order tracking
- Natural language AI assistant
