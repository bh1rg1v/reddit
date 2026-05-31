#!/bin/bash

while true
do
    echo "Running scraper..."
    python reddit_scraper_full_replacement.py
    echo "Sleeping 3 hours..."
    sleep 10800
done