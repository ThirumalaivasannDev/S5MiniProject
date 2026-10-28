#!/usr/bin/env bash
# Exit on error
set -o errexit

# Install python dependencies
pip install -r requirements.txt

# Generate synthetic booking data and train the AI pricing model
python generate_data.py
python pricing_engine.py
