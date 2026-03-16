# Hypercheap AI Voice Agent

<p align="center">
  <img alt="Hypercheap Voice Agent" src="assets/hero.png" width="900">
</p>

### Welcome to the cheapest, lowest-latency, and best performing AI voice agent possible today. 

**This stack achieves:**
- Total costs as low as **\$0.28 per hour** ($0.0046 per minute)
- Latency between 600-800ms from end of speech to first audio frame
- Full server VAD controls thanks to Fennec-ASR with instant barge-in capability
- State-of-the-art voice performance thanks to inworld.ai

From a cost perspective, the Hypercheap stack is: 
- **32x** cheaper than OpenAI Realtime
- **20x** cheaper than Elevenlabs Voice Agents
- **10x** cheaper than most Vapi stacks

> **Stack:** Fennec (Realtime ASR) → Groq (LLM via OpenAI-compatible API + strict JSON schema output) → Inworld (streamed TTS)

---
# Demo

https://github.com/user-attachments/assets/831f5196-de5b-41d7-bb4c-b03e9df07f53

---

# Try the hosted version 

[https://hypercheap-voiceai.onrender.com/](https://hypercheap-voiceai.onrender.com/)

# Setup

## 1) Create accounts & grab keys

### A. Fennec ASR (Realtime speech-to-text)

1. Go to **Fennec** and create a free account (10 hours included): [https://fennec-asr.com](https://fennec-asr.com)
2. Create your first **API key** in the dashboard.

You’ll paste that key into your .env as `FENNEC_API_KEY`.

---

### B. Groq (LLM — OpenAI-compatible)

1. Create a **Groq** account and generate an API key: [https://console.groq.com/keys](https://console.groq.com/keys)
2. This setup calls Groq via the **OpenAI-compatible** endpoint. The default base URL in this repo is `https://api.groq.com/openai/v1` and the default model is `openai/gpt-oss-120b`.
3. The backend requests strict structured output (`response_format.type=json_schema`) and validates it before sending text to TTS.

You’ll paste the API key as `GROQ_API_KEY` into your `.env`. Keep the provided base URL/model, or swap to another Groq model that supports structured outputs.

---

### C. Inworld (Text‑to‑Speech)

1. Create an **Inworld** account and open the TTS page: [https://inworld.ai/tts](https://inworld.ai/tts)
2. In the **Portal**, generate an **API key (Base64)** and **copy the Base64 value**: [https://portal.inworld.ai](https://portal.inworld.ai)
3. (Optional) Choose a voice and set your defaults (model `inworld-tts-1`, 48 kHz, etc.). You can also clone voices with the inworld platform at no extra cost. 

> The backend expects the **Base64** form for Basic auth. In the portal there’s a *“Copy base64”* button—use that.

Paste the Base64 API key as `INWORLD_API_KEY`. You can also set `INWORLD_VOICE_ID` (e.g. `Olivia`).

---

## 2) Fill your `.env`

Create `voice_backend/.env` (or copy from `voice_backend/.env.example`) and fill the values you just collected:

```env
# Fennec ASR
FENNEC_API_KEY=...
FENNEC_SAMPLE_RATE=16000
FENNEC_CHANNELS=1

# Groq (OpenAI-compatible)
GROQ_API_KEY=...
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_MODEL=openai/gpt-oss-120b

# Inworld TTS
INWORLD_API_KEY=...  
INWORLD_MODEL_ID=inworld-tts-1
INWORLD_VOICE_ID=Olivia
INWORLD_SAMPLE_RATE=48000
```

For the frontend, create `voice_frontend/.env.local` and point to your backend WebSocket:

```env
VITE_AGENT_WS_URL=ws://localhost:8000/ws/agent
```

---

## 3) Run locally

**Backend**

```bash
cd voice_backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend**

```bash
cd voice_frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) and click the mic button to start chatting.

---

## 4) Docker (optional)

Build the container and run it with your `.env`:

```bash
docker build -t hypercheap-agent:latest -f voice_backend/Dockerfile .
docker run --env-file voice_backend/.env -p 8000:8000 hypercheap-agent:latest
```

If you also want the built UI served by FastAPI, run `npm run build` in `voice_frontend` first — it outputs to `voice_backend/app/static`.

---

## 5) Cost Breakdown (how it’s \~\$0.28/hr)

* **ASR (Fennec, streaming):** as low as **\$0.11/hr** on scale tier (or **\$0.16/hr** starter), with a generous free trial
* **LLM (Groq `openai/gpt-oss-120b`):** pricing varies by model tier and can be checked in the Groq console pricing page.
* **TTS (Inworld):** **\$5.00 / 1M characters**, which they estimate as **≈\$0.25 per audio‑hour** of generated speech.

> **Example:** In a typical chat, the AI speaks \~40–60% of the time.
>
> • Fennec ASR: \~\$0.11/hr
> • Inworld TTS: \$0.25 × 0.5 = **\$0.125/hr** (assumes 30 min of AI speech per session hour)
> • Groq LLM tokens: usually small relative to TTS/ASR in short, concise responses (verify against your selected model pricing).
>
> **Total:** **\~\$0.25–\$0.35 per session hour**

> Actual costs vary with ASR plan, talk ratio, and how verbose the model is. The defaults in this repo (short replies, low max tokens) are tuned to keep costs as low as possible.

---


## 6) Customizations

* Swap voices (Inworld) or LLM models (Groq) by changing the env vars.
* Tune VAD in `voice_backend/app/agent/fennec_ws.py` for faster/longer turns. It is extremely aggressive by default, which can cut off slow speakers.
* Swap Groq LLM models for better intelligence at the price of increased cost and higher latency
* Add in the audio markups into the LLM prompt, and switch the model to the Inworld `inworld-tts-1-max` model for increased realism (at double the cost and ~50% increased latency).
* Adjust history length in `voice_backend/session.py` by altering this: `self._max_history_msgs`. This will increase costs.

MIT © Jordan Gibbs
