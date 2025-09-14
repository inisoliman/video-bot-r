#!/usr/bin/env bash
# exit on error
set -o errexit

echo "🚀 Starting build process..."

# 1. Install system dependencies
echo "🔧 Installing system dependencies..."
apt-get update
apt-get install -y ffmpeg

# 2. Upgrade pip and install wheel for faster builds
echo "⚡ Upgrading pip and installing build tools..."
pip install --upgrade pip setuptools wheel

# 3. Install Python dependencies with optimizations
echo "📚 Installing Python dependencies..."
pip install --no-cache-dir -r requirements.txt

# 4. Create necessary directories
echo "📁 Creating directories..."
mkdir -p logs
mkdir -p temp

# 5. Set proper permissions
echo "🔐 Setting permissions..."
chmod +x *.py

# 6. Verify installation
echo "✅ Verifying installation..."
python3 -c "import telebot, psycopg2, psutil, pymediainfo; print('All dependencies installed successfully!')"

echo "🎉 Build completed successfully!"
