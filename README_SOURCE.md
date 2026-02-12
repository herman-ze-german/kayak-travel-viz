# Kayak Travel Viz (static-hostable)

Turns Kayak trip-history exports (the `trips/*.txt` JSON files) into:

- a **static website** (Leaflet map + filters) you can host on GitHub Pages / Netlify / any dumb web server
- a **static PNG graphic** (world map with your routes)

## Quick start

### 1) Put your Kayak export files here

Copy the exported files into:

```
./data/raw/
```

(Each file should look like JSON with a top-level `tripEvents` array.)

### 2) Build everything

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/build.py --in ./data/raw --out ./site --png ./output/routes.png
```

Then open:

- `site/index.html` (interactive map)
- `output/routes.png` (static graphic)

## Notes / assumptions

- Many segments include full coordinates (e.g., hotels, train stations).
- Some flight segments only contain airport codes in `rawAddress` like `"LHR (LHR)"`. For those, we auto-download an airport dataset from OurAirports and resolve IATA → lat/lon.

## Deploy (static hosting)

Any static host works. For GitHub Pages:

- commit the `site/` folder (or configure Pages to publish it)
- browse to your Pages URL

## Privacy

Your raw trip export is very personal. This project keeps everything local unless you choose to publish the generated `site/` folder.
