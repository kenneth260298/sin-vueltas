from fastapi import FastAPI

app = FastAPI(title="Sin Vueltas")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
