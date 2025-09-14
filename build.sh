#!/usr/bin/env bash
# exit on error
set -o errexit

# 1. Install system dependencies required by pymediainfo
apt-get update && apt-get install -y mediainfo

# 2. Install Python dependencies
pip install -r requirements.txt
