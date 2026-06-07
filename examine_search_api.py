from curl_cffi import requests as cr
import re

cookie = open(r'D:\Job\.iimjobs_cookie.txt', encoding='utf-8').read().strip()
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Cookie': cookie,
}

base = 'https://js-static.iimjobs.com/production/9.2.0/_next/static/chunks/'
r = cr.get(base + 'pages/_app-0217bed1dfc3fff0.js', headers=headers, impersonate='chrome124', timeout=20)
text = r.text

# Find the searchJobsbyQuery and extract more context
idx = text.find('searchJobsbyQuery')
if idx >= 0:
    # Get 2000 chars after
    ctx = text[idx:idx+2000]
    print(ctx)
    print("\n\n=====\n\n")

# Also look for the O variable definition - search backwards from '/search'
# First find all .get("/search" occurrences
for m in re.finditer(r'\.get\s*\(\s*["\']/(?:search|Search)["\']', text):
    # Look 200 chars before for variable assignment
    start = max(0, m.start()-400)
    ctx = text[start:m.end()+50]
    print(f'GET /search context:')
    print(ctx)
    print('---')
