import os
import sys
import json
import requests
import subprocess
import time
import google.generativeai as genai
from bs4 import BeautifulSoup
import re

# Configuration
STREAM_P2P_API_KEY = "a7165e18e69dc32127258688"
GEMINI_API_KEY = "AIzaSyB07_z63Jz93_oRVrYPW1vCPsbWxiZMNBs"
TORRENTSDB_API_BASE = "https://torrentsdb.com/stream/anime/"
DOWNLOADED_LOG_FILE = "downloaded_anime.json"

# Initialize AI
genai.configure(api_key=GEMINI_API_KEY)

def get_model():
    """Try to find an available Gemini model with robust detection."""
    models = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]
    for model_name in models:
        try:
            available_models = [m.name for m in genai.list_models()]
            full_name = f"models/{model_name}"
            if full_name in available_models:
                print(f"Using Gemini model: {model_name}")
                return genai.GenerativeModel(model_name)
        except Exception as e:
            print(f"Error checking model {model_name}: {e}")
            continue
    print("Falling back to gemini-1.5-flash")
    return genai.GenerativeModel("gemini-1.5-flash")

model = get_model()

def load_downloaded_log():
    if os.path.exists(DOWNLOADED_LOG_FILE):
        with open(DOWNLOADED_LOG_FILE, 'r') as f:
            return json.load(f)
    return []

def save_downloaded_log(log):
    with open(DOWNLOADED_LOG_FILE, 'w') as f:
        json.dump(log, f, indent=4)

def is_already_downloaded(anime_title, release_type, episode_number, downloaded_log):
    for entry in downloaded_log:
        if entry.get('anime_title') == anime_title and \
           entry.get('release_type') == release_type and \
           entry.get('episode_number') == episode_number:
            return True
    return False

def extract_episode_number(title):
    # Regex to find episode numbers like E01, EP01, #01, 01, S01E01
    match = re.search(r'[EePpSs]?(\d{2,4})', title)
    if match:
        return match.group(1).lstrip('0') or '0'
    return None

def ai_research_anime(anime_name):
    print(f"AI Researching: {anime_name}")
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
    Example: [{{"title": "Anime Name", "type": "TV", "year": 2023, "episodes": 12, "remaster": false}}]
    """
    try:
        response = model.generate_content(prompt)
        text = response.text
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text.strip())
    except Exception as e:
        print(f"AI Research failed: {e}. Falling back to basic search for '{anime_name}'.")
        return [{"title": anime_name, "type": "TV", "remaster": False}]

def search_sources(rel):
    results = []
    # 1. TorrentsDB
    if rel.get('kitsu_id'):
        url = f"{TORRENTSDB_API_BASE}kitsu:{rel['kitsu_id']}.json"
        try:
            resp = requests.get(url, timeout=15).json()
            for s in resp.get('streams', []):
                if 'infoHash' in s:
                    results.append({
                        'title': s.get('title', rel['title']),
                        'magnet': f"magnet:?xt=urn:btih:{s['infoHash']}",
                        'seeders': 999
                    })
        except: pass

    # 2. Nyaa
    query = f"{rel['title']} 1080p"
    nyaa_url = f"https://nyaa.si/?f=0&c=1_2&q={query.replace(' ', '+')}&s=seeders&o=desc"
    try:
        resp = requests.get(nyaa_url, timeout=15)
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
    
    results.sort(key=lambda x: x['seeders'], reverse=True)
    return results

def download_torrent(magnet, timeout=60):
    print(f"Downloading magnet: {magnet[:60]}...")
    cmd = [
        "aria2c", 
        "--seed-time=0", 
        "--summary-interval=10", 
        "--follow-torrent=mem",
        "--max-overall-download-limit=0",
        "--bt-stop-timeout=60",
        magnet
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    last_check = time.time()
    
    while True:
        line = process.stdout.readline()
        if not line: break
        print(line.strip())
        
        if "DL:" in line:
            if "DL:0B" in line:
                if (time.time() - last_check) > timeout:
                    print("Download stalled for 1 minute. Moving on.")
                    process.terminate()
                    return None
            else:
                last_check = time.time()
                
        if process.poll() is not None: break
        
    if process.returncode == 0:
        for f in os.listdir('.'):
            if f.endswith(('.mkv', '.mp4')):
                return f
    return None

def process_and_upload(file_path, info, downloaded_log):
    remaster = "Remastered" if info.get('remaster') else "Original"
    num = info.get('episodes', info.get('number', ''))
    base_name = f"{info['title']} {info['type']} {num} {remaster}"
    
    ss_name = f"{base_name} Ss.mkv"
    hs_name = f"{base_name} Hs.mp4"
    
    print(f"Processing: {base_name}")
    
    if os.path.exists(file_path):
        os.rename(file_path, ss_name)
    else:
        print(f"Error: Downloaded file {file_path} not found.")
        return
    
    print("Hardsubbing...")
    try:
        subprocess.run([
            "ffmpeg", "-i", ss_name, "-vf", f"subtitles='{ss_name}'",
            "-c:v", "libx264", "-crf", "22", "-preset", "veryfast", "-c:a", "copy", hs_name
        ], check=True)
    except Exception as e:
        print(f"Hardsubbing failed: {e}. Only Softsub will be uploaded.")
        hs_name = None

    for f in [ss_name, hs_name]:
        if not f or not os.path.exists(f): continue
        size_gb = os.path.getsize(f) / (1024**3)
        if size_gb > 50:
            print(f"Splitting large file: {f}")
            subprocess.run(["split", "-b", "49G", f, f + ".part"], check=True)
            for part in sorted([p for p in os.listdir('.') if p.startswith(f + ".part")]):
                upload_to_streamp2p(part)
                try: os.remove(part)
                except: pass
        else:
            upload_to_streamp2p(f)
        
        try: os.remove(f)
        except: pass
    
    downloaded_log.append({
        'anime_title': info['title'],
        'release_type': info['type'],
        'episode_number': num,
        'timestamp': time.time()
    })
    save_downloaded_log(downloaded_log)

def upload_to_streamp2p(file_path):
    print(f"Uploading {file_path}...")
    url = "https://streamp2p.com/api/upload/server"
    try:
        resp = requests.get(url, params={'key': STREAM_P2P_API_KEY}, timeout=20).json()
        upload_url = resp['result']
        with open(file_path, 'rb') as f:
            r = requests.post(upload_url, data={'key': STREAM_P2P_API_KEY}, files={'file': f}, timeout=7200)
            if r.status_code == 200:
                print(f"Upload of {file_path} successful. Response: {r.json()}")
            else:
                print(f"Upload of {file_path} failed with status {r.status_code}. Response: {r.text}")
    except Exception as e:
        print(f"Upload failed for {file_path} due to exception: {e}")

def main(anime_name):
    downloaded_log = load_downloaded_log()
    releases = ai_research_anime(anime_name)
    for rel in releases:
        episode_num = extract_episode_number(rel.get('title', '')) or rel.get('episodes', rel.get('number', ''))
        
        if is_already_downloaded(rel['title'], rel['type'], episode_num, downloaded_log):
            print(f"Skipping {rel['title']} {rel['type']} {episode_num} - already downloaded.")
            continue

        print(f"Found Release: {rel['title']} ({rel['type']})")
        results = search_sources(rel)
        if not results:
            print(f"No torrents found for {rel['title']}")
            continue
        
        for best in results[:3]:
            downloaded = download_torrent(best['magnet'])
            if downloaded:
                process_and_upload(downloaded, rel, downloaded_log)
                break

if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(sys.argv[1])
    else:
        print("Usage: python bulk_downloader.py 'Anime Name'")
