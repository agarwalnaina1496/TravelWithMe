from fastapi import FastAPI

app = FastAPI(
    title="TravelWithMe"
)

@app.get("/")
def root():
    return {
        "message": "TravelWithMe Backend"
    }
