# KORA EATS - Food Delivery Platform

## Architecture
- **Backend**: FastAPI (hosted on Railway)
- **Frontend**: Static HTML/JS (hosted on Vercel)
- **Database**: PostgreSQL (hosted on Railway)

## Deployment Guide

### Backend on Railway

1. **Push code to GitHub** (already done at: https://github.com/Alexyeboah1719/Kora-eats)

2. **Deploy to Railway**:
   - Go to [railway.app](https://railway.app)
   - Sign up/login with GitHub
   - Click **New Project** → **Deploy from GitHub repo**
   - Select `Kora-eats` repository
   - Railway will auto-detect Python and deploy

3. **Add PostgreSQL Database**:
   - In Railway project: **+ New Service** → **Database** → **PostgreSQL**
   - Copy the `DATABASE_URL` from the database service

4. **Configure Environment Variables** (in app service):
   ```
   DATABASE_URL = [paste from PostgreSQL service]
   SECRET_KEY = kora_eats_secret_2024
   CORS_ORIGINS = https://your-vercel-app.vercel.app
   ENVIRONMENT = production
   DEBUG = false
   RUN_MIGRATIONS = true
   PAYSTACK_SECRET_KEY = [your Paystack secret key]
   PAYSTACK_PUBLIC_KEY = [your Paystack public key]
   ```

5. **Redeploy** after adding variables

6. **Copy Railway Backend URL** (looks like: `https://kora-eats-production.up.railway.app`)

### Frontend on Vercel

1. **Deploy to Vercel**:
   - Go to [vercel.com](https://vercel.com)
   - Sign up/login with GitHub
   - Click **Add New Project**
   - Import the `Kora-eats` repository
   - Select **Frontend** directory (if prompted, or set root to `frontend`)
   - Click **Deploy**

2. **Add Environment Variable** (in Vercel project settings):
   - Variable name: `RAILWAY_BACKEND_URL`
   - Value: Your Railway backend URL (e.g., `https://kora-eats-production.up.railway.app`)

3. **Redeploy** after adding the variable

4. **Copy Vercel Frontend URL** (looks like: `https://kora-eats.vercel.app`)

### Update CORS on Railway

After getting your Vercel URL:
1. Go to Railway app service → **Variables**
2. Update `CORS_ORIGINS` to include your Vercel URL
3. Redeploy Railway

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
