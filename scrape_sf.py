from playwright.sync_api import sync_playwright
import time
from datetime import datetime
import json
import os
import re
from urllib.parse import urlparse
from pathlib import Path

# Configuration - for Squeaky Feet
SCRIPT_DIR = Path(__file__).parent if '__file__' in globals() else Path.cwd()
SHOWS_DB_FILE = SCRIPT_DIR / "shows_database.json"
SONGS_DB_FILE = SCRIPT_DIR / "songs_database.json"
STATS_HTML_FILE = SCRIPT_DIR / "song_stats.html"
OBSIDIAN_VAULT_PATH = r"E:\Obsidian Review Vault\Reviews\Squeaky Feet\Setlists"
MAX_SHOWS_TO_SCRAPE = 0  # No web scraping for Squeaky Feet

# ---------------------------
# Helpers for Obsidian parsing
# ---------------------------

def clean_wiki_links(s: str) -> str:
    s = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', s)
    s = re.sub(r'\[\[([^\]]+)\]\]', r'\1', s)
    return s.strip()

def parse_obsidian_frontmatter(content: str) -> dict:
    metadata = {}
    fm = re.search(r'^---\s*\n(.*?)\n---\s*', content, re.DOTALL | re.MULTILINE)
    if not fm:
        return metadata
    body = fm.group(1)
    for m in re.finditer(r'^\s*([^:\n]+)\s*:\s*(.*?)\s*$', body, re.MULTILINE):
        key = m.group(1).strip()
        value = m.group(2).strip().strip('"').strip("'")
        
        if not value:
            continue
            
        if key.lower() == 'date':
            dm = re.search(r'(\d{4}-\d{2}-\d{2})', value)
            if dm:
                value = dm.group(1)
        
        metadata[key] = value
    return metadata

def extract_review_section(content: str) -> str:
    """Extract review/notes content between third --- and end of file."""
    lines = content.splitlines()
    
    markers = []
    for i, line in enumerate(lines):
        if line.strip() == '---':
            markers.append(i)
    
    if len(markers) < 3:
        return ""
    
    start_line = markers[2] + 1
    
    if len(markers) >= 4:
        end_line = markers[3]
        review_lines = lines[start_line:end_line]
    else:
        review_lines = lines[start_line:]
    
    review_content = '\n'.join(review_lines).strip()
    review_content = re.sub(r'\*\*MVP:\*\*\s*$', '', review_content, flags=re.MULTILINE).strip()
    
    return review_content

def parse_obsidian_setlist(content: str) -> dict:
    """Parse setlist preserving arrows and timing information."""
    lines = content.splitlines()
    sets = {}
    
    markers = []
    for i, line in enumerate(lines):
        if line.strip() == '---':
            markers.append(i)
    
    if len(markers) < 2:
        return {}
    
    frontmatter_end = markers[1]
    
    if len(markers) >= 3:
        setlist_end = markers[2]
    else:
        setlist_end = len(lines)
    
    for idx in range(frontmatter_end + 1, setlist_end):
        line = lines[idx].strip()
        
        if not line:
            continue
        
        m = re.match(r'\*{2,3}([^*:]+)\s*:\*{2,3}\s*(.+)', line, re.IGNORECASE)
        if m:
            label = m.group(1).strip()
            content_text = clean_wiki_links(m.group(2))
            sets[label] = content_text
            continue
    
    return sets

def parse_songs_from_setlist(set_content: str) -> list:
    """Extract song names, removing timing but keeping all occurrences."""
    if not set_content or not set_content.strip():
        return []

    SONGS_WITH_COMMAS = [
        "April 29, 1992 (Miami)",
        "Smooth, Relax, Down",
        "To Be Young (Is To Be Sad, Is To Be High)"
    ]
    
    protected_content = set_content
    replacements = {}
    for idx, song in enumerate(SONGS_WITH_COMMAS):
        placeholder = f"___PROTECTED_SONG_{idx}___"
        patterns = [
            re.escape(song) + r'\s*\(\d+[\d:]*\)',
            re.escape(song) + r'\s*\[\s*\d+[\d:]*\s*\]',
            re.escape(song)
        ]
        for pattern in patterns:
            if re.search(pattern, protected_content, re.IGNORECASE):
                protected_content = re.sub(pattern, placeholder, protected_content, flags=re.IGNORECASE)
                replacements[placeholder] = song
                break

    content = re.sub(r'(?:->|>|→|–|—)', ',', protected_content)
    parts = [s.strip() for s in re.split(r'\s*(?:,|;|\|)\s*', content) if s.strip()]

    songs = []
    for part in parts:
        if part in replacements:
            songs.append(replacements[part])
            continue
            
        song_name = re.sub(r'\s*\(\d+[\d:]*\)', '', part)
        song_name = re.sub(r'\s*\[\s*\d+[\d:]*\s*\]', '', song_name)
        song_name = re.sub(r'\s+\d+$', '', song_name)
        song_name = re.sub(r'\s+', ' ', song_name.rstrip('-').strip())
        
        if song_name:
            songs.append(song_name)
    
    return songs

def scan_obsidian_vault(vault_path):
    print(f"\nScanning Obsidian vault: {vault_path}")
    vault = Path(vault_path)
    obsidian_shows = {}

    if not vault.exists():
        print("ERROR: Vault path does not exist!")
        return obsidian_shows

    md_files = list(vault.rglob("*.md"))
    print(f"Found {len(md_files)} markdown files")
    
    for idx, md_file in enumerate(md_files, 1):
        if idx % 50 == 0:
            print(f"Processing Obsidian files: {idx}/{len(md_files)}")
        
        if '_' in md_file.stem or not re.match(r'^\d{4}-\d{2}-\d{2}$', md_file.stem):
            continue
        
        try:
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"Error reading {md_file}: {e}")
            continue

        metadata = parse_obsidian_frontmatter(content)
        date = None

        if 'date' in metadata and metadata['date'].strip():
            date = metadata['date'].strip()
        else:
            fn_match = re.search(r'(\d{4}-\d{2}-\d{2})', md_file.stem)
            if fn_match:
                date = fn_match.group(1)

        if not date:
            continue

        sets = parse_obsidian_setlist(content)
        if not sets:
            print(f"No setlist found in {md_file.name} (date: {date})")
            continue

        print(f"✓ Found show: {date} - {md_file.name} - {len(sets)} sets")

        review = extract_review_section(content)

        show_data = {
            "date": date,
            "venue": metadata.get('venue', 'Unknown Venue'),
            "city": metadata.get('city', ''),
            "state": metadata.get('state', ''),
            "tour": metadata.get('tour', ''),
            "show_number": metadata.get('show_number', ''),
            "rating": metadata.get('rating', ''),
            "sets": sets,
            "notes": [review] if review else [],
            "source": "obsidian",
            "obsidian_file": str(md_file),
            "url": ""
        }
        obsidian_shows[date] = show_data

    print(f"Found {len(obsidian_shows)} shows in Obsidian")
    return obsidian_shows

# ---------------------------
# Songs database building
# ---------------------------

def build_song_database(shows_db):
    """Build a song database with all occurrences preserved."""
    print("\nBuilding song database...")
    song_db = {}

    for show_data in shows_db.values():
        for set_name, set_content in show_data.get("sets", {}).items():
            songs = parse_songs_from_setlist(set_content)
            for song in songs:
                clean_song = song
                if clean_song not in song_db:
                    song_db[clean_song] = []

                song_db[clean_song].append({
                    "date": show_data.get("date", ""),
                    "url": show_data.get("url", ""),
                    "set": set_name
                })

    print(f"Found {len(song_db)} unique songs")
    return song_db

# ---------------------------
# Database load/save
# ---------------------------

def save_database(data, filename):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved {filename}")

def load_database(filename):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

# ---------------------------
# Stats HTML generation
# ---------------------------

def generate_stats_html(songs_db):
    html = ["<html><body><h1>Song Stats - Squeaky Feet</h1><ul>"]
    for song, plays in songs_db.items():
        html.append(f"<li><b>{song}</b> ({len(plays)} plays)<ul>")
        for play in sorted(plays, key=lambda x: x['date']):
            date_display = play['date'].split('/')[0]
            url_display = play.get('url', '#')
            set_name = play.get('set', '')
            html.append(f'<li>{date_display} - {set_name}</li>')
        html.append("</ul></li>")
    html.append("</ul></body></html>")
    return "\n".join(html)

# ---------------------------
# Main - Obsidian Only
# ---------------------------

def main():
    print(f"Working directory: {SCRIPT_DIR}")
    print(f"Output files will be saved to: {SCRIPT_DIR}")
    
    shows_db = load_database(SHOWS_DB_FILE)
    print(f"Loaded database: {len(shows_db)} shows")

    # --- Obsidian ONLY ---
    obsidian_shows = scan_obsidian_vault(OBSIDIAN_VAULT_PATH)
    for date, show_data in obsidian_shows.items():
        shows_db[date] = show_data
    print(f"After Obsidian: {len(shows_db)} shows")

    # --- Save ---
    save_database(shows_db, SHOWS_DB_FILE)
    songs_db = build_song_database(shows_db)
    save_database(songs_db, SONGS_DB_FILE)

    # --- Generate stats HTML ---
    stats_html = generate_stats_html(songs_db)
    with open(STATS_HTML_FILE, "w", encoding="utf-8") as f:
        f.write(stats_html)
    print(f"Saved {STATS_HTML_FILE}")

    print(f"\nComplete! {len(shows_db)} shows, {len(songs_db)} songs")
    print(f"\nAll files saved to: {SCRIPT_DIR}")

if __name__ == "__main__":
    main()