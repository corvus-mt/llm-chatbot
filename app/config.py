import os

from dotenv import load_dotenv

from pydantic import BaseModel


class PersonaConfig(BaseModel):
    name: str = "별빛 친구"
    description: str = "9세 아이에게 안전하고 친절하게 설명하는 챗봇"
    tone: str = "밝고 다정함"


load_dotenv()


class Settings(BaseModel):
    app_name: str = "Kid Chatbot"
    persona: PersonaConfig = PersonaConfig()
    llm_provider: str = "gemini"
    llm_api_key: str = os.getenv("GEMINI_API_KEY", "")
    llm_model: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash-latest")


settings = Settings()
