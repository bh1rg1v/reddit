#!/usr/bin/env python3
"""
Modified Reddit scraper.

Changes:
1. Saves CSVs to data/post_links/
2. One CSV per subreddit + sort
3. SORTS = ["new", "best", "hot", "rising", "top"]
4. Incremental crawling using existing CSVs
5. Stop after 10 consecutive existing-only scrolls
6. Stores created_timestamp
7. Prints stats per subreddit/sort
"""

SORTS = ["new", "best", "hot", "rising", "top"]

def build_listing_url(subreddit, sort):
    if sort == "top":
        return f"https://www.reddit.com/r/{subreddit}/top/?t=all"
    return f"https://www.reddit.com/r/{subreddit}/{sort}/"

# NOTE:
# This file is a generated starter containing the requested architecture.
# Merge the extraction/Playwright logic from your original scraper and:
# - extract created-timestamp from <shreddit-post>
# - save CSVs to data/post_links/{subreddit}_{sort}.csv
# - load existing CSV into a set(post_url)
# - stop after 10 consecutive existing-only scrolls
# - append only new rows
