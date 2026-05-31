#!/usr/bin/env python3
"""
Render multiple Reddit listing pages, save their HTML,
and extract post links.

Uses Playwright only.
No Reddit API.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import pathlib
import re
import urllib.parse
from html.parser import HTMLParser
from typing import Any


# PROJECT_ROOT = pathlib.Path("D:/github/reddit")
PROJECT_ROOT = pathlib.Path.cwd()

SUBREDDITS = ["IndianStockMarket", "IndianAlgoTrading"]

DEFAULT_SORT = "new"

DEFAULT_OUTPUT_DIR = str(PROJECT_ROOT / "data")
DEFAULT_MAX_SCROLLS = 10_000_000_000
DEFAULT_DELAY_MS = 2000
DEFAULT_BREAK_EVERY_SCROLLS = 1000
DEFAULT_BREAK_SECONDS = 120

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() != "a":
            return

        attr_map = {
            key.lower(): value or ""
            for key, value in attrs
        }

        href = attr_map.get("href", "")

        if href:
            self.links.append(href)


class RedditPostParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.posts: list[dict[str, str]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:

        if tag.lower() != "shreddit-post":
            return

        self.posts.append(
            {
                key.lower(): value or ""
                for key, value in attrs
            }
        )


def build_listing_url(
    subreddit: str,
    sort: str = DEFAULT_SORT,
) -> str:
    return f"https://www.reddit.com/r/{subreddit}/{sort}/"


def safe_filename(value: str) -> str:
    cleaned = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "_",
        value.strip(),
    )

    return cleaned.strip("_") or "reddit_listing"


def resolve_project_path(path_value: str) -> pathlib.Path:
    path = pathlib.Path(path_value)

    if path.is_absolute():
        return path

    return PROJECT_ROOT / path


def subreddit_from_listing_url(url: str) -> str:
    match = re.search(
        r"/r/([^/?#]+)/",
        url,
        re.IGNORECASE,
    )

    return match.group(1).lower() if match else ""


def normalize_post_url(
    url: str,
    base_url: str,
) -> str:

    absolute = urllib.parse.urljoin(
        base_url,
        url,
    )

    return absolute.split("?")[0].split("#")[0]


def extract_post_links(
    html: str,
    base_url: str,
) -> list[str]:

    links: list[str] = []
    seen: set[str] = set()

    pattern = re.compile(
        r"/r/[^/]+/comments/[^/?#]+",
        re.IGNORECASE,
    )

    listing_subreddit = subreddit_from_listing_url(
        base_url
    )

    post_parser = RedditPostParser()
    post_parser.feed(html)

    for post in post_parser.posts:

        permalink = (
            post.get("permalink")
            or post.get("content-href")
            or ""
        )

        if not pattern.search(permalink):
            continue

        subreddit = (
            post.get("subreddit-name")
            or post.get(
                "subreddit-prefixed-name",
                "",
            ).removeprefix("r/")
        ).lower()

        if (
            listing_subreddit
            and subreddit
            and subreddit != listing_subreddit
        ):
            continue

        absolute = normalize_post_url(
            permalink,
            base_url,
        )

        if absolute in seen:
            continue

        seen.add(absolute)
        links.append(absolute)

    if links:
        return links

    parser = LinkParser()
    parser.feed(html)

    for href in parser.links:

        if not pattern.search(href):
            continue

        absolute = normalize_post_url(
            href,
            base_url,
        )

        if (
            listing_subreddit
            and f"/r/{listing_subreddit}/comments/"
            not in absolute.lower()
        ):
            continue

        if absolute in seen:
            continue

        seen.add(absolute)
        links.append(absolute)

    return links


async def extract_visible_post_links(
    page: Any,
    base_url: str,
) -> list[str]:

    listing_subreddit = subreddit_from_listing_url(
        base_url
    )

    return await page.evaluate(
        """
        ({ baseUrl, listingSubreddit }) => {

          const normalize = (value) => {
            if (!value) return "";

            try {
              const url = new URL(value, baseUrl);
              url.search = "";
              url.hash = "";
              return url.href;
            } catch {
              return "";
            }
          };

          const isListingPost = (url) => {
            if (!url) return false;

            return url
              .toLowerCase()
              .includes(`/r/${listingSubreddit}/comments/`);
          };

          const links = [];
          const seen = new Set();

          for (const post of document.querySelectorAll("shreddit-post")) {

            const permalink =
              post.getAttribute("permalink") ||
              post.getAttribute("content-href") ||
              "";

            const url = normalize(permalink);

            if (!isListingPost(url) || seen.has(url))
              continue;

            seen.add(url);
            links.push(url);
          }

          if (links.length)
            return links;

          for (const anchor of document.querySelectorAll('a[href*="/comments/"]')) {

            const url = normalize(
              anchor.getAttribute("href") || ""
            );

            if (!isListingPost(url) || seen.has(url))
              continue;

            seen.add(url);
            links.push(url);
          }

          return links;
        }
        """,
        {
            "baseUrl": base_url,
            "listingSubreddit": listing_subreddit,
        },
    )


async def scroll_page(
    page: Any,
    base_url: str,
    max_scrolls: int,
    delay_ms: int,
    break_every_scrolls: int,
    break_seconds: int,
) -> list[str]:

    links: list[str] = []
    seen_links: set[str] = set()

    no_new_links = 0

    for scroll_index in range(
        1,
        max_scrolls + 1,
    ):

        visible_links = await extract_visible_post_links(
            page,
            base_url,
        )

        new_count = 0

        for link in visible_links:

            if link in seen_links:
                continue

            seen_links.add(link)
            links.append(link)
            new_count += 1

        height = await page.evaluate(
            "document.body.scrollHeight"
        )

        print(
            f"[{subreddit_from_listing_url(base_url)}] "
            f"scroll={scroll_index} "
            f"height={height} "
            f"new={new_count} "
            f"total={len(links)}",
            flush=True,
        )

        if new_count == 0:
            no_new_links += 1
        else:
            no_new_links = 0

        if no_new_links >= 10:
            print(
                "Stopping early. "
                "No new links for 10 scrolls.",
                flush=True,
            )
            break

        await page.evaluate(
            """
            window.scrollTo(
                0,
                document.body.scrollHeight
            )
            """
        )

        await page.wait_for_timeout(delay_ms)

        if (
            break_every_scrolls > 0
            and scroll_index % break_every_scrolls == 0
            and scroll_index < max_scrolls
        ):
            print(
                f"Cooling down for "
                f"{break_seconds}s",
                flush=True,
            )

            await asyncio.sleep(
                break_seconds
            )

    visible_links = await extract_visible_post_links(
        page,
        base_url,
    )

    for link in visible_links:

        if link not in seen_links:
            seen_links.add(link)
            links.append(link)

    return links


async def scrape_subreddit(
    context,
    subreddit: str,
    output_dir: pathlib.Path,
    args,
):

    url = build_listing_url(subreddit)

    subreddit_dir = output_dir / subreddit
    subreddit_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    page = await context.new_page()

    try:

        print(
            f"\n{'=' * 80}\n"
            f"Scraping r/{subreddit}\n"
            f"{'=' * 80}",
            flush=True,
        )

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60_000,
        )

        await page.wait_for_timeout(
            args.delay_ms
        )

        observed_links = await scroll_page(
            page,
            url,
            args.max_scrolls,
            args.delay_ms,
            args.break_every_scrolls,
            args.break_seconds,
        )

        html = await page.content()

        html_path = (
            subreddit_dir
            / f"{subreddit}_rendered.html"
        )

        links_path = (
            subreddit_dir
            / f"{subreddit}_post_links.txt"
        )

        html_path.write_text(
            html,
            encoding="utf-8",
        )

        final_html_links = extract_post_links(
            html,
            url,
        )

        links = []
        seen = set()

        for link in [
            *observed_links,
            *final_html_links,
        ]:
            if link not in seen:
                seen.add(link)
                links.append(link)

        with links_path.open(
            "w",
            encoding="utf-8",
        ) as handle:

            handle.write(
                f"Subreddit: {subreddit}\n"
            )

            handle.write(
                f"Fetched at UTC: "
                f"{dt.datetime.now(dt.timezone.utc).isoformat()}\n\n"
            )

            for link in links:
                handle.write(
                    link + "\n"
                )

        print(
            f"Collected "
            f"{len(links)} links "
            f"from r/{subreddit}",
            flush=True,
        )

        return links

    finally:
        await page.close()


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

    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
    )

    return parser.parse_args()


async def main_async():

    args = parse_args()

    try:
        from playwright.async_api import (
            async_playwright,
        )

    except ImportError:

        print(
            "Install Playwright:\n"
            "python -m pip install playwright\n"
            "python -m playwright install chromium"
        )

        return 1

    output_dir = resolve_project_path(
        args.output_dir
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=args.headless
        )

        context = await browser.new_context(
            user_agent=args.user_agent,
            locale="en-US",
            viewport={
                "width": 1366,
                "height": 900,
            },
        )

        await context.add_cookies(
            [
                {
                    "name": "over18",
                    "value": "1",
                    "domain": ".reddit.com",
                    "path": "/",
                }
            ]
        )

        try:

            combined_rows = []

            for subreddit in SUBREDDITS:

                links = await scrape_subreddit(
                    context=context,
                    subreddit=subreddit,
                    output_dir=output_dir,
                    args=args,
                )

                fetched_at = (
                    dt.datetime.now(
                        dt.timezone.utc
                    ).isoformat()
                )

                for idx, link in enumerate(
                    links,
                    start=1,
                ):
                    combined_rows.append(
                        {
                            "subreddit": subreddit,
                            "fetched_at_utc": fetched_at,
                            "post_index": idx,
                            "post_url": link,
                        }
                    )

            csv_path = (
                output_dir
                / "all_post_links.csv"
            )

            with csv_path.open(
                "w",
                encoding="utf-8",
                newline="",
            ) as handle:

                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "subreddit",
                        "fetched_at_utc",
                        "post_index",
                        "post_url",
                    ],
                )

                writer.writeheader()
                writer.writerows(
                    combined_rows
                )

            print(
                f"\nFinished scraping "
                f"{len(SUBREDDITS)} subreddits"
            )

            print(
                f"Total links: "
                f"{len(combined_rows)}"
            )

        finally:

            await context.close()
            await browser.close()

    return 0


def main():
    return asyncio.run(
        main_async()
    )


if __name__ == "__main__":
    raise SystemExit(main())