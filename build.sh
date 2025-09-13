#!/usr/bin/env bash
# exit on error
set -o errexit

echo "🚀 Starting build process..."

# 1. Upgrade pip and install wheel for faster builds
echo "⚡ Upgrading pip and installing build tools..."
pip install --upgrade pip setuptools wheel

# 2. Install Python dependencies with optimizations
echo "📚 Installing Python dependencies..."
pip install --no-cache-dir -r requirements.txt

# 3. Create necessary directories
echo "📁 Creating directories..."
mkdir -p logs
mkdir -p temp

# 4. Set proper permissions
echo "🔐 Setting permissions..."
chmod +x *.py

# 5. Verify installation
echo "✅ Verifying installation..."
python3 -c "import telebot, psycopg2, psutil; print('Core dependencies installed successfully!')"

echo "🎉 Build completed successfully!"
