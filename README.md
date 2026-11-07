# Tray Check

Scan a receipt or DoorDash ticket to load the order. A phone camera then watches the packing tray, and every few seconds Claude checks it against the order. The screen shows what's missing, how long the tray has been open, and whether it's complete.

## 1. Install

Run these inside this folder. Python 3.11 or newer is required.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

## 2. Try it without an API key

```bash
TRAY_MOCK=1 .venv/bin/uvicorn app.main:app --port 8765
```

Open http://localhost:8765 in Chrome or Safari. Use the test panel to add and remove items.

## 3. Connect Claude

Put your OpenRouter key in `.env` as `OPENROUTER_API_KEY`, make sure it has `TRAY_MOCK=0`, and start the app:

```bash
.venv/bin/uvicorn app.main:app --port 8765
```

Tray scans use Claude Haiku 5.5 and receipts use Claude Sonnet 5.5. To save credits, press **Pause scanning** or close the page when you're not testing.

## 4. Connect the phone

**Use a link on any phone (recommended).** This needs `cloudflared` (`brew install cloudflared`):

```bash
.venv/bin/python phone_link.py
```

Add `--mock` to test with fake scans. The terminal shows a QR code and two links:

- **Camera link:** Open it on the phone, allow camera access, and it uses the rear camera. The phone's screen stays on while the page is open on recent phones.
- **Laptop link:** Open it on a laptop to see the status from a distance, since the mounted phone's screen will face the ceiling. If the test panel is on, it also works from here.

Each run creates new links, so scan the new QR code after restarting. Anyone with a link can run scans and spend your credits, so don't post them. The links stop working when you press Ctrl+C.

**Or use the phone as a Mac webcam.** With an iPhone and Mac, sign in to the same Apple account on both, with Wi-Fi and Bluetooth on, then choose the iPhone in the **Camera** dropdown at http://localhost:8765. On Android, use a webcam app such as DroidCam.

Mount the phone 2–3 ft directly above the tray, with the whole tray in frame.

## 5. Scan the order

1. Press **Scan receipt**, then take a photo of the receipt or choose a DoorDash ticket screenshot.
2. Check the list Claude read. Fix any mistakes, and use **Add item** for anything the ticket doesn't list, such as forks or napkins.
3. Press **Start checking**.

If a scan fails, press **Type order** to enter the order by hand.

Update **Looks like** to describe the packaging you actually received, such as "a red paper fries carton with a yellow logo." Better descriptions are the quickest way to fix wrong readings.

Anything in **Note**, such as "no onions," appears as a reminder to check by hand. It doesn't affect whether the tray is complete, because the camera can't see inside packaging.

The order is kept in memory, so scan it again after restarting the app.

## 6. Test

```bash
.venv/bin/python -m pytest
```

Before the demo, remove each item from the tray one at a time and confirm the screen catches it.

## Food-101 food identification

`app/food_id.py` runs a local classifier trained on all 75,750 [Food-101](https://huggingface.co/datasets/ethz/food101) training photos. It adds dish suggestions to each tray scan, and Claude makes the final decision. Suggestions below 60% confidence are dropped.

It scored 77.8% top-1 and 94.4% top-5 on the 25,250 held-out test photos. It knows only 101 dishes, and it was trained on plated food, so it can't identify food inside closed packaging.

To retrain it, which downloads about 5 GB:

```bash
SSL_CERT_FILE=$(.venv/bin/python -c 'import certifi; print(certifi.where())') .venv/bin/python -m training.train_food101 --cache-dir ./food101-data
```

## Settings to tune

| Setting | Where | What it does |
|---|---|---|
| `MIN_SECONDS_BETWEEN_SCANS` | `static/app.js` | Time between scans |
| `MAX_IMAGE_EDGE` | `static/app.js` | Image size. Raise it if small items get missed |
| `TIMER_WARN_SECONDS`, `TIMER_LATE_SECONDS` | `static/app.js` | When the timer turns yellow, then red |
| `MISSES_BEFORE_REMOVED` | `app/tray.py` | Scans an item must be gone before it counts as removed |
| `TRAY_SCAN_MODEL`, `TRAY_RECEIPT_MODEL` | `.env` | Which OpenRouter models to use |

## Code map

- `app/tray.py`: completion logic and timer
- `app/vision.py`: OpenRouter requests for receipts and tray scans, plus fake results for testing
- `app/main.py`: web server
- `phone_link.py`: private HTTPS link for your phone
- `static/`: employee screen
