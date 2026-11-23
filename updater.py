"""
Auto-updater: on start, checks the GitHub repository in config.json ("update_repo": "owner/repo")
for a newer version and copies in the new code. Never touches config.json, the save folder,
or start.bat. If anything goes wrong it just says so and the lookout starts with the code it has.
"""
import io
import json
import os
import shutil
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
KEEP = {"config.json", "save", ".version", "start.bat"}


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "lookout-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def main():
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except Exception as e:
        print("  Updater: couldn't read config.json:", e)
        return
    repo = str(cfg.get("update_repo") or "").strip().strip("/")
    branch = str(cfg.get("update_branch") or "main")
    if not repo or "/" not in repo:
        print("  Updater: no update_repo set in config.json, skipping.")
        return
    try:
        info = json.loads(get(f"https://api.github.com/repos/{repo}/commits/{branch}"))
        latest = info["sha"]
    except Exception as e:
        print("  Updater: couldn't reach GitHub, starting with current version.", str(e)[:80])
        return
    vfile = os.path.join(HERE, ".version")
    current = open(vfile).read().strip() if os.path.exists(vfile) else ""
    if current == latest:
        print("  Updater: already up to date.")
        return
    print("  Updater: new version found, downloading...")
    try:
        data = get(f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}", timeout=60)
        z = zipfile.ZipFile(io.BytesIO(data))
        root = z.namelist()[0].split("/")[0] + "/"
        count = 0
        for name in z.namelist():
            rel = name[len(root):]
            if not rel or name.endswith("/"):
                continue
            top = rel.split("/")[0]
            if top in KEEP or top.startswith("."):
                continue
            dest = os.path.join(HERE, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(name) as src, open(dest + ".new", "wb") as out:
                shutil.copyfileobj(src, out)
            os.replace(dest + ".new", dest)
            count += 1
        with open(vfile, "w") as f:
            f.write(latest)
        print(f"  Updater: updated {count} files to {latest[:7]}.")
    except Exception as e:
        print("  Updater: update failed, starting with current version.", str(e)[:120])


if __name__ == "__main__":
    main()
    sys.exit(0)
