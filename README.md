# mov-cli-openmovies

A **mov-cli v4 plugin** that scrapes a configurable, free-to-watch catalog of videos.

By default, it ships with a safe demo catalog containing [Blender open movies](https://studio.blender.org/films/) (CC-BY licensed) and a few public MP4 samples. You can easily swap the source to any other free catalog without editing code — just by changing scraper options in your `mov-cli` config.

---

## ✨ Features

- 🔍 **Search**: fuzzy-match titles from a catalog page or JSON feed  
- 🎬 **Play**: resolve directly to an MP4/WebM/HLS stream  
- 🔧 **Configurable**: switch between modes:
  - `blender-json` → tiny JSON list of demo MP4s (default)
  - `html-list` → any simple HTML index of `<a>` links
  - `css` → any page with predictable selectors

---

## 📦 Installation

Clone and install in editable mode:

```bash
git clone https://github.com/yourname/mov-cli-openmovies
cd mov-cli-openmovies
pip install -e . --config-settings editable_mode=compat
