#!/usr/bin/env python3
"""Scrape Reddit posts and threaded comments into text files.

This intentionally scrapes old.reddit.com HTML pages instead of using Reddit's
JSON or OAuth APIs. HTML scraping is brittle by nature: if Reddit changes or
blocks the HTML pages, the parser may need updates.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any


DEFAULT_SUBREDDITS = ["IndianAlgoTrading", "IndianStockMarket"]
DEFAULT_USER_AGENT = "Mozilla/5.0 reddit-html-thread-scraper/1.0"
OLD_REDDIT = "https://old.reddit.com"
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class Node:
    def __init__(self, tag: str, attrs: dict[str, str] | None = None, parent: "Node | None" = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.parent = parent
        self.children: list[Node] = []
        self.text_parts: list[str] = []

    def append_text(self, text: str) -> None:
        self.text_parts.append(text)

    def has_class(self, class_name: str) -> bool:
        return class_name in self.attrs.get("class", "").split()

    def classes_include(self, class_name: str) -> bool:
        return self.has_class(class_name)

    def find_all(self, tag: str | None = None, class_name: str | None = None) -> list["Node"]:
        matches: list[Node] = []
        for child in self.children:
            tag_matches = tag is None or child.tag == tag
            class_matches = class_name is None or child.has_class(class_name)
            if tag_matches and class_matches:
                matches.append(child)
            matches.extend(child.find_all(tag, class_name))
        return matches

    def first(self, tag: str | None = None, class_name: str | None = None) -> "Node | None":
        matches = self.find_all(tag, class_name)
        return matches[0] if matches else None

    def text(self) -> str:
        pieces: list[str] = []
        self._collect_text(pieces)
        text = "".join(pieces)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
        return text.strip()

    def _collect_text(self, pieces: list[str]) -> None:
        if self.tag in {"p", "div", "li", "blockquote", "pre"} and pieces and not pieces[-1].endswith("\n"):
            pieces.append("\n")
        pieces.extend(self.text_parts)
        for child in self.children:
            child._collect_text(pieces)
        if self.tag in {"p", "div", "li", "blockquote", "pre"}:
            pieces.append("\n")


class DOMParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), {key.lower(): value or "" for key, value in attrs}, self.current)
        self.current.children.append(node)
        if tag.lower() not in VOID_TAGS:
            self.current = node

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        node: Node | None = self.current
        while node and node.parent and node.tag != tag:
            node = node.parent
        if node and node.parent:
            self.current = node.parent

    def handle_data(self, data: str) -> None:
        self.current.append_text(data)


def parse_html(html: str) -> Node:
    parser = DOMParser()
    parser.feed(html)
    return parser.root


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return cleaned or "subreddit"


def fetch_html(url: str, user_agent: str, retries: int = 3) -> str:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Cookie": "over18=1",
    }
    request = urllib.request.Request(url, headers=headers)

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries:
                wait_seconds = 10 * attempt
                print(f"Rate limited. Waiting {wait_seconds}s before retrying {url}", file=sys.stderr)
                time.sleep(wait_seconds)
                continue
            if exc.code == 403:
                raise RuntimeError(
                    "Reddit blocked this HTML scrape with HTTP 403. Try increasing --delay, "
                    "using a normal browser User-Agent with --user-agent, or running later."
                ) from exc
            raise
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(3 * attempt)
                continue
            raise


def listing_url(subreddit: str, sort: str) -> str:
    return f"{OLD_REDDIT}/r/{urllib.parse.quote(subreddit)}/{sort}/"


def absolute_old_url(url: str) -> str:
    return urllib.parse.urljoin(OLD_REDDIT, url)


def extract_post_from_listing(node: Node) -> dict[str, str] | None:
    fullname = node.attrs.get("data-fullname", "")
    if not fullname.startswith("t3_"):
        return None

    title_node = node.first("a", "title")
    permalink = node.attrs.get("data-permalink", "")
    comments_url = absolute_old_url(permalink) if permalink else ""

    return {
        "id": fullname.removeprefix("t3_"),
        "title": clean_text(title_node.text() if title_node else ""),
        "author": node.attrs.get("data-author", "[deleted]"),
        "subreddit": node.attrs.get("data-subreddit", ""),
        "score": node.attrs.get("data-score", ""),
        "comments_count": node.attrs.get("data-comments-count", ""),
        "permalink": permalink,
        "comments_url": comments_url,
        "url": node.attrs.get("data-url", ""),
    }


def find_next_listing_url(root: Node) -> str | None:
    for link in root.find_all("a"):
        parent = link.parent
        href = link.attrs.get("href", "")
        if href and parent and parent.tag == "span" and parent.has_class("next-button"):
            return absolute_old_url(href)
    return None


def scrape_listing_posts(
    subreddit: str,
    sort: str,
    max_posts: int,
    delay_seconds: float,
    user_agent: str,
) -> list[dict[str, str]]:
    posts: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    url: str | None = listing_url(subreddit, sort)

    while url:
        html = fetch_html(url, user_agent)
        root = parse_html(html)

        for node in root.find_all("div", "thing"):
            post = extract_post_from_listing(node)
            if not post or post["id"] in seen_ids:
                continue
            seen_ids.add(post["id"])
            posts.append(post)
            if max_posts and len(posts) >= max_posts:
                return posts

        next_url = find_next_listing_url(root)
        if not next_url or next_url == url:
            break
        url = next_url
        time.sleep(delay_seconds)

    return posts


def first_direct_child(node: Node, tag: str, class_name: str) -> Node | None:
    for child in node.children:
        if child.tag == tag and child.has_class(class_name):
            return child
    return None


def immediate_comment_children(container: Node) -> list[Node]:
    comments: list[Node] = []
    for child in container.children:
        if child.tag == "div" and child.has_class("thing") and child.has_class("comment"):
            comments.append(child)
        elif child.tag == "div" and child.has_class("sitetable"):
            comments.extend(immediate_comment_children(child))
    return comments


def nested_comment_children(comment_node: Node) -> list[Node]:
    child_container = first_direct_child(comment_node, "div", "child")
    if not child_container:
        return []
    return immediate_comment_children(child_container)


def extract_comment_body(comment_node: Node) -> str:
    entry = first_direct_child(comment_node, "div", "entry")
    if not entry:
        return ""
    body = entry.first("div", "usertext-body")
    return clean_text(body.text() if body else "")


def format_comment(comment_node: Node, level: int = 0) -> list[str]:
    indent = "  " * level
    author = comment_node.attrs.get("data-author", "[deleted]")
    score = comment_node.attrs.get("data-score", "")
    fullname = comment_node.attrs.get("data-fullname", "")
    body = extract_comment_body(comment_node)

    lines = [f"{indent}- Comment by u/{author} | score: {score} | id: {fullname}"]
    if body:
        for body_line in body.splitlines():
            lines.append(f"{indent}  {body_line}")
    else:
        lines.append(f"{indent}  [No comment body found]")

    for child in nested_comment_children(comment_node):
        lines.extend(format_comment(child, level + 1))
    return lines


def extract_post_body(root: Node) -> str:
    for node in root.find_all("div", "thing"):
        if node.has_class("link") and node.attrs.get("data-fullname", "").startswith("t3_"):
            body = node.first("div", "usertext-body")
            return clean_text(body.text() if body else "")
    return ""


def scrape_post_page(post: dict[str, str], comment_sort: str, user_agent: str) -> tuple[str, list[str]]:
    url = post.get("comments_url", "")
    if not url:
        return "", ["[No comments URL found]"]

    separator = "&" if "?" in url else "?"
    html = fetch_html(f"{url}{separator}sort={urllib.parse.quote(comment_sort)}", user_agent)
    root = parse_html(html)
    post_body = extract_post_body(root)

    comment_area = root.first("div", "commentarea")
    if not comment_area:
        return post_body, ["[No comment area found]"]

    top_level_comments = immediate_comment_children(comment_area)
    if not top_level_comments:
        return post_body, ["[No comments]"]

    lines: list[str] = []
    for comment in top_level_comments:
        lines.extend(format_comment(comment))
    return post_body, lines


def format_post(post: dict[str, str], post_body: str, comments: list[str]) -> str:
    permalink = post.get("permalink", "")
    full_permalink = absolute_old_url(permalink) if permalink else ""

    lines = [
        "=" * 100,
        f"Title: {post.get('title', '')}",
        f"ID: {post.get('id', '')}",
        f"Author: u/{post.get('author', '[deleted]')}",
        f"Subreddit: r/{post.get('subreddit', '')}",
        f"Score: {post.get('score', '')}",
        f"Comments reported by Reddit: {post.get('comments_count', '')}",
        f"Permalink: {full_permalink}",
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


def fetch_subreddit(
    subreddit: str,
    output_dir: pathlib.Path,
    sort: str,
    comment_sort: str,
    max_posts: int,
    delay_seconds: float,
    user_agent: str,
) -> pathlib.Path:
    print(f"Scraping r/{subreddit} listing pages...", file=sys.stderr)
    posts = scrape_listing_posts(subreddit, sort, max_posts, delay_seconds, user_agent)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{safe_filename(subreddit)}.txt"

    with output_path.open("w", encoding="utf-8") as handle:
        handle.write(f"Subreddit: r/{subreddit}\n")
        handle.write(f"Fetched at UTC: {dt.datetime.now(dt.timezone.utc).isoformat()}\n")
        handle.write("Source: old.reddit.com HTML scrape\n")
        handle.write(f"Post sort: {sort}\n")
        handle.write(f"Comment sort: {comment_sort}\n")
        handle.write(f"Posts fetched: {len(posts)}\n\n")

        for index, post in enumerate(posts, start=1):
            print(f"[{subreddit}] {index}/{len(posts)} Scraping comments: {post.get('title', '')}", file=sys.stderr)
            try:
                post_body, comments = scrape_post_page(post, comment_sort, user_agent)
            except Exception as exc:
                post_body = ""
                comments = [f"[Failed to scrape comments: {exc}]"]

            handle.write(format_post(post, post_body, comments))
            handle.write("\n")
            time.sleep(delay_seconds)

    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape Reddit posts and threaded comments into txt files under data/."
    )
    parser.add_argument(
        "subreddits",
        nargs="*",
        default=DEFAULT_SUBREDDITS,
        help=f"Subreddits to scrape. Default: {', '.join(DEFAULT_SUBREDDITS)}",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Folder where txt files will be saved. Default: data",
    )
    parser.add_argument(
        "--sort",
        choices=["hot", "new", "top", "rising"],
        default="new",
        help="Post listing sort to scrape. Default: new",
    )
    parser.add_argument(
        "--comment-sort",
        choices=["confidence", "top", "new", "controversial", "old", "qa"],
        default="confidence",
        help="Comment sort order. Default: confidence",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=0,
        help="Maximum posts per subreddit. Use 0 to scrape until listing pages end. Default: 0",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay in seconds between Reddit requests. Default: 2.0",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="User-Agent header sent to Reddit HTML pages.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = pathlib.Path(args.output_dir)

    for subreddit in args.subreddits:
        try:
            output_path = fetch_subreddit(
                subreddit=subreddit,
                output_dir=output_dir,
                sort=args.sort,
                comment_sort=args.comment_sort,
                max_posts=args.max_posts,
                delay_seconds=args.delay,
                user_agent=args.user_agent,
            )
            print(f"Saved r/{subreddit} to {output_path}")
        except Exception as exc:
            print(f"Failed to scrape r/{subreddit}: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
