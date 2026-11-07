"""Web server: serves the screen, reads receipts, and handles tray scans."""

import asyncio
import os
from pathlib import Path
import secrets

from dotenv import load_dotenv

load_dotenv()  # Must run before app.vision reads settings.

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .food_id import MODEL_PATH, FoodIdentifier
from .tray import Detection, TrayTracker, clean_order
from .vision import MockVision, OpenRouterVision, VisionError

ROOT = Path(__file__).resolve().parent.parent
MAX_FRAME_BYTES = 4 * 1024 * 1024
MOCK_MODE = os.getenv("TRAY_MOCK") == "1"

tracker = TrayTracker()
vision = MockVision() if MOCK_MODE else OpenRouterVision()
order_name = ""

# Food-101 model. The app still works on Claude alone if it hasn't been trained.
food_identifier = None
if not MOCK_MODE and MODEL_PATH.exists():
    food_identifier = FoodIdentifier()

ACCESS_KEY = os.getenv("TRAY_ACCESS_KEY", "")  # Set by phone_link.py for public links.
ACCESS_COOKIE = "tray_access"

app = FastAPI()


@app.middleware("http")
async def require_access_key(request: Request, call_next):
    """Keep strangers out of a public link, since scans spend API credits."""
    if not ACCESS_KEY:
        return await call_next(request)

    link_key = request.query_params.get("key", "")
    if secrets.compare_digest(link_key.encode(), ACCESS_KEY.encode()):
        target = "/?display" if "display" in request.query_params else "/"
        response = RedirectResponse(target, status_code=303)  # Removes the key from the address bar.
        response.set_cookie(ACCESS_COOKIE, ACCESS_KEY, httponly=True, secure=True, samesite="lax")
        return response

    cookie = request.cookies.get(ACCESS_COOKIE, "")
    if not secrets.compare_digest(cookie.encode(), ACCESS_KEY.encode()):
        return PlainTextResponse("Open the full link from phone_link.py.", status_code=403)
    return await call_next(request)


class OrderLine(BaseModel):
    item: str
    qty: int = 1
    looks_like: str = ""
    note: str = ""


class OrderIn(BaseModel):
    order_name: str = ""
    items: list[OrderLine]


class MockTray(BaseModel):
    counts: dict[str, int] = {}
    extra_items: list[str] = []
    hands_over_tray: bool = False


async def read_image(request: Request) -> bytes:
    image = await request.body()
    if len(image) > MAX_FRAME_BYTES:
        raise HTTPException(413, "Image too large.")
    return image


@app.get("/api/state")
def get_state():
    return {"order_name": order_name, "mock_mode": MOCK_MODE, **tracker.snapshot()}


@app.post("/api/order/read")
async def read_order(request: Request):
    """Reads a receipt for review. The order only changes once it's confirmed."""
    image = await read_image(request)
    if not image and not MOCK_MODE:
        raise HTTPException(400, "No image received.")
    try:
        receipt = await vision.read_receipt(image)
    except VisionError as error:
        raise HTTPException(502, str(error)) from error
    if not receipt.is_order or not receipt.items:
        raise HTTPException(422, "Couldn't find an order in that image. Try a clearer photo.")
    return {"order_name": receipt.order_name, "items": clean_order([i.model_dump() for i in receipt.items])}


@app.post("/api/order")
def set_order(order: OrderIn):
    global order_name
    items = clean_order([line.model_dump() for line in order.items])
    if not items:
        raise HTTPException(400, "Add at least one item.")
    order_name = order.order_name.strip()
    vision.set_order(items)
    tracker.set_order(items)
    return get_state()


@app.post("/api/scan")
async def scan(request: Request):
    frame = await read_image(request)
    if not tracker.order:
        return get_state()
    if not frame and not MOCK_MODE:
        raise HTTPException(400, "No camera frame received.")

    generation = tracker.generation
    food_hints = await asyncio.to_thread(food_identifier.identify, frame) if food_identifier else {}
    try:
        detection = await vision.detect(frame, food_hints)
    except VisionError as error:
        raise HTTPException(502, str(error)) from error

    if generation == tracker.generation:  # Skip a scan that started before a reset or new order.
        tracker.apply(detection)
    return {**get_state(), "food_hints": food_hints}


@app.post("/api/reset")
def reset():
    tracker.reset()
    return get_state()


@app.post("/api/mock")
def set_mock_tray(tray: MockTray):
    if not MOCK_MODE:
        raise HTTPException(404, "Test panel is only available in mock mode.")
    vision.detection = Detection(tray.counts, tray.extra_items, tray.hands_over_tray)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
