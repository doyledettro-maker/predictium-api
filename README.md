# Predictium API

FastAPI backend for the Predictium NBA predictions platform.

## Features

- **JWT Authentication**: Cognito token validation with JWKS caching
- **Subscription Management**: Stripe integration for billing
- **Predictions API**: S3-backed prediction storage with caching
- **Plan Enforcement**: Server-side access control (never trust frontend)

## Project Structure

```
predictium-api/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app with CORS, routes
│   ├── config.py            # Pydantic settings from env vars
│   ├── dependencies.py      # Auth dependency (get_current_user)
│   ├── routers/
│   │   ├── health.py        # GET /health
│   │   ├── meta.py          # GET /meta (model info)
│   │   ├── predictions.py   # GET /predictions/latest, /predictions/games/{id}
│   │   ├── auth.py          # GET /auth/me
│   │   ├── billing.py       # GET/POST /billing/* endpoints
│   │   └── webhooks.py      # POST /webhooks/stripe
│   ├── services/
│   │   ├── cognito.py       # JWT validation with python-jose
│   │   ├── stripe_service.py# Stripe customer/subscription management
│   │   └── prediction_service.py # Read predictions from S3
│   ├── models/
│   │   ├── user.py          # User SQLAlchemy model
│   │   ├── subscription.py  # Subscription model
│   │   └── coupon.py        # Coupon + CouponRedemption models
│   └── db/
│       └── database.py      # SQLAlchemy async setup
├── requirements.txt
├── Dockerfile
├── docker-compose.yml       # Local dev with Postgres
└── env.example.txt          # Environment variables template
```

## Quick Start

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- AWS credentials (for Cognito & S3)
- Stripe account (for billing)

### Local Development

1. **Clone and setup**:
   ```bash
   cd predictium-api
   cp env.example.txt .env
   # Edit .env with your credentials
   ```

2. **Start with Docker Compose**:
   ```bash
   docker-compose up -d
   ```

3. **Or run locally**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   uvicorn app.main:app --reload
   ```

4. **Access the API**:
   - API: http://localhost:8000
   - Docs: http://localhost:8000/docs
   - Health: http://localhost:8000/health

### Database Setup

Run the migrations from `../Predictium_Front_End/database/migrations/`:

```bash
# Connect to Postgres and run migrations
psql -h localhost -U postgres -d predictium -f ../Predictium_Front_End/database/migrations/001_users.sql
psql -h localhost -U postgres -d predictium -f ../Predictium_Front_End/database/migrations/002_subscriptions.sql
psql -h localhost -U postgres -d predictium -f ../Predictium_Front_End/database/migrations/003_coupons.sql
psql -h localhost -U postgres -d predictium -f ../Predictium_Front_End/database/migrations/004_coupon_redemptions.sql
psql -h localhost -U postgres -d predictium -f ../Predictium_Front_End/database/migrations/005_updated_at_trigger.sql
```

## API Endpoints

### Public (No Auth)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/meta` | Model metadata |

### Authenticated

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/auth/me` | Current user info + subscription |
| GET | `/predictions/latest` | Today/tomorrow predictions |
| GET | `/predictions/games/{id}` | Game detail (plan-gated) |
| GET | `/billing/subscription` | Subscription status |
| POST | `/billing/redeem-coupon` | Apply coupon code |
| POST | `/billing/create-checkout-session` | Start Stripe checkout |
| POST | `/billing/create-portal-session` | Access billing portal |

### Webhooks

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/webhooks/stripe` | Stripe webhook handler |

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `COGNITO_USER_POOL_ID` | AWS Cognito User Pool ID | Yes |
| `COGNITO_CLIENT_ID` | AWS Cognito App Client ID | Yes |
| `COGNITO_REGION` | AWS region for Cognito | Yes |
| `STRIPE_SECRET_KEY` | Stripe secret key | Yes |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret | Yes |
| `STRIPE_PRO_PRICE_ID` | Stripe Price ID for Pro plan | Yes |
| `STRIPE_ELITE_PRICE_ID` | Stripe Price ID for Elite plan | Yes |
| `S3_PREDICTIONS_BUCKET` | S3 bucket for predictions | Yes |
| `AWS_ACCESS_KEY_ID` | AWS access key | No* |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key | No* |
| `AWS_REGION` | AWS region for S3 | Yes |
| `ALLOWED_ORIGINS` | CORS origins (comma-separated) | Yes |
| `APP_ENV` | Environment (development/production) | No |
| `LOG_LEVEL` | Logging level | No |

*AWS credentials can use IAM roles in production

## Plan Access Control

The API enforces subscription-based access:

| Feature | Free | Pro | Elite |
|---------|------|-----|-------|
| Latest predictions | ✅ | ✅ | ✅ |
| Basic game detail | ✅ | ✅ | ✅ |
| Player adjustments | ❌ | ✅ | ✅ |
| Scenario analysis | ❌ | ✅ | ✅ |
| Prediction history | ❌ | ❌ | ✅ |

**Important**: Never trust frontend for feature gating. All access control is enforced server-side.

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Code Formatting

```bash
black app/
isort app/
```

### Type Checking

```bash
mypy app/
```

## Deployment

### Docker Build

```bash
docker build -t predictium-api:latest .
docker run -p 8000:8000 --env-file .env predictium-api:latest
```

### Production Considerations

- Set `APP_ENV=production` to disable Swagger docs
- Use proper secrets management (AWS Secrets Manager, etc.)
- Configure CloudWatch logging
- Set up ALB health checks pointing to `/health`
- Enable HTTPS termination at load balancer

## License

Proprietary - Predictium Inc.
