"""A simple FastAPI app — add a POST /items endpoint."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

items_db: list[dict] = [
    {"id": 1, "name": "Widget", "price": 9.99},
    {"id": 2, "name": "Gadget", "price": 24.99},
]


@app.get("/items")
def list_items():
    return items_db


@app.get("/items/{item_id}")
def get_item(item_id: int):
    for item in items_db:
        if item["id"] == item_id:
            return item
    raise HTTPException(status_code=404, detail="Item not found")
