#!/bin/bash

set -e

python -m pip install --upgrade pip

pip install -r requirements.txt

playwright install chromium

while true
do
    echo "=================================================="
    echo "Starting scrape: $(date)"
    echo "=================================================="

    python reddit_scraper_full_replacement.py --headless

    echo "=================================================="
    echo "Finished scrape: $(date)"
    echo "Sleeping for 3 hours..."
    echo "=================================================="

    sleep 10800
done