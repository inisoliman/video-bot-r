#!/bin/bash

# Video Bot R - Build and Deployment Script

set -e

echo "🚀 Building Video Bot R..."

# Check if we're in the right directory
if [ ! -f "requirements.txt" ]; then
    echo "❌ Error: requirements.txt not found. Please run from the rebuilt_bot directory."
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    python -m venv .venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source .venv/bin/activate

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo "📚 Installing dependencies..."
pip install -r requirements.txt

# Run database patches if database is configured
if [ -n "$DATABASE_URL" ]; then
    echo "🛠️  Applying database compatibility patches..."
    python scripts/db_compat_patch.py
else
    echo "⚠️  DATABASE_URL not set, skipping database patches"
fi

# Run tests if they exist
if [ -d "tests" ]; then
    echo "🧪 Running tests..."
    python -m pytest tests/ -v
fi

# Build success
echo "✅ Build completed successfully!"
echo ""
echo "To run the bot locally:"
echo "  source .venv/bin/activate"
echo "  flask --app app.webhook run --host 0.0.0.0 --port 10000"
echo ""
echo "To set webhook:"
echo "  curl http://localhost:10000/set_webhook"
echo ""
echo "Available scripts:"
echo "  python scripts/db_audit.py --help"
echo "  python scripts/history_cleaner.py --help"
echo "  python scripts/update_metadata.py --help"