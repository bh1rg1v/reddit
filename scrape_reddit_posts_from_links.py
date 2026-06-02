#!/usr/bin/env python3
"""Open Reddit post links in a browser and save post/comment data to CSV."""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import pathlib
import json
import random
import re
import sys
import time
from typing import Any

PROJECT_ROOT = pathlib.Path("D:/github/reddit")
DEFAULT_INPUT_FOLDER = str(PROJECT_ROOT / "data" / "post_links")
DEFAULT_OUTPUT_FOLDER = str(PROJECT_ROOT / "data" / "posts")
DEFAULT_DELAY_MS = 2000
DEFAULT_COMMENT_SCROLLS = 20
DEFAULT_MAX_TABS = 5
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Reddit is rate-limiting for every 80 posts

SCRAPE_PAUSE_EVERY = 75
RATE_LIMIT_REMAINING_THRESHOLD = 100


class RateLimitError(Exception):
    """Raised when Reddit rate-limit headers indicate quota is nearly exhausted or a block occurs."""
    def __init__(self, msg: str = "", reset_after: float = 10.0):
        super().__init__(msg)
        self.reset_after = reset_after


successful_scrapes = 0
counter_lock = asyncio.Lock()
progress_lock = asyncio.Lock()

# Shared rate-limit state updated from response headers
_rl_used: float = 0.0
_rl_remaining: float = 999.0
_rl_reset: float = 600.0
_rl_lock = asyncio.Lock()

_completed_jobs = 0
_total_jobs_global = 0
_overall_start: float = 0.0


def print_progress(completed: int, total: int) -> None:
    pct = completed / total if total else 0
    bar_width = 40
    filled = int(bar_width * pct)
    bar = "█" * filled + "░" * (bar_width - filled)
    elapsed = time.monotonic() - _overall_start
    avg = elapsed / completed if completed else 0
    line1 = f"{completed}/{total}"
    line2 = f"[{bar}] {pct*100:.1f}%"
    line3 = f"avg: {avg:.1f}s/post | remaining requests: {_rl_remaining:.0f}"
    sys.stdout.write(f"\033[3A\r{line1:<30}\n{line2:<55}\n{line3:<55}\n")
    sys.stdout.flush()


def log(msg: str) -> None:
    """Print a message above the progress bar without corrupting it."""
    sys.stdout.write(f"\033[3A\r{msg:<120}\n{'':<30}\n{'':<55}\n{'':<55}\n")
    sys.stdout.flush()


def _is_saved(output_root: pathlib.Path, subreddit: str, link: str) -> bool:
    post_id = extract_post_id_from_url(link)
    if not post_id:
        return False
    return (
        (output_root / subreddit / f"{post_id}.json").exists()
        or (output_root / subreddit / f"t3_{post_id}.json").exists()
    )


def extract_post_id_from_url(url: str) -> str:
    """Attempt to extract the Reddit post ID from the URL to check for existing files."""
    # Matches standard Reddit format: /comments/POST_ID/
    match = re.search(r"/comments/([^/?#]+)", url)
    if match:
        return match.group(1)
    
    # Matches shortened Reddit format: redd.it/POST_ID
    match = re.search(r"redd\.it/([^/?#]+)", url)
    if match:
        return match.group(1)
        
    return ""


def save_post_json(
    output_root: pathlib.Path,
    subreddit: str,
    post_data: dict,
    comments: list,
) -> None:

    subreddit_dir = output_root / subreddit
    subreddit_dir.mkdir(parents=True, exist_ok=True)

    post_id = post_data.get("post_id") or "unknown"

    output_file = subreddit_dir / f"{post_id}.json"

    payload = {
        **post_data,
        "comments": comments,
    }

    with output_file.open("w", encoding="utf-8") as f:
        json.dump(
            payload,
            f,
            ensure_ascii=False,
            indent=2,
        )


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


def read_post_links_from_folder(folder_path: pathlib.Path, max_links: int) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    
    csv_files = list(folder_path.glob("*.csv"))
    
    if not csv_files:
        print(f"No CSV files found in {folder_path}", flush=True)
        return []
    
    print(f"Started reading links from {folder_path}.", flush=True)
    
    for csv_file in csv_files:
        # print(f"Reading links from {csv_file}", flush=True)
        
        with csv_file.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "post_url" not in reader.fieldnames:
                print(f"Warning: {csv_file} does not contain a post_url column, skipping", flush=True)
                continue

            for row in reader:
                link = clean_text(row.get("post_url", ""))
                if not link or link in seen:
                    continue
                seen.add(link)
                links.append(link)
                if max_links and len(links) >= max_links:
                    return links

    return links


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read Reddit post links from CSV, open each in a browser, and save post/comment data to CSV."
    )
    parser.add_argument("--input-folder", default=DEFAULT_INPUT_FOLDER, help=f"Default: {DEFAULT_INPUT_FOLDER}")
    parser.add_argument(
        "--output-folder",
        default=DEFAULT_OUTPUT_FOLDER,
        help=f"Default: {DEFAULT_OUTPUT_FOLDER}",
    )   
    parser.add_argument("--max-links", type=int, default=0, help="Maximum links to scrape. Use 0 for all. Default: 0")
    parser.add_argument("--delay-ms", type=int, default=DEFAULT_DELAY_MS, help="Delay after browser actions. Default: 2000")
    parser.add_argument("--comment-scrolls", type=int, default=DEFAULT_COMMENT_SCROLLS, help="Scrolls per post. Default: 20")
    parser.add_argument("--max-tabs", type=int, default=DEFAULT_MAX_TABS, help="Simultaneous browser tabs to run. Default: 5")
    parser.add_argument("--headless", action="store_true", help="Run browser hidden. Default is visible browser window.")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="Browser User-Agent.")
    return parser.parse_args()


async def scroll_page(page: Any, max_scrolls: int, delay_ms: int) -> None:
    previous_height = 0
    stable_scrolls = 0

    for _ in range(max_scrolls):
        # Stop early if no comments exist on the page
        has_comments = await page.evaluate(
            "() => document.querySelectorAll('shreddit-comment, [data-testid=\"comment\"], div[id^=\"t1_\"]').length > 0"
        )
        if not has_comments and stable_scrolls >= 2:
            break

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
    max_rounds = max(rounds, 50)  # keep clicking until exhausted, up to a safe cap
    for _ in range(max_rounds):
        clicked = await page.evaluate(
            """
            () => {
              const labels = ["view more comments", "more replies", "load more comments"];
              const buttons = Array.from(document.querySelectorAll("button, faceplate-partial button"));
              const clicked_buttons = buttons.filter((el) => {
                const text = (el.innerText || el.getAttribute("aria-label") || "").trim().toLowerCase();
                return labels.some((label) => text.includes(label));
              });
              if (!clicked_buttons.length) return false;
              clicked_buttons.forEach(b => b.click());
              return true;
            }
            """
        )
        if not clicked:
            return
        await page.wait_for_timeout(delay_ms)


async def _handle_response(response: Any) -> None:
    """Intercept every response and update shared rate-limit state from headers."""
    global _rl_used, _rl_remaining, _rl_reset
    try:
        headers = response.headers
        used      = headers.get("x-ratelimit-used")
        remaining = headers.get("x-ratelimit-remaining")
        reset     = headers.get("x-ratelimit-reset")
        if used is None and remaining is None:
            return
        async with _rl_lock:
            if used      is not None: _rl_used      = float(used)
            if remaining is not None: _rl_remaining = float(remaining)
            if reset     is not None: _rl_reset     = float(reset)
    except Exception:
        pass


async def scrape_post(page: Any, post_url: str, delay_ms: int, comment_scrolls: int) -> dict[str, Any]:

    # Check quota BEFORE opening the page — if already exhausted, fail fast
    async with _rl_lock:
        remaining = _rl_remaining
        reset_after = _rl_reset
    if remaining <= RATE_LIMIT_REMAINING_THRESHOLD:
        raise RateLimitError(
            f"Quota low before request (remaining={remaining:.0f}, reset in {reset_after:.0f}s)",
            reset_after=reset_after,
        )

    page.on("response", _handle_response)

    await asyncio.sleep(random.uniform(0, 1))
    try:
        await page.goto(
            post_url,
            wait_until="domcontentloaded",
            timeout=60_000,
        )
    except Exception as exc:
        msg = str(exc)
        if "ERR_HTTP_RESPONSE_CODE_FAILURE" in msg or "ERR_ABORTED" in msg or "TargetClosedError" in msg or "Connection closed" in msg:
            raise RateLimitError(msg, reset_after=_rl_reset) from exc
        raise

    # Check quota AFTER the page loaded too
    async with _rl_lock:
        remaining = _rl_remaining
        reset_after = _rl_reset
    if remaining <= RATE_LIMIT_REMAINING_THRESHOLD:
        raise RateLimitError(
            f"Quota low after request (remaining={remaining:.0f}, reset in {reset_after:.0f}s)",
            reset_after=reset_after,
        )

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
    post_data["post_url"] = post_url
    comments = data.get("comments", [])

    if not comments:
        return {
            "post": post_data,
            "comments": [],
        }

    return {
        "post": post_data,
        "comments": [
            {
                "comment_id": clean_text(comment.get("comment_id", "")),
                "parent_id": clean_text(comment.get("comment_parent_id", "")),
                "depth": clean_text(comment.get("comment_depth", "")),
                "author": clean_text(comment.get("comment_author", "")),
                "score": clean_text(comment.get("comment_score", "")),
                "body": clean_text(comment.get("comment_body", "")),
            }
            for comment in comments
        ]
    }


async def process_job(
    sem: asyncio.Semaphore,
    context: Any,
    output_root: pathlib.Path,
    subreddit: str,
    link: str,
    args: argparse.Namespace,
    job_index: int,
    total_jobs: int,
    rate_limit_event: asyncio.Event,
) -> None:

    async with sem:
        if rate_limit_event.is_set():
            return

        async with _rl_lock:
            remaining = _rl_remaining
        if remaining <= RATE_LIMIT_REMAINING_THRESHOLD:
            rate_limit_event.set()
            return

        page = await context.new_page()

        try:
            result = await scrape_post(
                page,
                link,
                args.delay_ms,
                args.comment_scrolls,
            )

            save_post_json(
                output_root,
                subreddit,
                result["post"],
                result["comments"],
            )

            global successful_scrapes
            async with counter_lock:
                successful_scrapes += 1

        except RateLimitError as exc:
            # log(f"Rate limited on {link} (remaining={_rl_remaining:.0f}, reset in {exc.reset_after:.0f}s) — signalling browser restart.")
            rate_limit_event.set()
            rate_limit_event.reset_after = exc.reset_after  # type: ignore[attr-defined]
            return

        except Exception as exc:
            log(f"Failed: {link} ({exc})")

        finally:
            try:
                await page.close()
            except Exception:
                pass
            async with progress_lock:
                global _completed_jobs
                _completed_jobs += 1
                print_progress(_completed_jobs, _total_jobs_global)


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

    input_folder = resolve_project_path(args.input_folder)
    output_root = resolve_project_path(args.output_folder)
    
    if not input_folder.is_dir():
        print(f"Error: {input_folder} is not a directory", flush=True)
        return 1

    subreddit_folders = [
        p for p in input_folder.iterdir()
        if p.is_dir() and p.name != "posts"
    ]

    all_jobs = []

    # 1. Filter out existing files BEFORE we even think about launching Playwright
    for subreddit_folder in subreddit_folders:
        links = read_post_links_from_folder(
            subreddit_folder,
            args.max_links,
        )

        for link in links:
            post_id = extract_post_id_from_url(link)
            if post_id:
                # Check for both "ID.json" and "t3_ID.json"
                file_normal = output_root / subreddit_folder.name / f"{post_id}.json"
                file_t3 = output_root / subreddit_folder.name / f"t3_{post_id}.json"
                
                if file_normal.exists() or file_t3.exists():
                    # print(f"Skipping already scraped post: {post_id}", flush=True)
                    continue  # Skip adding this to the jobs list

            all_jobs.append((subreddit_folder.name, link))

    print(f"Loaded {len(all_jobs)} post links to scrape from {input_folder}", flush=True)

    if not all_jobs:
        print("No new links to scrape. Exiting.", flush=True)
        return 0

    # 2. Launch browser only for jobs that need doing, restarting every SCRAPE_PAUSE_EVERY posts
    global _completed_jobs, _total_jobs_global, _rl_used, _rl_remaining, _rl_reset
    total_jobs = len(all_jobs)
    _total_jobs_global = total_jobs
    _completed_jobs = 0

    # Print three lines so the progress display has room to overwrite
    # print(f"", flush=True)
    # print(f"0/{total_jobs}", flush=True)
    # print(f"[{'░' * 40}] 0.0%", flush=True)
    # print(f"avg: 0.0s/post | remaining requests: 999", flush=True)

    global _overall_start
    _overall_start = time.monotonic()
    overall_start = _overall_start

    async def make_browser_context(p: Any) -> tuple[Any, Any]:
        browser = await p.chromium.launch(
            headless=args.headless,
            args=["--incognito", "--no-first-run", "--no-default-browser-check"],
        )
        context = await browser.new_context(
            user_agent=args.user_agent,
            locale="en-US",
            viewport={"width": 1366, "height": 900},
            storage_state=None,
        )
        await context.add_cookies(
            [{"name": "over18", "value": "1", "domain": ".reddit.com", "path": "/"}]
        )
        return browser, context

    async def close_browser(browser: Any, context: Any) -> None:
        try:
            await context.close()
        except Exception:
            pass
        try:
            await browser.close()
        except Exception:
            pass

    async with async_playwright() as p:
        job_index = 0

        while job_index < total_jobs:
            batch = all_jobs[job_index: job_index + SCRAPE_PAUSE_EVERY]
            batch_start = time.monotonic()

            print(f"", flush=True)
            print(f"{_completed_jobs}/{total_jobs}", flush=True)
            print(f"[{'░' * 40}] 0.0%", flush=True)
            print(f"avg: 0.0s/post | remaining requests: {_rl_remaining:.0f}", flush=True)

            browser, context = await make_browser_context(p)
            sem = asyncio.Semaphore(args.max_tabs)
            rate_limit_event = asyncio.Event()

            # track which links in this batch still need doing
            pending = list(batch)

            while pending:
                rate_limit_event.clear()
                tasks = [
                    asyncio.ensure_future(process_job(
                        sem, context, output_root, subreddit, link, args,
                        job_index + i + 1, total_jobs, rate_limit_event,
                    ))
                    for i, (subreddit, link) in enumerate(pending)
                ]

                await asyncio.gather(*tasks, return_exceptions=True)

                if rate_limit_event.is_set():
                    skipped = [
                        (subreddit, link) for subreddit, link in pending
                        if not _is_saved(output_root, subreddit, link)
                    ]
                    # log(f"Rate limit hit (used={_rl_used:.0f}, remaining={_rl_remaining:.0f}) — closing browser, waiting 5s, restarting fresh. {len(skipped)} links re-queued.")
                    # print()
                    await close_browser(browser, context)
                    await asyncio.sleep(1)
                    _rl_used = 0.0
                    _rl_remaining = 999.0
                    _rl_reset = 600.0
                    browser, context = await make_browser_context(p)
                    sem = asyncio.Semaphore(args.max_tabs)
                    pending = skipped
                else:
                    break  # batch done cleanly

            # await close_browser(browser, context)

            batch_elapsed = time.monotonic() - batch_start
            # log(f"Batch done in {batch_elapsed:.1f}s. Browser closed after job {job_index + len(batch)}/{total_jobs}.")

            job_index += len(batch)

            if job_index < total_jobs:
                log(f"Opening new browser for next batch...")

    overall_elapsed = time.monotonic() - overall_start
    avg_per_post = overall_elapsed / successful_scrapes if successful_scrapes else 0
    print(f"\nFinished at UTC: {dt.datetime.now(dt.timezone.utc).isoformat()}", flush=True)
    print(f"Posts successfully fetched : {successful_scrapes}/{total_jobs}", flush=True)
    print(f"Total time                 : {overall_elapsed:.1f}s", flush=True)
    print(f"Average time per post      : {avg_per_post:.1f}s", flush=True)
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())