import os
import sys
import json
import requests
import subprocess
import time
import google.generativeai as genai
from bs4 import BeautifulSoup

# Configuration
STREAM_P2P_API_KEY = "a7165e18e69dc32127258688"
GEMINI_API_KEY = "AIzaSyAP87S2pmV4N5ZinxSRqZpu6D1Y7CTidJg"
TORRENTSDB_API_BASE = "https://torrentsdb.com/stream/anime/"

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

def ai_research_anime(anime_name):
    prompt = f"""
    Research the anime "{anime_name}". 
    Provide a JSON list of all its releases including:
    - Title (English and Romaji)
    - Kitsu ID (if known, else null)
    - Type (TV, Movie, OVA, ONA, Special)
    - Release Year
    - Episode Count (if TV) or Number (if Movie/OVA)
    - Is it a Remaster? (True/False)
    
    Format the output strictly as a JSON array of objects.
    """
    response = model.generate_content(prompt)
    text = response.text
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    return json.loads(text.strip())

def search_sources(rel):
    results = []
    # 1. TorrentsDB
    if rel.get('kitsu_id'):
        url = f"{TORRENTSDB_API_BASE}kitsu:{rel['kitsu_id']}.json"
        try:
            resp = requests.get(url, timeout=10).json()
            for s in resp.get('streams', []):
                if 'infoHash' in s:
                    results.append({
                        'title': s.get('title', rel['title']),
                        'magnet': f"magnet:?xt=urn:btih:{s['infoHash']}",
                        'seeders': 999 # TorrentsDB doesn't always show seeds, assume high
                    })
        except: pass

    # 2. Nyaa
    query = f"{rel['title']} 1080p"
    nyaa_url = f"https://nyaa.si/?f=0&c=1_2&q={query.replace(' ', '+')}&s=seeders&o=desc"
    try:
        resp = requests.get(nyaa_url, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        table = soup.find('table', class_='torrent-list')
        if table:
            for row in table.find_all('tr')[1:]:
                cols = row.find_all('td')
                title = cols[1].find_all('a')[-1].text.strip()
                magnet = cols[2].find_all('a')[1]['href']
                seeders = int(cols[5].text)
                results.append({'title': title, 'magnet': magnet, 'seeders': seeders})
    except: pass
    
    # Sort by seeders
    results.sort(key=lambda x: x['seeders'], reverse=True)
    return results

def download_torrent(magnet, timeout=60):
    print(f"Downloading: {magnet}")
    cmd = ["aria2c", "--seed-time=0", "--summary-interval=10", magnet]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    last_check = time.time()
    while True:
        line = process.stdout.readline()
        if not line: break
        print(line.strip())
        if "DL:0B" in line and (time.time() - last_check) > timeout:
            process.terminate()
            return None
        elif "DL:" in line and "DL:0B" not in line:
            last_check = time.time()
        if process.poll() is not None: break
        
    if process.returncode == 0:
        for f in os.listdir('.'):
            if f.endswith(('.mkv', '.mp4')): return f
    return None

def process_and_upload(file_path, info):
    remaster = "Remastered" if info.get('remaster') else "Original"
    num = info.get('episodes', info.get('number', ''))
    base_name = f"{info['title']} {info['type']} {num} {remaster}"
    
    ss_name = f"{base_name} SS.mkv"
    hs_name = f"{base_name} HS.mp4"
    
    os.rename(file_path, ss_name)
    
    # Hardsub
    subprocess.run([
        "ffmpeg", "-i", ss_name, "-vf", f"subtitles='{ss_name}'",
        "-c:v", "libx264", "-crf", "22", "-preset", "veryfast", "-c:a", "copy", hs_name
    ], check=True)
    
    # Chunking & Upload
    for f in [ss_name, hs_name]:
        size_gb = os.path.getsize(f) / (1024**3)
        if size_gb > 50:
            print(f"Splitting {f}...")
            subprocess.run(["split", "-b", "49G", f, f + ".part"], check=True)
            for part in sorted([p for p in os.listdir('.') if p.startswith(f + ".part")]):
                upload_to_streamp2p(part)
        else:
            upload_to_streamp2p(f)

def upload_to_streamp2p(file_path):
    url = "https://streamp2p.com/api/upload/server"
    try:
        resp = requests.get(url, params={'key': STREAM_P2P_API_KEY}).json()
        upload_url = resp['result']
        with open(file_path, 'rb') as f:
            requests.post(upload_url, data={'key': STREAM_P2P_API_KEY}, files={'file': f})
    except: pass

def main(anime_name):
    releases = ai_research_anime(anime_name)
    for rel in releases:
        print(f"Processing: {rel['title']}")
        results = search_sources(rel)
        if not results: continue
        
        # Try top 3 magnets
        for best in results[:3]:
            downloaded = download_torrent(best['magnet'])
            if downloaded:
                process_and_upload(downloaded, rel)
                break

if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(sys.argv[1])
