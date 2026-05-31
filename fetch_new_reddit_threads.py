#!/usr/bin/env python3
"""Scrape New Reddit posts and threaded comments into text files.

This script uses browser automation because New Reddit is a JavaScript-heavy
site and direct HTTP requests are often blocked or return incomplete shell
HTML. It does not use Reddit's JSON or OAuth APIs.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import pathlib
import re
import sys
from typing import Any


DEFAULT_SUBREDDITS = ["IndianAlgoTrading", "IndianStockMarket"]
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
NEW_REDDIT = "https://new.reddit.com"


def debug_log(enabled: bool, message: str) -> None:
    if not enabled:
        return
    timestamp = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[DEBUG {timestamp}] {message}", file=sys.stderr, flush=True)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return cleaned or "subreddit"


def listing_url(base_url: str, subreddit: str, sort: str) -> str:
    return f"{base_url.rstrip('/')}/r/{subreddit}/{sort}/"


async def page_diagnostics(page: Any) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => ({
          url: location.href,
          title: document.title,
          bodyTextStart: (document.body?.innerText || "").slice(0, 800),
          shredditPosts: document.querySelectorAll("shreddit-post").length,
          articleCount: document.querySelectorAll("article").length,
          commentLinks: document.querySelectorAll('a[href*="/comments/"]').length,
          shredditComments: document.querySelectorAll("shreddit-comment").length,
          commentTestIds: document.querySelectorAll('[data-testid="comment"]').length,
          loginLinks: document.querySelectorAll('a[href*="/login"], shreddit-async-loader[bundlename*="login"]').length,
        })
        """
    )


async def save_debug_artifacts(page: Any, output_dir: pathlib.Path, name: str, debug: bool) -> None:
    if not debug:
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = safe_filename(name)
    html_path = output_dir / f"{safe_name}.html"
    screenshot_path = output_dir / f"{safe_name}.png"

    html_path.write_text(await page.content(), encoding="utf-8")
    await page.screenshot(path=str(screenshot_path), full_page=True)
    debug_log(debug, f"Saved debug HTML: {html_path}")
    debug_log(debug, f"Saved debug screenshot: {screenshot_path}")


async def auto_scroll(page: Any, max_scrolls: int, delay_ms: int, debug: bool = False) -> None:
    previous_height = 0
    stable_scrolls = 0

    for scroll_index in range(1, max_scrolls + 1):
        height = await page.evaluate("document.body.scrollHeight")
        debug_log(debug, f"Scroll {scroll_index}/{max_scrolls}: page height={height}, stable_scrolls={stable_scrolls}")
        if height == previous_height:
            stable_scrolls += 1
        else:
            stable_scrolls = 0

        if stable_scrolls >= 3:
            debug_log(debug, "Stopping scroll because page height stopped changing")
            break

        previous_height = height
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(delay_ms)


async def extract_listing_posts(page: Any) -> list[dict[str, str]]:
    return await page.evaluate(
        """
        () => {
          const abs = (value) => {
            if (!value) return "";
            try { return new URL(value, location.origin).href; } catch { return value; }
          };

          const text = (node) => (node?.innerText || node?.textContent || "").trim();
          const postIdFromPermalink = (value) => {
            const match = (value || "").match(/\\/r\\/[^/]+\\/comments\\/([^/]+)/);
            return match ? match[1] : "";
          };
          const normalizeId = (id, permalink) => (id || postIdFromPermalink(permalink) || "").replace(/^t3_/, "");
          const posts = [];
          const seen = new Set();

          for (const el of document.querySelectorAll("shreddit-post")) {
            const permalink = el.getAttribute("permalink") || el.getAttribute("content-href") || "";
            const id = normalizeId(el.getAttribute("id") || el.getAttribute("thingid"), permalink);
            if (!id || seen.has(id)) continue;
            seen.add(id);

            posts.push({
              id,
              title: el.getAttribute("post-title") || text(el.querySelector('[slot="title"]')) || text(el.querySelector("a")),
              author: el.getAttribute("author") || "",
              subreddit: (el.getAttribute("subreddit-prefixed-name") || "").replace(/^r\\//, ""),
              score: el.getAttribute("score") || "",
              comments_count: el.getAttribute("comment-count") || "",
              permalink,
              comments_url: abs(permalink),
              url: abs(el.getAttribute("content-href") || permalink),
            });
          }

          for (const article of document.querySelectorAll("article")) {
            const link = article.querySelector('a[href*="/comments/"]');
            const permalink = link?.getAttribute("href") || "";
            const id = normalizeId("", permalink);
            if (!id || seen.has(id)) continue;
            seen.add(id);

            posts.push({
              id,
              title: text(article.querySelector("h1,h2,h3,a")),
              author: "",
              subreddit: "",
              score: "",
              comments_count: "",
              permalink,
              comments_url: abs(permalink),
              url: abs(permalink),
            });
          }

          for (const link of document.querySelectorAll('a[href*="/comments/"]')) {
            const permalink = link.getAttribute("href") || "";
            const match = permalink.match(/\\/r\\/([^/]+)\\/comments\\/([^/]+)\\/([^/?#]+)/);
            if (!match) continue;

            const id = match[2];
            if (!id || seen.has(id)) continue;
            seen.add(id);

            const card = link.closest("shreddit-post, article, faceplate-tracker, div");
            const title =
              card?.getAttribute?.("post-title") ||
              card?.querySelector?.("h1,h2,h3,[slot='title'],a[slot='title']")?.innerText ||
              link.getAttribute("aria-label") ||
              link.innerText ||
              decodeURIComponent(match[3]).replace(/_/g, " ");

            posts.push({
              id,
              title: (title || "").trim(),
              author: card?.getAttribute?.("author") || "",
              subreddit: match[1],
              score: card?.getAttribute?.("score") || "",
              comments_count: card?.getAttribute?.("comment-count") || "",
              permalink,
              comments_url: abs(permalink),
              url: abs(permalink),
            });
          }

          return posts;
        }
        """
    )


async def scrape_listing_posts(
    page: Any,
    base_url: str,
    subreddit: str,
    sort: str,
    max_posts: int,
    max_scrolls: int,
    delay_ms: int,
    debug: bool,
) -> list[dict[str, str]]:
    url = listing_url(base_url, subreddit, sort)
    debug_log(debug, f"Opening listing page: {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    debug_log(debug, f"Listing loaded: title={await page.title()!r}, current_url={page.url}")
    await page.wait_for_timeout(delay_ms)
    debug_log(debug, f"Listing diagnostics after load: {await page_diagnostics(page)}")

    posts: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for scroll_index in range(1, max_scrolls + 1):
        extracted_posts = await extract_listing_posts(page)
        debug_log(debug, f"Listing pass {scroll_index}/{max_scrolls}: extracted={len(extracted_posts)}, kept={len(posts)}")
        for post in extracted_posts:
            post_id = post.get("id", "")
            if not post_id or post_id in seen_ids:
                continue
            seen_ids.add(post_id)
            posts.append(post)
            debug_log(debug, f"Added post {len(posts)}: id={post_id!r}, title={post.get('title', '')!r}")
            if max_posts and len(posts) >= max_posts:
                debug_log(debug, f"Reached max_posts={max_posts}")
                return posts

        previous_count = len(posts)
        await auto_scroll(page, 1, delay_ms, debug)
        if len(posts) == previous_count:
            debug_log(debug, "No new posts were added after this scroll")
            await page.wait_for_timeout(delay_ms)

    debug_log(debug, f"Finished listing scrape with {len(posts)} posts")
    return posts


async def scrape_post_page(
    page: Any,
    post: dict[str, str],
    comment_sort: str,
    delay_ms: int,
    debug: bool,
) -> tuple[str, list[str]]:
    url = post.get("comments_url", "")
    if not url:
        debug_log(debug, f"Skipping post without comments URL: id={post.get('id', '')!r}")
        return "", ["[No comments URL found]"]

    separator = "&" if "?" in url else "?"
    post_url = f"{url}{separator}sort={comment_sort}"
    debug_log(debug, f"Opening post page: {post_url}")
    await page.goto(post_url, wait_until="domcontentloaded", timeout=60_000)
    debug_log(debug, f"Post page loaded: title={await page.title()!r}, current_url={page.url}")
    await page.wait_for_timeout(delay_ms)
    await auto_scroll(page, 8, delay_ms, debug)
    debug_log(debug, f"Post diagnostics after scroll: {await page_diagnostics(page)}")

    data = await page.evaluate(
        """
        () => {
          const text = (node) => (node?.innerText || node?.textContent || "").trim();
          const postEl = document.querySelector("shreddit-post");
          const postBody = text(postEl?.querySelector('[slot="text-body"]')) ||
                           text(postEl?.querySelector('[slot="body"]')) ||
                           "";

          const comments = [];
          for (const el of document.querySelectorAll("shreddit-comment")) {
            const depth = Number(el.getAttribute("depth") || el.getAttribute("nest-level") || 0);
            const author = el.getAttribute("author") || "[deleted]";
            const score = el.getAttribute("score") || "";
            const id = el.getAttribute("thingid") || el.getAttribute("id") || "";
            const body = text(el.querySelector('[slot="comment"]')) ||
                         text(el.querySelector('[slot="body"]')) ||
                         text(el.querySelector(".md")) ||
                         text(el);
            comments.push({ depth, author, score, id, body });
          }

          if (!comments.length) {
            for (const el of document.querySelectorAll('[data-testid="comment"], div[id^="t1_"]')) {
              const style = getComputedStyle(el);
              if (style.display === "none" || style.visibility === "hidden") continue;

              const id = el.getAttribute("id") || "";
              const author =
                el.querySelector('a[href^="/user/"], a[href*="/user/"]')?.innerText ||
                el.querySelector('[data-testid="comment_author_link"]')?.innerText ||
                "[deleted]";
              const body =
                text(el.querySelector('[data-testid="comment"] [data-click-id="text"]')) ||
                text(el.querySelector('[data-click-id="text"]')) ||
                text(el.querySelector("p")) ||
                text(el);
              const depth = Number(el.getAttribute("depth") || el.getAttribute("nest-level") || 0);
              comments.push({ depth, author, score: "", id, body });
            }
          }

          return { postBody, comments };
        }
        """
    )
    debug_log(
        debug,
        "Extracted post page data: "
        f"post_body_chars={len(clean_text(data.get('postBody', '')))}, "
        f"comments={len(data.get('comments', []))}",
    )

    comment_lines: list[str] = []
    for comment_index, comment in enumerate(data.get("comments", []), start=1):
        level = max(0, int(comment.get("depth") or 0))
        indent = "  " * level
        debug_log(
            debug,
            f"Formatting comment {comment_index}: "
            f"depth={level}, author={comment.get('author', '[deleted]')!r}, id={comment.get('id', '')!r}",
        )
        comment_lines.append(
            f"{indent}- Comment by u/{comment.get('author', '[deleted]')} | "
            f"score: {comment.get('score', '')} | id: {comment.get('id', '')}"
        )
        body = clean_text(comment.get("body", ""))
        if body:
            for body_line in body.splitlines():
                comment_lines.append(f"{indent}  {body_line}")
        else:
            comment_lines.append(f"{indent}  [No comment body found]")

    return clean_text(data.get("postBody", "")), comment_lines or ["[No comments]"]


def format_post(post: dict[str, str], post_body: str, comments: list[str]) -> str:
    lines = [
        "=" * 100,
        f"Title: {post.get('title', '')}",
        f"ID: {post.get('id', '')}",
        f"Author: u/{post.get('author', '[deleted]')}",
        f"Subreddit: r/{post.get('subreddit', '')}",
        f"Score: {post.get('score', '')}",
        f"Comments reported by Reddit: {post.get('comments_count', '')}",
        f"Permalink: {post.get('comments_url', '')}",
        f"URL: {post.get('url', '')}",
        "",
        "Post body:",
        post_body or "[No text body]",
        "",
        "Comments:",
        *comments,
        "",
    ]
    return "\n".join(lines)


async def scrape_subreddit(
    browser: Any,
    subreddit: str,
    output_dir: pathlib.Path,
    sort: str,
    comment_sort: str,
    max_posts: int,
    max_scrolls: int,
    delay_ms: int,
    debug: bool,
    user_agent: str,
    base_url: str,
) -> pathlib.Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{safe_filename(subreddit)}_new_reddit.txt"
    context = await browser.new_context(
        user_agent=user_agent,
        locale="en-US",
        viewport={"width": 1366, "height": 900},
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
    page = await context.new_page()

    try:
        print(f"Scraping New Reddit r/{subreddit} listing...", flush=True)
        debug_log(debug, f"Created browser context/page for r/{subreddit}")
        posts = await scrape_listing_posts(page, base_url, subreddit, sort, max_posts, max_scrolls, delay_ms, debug)
        debug_log(debug, f"r/{subreddit}: total listing posts collected={len(posts)}")

        if not posts:
            diagnostics = await page_diagnostics(page)
            debug_log(debug, f"No posts found. Final listing diagnostics: {diagnostics}")
            await save_debug_artifacts(page, output_dir, f"{subreddit}_listing_debug", debug)

        debug_log(debug, f"Writing output file: {output_path}")

        with output_path.open("w", encoding="utf-8") as handle:
            handle.write(f"Subreddit: r/{subreddit}\n")
            handle.write(f"Fetched at UTC: {dt.datetime.now(dt.timezone.utc).isoformat()}\n")
            handle.write("Source: www.reddit.com browser scrape\n")
            handle.write(f"Post sort: {sort}\n")
            handle.write(f"Comment sort: {comment_sort}\n")
            handle.write(f"Posts fetched: {len(posts)}\n\n")

            if not posts:
                handle.write("No posts were found on the rendered New Reddit page.\n")
                handle.write("Run again with --debug to save diagnostic HTML and screenshots.\n\n")

            for index, post in enumerate(posts, start=1):
                print(f"[{subreddit}] {index}/{len(posts)} Scraping comments: {post.get('title', '')}", flush=True)
                try:
                    post_body, comments = await scrape_post_page(page, post, comment_sort, delay_ms, debug)
                    debug_log(
                        debug,
                        f"Post {index}: post_body_chars={len(post_body)}, formatted_comment_lines={len(comments)}",
                    )
                    if comments == ["[No comments]"]:
                        await save_debug_artifacts(page, output_dir, f"{subreddit}_post_{index}_debug", debug)
                except Exception as exc:
                    debug_log(debug, f"Post {index}: failed while scraping comments: {exc!r}")
                    await save_debug_artifacts(page, output_dir, f"{subreddit}_post_{index}_error_debug", debug)
                    post_body = ""
                    comments = [f"[Failed to scrape comments: {exc}]"]

                handle.write(format_post(post, post_body, comments))
                handle.write("\n")

        return output_path
    finally:
        await context.close()
        debug_log(debug, f"Closed browser context for r/{subreddit}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape New Reddit posts and threaded comments into txt files under data/."
    )
    parser.add_argument(
        "subreddits",
        nargs="*",
        default=DEFAULT_SUBREDDITS,
        help=f"Subreddits to scrape. Default: {', '.join(DEFAULT_SUBREDDITS)}",
    )
    parser.add_argument("--output-dir", default="data", help="Folder where txt files will be saved. Default: data")
    parser.add_argument("--sort", choices=["hot", "new", "top", "rising"], default="new")
    parser.add_argument(
        "--comment-sort",
        choices=["confidence", "top", "new", "controversial", "old", "qa"],
        default="confidence",
    )
    parser.add_argument("--max-posts", type=int, default=20, help="Maximum posts per subreddit. Default: 20")
    parser.add_argument("--max-scrolls", type=int, default=20, help="Maximum listing scrolls. Default: 20")
    parser.add_argument("--delay-ms", type=int, default=2500, help="Delay between browser actions. Default: 2500")
    parser.add_argument("--headful", action="store_true", help="Show the browser window while scraping.")
    parser.add_argument("--debug", action="store_true", help="Print detailed scraper debug logs to stderr.")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="Browser User-Agent used for scraping.")
    parser.add_argument(
        "--base-url",
        default=NEW_REDDIT,
        help="Reddit web base URL to scrape. Default: https://new.reddit.com",
    )
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "Playwright is required for the New Reddit scraper.\n"
            "Install it with:\n"
            "  python -m pip install playwright\n"
            "  python -m playwright install chromium",
        )
        return 1

    output_dir = pathlib.Path(args.output_dir)
    debug_log(
        args.debug,
        "Starting New Reddit scraper with "
        f"subreddits={args.subreddits}, sort={args.sort}, comment_sort={args.comment_sort}, "
        f"max_posts={args.max_posts}, max_scrolls={args.max_scrolls}, delay_ms={args.delay_ms}, "
        f"headful={args.headful}, base_url={args.base_url!r}, user_agent={args.user_agent!r}",
    )

    async with async_playwright() as p:
        debug_log(args.debug, "Launching Chromium")
        browser = await p.chromium.launch(headless=not args.headful)
        try:
            for subreddit in args.subreddits:
                debug_log(args.debug, f"Starting subreddit scrape: r/{subreddit}")
                try:
                    output_path = await scrape_subreddit(
                        browser=browser,
                        subreddit=subreddit,
                        output_dir=output_dir,
                        sort=args.sort,
                        comment_sort=args.comment_sort,
                        max_posts=args.max_posts,
                        max_scrolls=args.max_scrolls,
                        delay_ms=args.delay_ms,
                        debug=args.debug,
                        user_agent=args.user_agent,
                        base_url=args.base_url,
                    )
                    print(f"Saved r/{subreddit} to {output_path}")
                    debug_log(args.debug, f"Finished subreddit scrape: r/{subreddit}")
                except Exception as exc:
                    debug_log(args.debug, f"Subreddit scrape failed for r/{subreddit}: {exc!r}")
                    print(f"Failed to scrape r/{subreddit}: {exc}")
        finally:
            debug_log(args.debug, "Closing Chromium")
            await browser.close()

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
