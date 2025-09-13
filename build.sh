#!/usr/bin/env bash
# exit on error
set -o errexit

echo "🚀 Starting build process..."

# 1. Update system packages
echo "📦 Updating system packages..."
apt-get update

# 2. Install system dependencies
echo "🔧 Installing system dependencies..."
apt-get install -y \
    mediainfo \
    postgresql-client \
    curl \
    wget \
    build-essential \
    python3-dev \
    libpq-dev

# 3. Upgrade pip and install wheel for faster builds
echo "⚡ Upgrading pip and installing build tools..."
pip install --upgrade pip setuptools wheel

# 4. Install Python dependencies with optimizations
echo "📚 Installing Python dependencies..."
pip install --no-cache-dir -r requirements.txt

# 5. Create necessary directories
echo "📁 Creating directories..."
mkdir -p logs
mkdir -p temp

# 6. Set proper permissions
echo "🔐 Setting permissions..."
chmod +x *.py

# 7. Verify installation
echo "✅ Verifying installation..."
python3 -c "import telebot, psycopg2, psutil, pymediainfo; print('All dependencies installed successfully!')"

echo "🎉 Build completed successfully!"
