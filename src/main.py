import requests
from fastapi import FastAPI

N8N_TRIP_MATCHER_WEBHOOK_URL = "https://twm.app.n8n.cloud/webhook/trip-matcher"

app = FastAPI(
    title="TravelWithMe"
)

@app.post("/trip-matcher")
async def trip_match(request: dict):

    response = requests.post(
        N8N_TRIP_MATCHER_WEBHOOK_URL,
        json=request
    )

    return response.json()
