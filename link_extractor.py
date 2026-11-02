import argparse
import re

# Extracts the exact hostname from a URL using regex.
def extract_hostname(url):
    match = re.search(r"https?://([^/]+)", url)
    return match.group(1) if match else ""

def extract_links(html_content, current_url):
    # Changed .*# to ^# so it only blocks links starting with a hash, not URLs with fragments at the end
    pattern = r"\bhref=[\"'](?!#|[^\"']*\.(?:png|svg|jpg|jpeg|gif|webp|css|xml|ico|html)[\'\"])([^\"']+)[\"']"
    
    current_host = extract_hostname(current_url)
    
    # Base URL for relative links (scheme + host)
    base_match = re.match(r"(https?://[^/]+)", current_url)
    base_url = base_match.group(1) if base_match else current_url
    
    matches = re.findall(pattern, html_content)
    valid_links = []
    
    for link in matches:
        # 1. Handle absolute URLs (check exact host match)
        if link.startswith("http://") or link.startswith("https://"):
            link_host = extract_hostname(link)
            if link_host != current_host:
                continue
        # 2. Handle protocol-relative URLs (e.g., //keeptrack.space/...)
        elif link.startswith("//"):
            link_host_match = re.match(r"//([^/]+)", link)
            link_host = link_host_match.group(1) if link_host_match else ""
            if link_host != current_host:
                continue
            link = "https:" + link
        # 3. Ignore non-HTTP schemes
        elif link.startswith("mailto:") or link.startswith("tel:") or link.startswith("javascript:"):
            continue
        
        # 4. Append base URL to relative links
        if not (link.startswith("http://") or link.startswith("https://") or link.startswith("//")):
            if link.startswith("/"):
                link = base_url + link
            else:
                link = base_url + "/" + link.lstrip('/')
                
        valid_links.append(link)
        
    return valid_links

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_html", type=str, required=True, help="Path to the input HTML file.")
    parser.add_argument("--current_url", type=str, required=True, help="The current URL of the page being processed.")

    args = parser.parse_args()

    with open(args.input_html, 'r', encoding='utf-8') as f:
        html_content = f.read()

    extracted_links = extract_links(html_content, args.current_url)
    for link in extracted_links:
        print(link)

if __name__ == "__main__":
    main()