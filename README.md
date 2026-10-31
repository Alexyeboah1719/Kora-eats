# KORA EATS - Food Delivery Platform

## Local Development

### Prerequisites
- Python 3.12+
- PostgreSQL
- Node.js (for frontend development)

### Backend Setup
```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your database URL and settings
alembic upgrade head
uvicorn app.main:app --reload
```

### Database Setup
```bash
# Create PostgreSQL database
createdb kora_eats

# Run migrations
cd backend
alembic upgrade head
```

### Environment Variables
Copy `.env.example` to `.env` and configure:
- `DATABASE_URL`: PostgreSQL connection string
- `SECRET_KEY`: Random secret for JWT
- `CORS_ORIGINS`: Frontend URLs (comma-separated)

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

## Railway Deployment

1. Push this code to GitHub
2. Go to [railway.app](https://railway.app)
3. Click "New Project" → "Deploy from GitHub repo"
4. Select this repository
5. Railway will auto-detect Python and deploy
6. Add environment variables from your `.env` file
7. Get your public URL and share with testers!

### Required Environment Variables on Railway
- `DATABASE_URL` (Railway PostgreSQL)
- `SECRET_KEY` (generate a random string)
- `CORS_ORIGINS` (your Railway URL)
- `PAYSTACK_SECRET_KEY` (for payments)
- `PAYSTACK_PUBLIC_KEY` (for payments)
