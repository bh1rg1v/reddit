
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import pathlib
import re
import urllib.parse
from typing import Any
from collections import defaultdict

PROJECT_ROOT = pathlib.Path("D:/github/reddit")

SUBREDDITS = ["IndianStockMarket", "IndianAlgoTrading", "wallstreetbets", "stocks", "investing", "NSEbets"]
SORTS = ["new", "best", "hot", "rising", "top"]

DEFAULT_OUTPUT_DIR = str(PROJECT_ROOT / "data")
POST_LINKS_DIR = "post_links"

DEFAULT_MAX_SCROLLS = 250
DEFAULT_DELAY_MS = 2000
DEFAULT_BREAK_EVERY_SCROLLS = 1000
DEFAULT_BREAK_SECONDS = 120

time_taken = defaultdict(float)

CSV_FIELDS = [
    "subreddit",
    "sort",
    "post_id",
    "post_url",
    "created_timestamp",
    "fetched_at_utc",
]

LAST_FETCHED_CSV = (
    PROJECT_ROOT
    / "data"
    / "last_fetched.csv"
)

LAST_FETCHED_FIELDS = [
    "subreddit",
    "last_fetched_utc",
    "time_taken_seconds",
]

def subreddit_exists_in_last_fetched(
    subreddit,
):

    data = load_last_fetched()

    return subreddit in data

def load_last_fetched():

    data = {}

    if not LAST_FETCHED_CSV.exists():
        return data

    with LAST_FETCHED_CSV.open(
        "r",
        encoding="utf-8",
        newline=""
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            data[row["subreddit"]] = {
                "last_fetched_utc":
                    row["last_fetched_utc"],
                "time_taken_seconds":
                    float(
                        row["time_taken_seconds"]
                    ),
            }

    return data

def save_last_fetched(data):

    LAST_FETCHED_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with LAST_FETCHED_CSV.open(
        "w",
        encoding="utf-8",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=LAST_FETCHED_FIELDS,
        )

        writer.writeheader()

        for subreddit, info in data.items():

            writer.writerow(
                {
                    "subreddit": subreddit,
                    "last_fetched_utc":
                        info["last_fetched_utc"],
                    "time_taken_seconds":
                        info["time_taken_seconds"],
                }
            )

def should_scrape_subreddit(
    subreddit,
    hours=3,
):

    data = load_last_fetched()

    if subreddit not in data:
        return True

    last_fetched = dt.datetime.fromisoformat(
        data[subreddit][
            "last_fetched_utc"
        ]
    )

    elapsed = (
        dt.datetime.now(
            dt.timezone.utc
        )
        - last_fetched
    ).total_seconds()

    return elapsed >= hours * 3600

def update_last_fetched(
    subreddit,
    time_taken_seconds,
):

    data = load_last_fetched()

    data[subreddit] = {
        "last_fetched_utc":
            dt.datetime.now(
                dt.timezone.utc
            ).isoformat(),
        "time_taken_seconds":
            round(
                time_taken_seconds,
                2,
            ),
    }

    save_last_fetched(data)


def build_listing_url(subreddit: str, sort: str) -> str:
    if sort == "top":
        return f"https://www.reddit.com/r/{subreddit}/top/?t=all"
    return f"https://www.reddit.com/r/{subreddit}/{sort}/"


def resolve_project_path(path_value: str) -> pathlib.Path:
    path = pathlib.Path(path_value)
    return path if path.is_absolute() else PROJECT_ROOT / path


async def extract_visible_posts(page: Any, base_url: str):
    return await page.evaluate(
        """
        ({baseUrl}) => {
            const posts = [];
            const seen = new Set();

            for (const post of document.querySelectorAll("shreddit-post")) {

                const permalink =
                    post.getAttribute("permalink") ||
                    post.getAttribute("content-href") ||
                    "";

                if (!permalink.includes("/comments/"))
                    continue;

                const url = new URL(permalink, baseUrl);
                url.search = "";
                url.hash = "";

                const postId = (post.getAttribute("id") || "")
                    .replace("t3_", "");

                const created =
                    post.getAttribute("created-timestamp") || "";

                if (seen.has(url.href))
                    continue;

                seen.add(url.href);

                posts.push({
                    post_id: postId,
                    post_url: url.href,
                    created_timestamp: created,
                });
            }

            return posts;
        }
        """,
        {"baseUrl": base_url},
    )


def load_existing_links(csv_path: pathlib.Path):
    existing = set()

    if not csv_path.exists():
        return existing

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            existing.add(row["post_url"])

    return existing


def append_rows(csv_path: pathlib.Path, rows):
    file_exists = csv_path.exists()

    with csv_path.open(
        "a",
        encoding="utf-8",
        newline="",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=CSV_FIELDS,
        )

        if not file_exists:
            writer.writeheader()

        writer.writerows(rows)


async def scroll_page(
    page,
    base_url,
    existing_links,
    max_scrolls,
    delay_ms,
    break_every_scrolls,
    break_seconds,
):

    discovered = {}
    consecutive_existing_only = 0
    consecutive_no_new_links = 0

    for scroll_index in range(
        1,
        max_scrolls + 1,
    ):

        posts = await extract_visible_posts(
            page,
            base_url,
        )

        new_this_scroll = 0
        existing_this_scroll = 0

        for post in posts:

            url = post["post_url"]

            if url in discovered:
                continue

            discovered[url] = post

            if url in existing_links:
                existing_this_scroll += 1
            else:
                new_this_scroll += 1

        print(
            f"scroll={scroll_index} "
            f"new={new_this_scroll} "
            f"existing={existing_this_scroll} "
            f"seen={len(discovered)}",
            flush=True,
        )

        # Condition 1:
        # Only existing links visible for 10 consecutive scrolls

        if (
            existing_this_scroll > 0
            and new_this_scroll == 0
        ):
            consecutive_existing_only += 1
        else:
            consecutive_existing_only = 0


        # Condition 2:
        # No new links discovered for 10 consecutive scrolls

        if new_this_scroll == 0:
            consecutive_no_new_links += 1
        else:
            consecutive_no_new_links = 0


        if consecutive_existing_only >= 10:
            print(
                "Stopping. Reached historical region."
            )
            break


        if consecutive_no_new_links >= 10:
            print(
                "Stopping. No new links found "
                "for 10 consecutive scrolls."
            )
            break

        await page.evaluate(
            "window.scrollTo(0, document.body.scrollHeight)"
        )

        await page.wait_for_timeout(
            delay_ms
        )

        if (
            break_every_scrolls > 0
            and scroll_index % break_every_scrolls == 0
        ):
            await asyncio.sleep(
                break_seconds
            )

    return list(discovered.values())

async def scrape_sort(
    context,
    subreddit,
    sort,
    csv_path,
    args,
):
    
    start_time = dt.datetime.now()

    url = build_listing_url(
        subreddit,
        sort,
    )

    existing_links = load_existing_links(
        csv_path
    )

    page = await context.new_page()

    try:

        print(
            f"\n{'=' * 80}\n"
            f"r/{subreddit} [{sort}]\n"
            f"{'=' * 80}"
        )

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        await page.wait_for_timeout(
            args.delay_ms
        )

        posts = await scroll_page(
            page=page,
            base_url=url,
            existing_links=existing_links,
            max_scrolls=args.max_scrolls,
            delay_ms=args.delay_ms,
            break_every_scrolls=args.break_every_scrolls,
            break_seconds=args.break_seconds,
        )

        fetched_at = dt.datetime.now(
            dt.timezone.utc
        ).isoformat()

        new_rows = []

        for post in posts:

            if (
                post["post_url"]
                in existing_links
            ):
                continue

            new_rows.append(
                {
                    "subreddit": subreddit,
                    "sort": sort,
                    "post_id": post["post_id"],
                    "post_url": post["post_url"],
                    "created_timestamp": post["created_timestamp"],
                    "fetched_at_utc": fetched_at,
                }
            )

        append_rows(
            csv_path,
            new_rows,
        )

        print("\nSTATS")
        print(
            f"Existing Links : {len(existing_links)}"
        )
        print(
            f"New Links      : {len(new_rows)}"
        )
        print(
            f"Total Seen     : {len(posts)}"
        )

    finally:
        await page.close()

        end_time = dt.datetime.now()
        duration = (end_time - start_time).total_seconds()

        time_taken[subreddit] += duration

def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
    )
    parser.add_argument(
        "--max-scrolls",
        type=int,
        default=DEFAULT_MAX_SCROLLS,
    )
    parser.add_argument(
        "--delay-ms",
        type=int,
        default=DEFAULT_DELAY_MS,
    )
    parser.add_argument(
        "--break-every-scrolls",
        type=int,
        default=DEFAULT_BREAK_EVERY_SCROLLS,
    )
    parser.add_argument(
        "--break-seconds",
        type=int,
        default=DEFAULT_BREAK_SECONDS,
    )
    parser.add_argument(
        "--headless",
        action="store_true",
    )

    return parser.parse_args()


async def main_async():

    args = parse_args()

    from playwright.async_api import (
        async_playwright,
    )

    output_dir = resolve_project_path(
        args.output_dir
    )

    csv_dir = (
        output_dir
        / POST_LINKS_DIR
    )

    csv_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=args.headless
        )

        context = await browser.new_context()

        try:

            for subreddit in SUBREDDITS:

                if not should_scrape_subreddit(subreddit):

                    print(
                        f"Skipping r/{subreddit}. "
                        f"Fetched within last 3 hours."
                    )

                    continue

                time_taken[subreddit] = 0

                sorts_to_scrape = (
                    ["new"]
                    if subreddit_exists_in_last_fetched(
                        subreddit
                    )
                    else SORTS
                )

                for sort in sorts_to_scrape:

                    csv_path = (
                        csv_dir
                        / subreddit
                        / f"{subreddit}_{sort}.csv"
                    )

                    csv_path.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                    try:

                        await scrape_sort(
                            context=context,
                            subreddit=subreddit,
                            sort=sort,
                            csv_path=csv_path,
                            args=args,
                        )

                    except Exception as e:

                        print(
                            f"Error scraping r/{subreddit} "
                            f"[{sort}]: {e}"
                        )

                update_last_fetched(
                    subreddit=subreddit,
                    time_taken_seconds=time_taken[subreddit],
                )

            for subreddit, duration in time_taken.items():
                print(
                    f"Total time taken for r/{subreddit}: "
                    f"{duration:.2f} seconds"
                )

        finally:

            await context.close()
            await browser.close()


def main():
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
