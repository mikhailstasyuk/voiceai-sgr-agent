from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Fennec
    fennec_api_key: str
    fennec_sample_rate: int = 16000
    fennec_channels: int = 1

    # Groq (OpenAI-compatible)
    groq_api_key: str
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-120b"

    # Inworld TTS
    inworld_api_key: str
    inworld_model_id: str = "inworld-tts-1"
    inworld_voice_id: str = "Olivia"
    inworld_sample_rate: int = 48000


settings = Settings()
