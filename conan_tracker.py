import os
import requests
import feedparser
import subprocess
import time
from datetime import datetime
import pytz

# Configuration
STREAM_P2P_API_KEY = "a7165e18e69dc32127258688"
SEARCH_QUERY = "Detective Conan 1080p"
NYAA_RSS_URL = f"https://nyaa.si/?page=rss&q={SEARCH_QUERY.replace(' ', '+')}&c=1_2&f=0"
# TorrentsDB (Stremio Addon API structure)
TORRENTSDB_API_BASE = "https://torrentsdb.com/stream/anime/"

def get_est_time():
    est = pytz.timezone('US/Eastern')
    return datetime.now(est)

def search_torrentsdb(kitsu_id):
    """
    Search TorrentsDB using Kitsu ID (Detective Conan is kitsu:214)
    Endpoint: /stream/anime/{id}.json
    """
    url = f"{TORRENTSDB_API_BASE}{kitsu_id}.json"
    print(f"Searching TorrentsDB: {url}")
    try:
        resp = requests.get(url, timeout=10).json()
        streams = resp.get('streams', [])
        results = []
        for s in streams:
            # Stremio streams usually have infoHash or url
            if 'infoHash' in s:
                results.append({
                    'title': s.get('title', 'Unknown'),
                    'magnet': f"magnet:?xt=urn:btih:{s['infoHash']}",
                    'source': 'TorrentsDB'
                })
        return results
    except Exception as e:
        print(f"TorrentsDB search failed: {e}")
        return []

def get_latest_nyaa():
    print(f"Searching Nyaa RSS: {NYAA_RSS_URL}")
    feed = feedparser.parse(NYAA_RSS_URL)
    if not feed.entries:
        return []
    return [{
        'title': e.title,
        'magnet': e.link,
        'source': 'Nyaa'
    } for e in feed.entries]

def download_torrent(magnet, timeout=120):
    print(f"Starting download: {magnet}")
    # --max-overall-download-limit=0 (unlimited)
    # --seed-time=0 (stop after download)
    cmd = [
        "aria2c", 
        "--seed-time=0", 
        "--summary-interval=10",
        "--follow-torrent=mem",
        magnet
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    last_check_time = time.time()
    
    while True:
        line = process.stdout.readline()
        if not line: break
        print(line.strip())
        
        # Check for stalls (0B/s)
        if "DL:0B" in line and (time.time() - last_check_time) > timeout:
            print("Download stalled. Killing process.")
            process.terminate()
            return None
        elif "DL:" in line and "DL:0B" not in line:
            last_check_time = time.time()
            
        if process.poll() is not None: break
        
    if process.returncode == 0:
        for f in os.listdir('.'):
            if f.endswith(('.mkv', '.mp4')):
                return f
    return None

def process_video(input_file, title):
    # Standardize naming: Detective Conan quality episode number hs/ss
    # Extract episode number if possible, else use title
    clean_title = title.split(']')[1].split('[')[0].strip() if ']' in title else title
    ss_file = f"{clean_title} SS.mkv"
    hs_file = f"{clean_title} HS.mp4"
    
    print(f"Naming: {ss_file} / {hs_file}")
    os.rename(input_file, ss_file)
    
    # Hardsubbing with optimized settings for high quality
    # -crf 18 is high quality, -preset slow/medium for better compression
    print("Hardsubbing (this may take a while)...")
    cmd_hs = [
        "ffmpeg", "-i", ss_file, 
        "-vf", f"subtitles='{ss_file}'", 
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", 
        "-c:a", "copy", hs_file
    ]
    subprocess.run(cmd_hs, check=True)
    return ss_file, hs_file

def upload_to_streamp2p(file_path):
    print(f"Uploading {file_path}...")
    url = "https://streamp2p.com/api/upload/server"
    try:
        # Step 1: Get Server
        resp = requests.get(url, params={'key': STREAM_P2P_API_KEY}, timeout=15).json()
        upload_url = resp['result']
        # Step 2: POST File
        with open(file_path, 'rb') as f:
            r = requests.post(upload_url, data={'key': STREAM_P2P_API_KEY}, files={'file': f}, timeout=3600)
            print(f"Upload Result: {r.text}")
    except Exception as e:
        print(f"Upload failed: {e}")

def main():
    now_est = get_est_time()
    print(f"Current EST Time: {now_est.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Try TorrentsDB first (kitsu:214 is Conan)
    results = search_torrentsdb("kitsu:214")
    # Merge with Nyaa
    results += get_latest_nyaa()
    
    if not results:
        print("No torrents found.")
        return

    # Pick the first one (usually latest/most seeded)
    target = results[0]
    print(f"Selected: {target['title']} from {target['source']}")
    
    downloaded = download_torrent(target['magnet'])
    if downloaded:
        ss, hs = process_video(downloaded, target['title'])
        upload_to_streamp2p(ss)
        upload_to_streamp2p(hs)

if __name__ == "__main__":
    main()
