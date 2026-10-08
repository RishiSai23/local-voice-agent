from fastapi import FastAPI
from pydantic import BaseModel

from agent import ask_agent


app = FastAPI(title="Local Voice Agent")


class ChatRequest(BaseModel):
    message: str


@app.get("/")
def root():
    return {
        "status": "running",
        "agent": "Local Voice Agent"
    }


@app.post("/chat")
def chat(request: ChatRequest):
    reply = ask_agent(request.message)

    return {
        "reply": reply
    }