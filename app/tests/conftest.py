"""Test defaults: the settings the app refuses to start without.

Only fills what is unset, so a real environment always wins, and the values
here are dummies: no test reaches Cognito, Stripe, S3 or a real database.
"""

import os

for key, value in {
    "COGNITO_USER_POOL_ID": "us-east-1_test",
    "COGNITO_CLIENT_ID": "test-client",
    "STRIPE_SECRET_KEY": "sk_test_dummy",
    "STRIPE_WEBHOOK_SECRET": "whsec_dummy",
}.items():
    os.environ.setdefault(key, value)
