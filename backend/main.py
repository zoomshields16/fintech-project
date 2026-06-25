from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def home():
    return {"message": "Backend is running"}


@app.post("/api/run-model")
def run_model():
    return {
        "ticker": "AAPL",
        "fair_value": 195.50,
        "current_price": 182.30,
        "implied_upside": "7.2%"
    }