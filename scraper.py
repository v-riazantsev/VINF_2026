import argparse
import random
import threading
import time
import os
import requests

SPAWN_INTERVAL = 0.1
REQUEST_INTERVAL = 0.25

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
TIMEOUT = 15

input_lock = threading.Lock()
output_lock = threading.Lock()
state_lock = threading.Lock()
proxy_lock = threading.Lock()
rate_lock = threading.Lock()

active_threads = 0
awaiting_urls = []
active_proxies = []
last_request_time = 0.0

def read_lines(file_path):
    with open(file_path, 'r') as f:
        # splitlines() removes \n, line.strip() filters out empty or whitespace-only lines
        lines = [line for line in f.read().splitlines() if line.strip()]
    return lines

def append_line(lock: threading.Lock, file_path, line):
    with lock:
        with open(file_path, 'a', buffering=1) as f:
            f.write(line + '\n')

def scrape_url(url, proxy_url, output_dir, urls_done_file):
    try:
        global last_request_time
        # Set up the proxy configuration for the request
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

        # Set up the headers for the request
        header = {"User-Agent": random.choice(HEADERS)}

        # Rate limiting: Ensure that requests are spaced out by at least REQUEST_INTERVAL seconds
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
            print(f"[!] Request failed for {url} via {proxy_url}: {e}")

            if proxy_url:
                with proxy_lock:
                    if proxy_url in active_proxies:
                        active_proxies.remove(proxy_url)
            with input_lock:
                awaiting_urls.append(url)
            return  # Exit function cleanly for this worker thread
            

        match response.status_code:
            case 200 | 404: # Success
                append_line(output_lock, urls_done_file, url)
                if response.status_code == 200: # Save the response text to a file in the output directory
                    with open(f"{os.path.join(output_dir, url.split('/')[-1])}.txt", 'w') as f:
                        f.write(response.text)
                print(f"[+] Successfully scraped {url} via {proxy_url}")
            case 403: # Assume the proxy is blocked, remove it from the active proxies list and return url to the awaiting_urls list
                with proxy_lock:
                    if proxy_url in active_proxies:
                        active_proxies.remove(proxy_url)
                with input_lock:
                    awaiting_urls.append(url)
                print(f"[!] Proxy {proxy_url} blocked for {url}, removed from active proxies.")
            case _: # Unknown error, print error and stop the program
                print(f"Error: {response.status_code} for URL: {url} with proxy: {proxy_url}\n{response.text}")
                with open("error_log.txt", 'a') as f:
                    f.write(f"Error: {response.status_code} for URL: {url} with proxy: {proxy_url}\n{response.text}\n")
                os._exit(1)
    finally:
        with state_lock:
            global active_threads
            active_threads -= 1

def spawn_loop(urls_done_file, output_dir, max_threads, spawn_interval):
    global active_threads, awaiting_urls, active_proxies

    with input_lock:
        random.shuffle(awaiting_urls)

    while True:
        with state_lock:
            if active_threads >= max_threads:
                time.sleep(spawn_interval)
                continue

        proxy = None
        with proxy_lock:
            if active_proxies:
                proxy = random.choice(active_proxies) 

        if not proxy:
            time.sleep(spawn_interval)
            continue


        with input_lock:
            has_urls = bool(awaiting_urls)
            url = awaiting_urls.pop() if has_urls else None

        if not has_urls:
            with state_lock:
                if active_threads == 0:
                    break
            # Wait for active urls to finish before checking again
            time.sleep(spawn_interval) 
            continue

        with state_lock:
            active_threads += 1

        thread = threading.Thread(target=scrape_url, args=(url, proxy, output_dir, urls_done_file,))
        thread.start()

        time.sleep(spawn_interval)

def proxy_refresh_loop(refresh_interval):
    global active_proxies
    while True:
        try:
            new_proxies = get_proxies()
            with proxy_lock:
                active_proxies = new_proxies
            print(f"[+] Refreshed proxies. Total active proxies: {len(active_proxies)}")
        except Exception as e:
            print(f"[!] Failed to refresh proxies: {e}")
        time.sleep(refresh_interval)


def get_proxies():
    response = requests.get(PROXY_API_URL)
    response.raise_for_status()
    proxy_list = response.text.strip().split()
    return proxy_list

def main():
    global awaiting_urls

    parser = argparse.ArgumentParser()
    parser.add_argument('--urls_path', type=str, required=True, help='List of URLs to scrape')
    parser.add_argument('--urls_done', type=str, required=False, default="./urls_done.txt", help='List of URLs that have already been scraped')
    parser.add_argument('--output_dir', type=str, required=True, help='Output file to save the scraped data')
    parser.add_argument('--max_threads', type=int, required=False, default=64, help='Maximum number of threads to use for scraping')

    args = parser.parse_args()

    # Check if the output directory exists, if not create it
    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)

    # Check if the urls_done file exists, if not create it
    if not os.path.exists(args.urls_done):
        open(args.urls_done, 'w').close()


    # Create a list of URLs to scrape, excluding those that have already been scraped or failed
    done_urls = read_lines(args.urls_done)
    awaiting_urls = list(set(read_lines(args.urls_path)) - set(done_urls))


    # Start the proxy refresh loop in a separate thread
    proxy_refresh_thread = threading.Thread(target=proxy_refresh_loop, args=(PROXY_REFRESH_INTERVAL,), daemon=True)
    proxy_refresh_thread.start()


    # Start the spawn loop to scrape URLs
    spawn_loop(args.urls_done, args.output_dir, args.max_threads, SPAWN_INTERVAL)

if __name__ == "__main__":
    main()