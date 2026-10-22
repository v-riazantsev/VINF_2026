import argparse
import random
import threading
import time
import os
import requests
import asyncio
import tempfile
import shutil
import nodriver as uc

import link_extractor

SPAWN_INTERVAL = 0.1 # seconds
REQUEST_INTERVAL = 0.3 # seconds

PROXY_API_URL = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&protocol=http&anonymity=elite%2Canonymous&ssl=yes"
PROXY_REFRESH_INTERVAL = 60 # seconds

HEADERS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0"
]
TIMEOUT = 15 # seconds

NODRIVER_BROWSER_PATH = "/usr/bin/chromium"

IGNORE_URLS_MASK = [
    "https://keeptrack.space/launch/*",
    "https://keeptrack.space/tag/*",
    "https://keeptrack.space/app/*"
]

state_lock = threading.Lock()
proxy_lock = threading.Lock()
rate_lock = threading.Lock()
nodriver_lock = threading.Lock() # Lock to ensure only one nodriver instance runs at a time
captcha_list_lock = threading.Lock()
done_urls_lock = threading.Lock()
awaiting_urls_lock = threading.Lock()
processing_urls_lock = threading.Lock()

active_threads = 0
awaiting_urls = set() # Set of URLs that are waiting to be scraped
processing_urls = set() # Set of URLs that are currently being processed
captcha_urls = set() # List of URLs identified as protected by Cloudflare/captcha
done_urls = set() # Set of URLs that have already been scraped successfully
active_proxies = []
last_request_time = 0.0
last_nodriver_request_time = 0.0

max_threads = 32
output_dir = ""
awaiting_urls_file = ""
urls_done_file = ""

def read_lines(file_path):
    with open(file_path, 'r') as f:
        # splitlines() removes \n, line.strip() filters out empty or whitespace-only lines
        lines = [line for line in f.read().splitlines() if line.strip()]
    return lines

def append_line(file_path, line):
    with open(file_path, 'a', buffering=1) as f:
        f.write(line + '\n')

def mark_url_as_done(urls_done_file, url):
    with awaiting_urls_lock, processing_urls_lock, captcha_list_lock, done_urls_lock:
        if url not in done_urls:
            done_urls.add(url)
            append_line(urls_done_file, url)

        awaiting_urls.discard(url)
        processing_urls.discard(url)
        captcha_urls.discard(url)

def enqueue_new_url(urls_awaiting_file, url):
    with awaiting_urls_lock, processing_urls_lock, captcha_list_lock, done_urls_lock:
        if (
            url in done_urls
            or url in awaiting_urls
            or url in processing_urls
            or url in captcha_urls
            or is_url_ignored(url)
        ):
            return False

        awaiting_urls.add(url)
        append_line(urls_awaiting_file, url)
        return True

def add_url_to_awaiting(urls_awaiting_file, url):
    with processing_urls_lock:
        processing_urls.discard(url)

    with awaiting_urls_lock:
        awaiting_urls.add(url)
        append_line(urls_awaiting_file, url)
    
def process_scraped_content(html_content, current_url, output_dir, urls_awaiting_file):
    save_scraped_content(output_dir, current_url, html_content)

    links = link_extractor.extract_links(html_content, current_url)
    for link in links:
        if enqueue_new_url(urls_awaiting_file, link):
            print(f"[+] Found new link: {link} (from {current_url})")

def save_scraped_content(output_dir, url, content):
    # Remove protocol (e.g. https://)
    path_part = url.split("://")[-1]
    
    # Replace characters that are unsafe for filenames/paths while preserving structure
    # Convert query parameters (? -> _q_) and fragments (# -> _h_) to keep separate files distinct
    clean_path = path_part.replace('?', '_q_').replace('=', '_').replace('&', '_').replace('#', '_h_')
    
    # Split into directory components and filename
    parts = [p for p in clean_path.split('/') if p]
    
    if not parts:
        parts = ["index"]
        
    domain_and_dirs = parts[:-1]
    filename = parts[-1]
    
    if not filename.endswith('.html'):
        filename += '.html'
        
    # Construct target directory path and ensure it exists
    target_dir = os.path.join(output_dir, *domain_and_dirs)
    os.makedirs(target_dir, exist_ok=True)
    
    output_path = os.path.join(target_dir, filename)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

def is_captcha_protected_content(content):
    return (
        "Ray ID:" in content 
        or "Verify you are human" in content 
        or "Just a moment..." in content
        or "cf-challenge" in content
    )

def is_url_ignored(url):
    for mask in IGNORE_URLS_MASK:
        if mask.endswith('*'):
            if url.startswith(mask[:-1]):
                return True
        elif url == mask:
            return True
    return False

async def scrape_with_nodriver(url):
    # Create a completely fresh, temporary profile directory
    user_data_dir = tempfile.mkdtemp(prefix="nodriver_profile_")

    browser = None
    try:
        kwargs = {
            "user_data_dir": user_data_dir,
            "sandbox": True,
            "browser_executable_path": NODRIVER_BROWSER_PATH,
        }
            
        browser = await uc.start(**kwargs)
        
        print(f"[*] Navigating to {url} via nodriver (without proxy)...")
        page = await browser.get(url)
        
        # Check if page is loaded before evaluating content; timeout after 60 seconds
        is_loaded = False
        is_solved = False
        
        for second in range(30):
            await asyncio.sleep(1)
            try:
                # Check readyState first to confirm the page has actually finished initial loading
                ready_state = await page.evaluate("document.readyState")
                if ready_state in ["interactive", "complete"]:
                    is_loaded = True
            except Exception:
                continue
                
            if not is_loaded:
                if second % 10 == 0 and second > 0:
                    print(f"[*] Waiting for connection / page load on {url}... ({second}s)")
                continue
            
            try:
                content = await page.get_content()
            except Exception:
                continue
            
            # Once loaded and challenge is cleared, we have success
            if not is_captcha_protected_content(content):
                print(f"[+] Cloudflare verification cleared successfully after {second} seconds!")
                is_solved = True
                break
                
            if second % 10 == 0 and second > 0:
                print(f"[*] Waiting for challenge resolution on {url}... ({second}s elapsed)")

        if not is_loaded or not is_solved:
            raise TimeoutError(f"Nodriver timeout or failed to resolve challenge for {url}")

        # Extra buffer for slow DOM/table data initialization
        await asyncio.sleep(6)
        html_content = await page.get_content()
        return html_content
    finally:
        if browser:
            try:
                # Explicitly close all WebSocket connections and CDP targets to prevent leaks
                await asyncio.gather(
                    *(connection.aclose() for connection in [*getattr(browser, 'targets', []), browser]),
                    return_exceptions=True,
                )
            except Exception:
                pass
            
            process = getattr(browser, '_process', None)
            try:
                browser.stop()
            except Exception:
                pass
                
            if process is not None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except Exception:
                    try:
                        process.terminate()
                    except Exception:
                        pass

        # Clean up temporary profile directory
        shutil.rmtree(user_data_dir, ignore_errors=True)

def scrape_url(url, proxy_url):
    try:
        global last_request_time, active_threads
        
        # Standard requests scraping logic
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

        # Set up the headers for the request
        header = {"User-Agent": random.choice(HEADERS)}

        # Rate limiting
        with rate_lock:
            now = time.time()
            elapsed = now - last_request_time
            if elapsed < REQUEST_INTERVAL:
                time.sleep(REQUEST_INTERVAL - elapsed)

        try:
            # Make the request to the URL with the specified proxy and headers
            response = requests.get(
                url,
                proxies=proxies,
                timeout=TIMEOUT,
                headers=header,
                verify=True
            )

            # Update the last request time after the request is made
            with rate_lock:
                last_request_time = time.time()

        except (requests.exceptions.RequestException) as e:
            should_remove_proxy = False

            # 1. Check for SSL Errors
            if isinstance(e, requests.exceptions.SSLError):
                should_remove_proxy = True

            # 2. Check for Connection Reset (Errno 104) or Remote Disconnections
            elif isinstance(e, requests.exceptions.ConnectionError):
                error_msg = str(e)
                if (
                    "104" in error_msg 
                    or "Connection reset by peer" in error_msg 
                    or "Remote end closed connection" in error_msg
                    or "IncompleteRead" in error_msg
                    or "Connection broken" in error_msg
                ):
                    should_remove_proxy = True

            # 3. Handle proxy removal
            if should_remove_proxy:
                with proxy_lock:
                    if proxy_url in active_proxies:
                        active_proxies.remove(proxy_url)
                        print(f"[-] Removed dead proxy: {proxy_url}")
            elif not isinstance(e, ( # Ignore known transient errors
                requests.exceptions.ProxyError, 
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout
            )):
                print(f"[!] Request failed for {url} via {proxy_url}: {e}")

            # Return the URL back to the awaiting list for future retry
            add_url_to_awaiting(awaiting_urls_file, url)

            return

        def add_url_to_captcha_list(url):
            with processing_urls_lock, captcha_list_lock:
                captcha_urls.add(url)
                processing_urls.discard(url)

        match response.status_code:
            case 200 | 404: # Success
                # Check if content is protected by Cloudflare / captcha challenge
                if is_captcha_protected_content(response.text):
                    print(f"[!] Page {url} is protected by Cloudflare/CAPTCHA. Adding to captcha list.")
                    add_url_to_captcha_list(url)
                else:
                    if response.status_code == 200: # Save the response text to a file in the output directory
                        process_scraped_content(response.text, url, output_dir, awaiting_urls_file)
                    mark_url_as_done(urls_done_file, url)
                    print(f"[+] Scraped {url} via {proxy_url}")
            case 403 | 502: # Check if 403 is due to Cloudflare challenge before assuming proxy block
                if is_captcha_protected_content(response.text):
                    print(f"[!] Page {url} returned 403 due to Cloudflare/CAPTCHA. Adding to captcha list.")
                    add_url_to_captcha_list(url)
                else:
                    with proxy_lock:
                        if proxy_url in active_proxies:
                            active_proxies.remove(proxy_url)
                    add_url_to_awaiting(awaiting_urls_file, url)
                    print(f"[!] Proxy {proxy_url} blocked for {url}, removed from active proxies.")
            case _: # Unknown error, print error and stop the program
                print(f"Error: {response.status_code} for URL: {url} with proxy: {proxy_url}\n{response.text}")
                with open("error_log.txt", 'a') as f:
                    f.write(f"Error: {response.status_code} for URL: {url} with proxy: {proxy_url}\n{response.text}\n")
                os._exit(1)
    finally:
        with state_lock:
            active_threads -= 1

def scraper_spawn_loop():
    global active_threads

    while True:
        # Check if we can spawn a new thread based on the max_threads limit
        with state_lock:
            if active_threads >= max_threads:
                time.sleep(SPAWN_INTERVAL)
                continue

        # Select a proxy from the active_proxies list
        proxy = None
        with proxy_lock:
            if active_proxies:
                proxy = random.choice(active_proxies) 

        # If no proxy is available, wait and retry
        if not proxy:
            time.sleep(SPAWN_INTERVAL)
            continue

        # Select a URL from the awaiting_urls list that is not currently being processed
        with awaiting_urls_lock, processing_urls_lock:
            available_urls = awaiting_urls - processing_urls - captcha_urls
            if available_urls:
                url = available_urls.pop()
                processing_urls.add(url)
            else:
                url = None

        # If no URL is available, check if there are any active threads
        if not url:
            with processing_urls_lock, captcha_list_lock:
                if not processing_urls and not captcha_urls:
                    break # Exit the loop if there are no more URLs to process and no active threads
            # Wait for active urls to finish before checking again
            time.sleep(SPAWN_INTERVAL)
            continue

        # Spawn a new thread to scrape the selected URL with the selected proxy
        with state_lock:
            active_threads += 1

        thread = threading.Thread(target=scrape_url, args=(url, proxy,))
        thread.start()

        time.sleep(SPAWN_INTERVAL)

def nodriver_loop():
    while True:
        # Check if there are any URLs in the captcha_urls list to process
        with processing_urls_lock, captcha_list_lock:
            available_urls = captcha_urls - processing_urls
            if available_urls:
                print(f"[*] Remaining URLs in captcha list: {len(captcha_urls)}")
                url = available_urls.pop()
                processing_urls.add(url)
            else:
                url = None

        # If no URL is available, check if there are any active threads
        if not url:
            with awaiting_urls_lock, processing_urls_lock:
                if not processing_urls and not awaiting_urls:
                    break # Exit the loop if there are no more URLs to process and no active threads
            # Wait for active urls to finish before checking again
            time.sleep(SPAWN_INTERVAL)
            continue

        try:
            # scrape the URL using nodriver
            html_content = asyncio.run(scrape_with_nodriver(url))

            # Check for not found
            if "Not Found" in html_content or "not found" in html_content:
                print(f"[*] URL not found: {url}")
                mark_url_as_done(urls_done_file, url)
                continue

            # Extract links and save the content, then mark the URL as done
            process_scraped_content(html_content, url, output_dir, awaiting_urls_file)
            mark_url_as_done(urls_done_file, url)

            print(f"[+] Scraped (nodriver) {url}")
        except Exception as e:
            print(f"[!] Nodriver failed / timed out for {url}: {e}")
            # Return URL back to captcha list for future retry
            with captcha_list_lock, processing_urls_lock:
                captcha_urls.add(url)
                processing_urls.discard(url)

        time.sleep(SPAWN_INTERVAL)

def proxy_refresh_loop(refresh_interval):
    global active_proxies
    while True:
        try:
            new_proxies = get_proxies()
            with proxy_lock:
                active_proxies = new_proxies
            print(f"[+] Refreshed proxies. Total active proxies: {len(active_proxies)}")
        except Exception as e:
            print(f"[!] Failed to refresh proxies: {e}\n retrying in 5 seconds...")
            time.sleep(5)
            continue

        time.sleep(refresh_interval)


def get_proxies():
    response = requests.get(PROXY_API_URL, timeout=TIMEOUT)
    response.raise_for_status()
    proxy_list = response.text.strip().split()
    return proxy_list

def main():
    global awaiting_urls, done_urls, output_dir
    global awaiting_urls_file, urls_done_file, max_threads

    parser = argparse.ArgumentParser()
    parser.add_argument('--urls_path', type=str, required=True, help='List of URLs to scrape')
    parser.add_argument('--urls_done', type=str, required=False, default="./urls_done.txt", help='List of URLs that have already been scraped')
    parser.add_argument('--output_dir', type=str, required=True, help='Output directory to save the scraped data')
    parser.add_argument('--max_threads', type=int, required=False, default=64, help='Maximum number of threads to use for scraping')

    args = parser.parse_args()

    output_dir = args.output_dir
    awaiting_urls_file = args.urls_path
    urls_done_file = args.urls_done
    max_threads = args.max_threads

    # Check if the urls_path file exists
    if not os.path.exists(args.urls_path):
        print(f"[!] The specified urls_path file does not exist: {args.urls_path}")
        return

    # Check if the output directory exists, if not create it
    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)

    # Check if the urls_done file exists, if not create it
    if not os.path.exists(args.urls_done):
        open(args.urls_done, 'w', encoding='utf-8').close()

    # Create a list of URLs to scrape, excluding those that have already been scraped or failed
    done_urls = set(read_lines(args.urls_done))
    awaiting_urls = set(set(read_lines(args.urls_path)) - done_urls)

    print(f"Total URLs to scrape: {len(awaiting_urls)}")


    # Start the proxy refresh loop in a separate thread
    proxy_refresh_thread = threading.Thread(target=proxy_refresh_loop, args=(PROXY_REFRESH_INTERVAL,), daemon=True)
    proxy_refresh_thread.start()

    # Start the scraper spawn loop to scrape URLs
    scraper_spawn_thread = threading.Thread(target=scraper_spawn_loop, daemon=True)
    scraper_spawn_thread.start()

    # Start the nodriver loop to handle Cloudflare/captcha protected pages
    nodriver_thread = threading.Thread(target=nodriver_loop, daemon=True)
    nodriver_thread.start()
    
    # Wait for the scraper spawn loop and nodriver loop to finish
    scraper_spawn_thread.join()
    nodriver_thread.join()

if __name__ == "__main__":
    main()