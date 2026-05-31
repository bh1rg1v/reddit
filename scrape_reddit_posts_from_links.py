#!/usr/bin/env python3
"""Open Reddit post links in a browser and save post/comment data to CSV."""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import pathlib
from typing import Any


PROJECT_ROOT = pathlib.Path("D:/github/reddit")
DEFAULT_INPUT_CSV = str(PROJECT_ROOT / "data" / "post_links.csv")
DEFAULT_OUTPUT_CSV = str(PROJECT_ROOT / "data" / "post_comments.csv")
DEFAULT_DELAY_MS = 2000
DEFAULT_COMMENT_SCROLLS = 20
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


CSV_FIELDS = [
    "post_url",
    "post_id",
    "post_title",
    "post_author",
    "post_subreddit",
    "post_score",
    "post_comment_count",
    "post_created_timestamp",
    "post_type",
    "post_body",
    "comment_id",
    "comment_parent_id",
    "comment_depth",
    "comment_author",
    "comment_score",
    "comment_body",
]


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def resolve_project_path(path_value: str) -> pathlib.Path:
    path = pathlib.Path(path_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def read_post_links(path: pathlib.Path, max_links: int) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "post_url" not in reader.fieldnames:
            raise ValueError(f"{path} must contain a post_url column")

        for row in reader:
            link = clean_text(row.get("post_url", ""))
            if not link or link in seen:
                continue
            seen.add(link)
            links.append(link)
            if max_links and len(links) >= max_links:
                break

    return links


async def scroll_page(page: Any, max_scrolls: int, delay_ms: int) -> None:
    previous_height = 0
    stable_scrolls = 0

    for _ in range(max_scrolls):
        height = await page.evaluate("document.body.scrollHeight")
        if height == previous_height:
            stable_scrolls += 1
        else:
            stable_scrolls = 0

        if stable_scrolls >= 4:
            break

        previous_height = height
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(delay_ms)


async def click_more_buttons(page: Any, rounds: int, delay_ms: int) -> None:
    for _ in range(rounds):
        clicked = await page.evaluate(
            """
            () => {
              const labels = ["view more comments", "more replies", "load more comments"];
              const buttons = Array.from(document.querySelectorAll("button, faceplate-partial button"));
              const button = buttons.find((el) => {
                const text = (el.innerText || el.getAttribute("aria-label") || "").trim().toLowerCase();
                return labels.some((label) => text.includes(label));
              });
              if (!button) return false;
              button.click();
              return true;
            }
            """
        )
        if not clicked:
            return
        await page.wait_for_timeout(delay_ms)


async def scrape_post(page: Any, post_url: str, delay_ms: int, comment_scrolls: int) -> list[dict[str, str]]:
    await page.goto(post_url, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(delay_ms)
    await click_more_buttons(page, 3, delay_ms)
    await scroll_page(page, comment_scrolls, delay_ms)
    await click_more_buttons(page, 3, delay_ms)

    data = await page.evaluate(
        """
        () => {
          const text = (node) => (node?.innerText || node?.textContent || "").trim();
          const post = document.querySelector("shreddit-post");
          const postData = {
            post_id: post?.getAttribute("id") || post?.getAttribute("thingid") || "",
            post_title: post?.getAttribute("post-title") || text(post?.querySelector('[slot="title"]')) || document.title,
            post_author: post?.getAttribute("author") || "",
            post_subreddit: (post?.getAttribute("subreddit-prefixed-name") || post?.getAttribute("subreddit-name") || "").replace(/^r\\//, ""),
            post_score: post?.getAttribute("score") || "",
            post_comment_count: post?.getAttribute("comment-count") || "",
            post_created_timestamp: post?.getAttribute("created-timestamp") || "",
            post_type: post?.getAttribute("post-type") || "",
            post_body:
              text(post?.querySelector('[slot="text-body"]')) ||
              text(post?.querySelector('[slot="body"]')) ||
              text(post?.querySelector("shreddit-post-text-body")) ||
              "",
          };

          const comments = [];
          const seen = new Set();

          for (const el of document.querySelectorAll("shreddit-comment")) {
            const id = el.getAttribute("thingid") || el.getAttribute("id") || "";
            if (id && seen.has(id)) continue;
            if (id) seen.add(id);

            comments.push({
              comment_id: id,
              comment_parent_id: el.getAttribute("parentid") || el.getAttribute("parent-id") || "",
              comment_depth: el.getAttribute("depth") || el.getAttribute("nest-level") || "0",
              comment_author: el.getAttribute("author") || "[deleted]",
              comment_score: el.getAttribute("score") || "",
              comment_body:
                text(el.querySelector('[slot="comment"]')) ||
                text(el.querySelector('[slot="body"]')) ||
                text(el.querySelector('[data-click-id="text"]')) ||
                text(el),
            });
          }

          if (!comments.length) {
            for (const el of document.querySelectorAll('[data-testid="comment"], div[id^="t1_"]')) {
              const id = el.getAttribute("id") || "";
              if (id && seen.has(id)) continue;
              if (id) seen.add(id);

              comments.push({
                comment_id: id,
                comment_parent_id: "",
                comment_depth: el.getAttribute("depth") || el.getAttribute("nest-level") || "0",
                comment_author:
                  text(el.querySelector('a[href^="/user/"], a[href*="/user/"]')) ||
                  text(el.querySelector('[data-testid="comment_author_link"]')) ||
                  "[deleted]",
                comment_score: "",
                comment_body:
                  text(el.querySelector('[data-click-id="text"]')) ||
                  text(el.querySelector("p")) ||
                  text(el),
              });
            }
          }

          return { postData, comments };
        }
        """
    )

    post_data = {key: clean_text(value) for key, value in data.get("postData", {}).items()}
    comments = data.get("comments", [])

    if not comments:
        row = {field: "" for field in CSV_FIELDS}
        row.update(post_data)
        row["post_url"] = post_url
        return [row]

    rows: list[dict[str, str]] = []
    for comment in comments:
        row = {field: "" for field in CSV_FIELDS}
        row.update(post_data)
        row["post_url"] = post_url
        for key in [
            "comment_id",
            "comment_parent_id",
            "comment_depth",
            "comment_author",
            "comment_score",
            "comment_body",
        ]:
            row[key] = clean_text(comment.get(key, ""))
        rows.append(row)

    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read Reddit post links from CSV, open each in a browser, and save post/comment data to CSV."
    )
    parser.add_argument("--input-csv", default=DEFAULT_INPUT_CSV, help=f"Default: {DEFAULT_INPUT_CSV}")
    parser.add_argument("--output-csv", default=DEFAULT_OUTPUT_CSV, help=f"Default: {DEFAULT_OUTPUT_CSV}")
    parser.add_argument("--max-links", type=int, default=0, help="Maximum links to scrape. Use 0 for all. Default: 0")
    parser.add_argument("--delay-ms", type=int, default=DEFAULT_DELAY_MS, help="Delay after browser actions. Default: 2000")
    parser.add_argument("--comment-scrolls", type=int, default=DEFAULT_COMMENT_SCROLLS, help="Scrolls per post. Default: 20")
    parser.add_argument("--headless", action="store_true", help="Run browser hidden. Default is visible browser window.")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="Browser User-Agent.")
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "Playwright is required.\n"
            "Install it with:\n"
            "  python -m pip install playwright\n"
            "  python -m playwright install chromium"
        )
        return 1

    input_csv = resolve_project_path(args.input_csv)
    output_csv = resolve_project_path(args.output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    links = read_post_links(input_csv, args.max_links)
    print(f"Loaded {len(links)} post links from {input_csv}", flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=args.headless)
        context = await browser.new_context(
            user_agent=args.user_agent,
            locale="en-US",
            viewport={"width": 1366, "height": 900},
        )
        await context.add_cookies(
            [{"name": "over18", "value": "1", "domain": ".reddit.com", "path": "/"}]
        )
        page = await context.new_page()

        try:
            with output_csv.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                writer.writeheader()

                for index, link in enumerate(links, start=1):
                    print(f"[{index}/{len(links)}] Scraping {link}", flush=True)
                    try:
                        rows = await scrape_post(page, link, args.delay_ms, args.comment_scrolls)
                    except Exception as exc:
                        rows = [{field: "" for field in CSV_FIELDS}]
                        rows[0]["post_url"] = link
                        rows[0]["comment_body"] = f"[Failed to scrape post: {exc}]"

                    writer.writerows(rows)
                    handle.flush()

            print(f"Saved post/comment rows to {output_csv}", flush=True)
        finally:
            await context.close()
            await browser.close()

    print(f"Finished at UTC: {dt.datetime.now(dt.timezone.utc).isoformat()}", flush=True)
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
