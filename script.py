import os
import sys
import json
import time
from datetime import datetime
import requests
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
MODRINTH_TOKEN = os.environ.get("MODRINTH_TOKEN")
COLLECTION_ID = os.environ.get("COLLECTION_ID", "QLg88A8c")
LOADERS = [x.strip() for x in os.environ.get("LOADERS", "").split(",") if x.strip()]
GAME_VERSIONS = [x.strip() for x in os.environ.get("GAME_VERSIONS", "").split(",") if x.strip()]
API_BASE = "https://api.modrinth.com/v2"
CACHE_FILE = "last_versions.json"
HEADERS = {"User-Agent": "Modrinth-Check-Bot/1.0.0 (GitHub Actions; replace with your repo URL)"}
if MODRINTH_TOKEN:
    HEADERS["Authorization"] = MODRINTH_TOKEN
CHANNEL_COLORS = {"release": 3066993, "beta": 15844367, "alpha": 15158332}
def http_get_json(url, params=None, retries=3):
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"Attempt {attempt}/{retries} ({url}): {e}")
            time.sleep(2 * attempt)
    return None
def load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
        f.write("\n")
def fetch_latest_version(project):
    """Newest version (by publish date), None on API failure, {} if the project has no version."""
    params = {}
    if LOADERS:
        params["loaders"] = json.dumps(LOADERS)
    if GAME_VERSIONS:
        params["game_versions"] = json.dumps(GAME_VERSIONS)
    versions = http_get_json(f"{API_BASE}/project/{project['id']}/version", params=params)
    if versions is None:
        return None
    if not versions:
        return {}
    return max(versions, key=lambda v: v.get("date_published", ""))
def build_embed(project, v):
    files = v.get("files") or []
    f = next((x for x in files if x.get("primary")), files[0] if files else {})
    size = f.get("size")
    size_txt = f"{size / (1024 * 1024):.2f} MB" if size else "N/A"
    channel = v.get("version_type", "unknown")
    mc = v.get("game_versions") or []
    fields = [
        {"name": "🆔 Version", "value": f"`{v.get('version_number', '?')}`", "inline": True},
        {"name": "📦 Version Minecraft", "value": f"`{', '.join(mc[-4:]) or 'N/A'}`", "inline": True},
        {"name": "🏷️ Channel", "value": channel.upper(), "inline": True},
        {"name": "🧩 Loaders", "value": ", ".join(v.get("loaders") or []) or "N/A", "inline": True},
        {"name": "📁 JAR File", "value": f.get("filename", "N/A"), "inline": False},
        {"name": "⚖️ Size", "value": size_txt, "inline": True},
    ]
    changelog = (v.get("changelog") or "").strip()
    if changelog:
        fields.append({"name": "📝 Changes", "value": changelog[:900] + ("…" if len(changelog) > 900 else ""), "inline": False})
    embed = {
        "title": f"🟢 {project['title'].upper()} - New Version Detected",
        "url": f"https://modrinth.com/project/{project['slug']}/version/{v['id']}",
        "color": CHANNEL_COLORS.get(channel, 15844367),
        "fields": fields,
        "footer": {"text": "Plugin Update Checker - Automatic via GitHub"},
    }
    if f.get("url"):
        embed["fields"].append({"name": "⬇️ Download", "value": f"[{f.get('filename', 'JAR')}]({f['url']})", "inline": False})
    if project.get("icon_url"):
        embed["thumbnail"] = {"url": project["icon_url"]}
    if v.get("date_published"):
        try:
            embed["timestamp"] = datetime.fromisoformat(v["date_published"].replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
    return embed
def send_discord(embed):
    if DRY_RUN:
        print(json.dumps(embed, indent=2, ensure_ascii=False))
        return True
    for _ in range(3):
        try:
            res = requests.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=15)
        except requests.RequestException as e:
            print(f"Discord network error : {e}")
            time.sleep(2)
            continue
        if res.status_code == 429:
            time.sleep(float(res.json().get("retry_after", 2)))
            continue
        if res.status_code < 400:
            return True
        print(f"Discord failure : {res.status_code} - {res.text}")
        return False
    return False
def main():
    if not WEBHOOK_URL and not DRY_RUN:
        print("Error: DISCORD_WEBHOOK is missing.")
        sys.exit(1)
    collection = http_get_json(f"{API_BASE}/collection/{COLLECTION_ID}")
    if not collection:
        print("Collection not found (wrong ID, or private: set MODRINTH_TOKEN).")
        sys.exit(1)
    ids = collection.get("projects", [])
    projects = http_get_json(f"{API_BASE}/projects", params={"ids": json.dumps(ids)})
    if projects is None:
        sys.exit(1)
    cache = load_cache()
    first_run = not cache
    failures = 0
    for p in projects:
        latest = fetch_latest_version(p)
        if latest is None:
            failures += 1
            continue
        if not latest:
            print(f"{p['slug']}: no version found.")
            continue
        known = cache.get(p["id"])
        print(f"{p['slug']} | API: {latest['version_number']} | Cache: {known or '-'}")
        if known == latest["id"]:
            continue
        if first_run or known is None:
            cache[p["id"]] = latest["id"]
            continue
        if send_discord(build_embed(p, latest)):
            cache[p["id"]] = latest["id"]
            print(f"Notification sent for {p['slug']} {latest['version_number']}.")
        else:
            print(f"Send failed for {p['slug']}: will retry on the next run.")
            failures += 1
    if not DRY_RUN:
        save_cache(cache)
    if failures:
        sys.exit(1)
if __name__ == "__main__":
    main()
