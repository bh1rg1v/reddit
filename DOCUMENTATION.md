### fetch_reddit_threads.py

    code related to scraping old.reddit.com posts and comments is available here

    This script scrapes Reddit HTML pages without using Reddit JSON, OAuth, or API endpoints.
    It saves subreddit data in txt files inside the data/ folder.

    Different Classes in fetch_reddit_threads.py are:

    1) Node:
        stores parsed HTML node data

        it tracks tag name, attributes, parent node, children nodes, and text parts

        important functions in Node are:

        a) __init__(tag, attrs, parent):
            initializes one HTML node

        b) append_text(text):
            adds text content to the node

        c) has_class(class_name):
            checks whether the node has a CSS class

        d) classes_include(class_name):
            alias for has_class

        e) find_all(tag, class_name):
            recursively finds matching child nodes by tag and class

        f) first(tag, class_name):
            returns the first matching child node

        g) text:
            returns cleaned combined text from the node and its children

        h) _collect_text:
            internal helper used by text to recursively collect node text

    2) DOMParser:
        parses raw HTML into the custom Node tree

        a) Constructor __init__:
            initializes the root document node and current parser pointer

        b) handle_starttag(tag, attrs):
            creates a Node for every HTML start tag and attaches it to the tree

        c) handle_endtag(tag):
            moves the parser pointer back to the parent when an end tag is found

        d) handle_data(data):
            appends text data to the current node

    Different Functions in fetch_reddit_threads.py are:

    1) parse_html(html):
        parses an HTML string and returns the root Node

    2) clean_text(value):
        normalizes text by removing extra blank lines and whitespace

    3) safe_filename(name):
        converts subreddit names into safe txt filenames

    4) fetch_html(url, user_agent, retries):
        fetches an HTML page using urllib

        it sends browser-like headers and retries on temporary failures

        if Reddit returns 403, it raises a clear scraping block message

    5) listing_url(subreddit, sort):
        creates the old.reddit.com listing URL for a subreddit and sort order

    6) absolute_old_url(url):
        converts a relative Reddit URL into a full old.reddit.com URL

    7) extract_post_from_listing(node):
        extracts post metadata from a listing page div with class thing

        it returns post id, title, author, subreddit, score, comment count, permalink, comments URL, and target URL

    8) find_next_listing_url(root):
        finds the next listing page URL from the old Reddit next-button

    9) scrape_listing_posts(subreddit, sort, max_posts, delay_seconds, user_agent):
        scrapes subreddit listing pages and returns post metadata

        it follows next pages until max_posts is reached or there are no more listing pages

    10) first_direct_child(node, tag, class_name):
        returns the first direct child matching a tag and class

    11) immediate_comment_children(container):
        finds top-level comment nodes inside a comment container

    12) nested_comment_children(comment_node):
        finds direct reply comments under a comment node

    13) extract_comment_body(comment_node):
        extracts comment text from old Reddit's usertext-body markup

    14) format_comment(comment_node, level):
        formats one comment and its nested replies into indented txt lines

    15) extract_post_body(root):
        extracts the post body from the post page

    16) scrape_post_page(post, comment_sort, user_agent):
        opens a post comments page and returns the post body and formatted comment thread

    17) format_post(post, post_body, comments):
        formats one complete post with metadata, body, and comments for txt output

    18) fetch_subreddit(subreddit, output_dir, sort, comment_sort, max_posts, delay_seconds, user_agent):
        scrapes all selected posts for one subreddit

        it creates the data/ folder if required and writes the subreddit txt file

    19) parse_args:
        reads command line options like subreddits, output folder, sort order, max posts, delay, and user agent

    20) main:
        loops through the selected subreddits and calls fetch_subreddit for each one


### fetch_new_reddit_threads.py

    code related to scraping www.reddit.com New Reddit pages is available here

    This script does not use Reddit API endpoints.
    It uses Playwright browser automation because New Reddit is a JavaScript-heavy website.
    It saves subreddit data in txt files inside the data/ folder with _new_reddit suffix.

    Different Functions in fetch_new_reddit_threads.py are:

    1) debug_log(enabled, message):
        prints timestamped debug logs to stderr when --debug is enabled

    2) clean_text(value):
        normalizes text by removing extra blank lines and whitespace

    3) safe_filename(name):
        converts subreddit names into safe txt filenames

    4) listing_url(base_url, subreddit, sort):
        creates the Reddit listing URL for a subreddit and sort order

        the default base URL is https://new.reddit.com

    5) page_diagnostics(page):
        collects diagnostic details from the currently rendered browser page

        it reports current URL, page title, visible text sample, number of shreddit-post elements, article elements, comments links, comment elements, and login indicators

    6) save_debug_artifacts(page, output_dir, name, debug):
        saves rendered page HTML and a screenshot when --debug is enabled

        this helps identify whether Reddit served posts, a login page, an empty shell, or a blocked/interstitial page

    7) auto_scroll(page, max_scrolls, delay_ms, debug):
        scrolls the browser page to load dynamically rendered Reddit content

        it stops early when the page height stops changing

        when debug is enabled, it logs scroll number, page height, and early stop condition

    8) extract_listing_posts(page):
        runs JavaScript inside the browser page to extract rendered post elements

        it first looks for shreddit-post elements

        it also checks article elements with comments links

        it also checks all rendered links containing /comments/

        all strategies run together and post ids are normalized to avoid duplicate posts

    9) scrape_listing_posts(page, base_url, subreddit, sort, max_posts, max_scrolls, delay_ms, debug):
        opens a subreddit listing page and collects post metadata

        it scrolls the page until max_posts is reached or max_scrolls is exhausted

        when debug is enabled, it logs page URL, page title, extracted post counts, and newly added posts

    10) scrape_post_page(page, post, comment_sort, delay_ms, debug):
        opens one post page and extracts post body and comments

        it scrapes rendered shreddit-comment elements and uses their depth attribute to keep thread indentation

        if shreddit-comment elements are not found, it falls back to common comment test id and t1_ id selectors

        when debug is enabled, it logs post page URL, page title, post body length, comment count, and formatted comment details

    11) format_post(post, post_body, comments):
        formats one complete post with metadata, body, and comments for txt output

    12) scrape_subreddit(browser, subreddit, output_dir, sort, comment_sort, max_posts, max_scrolls, delay_ms, debug, user_agent, base_url):
        scrapes one subreddit using the Playwright browser

        it writes output to data/<subreddit>_new_reddit.txt

        it creates a browser context with a browser-like user agent and over18 cookie

        when debug is enabled, it logs page creation, output path, per-post scrape results, failures, and context close

    13) parse_args:
        reads command line options like subreddits, output folder, sort order, max posts, max scrolls, delay, headful mode, debug mode, user agent, and base URL

    14) main_async:
        loads Playwright, starts Chromium, loops through subreddits, and calls scrape_subreddit

        if Playwright is not installed, it prints the install commands

        when debug is enabled, it logs startup configuration, browser launch, subreddit start/end, failures, and browser close

    15) main:
        runs the async scraper entry point using asyncio.run


### Running the scripts

    1) Old Reddit scraper:

        python fetch_reddit_threads.py

    2) New Reddit scraper:

        python fetch_new_reddit_threads.py

    3) Install requirements for New Reddit scraper:

        python -m pip install playwright
        python -m playwright install chromium

    4) Test with fewer posts:

        python fetch_reddit_threads.py --max-posts 5
        python fetch_new_reddit_threads.py --max-posts 5

    5) Run New Reddit scraper with debug logs:

        python fetch_new_reddit_threads.py --max-posts 5 --debug
