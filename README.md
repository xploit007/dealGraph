# DealGraph

AI-powered due diligence copilot for investors.

- **Frontend:** Next.js (React) — deck upload, scorecard, competitive graph, claim tracker, CopilotKit chat.
- **Backend:** FastAPI — Strands Agents, AWS Bedrock, Neo4j, MiniMax TTS, analysis pipeline.

---

## Local development

1. **Backend** (port 8000):
   ```bash
   cd backend
   pip install -r requirements.txt
   python main.py
   ```
   Or: `uvicorn main:app --reload --port 8000`

2. **Frontend** (port 3000):
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

3. Copy `.env.example` to `.env` in the project root and in `backend/`, then fill in AWS, Neo4j, MiniMax, and other keys. The frontend expects the API at `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).

See `backend/README.md` for API endpoints, Neo4j seed data, and troubleshooting.

---

## Deploy frontend on Vercel

The app is a monorepo: the **frontend** lives in the `frontend/` folder and can be deployed to Vercel. The **backend** must be hosted separately (e.g. Railway, Render, or an EC2/ECS instance) and is not deployed by Vercel.

### 1. Import the repo in Vercel

- Go to [vercel.com](https://vercel.com) and sign in.
- **Add New** → **Project** and import your Git repository (GitHub, GitLab, or Bitbucket).

### 2. Set the root directory

- In **Project Settings** → **General** → **Root Directory**, set:
  - **Root Directory:** `frontend`
- Click **Edit**, enter `frontend`, and save. Vercel will run `npm install` and `npm run build` from that folder.

### 3. Configure environment variables

In **Project Settings** → **Environment Variables**, add:

| Name | Value | Notes |
|------|--------|--------|
| `NEXT_PUBLIC_API_URL` | `https://your-backend-url.com` | Public URL of your deployed FastAPI backend (no trailing slash). |

Add this for **Production** (and optionally Preview/Development if you use different API URLs).

### 4. Deploy

- Push to your connected branch, or trigger a deployment from the Vercel dashboard.
- After the build finishes, the frontend will be live at your Vercel URL (e.g. `https://your-project.vercel.app`).

### 5. Backend hosting

Host the `backend/` app on a platform that runs Python (e.g. **Railway**, **Render**, **Fly.io**, or AWS). Ensure:

- The backend is reachable over HTTPS.
- CORS allows your Vercel frontend origin (the backend already configures CORS in `main.py`).
- All required env vars (AWS, Neo4j, MiniMax, etc.) are set in the backend’s environment.

Then set that backend URL as `NEXT_PUBLIC_API_URL` in Vercel as in step 3.

---

## Repository structure

```
dealGraph/
├── frontend/          # Next.js app (deploy this to Vercel)
│   ├── src/
│   ├── vercel.json
│   └── package.json
├── backend/           # FastAPI app (host separately)
│   ├── agents/
│   ├── tools/
│   ├── main.py
│   └── requirements.txt
├── .env.example
└── README.md
```
