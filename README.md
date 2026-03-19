# Hypercheap AI Voice Agent

Local development setup (no Docker).

## 1) Install backend requirements

```bash
cd voice_backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) Configure backend `.env`

```bash
cp voice_backend/.env.example voice_backend/.env
```

Edit `voice_backend/.env` and set:
- `FENNEC_API_KEY`
- `GROQ_API_KEY`
- `INWORLD_API_KEY`

You can keep the default values for sample rates, base URL, model, and voice unless you want to customize them.

## 3) Configure frontend `.env`

```bash
cp voice_frontend/.env.example voice_frontend/.env.local
```

Default value should be:

```env
VITE_AGENT_WS_URL=ws://localhost:8000/ws/agent
```

## 4) Run backend

```bash
cd voice_backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 5) Run frontend

```bash
cd voice_frontend
npm install
npm run dev
```

Open `http://localhost:5173`.
