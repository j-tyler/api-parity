"""Mock FastAPI server for api-parity integration tests.

This server implements the test API from tests/fixtures/test_api.yaml.
It can be run standalone or spawned as a subprocess by pytest fixtures.

Usage:
    python -m tests.integration.mock_server --port 9999
    python -m tests.integration.mock_server --port 9998 --variant b

The --variant flag introduces controlled differences to test comparison logic:
    - variant "a" (default): Standard behavior
    - variant "b": Slight price rounding differences, shuffled arrays, etc.
"""

from __future__ import annotations

import argparse
import random
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field


# --- Storage ---

class Storage:
    """In-memory storage for test data."""

    def __init__(self):
        self.widgets: dict[str, dict] = {}
        self.users: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.start_time = datetime.now(timezone.utc)
        self._seed_data()

    def _seed_data(self):
        """Seed with some initial data for GET tests."""
        # Seed users
        for i, (username, roles) in enumerate([
            ("alice", ["admin", "user"]),
            ("bob", ["user", "moderator"]),
            ("charlie", ["guest"]),
        ]):
            user_id = f"00000000-0000-0000-0000-00000000000{i+1}"
            self.users[user_id] = {
                "id": user_id,
                "username": username,
                "email": f"{username}@example.com",
                "roles": roles,
                "scores": {
                    "reputation": 75.5 + i * 5,
                    "activity": 60.0 + i * 10,
                    "trust": 0.85 + i * 0.05,
                },
                "last_login": datetime.now(timezone.utc).isoformat(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        # Seed widgets
        for i, (name, category, price) in enumerate([
            ("Gizmo Pro", "gadgets", 29.99),
            ("Super Wrench", "tools", 15.50),
            ("Bolt Pack", "parts", 5.99),
        ]):
            widget_id = f"10000000-0000-0000-0000-00000000000{i+1}"
            self.widgets[widget_id] = {
                "id": widget_id,
                "name": name,
                "description": f"A fine {name.lower()}",
                "price": price,
                "category": category,
                "in_stock": True,
                "stock_count": 100 - i * 20,
                "tags": ["popular", "sale"] if i == 0 else ["new"],
                "metadata": {"sku": f"SKU-{i:04d}"},
                "created_at": datetime.now(timezone.utc).isoformat(),
            }


storage = Storage()


# --- Variant behavior ---

class VariantBehavior:
    """Controls variant-specific behavior for testing comparisons."""

    def __init__(self, variant: str = "a"):
        self.variant = variant

    def adjust_price(self, price: float) -> float:
        """Variant B adds small price difference within tolerance."""
        if self.variant == "b":
            # Add small difference within 0.01 tolerance (no rounding to preserve difference)
            return price + 0.001
        return price

    def shuffle_array(self, arr: list) -> list:
        """Variant B shuffles arrays."""
        if self.variant == "b" and len(arr) > 1:
            result = arr.copy()
            random.shuffle(result)
            return result
        return arr

    def generate_request_id(self) -> str:
        """Always generates unique request IDs."""
        return str(uuid.uuid4())

    def generate_confirmation_code(self) -> str:
        """Always generates unique confirmation codes."""
        return f"CONF-{uuid.uuid4().hex[:8].upper()}"


behavior = VariantBehavior()


# --- Request/Response Models ---

class WidgetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    price: float = Field(gt=0)
    category: str = Field(pattern="^(gadgets|tools|parts)$")
    in_stock: bool = True
    stock_count: int = Field(default=0, ge=0)
    tags: list[str] = Field(default_factory=list, max_length=10)
    metadata: dict[str, Any] | None = None


class WidgetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    price: float | None = Field(default=None, gt=0)
    in_stock: bool | None = None
    stock_count: int | None = Field(default=None, ge=0)
    tags: list[str] | None = Field(default=None, max_length=10)
    metadata: dict[str, Any] | None = None


class OrderItemCreate(BaseModel):
    widget_id: str
    quantity: int = Field(ge=1, le=100)


class OrderCreate(BaseModel):
    user_id: str
    items: list[OrderItemCreate] = Field(min_length=1)


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


# --- App ---

app = FastAPI(
    title="api-parity Integration Test API",
    version="1.0.0",
    lifespan=lifespan,
)


# --- Endpoints ---

@app.get("/health")
async def health_check(response: Response):
    now = datetime.now(timezone.utc)
    uptime = (now - storage.start_time).total_seconds()
    response.headers["X-Request-Id"] = behavior.generate_request_id()
    return {
        "status": "healthy",
        "timestamp": now.isoformat(),
        "uptime_seconds": uptime,
        "version": "1.0.0",
    }


@app.get("/widgets")
async def list_widgets(
    response: Response,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock: bool | None = None,
):
    widgets = list(storage.widgets.values())

    if category:
        widgets = [w for w in widgets if w["category"] == category]
    if min_price is not None:
        widgets = [w for w in widgets if w["price"] >= min_price]
    if max_price is not None:
        widgets = [w for w in widgets if w["price"] <= max_price]
    if in_stock is not None:
        widgets = [w for w in widgets if w["in_stock"] == in_stock]

    # Apply variant behavior
    result_widgets = []
    for w in widgets:
        widget_copy = w.copy()
        widget_copy["price"] = behavior.adjust_price(w["price"])
        widget_copy["tags"] = behavior.shuffle_array(w.get("tags", []))
        result_widgets.append(widget_copy)

    response.headers["X-Request-Id"] = behavior.generate_request_id()
    response.headers["X-Total-Count"] = str(len(result_widgets))

    return {
        "widgets": result_widgets,
        "total": len(result_widgets),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/widgets", status_code=201)
async def create_widget(widget: WidgetCreate, response: Response):
    widget_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    widget_data = {
        "id": widget_id,
        "name": widget.name,
        "description": widget.description,
        "price": behavior.adjust_price(widget.price),
        "category": widget.category,
        "in_stock": widget.in_stock,
        "stock_count": widget.stock_count,
        "tags": behavior.shuffle_array(widget.tags),
        "metadata": widget.metadata,
        "created_at": now,
    }

    storage.widgets[widget_id] = widget_data

    response.headers["X-Request-Id"] = behavior.generate_request_id()
    response.headers["Location"] = f"/widgets/{widget_id}"

    return widget_data


@app.get("/widgets/{widget_id}")
async def get_widget(widget_id: str, response: Response):
    if widget_id not in storage.widgets:
        raise HTTPException(status_code=404, detail={
            "error": "not_found",
            "message": f"Widget {widget_id} not found",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": behavior.generate_request_id(),
        })

    widget = storage.widgets[widget_id].copy()
    widget["price"] = behavior.adjust_price(widget["price"])
    widget["tags"] = behavior.shuffle_array(widget.get("tags", []))

    response.headers["X-Request-Id"] = behavior.generate_request_id()
    response.headers["ETag"] = f'"{uuid.uuid4().hex[:16]}"'

    return widget


@app.put("/widgets/{widget_id}")
async def update_widget(widget_id: str, update: WidgetUpdate, response: Response):
    if widget_id not in storage.widgets:
        raise HTTPException(status_code=404, detail={
            "error": "not_found",
            "message": f"Widget {widget_id} not found",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": behavior.generate_request_id(),
        })

    widget = storage.widgets[widget_id]

    if update.name is not None:
        widget["name"] = update.name
    if update.description is not None:
        widget["description"] = update.description
    if update.price is not None:
        widget["price"] = update.price
    if update.in_stock is not None:
        widget["in_stock"] = update.in_stock
    if update.stock_count is not None:
        widget["stock_count"] = update.stock_count
    if update.tags is not None:
        widget["tags"] = update.tags
    if update.metadata is not None:
        widget["metadata"] = update.metadata

    widget["updated_at"] = datetime.now(timezone.utc).isoformat()

    result = widget.copy()
    result["price"] = behavior.adjust_price(widget["price"])
    result["tags"] = behavior.shuffle_array(widget.get("tags", []))

    response.headers["X-Request-Id"] = behavior.generate_request_id()

    return result


@app.delete("/widgets/{widget_id}", status_code=204)
async def delete_widget(widget_id: str, response: Response):
    if widget_id not in storage.widgets:
        raise HTTPException(status_code=404, detail={
            "error": "not_found",
            "message": f"Widget {widget_id} not found",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": behavior.generate_request_id(),
        })

    del storage.widgets[widget_id]
    response.headers["X-Request-Id"] = behavior.generate_request_id()
    return Response(status_code=204)


@app.get("/users/{user_id}/profile")
async def get_user_profile(user_id: str, response: Response):
    if user_id not in storage.users:
        raise HTTPException(status_code=404, detail={
            "error": "not_found",
            "message": f"User {user_id} not found",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": behavior.generate_request_id(),
        })

    user = storage.users[user_id].copy()
    user["roles"] = behavior.shuffle_array(user["roles"])

    # Apply small score variations for variant b
    if behavior.variant == "b":
        user["scores"] = {
            "reputation": round(user["scores"]["reputation"] + 0.05, 1),
            "activity": round(user["scores"]["activity"] + 0.05, 1),
            "trust": round(user["scores"]["trust"] + 0.005, 3),
        }

    response.headers["X-Request-Id"] = behavior.generate_request_id()

    return user


@app.post("/orders", status_code=201)
async def create_order(order: OrderCreate, response: Response):
    # Validate user exists
    if order.user_id not in storage.users:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_user",
            "message": f"User {order.user_id} not found",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": behavior.generate_request_id(),
        })

    # Build order items
    order_items = []
    subtotal = 0.0

    for item in order.items:
        if item.widget_id not in storage.widgets:
            raise HTTPException(status_code=400, detail={
                "error": "invalid_widget",
                "message": f"Widget {item.widget_id} not found",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": behavior.generate_request_id(),
            })

        widget = storage.widgets[item.widget_id]
        unit_price = behavior.adjust_price(widget["price"])
        item_subtotal = unit_price * item.quantity

        order_items.append({
            "widget_id": item.widget_id,
            "name": widget["name"],
            "quantity": item.quantity,
            "unit_price": unit_price,
            "subtotal": round(item_subtotal, 2),
        })
        subtotal += item_subtotal

    subtotal = round(subtotal, 2)
    tax = round(subtotal * 0.08, 2)  # 8% tax
    total = round(subtotal + tax, 2)

    order_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    order_data = {
        "id": order_id,
        "user_id": order.user_id,
        "request_id": behavior.generate_request_id(),
        "confirmation_code": behavior.generate_confirmation_code(),
        "items": order_items,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "status": "pending",
        "created_at": now,
    }

    storage.orders[order_id] = order_data

    response.headers["X-Request-Id"] = behavior.generate_request_id()
    response.headers["X-Confirmation-Code"] = order_data["confirmation_code"]

    return order_data


@app.get("/orders/{order_id}")
async def get_order(order_id: str, response: Response):
    if order_id not in storage.orders:
        raise HTTPException(status_code=404, detail={
            "error": "not_found",
            "message": f"Order {order_id} not found",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": behavior.generate_request_id(),
        })

    order = storage.orders[order_id].copy()
    # Generate new volatile fields on each read
    order["request_id"] = behavior.generate_request_id()

    response.headers["X-Request-Id"] = behavior.generate_request_id()

    return order


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Mock server for api-parity tests")
    parser.add_argument("--port", type=int, default=9999, help="Port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--variant", choices=["a", "b"], default="a",
                        help="Behavior variant (a=standard, b=slight differences)")
    args = parser.parse_args()

    global behavior
    behavior = VariantBehavior(args.variant)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
