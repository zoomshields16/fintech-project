from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ModelRequest(BaseModel):
    ticker: str


@app.get("/")
def home():
    return {"message": "Backend is running"}


@app.post("/api/run-model")
def run_model(request: ModelRequest):
    ticker = request.ticker.upper()
    return {
        "ticker": ticker,
        "fair_value": 195.50,
        "current_price": 182.30,
        "implied_upside": "7.2%"
    }