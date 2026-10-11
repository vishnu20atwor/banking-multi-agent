import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

import agents
import bank

app = FastAPI(title="Meridian Bank - Multi-Agent Console")
INDEX = Path(__file__).parent / "static" / "index.html"


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    customer_id: str
    message: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default=[], max_length=20)


class ApprovalRequest(BaseModel):
    id: str
    approved: bool


@app.get("/")
def index():
    return FileResponse(INDEX)


@app.get("/api/health")
def health():
    return {"mode": "llm" if agents.client else "rules", "model": agents.MODEL if agents.client else None}


@app.get("/api/customers")
def customers():
    return bank.list_customers()


@app.post("/api/chat")
def chat(req: ChatRequest):
    if "error" in bank.get_profile(req.customer_id):
        raise HTTPException(404, "Unknown customer")
    if not req.message.strip():
        raise HTTPException(422, "Message is empty")

    def stream():
        try:
            for event in agents.run(req.customer_id, req.message, [t.model_dump() for t in req.history]):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Internal error: {e}'})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/approve")
def approve(req: ApprovalRequest):
    return agents.approve(req.id, req.approved)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
