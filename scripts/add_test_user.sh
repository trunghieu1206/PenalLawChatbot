#!/bin/bash

# Script to add test user to PenalLawChatbot database
# Usage: ./scripts/add_test_user.sh
# Or with custom DB host: DB_HOST=localhost DB_PORT=5432 ./scripts/add_test_user.sh

set -e

# Configuration
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-penallaw}"
DB_USER="${DB_USER:-postgres}"

echo "Adding test user to PostgreSQL..."
echo "Host: $DB_HOST:$DB_PORT"
echo "Database: $DB_NAME"

# SQL to insert test user
# Password "hieu" hashed with BCrypt (10 rounds)
SQL="
INSERT INTO users (id, email, password_hash, full_name, role, is_active, created_at)
VALUES (
  '550e8400-e29b-41d4-a716-446655440000',
  'hieu@gmail.com',
  '\$2a\$10\$9b1R8o41W6V/wJvvCeQkJetIpWNEQ7B8gzQWJ8y4T1D4H5K2SJ82a',
  'Hiếu Test User',
  'user',
  true,
  now()
)
ON CONFLICT (email) DO UPDATE SET password_hash = excluded.password_hash;

SELECT 'Test user added successfully:' as result;
SELECT id, email, full_name, role, is_active, created_at FROM users WHERE email = 'hieu@gmail.com';
"

# Execute SQL
PGPASSWORD=$DB_PASSWORD psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "$SQL"

echo "✅ Test user 'hieu@gmail.com' added successfully!"
echo "   Login with: email=hieu@gmail.com, password=hieu"
