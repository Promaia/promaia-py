from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from promaia.web.routers import chat as chat_router
import uvicorn
import os
from dotenv import load_dotenv
from promaia.utils.env_writer import get_env_path

dotenv_path = str(get_env_path())
load_dotenv(dotenv_path=dotenv_path)

app = FastAPI(
    title="Maia Web API",
    description="API endpoints for Maia project, including chat functionality.",
    version="0.1.0"
)

# CORS Middleware
# origins to allow, can be more restrictive in production
origins = [
    "http://localhost:5174",  # Local frontend
    "http://localhost:8000",  # Local backend
    # Add production domain here if needed
    # Add other origins as needed, potentially from environment variables for production
]

# Allow all origins if running in a local/dev environment for simplicity,
# otherwise use the specific list.
# This can be refined with a specific environment variable check e.g. if os.getenv("ENV") == "development":
if os.getenv("HOST", "0.0.0.0") in ["0.0.0.0", "localhost", "127.0.0.1"] or os.getenv("PYTHON_ENV") == "development":
    effective_origins = ["*"]
else:
    # For production, you might get this from an environment variable
    # e.g., PRODUCTION_ORIGIN = os.getenv("PRODUCTION_ORIGIN")
    # if PRODUCTION_ORIGIN:
    #     origins.append(PRODUCTION_ORIGIN)
    effective_origins = origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=effective_origins,
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Include routers
app.include_router(chat_router.router, prefix="/api/chat", tags=["Chat"])

@app.get("/api/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}

# It's good practice to allow configuring the host and port via environment variables for deployment
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port) 